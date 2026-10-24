"""Frozen, non-destructive delivery exports from the acceptance desk.

The selected version's recorded points are authoritative.  The current H3
window is deliberately not consulted after the preview manifest is built.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET

try:
    from .preview_desk import FPS, _project_file, subtitle_srt
    from .premiere_native import project_bytes as premiere_project_bytes
except ImportError:  # Standalone test runner imports modules from this folder.
    from preview_desk import FPS, _project_file, subtitle_srt
    from premiere_native import project_bytes as premiere_project_bytes


EXPORT_KINDS = {"movie", "fcp_xml"}
MARKER_FIELDS = (
    "shot", "version", "track", "source_file", "source_total_frames",
    "source_in_frame", "source_out_frame_exclusive", "timeline_in_frame",
    "timeline_out_frame_exclusive", "full_clip_start_frame",
    "full_clip_end_frame_exclusive", "timeline_points_recorded",
    "incoming_transition", "parent_version_id",
)


class ExportCancelled(Exception):
    pass


def srt_to_webvtt(srt_text: str) -> str:
    """Serve an exported SRT as a browser text track without altering the package."""
    normalized = srt_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues = re.sub(
        r"(?m)^(\d{2,}:\d{2}:\d{2}),(\d{3}) --> (\d{2,}:\d{2}:\d{2}),(\d{3})$",
        r"\1.\2 --> \3.\4", normalized,
    )
    return "WEBVTT\n\n" + cues


def _safe_name(value: str) -> str:
    return re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._")[:90] or "shot"


def _media_path(descriptor: dict[str, str], project: dict[str, Any],
                output_root: Path) -> Path:
    subfolder = str(descriptor.get("subfolder") or "").replace("\\", "/")
    filename = str(descriptor.get("filename") or "")
    if not filename or Path(filename).name != filename:
        raise ValueError("素材文件名无效。")
    if descriptor.get("type") == "input":
        expected = f"xiaoyue_mv_projects/{project['project_id']}/source/audio"
        if subfolder != expected:
            raise ValueError("原曲路径不属于本工程。")
        return _project_file(Path(project["paths"]["input"]),
                             f"source/audio/{filename}")
    if descriptor.get("type") == "output":
        expected = f"xiaoyue_mv_projects/{project['project_id']}/generations"
        if subfolder != expected and not subfolder.startswith(expected + "/"):
            raise ValueError("镜头素材路径不属于本工程。")
        return _project_file(output_root, f"{subfolder}/{filename}")
    raise ValueError("素材类型无效。")


def probe_video(path: Path, ffprobe: str = "ffprobe") -> dict[str, int]:
    command = [ffprobe, "-v", "error",
               "-show_entries", "stream=codec_type,nb_frames,width,height,sample_rate,channels,bits_per_sample,bits_per_raw_sample",
               "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                            check=True)
    streams = json.loads(result.stdout).get("streams") or []
    if not streams:
        raise ValueError(f"视频无法读取：{path.name}")
    stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    if stream is None:
        raise ValueError(f"视频无法读取：{path.name}")
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    depth = next((int(value) for value in
                  (audio.get("bits_per_sample"), audio.get("bits_per_raw_sample"))
                  if str(value).isdigit() and int(value) > 0), 16)
    return {key: int(stream.get(key) or 0) for key in
            ("nb_frames", "width", "height")} | {
                "audio_channels": int(audio.get("channels") or 0),
                "audio_sample_rate": int(audio.get("sample_rate") or 48000),
                "audio_depth": depth,
            }


def probe_audio(path: Path, ffprobe: str = "ffprobe") -> dict[str, int]:
    command = [ffprobe, "-v", "error", "-select_streams", "a:0",
               "-show_entries", "stream=sample_rate,channels,bits_per_sample,bits_per_raw_sample,duration:format=duration",
               "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                            check=True)
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"音频无法读取：{path.name}")
    stream = streams[0]
    seconds = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"音频时长无效：{path.name}")
    depth = next((int(value) for value in
                  (stream.get("bits_per_sample"), stream.get("bits_per_raw_sample"))
                  if str(value).isdigit() and int(value) > 0), 16)
    return {"sample_rate": int(stream.get("sample_rate") or 48000),
            "channels": int(stream.get("channels") or 2),
            "depth": depth, "frames": math.floor(seconds * FPS + 1e-6)}


def _allocate_edit_tracks(clips: list[dict[str, Any]]) -> None:
    placed: list[tuple[int, int, int]] = []
    by_uid: dict[str, dict[str, Any]] = {}
    normal_preference = 0
    previous_track = -1
    for clip_index, clip in enumerate(clips):
        start, end = clip["timeline_in_frame"], clip["timeline_out_frame_exclusive"]

        def available(track: int) -> bool:
            return all(track != old or end <= old_start or start >= old_end
                       for old, old_start, old_end in placed)

        parent = by_uid.get(clip["parent_shot_uid"])
        previous = clips[clip_index - 1] if clip_index else None
        overlapping_fade = bool(
            clip["incoming_transition"] == "crossfade" and previous
            and clip["timeline_in_frame"] < previous["timeline_out_frame_exclusive"])
        if ((clip["incoming_transition"] == "long_take" and parent)
                or overlapping_fade):
            track = (parent["track"] if parent and not overlapping_fade
                     else previous["track"]) + 1
            while not available(track):
                track += 1
        else:
            preferred = (normal_preference, 1 - normal_preference)
            track = next((t for t in preferred if t != previous_track and available(t)), None)
            if track is None:
                track = 2
                while track == previous_track or not available(track):
                    track += 1
            normal_preference = 1 - normal_preference
        clip["track"] = track
        previous_track = track
        placed.append((track, start, end))
        by_uid[clip["shot_uid"]] = clip


def build_export_snapshot(manifest: dict[str, Any], project: dict[str, Any],
                          output_root: Path, ffprobe: str = "ffprobe") -> dict[str, Any]:
    """Reject partial/unconfirmed desks and freeze all selected source points."""
    if manifest.get("schema") != "xiaoyue.mv.preview-desk.v1":
        raise ValueError("验收台清单版本不支持导出。")
    if str(manifest["project"]["id"]) != str(project["project_id"]):
        raise ValueError("验收台与工程不一致。")
    master = _media_path(manifest["master_audio"], project, output_root)
    shots = manifest.get("shots") or []
    if not shots:
        raise ValueError("工程没有可导出的镜头。")
    clips = []
    selected_by_uid = {}
    for shot in shots:
        label = str(shot.get("selected_label") or shot.get("label")
                    or shot.get("shot_uid") or "")
        if not shot.get("selection_confirmed"):
            raise ValueError(f"镜头 {label} 的多个版本尚未明确确认。")
        if not shot.get("selected_playable"):
            raise ValueError(f"镜头 {label} 的选版不可用；请先在验收台处理。")
        version = next((v for v in shot.get("versions") or []
                        if v.get("id") == shot.get("selected_version_id")), None)
        if not version or not version.get("source"):
            raise ValueError(f"镜头 {label} 缺少所选版本。")
        source = _media_path(version["source"], project, output_root)
        info = probe_video(source, ffprobe)
        source_frames = info["nb_frames"] or int(version.get("source_frames") or 0)
        if source_frames <= 0:
            raise ValueError(f"镜头 {label} 无法确定完整源片帧数。")
        source_in = int(version["source_in_frame"])
        source_out = int(version["source_out_frame_exclusive"])
        timeline_in = int(version["timeline_in_frame"])
        timeline_out = int(version["timeline_out_frame_exclusive"])
        if (source_in < 0 or source_out > source_frames or source_out <= source_in
                or timeline_in < 0 or timeline_out - timeline_in != source_out - source_in):
            raise ValueError(f"镜头 {label} 的源片或总轨切点无效。")
        uid = str(shot["shot_uid"])
        parent_version_id = str(version.get("parent_version_id") or "")
        incoming = str(shot.get("incoming_transition") or "hard_cut")
        parent_uid = str(shot.get("parent_shot_uid") or "")
        if incoming == "long_take" and not parent_uid and parent_version_id:
            # Compatibility with older preview manifests, which only exposed
            # the immediate predecessor of a continuation.
            parent_uid = str(shots[len(clips) - 1].get("shot_uid")) if clips else ""
        if incoming == "long_take" and (not parent_uid or
                (clips and parent_uid != clips[-1]["shot_uid"]) or
                selected_by_uid.get(parent_uid) != parent_version_id):
            raise ValueError(f"镜头 {label} 的续写父版本链不匹配。")
        if incoming != "long_take" and parent_uid:
            raise ValueError(f"镜头 {label} 的剪辑接法保留了意外的续写父节点。")
        selected_by_uid[uid] = str(version["id"])
        clips.append({
            "shot": label, "shot_uid": uid, "version": int(version.get("number") or 0),
            "version_id": str(version["id"]), "parent_version_id": parent_version_id,
            "parent_shot_uid": parent_uid, "incoming_transition": incoming,
            "source_path": str(source), "source_total_frames": source_frames,
            "source_in_frame": source_in, "source_out_frame_exclusive": source_out,
            "timeline_in_frame": timeline_in, "timeline_out_frame_exclusive": timeline_out,
            "timeline_points_recorded": bool(version.get("timeline_points_recorded")),
            "width": info["width"], "height": info["height"],
            "audio_channels": info.get("audio_channels", 0),
            "audio_sample_rate": info.get("audio_sample_rate", 48000),
            "audio_depth": info.get("audio_depth", 16),
        })
    for clip in clips:
        clip["full_clip_start_frame"] = (clip["timeline_in_frame"]
                                         - clip["source_in_frame"])
        clip["full_clip_end_frame_exclusive"] = (clip["full_clip_start_frame"]
                                                  + clip["source_total_frames"])
        clip["source_file"] = (f"media/video/{_safe_name(clip['shot'])}"
                               f"_v{clip['version']}_{_safe_name(clip['version_id'])}"
                               f"{Path(clip['source_path']).suffix.lower()}")
    _allocate_edit_tracks(clips)
    subtitles = list(manifest.get("subtitles") or [])
    total = int(manifest.get("total_frames") or 0)
    if total <= 0:
        raise ValueError("工程总时长无效。")
    audio_info = probe_audio(master, ffprobe)
    if audio_info["frames"] <= 0:
        raise ValueError("原曲没有可用的音频帧。")
    return {
        "schema": "xiaoyue.mv.delivery-export.v1", "fps": FPS,
        "project": manifest["project"], "total_frames": total,
        "project_offset_frames": 0, "master_path": str(master),
        "master_file": f"media/audio/master{master.suffix.lower()}",
        "master_audio_info": audio_info,
        "clips": clips, "subtitles": subtitles,
        "source_bytes": sum(Path(c["source_path"]).stat().st_size for c in clips)
                        + master.stat().st_size,
    }


def public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items()
            if key not in {"master_path", "source_bytes", "clips"}} | {
        "clips": [{key: value for key, value in clip.items()
                   if key not in {"source_path", "width", "height"}}
                  for clip in snapshot["clips"]],
    }


def markers_csv(snapshot: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=MARKER_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(snapshot["clips"])
    return output.getvalue()


def _xml_text(parent: ET.Element, tag: str, value: Any) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = str(value)
    return element


def _rate(parent: ET.Element) -> None:
    rate = ET.SubElement(parent, "rate")
    _xml_text(rate, "timebase", FPS)
    _xml_text(rate, "ntsc", "FALSE")


def _timecode(parent: ET.Element) -> None:
    timecode = ET.SubElement(parent, "timecode")
    _rate(timecode)
    _xml_text(timecode, "string", "00:00:00:00")
    _xml_text(timecode, "frame", 0)
    _xml_text(timecode, "displayformat", "NDF")


def _video_sample(parent: ET.Element, width: int, height: int,
                  include_depth: bool = False) -> None:
    sample = ET.SubElement(parent, "samplecharacteristics")
    _rate(sample)
    _xml_text(sample, "width", width)
    _xml_text(sample, "height", height)
    _xml_text(sample, "anamorphic", "FALSE")
    _xml_text(sample, "pixelaspectratio", "square")
    _xml_text(sample, "fielddominance", "none")
    if include_depth:
        _xml_text(sample, "colordepth", 24)


def _audio_sample(parent: ET.Element, info: dict[str, int]) -> None:
    sample = ET.SubElement(parent, "samplecharacteristics")
    _xml_text(sample, "depth", info["depth"])
    _xml_text(sample, "samplerate", info["sample_rate"])


def _clip_link(parent: ET.Element, clip_ref: str, media_type: str,
               track_index: int, clip_index: int, group_index: int = 0) -> None:
    link = ET.SubElement(parent, "link")
    _xml_text(link, "linkclipref", clip_ref)
    _xml_text(link, "mediatype", media_type)
    _xml_text(link, "trackindex", track_index)
    _xml_text(link, "clipindex", clip_index)
    if group_index:
        _xml_text(link, "groupindex", group_index)


def _opacity_fade_in(item: ET.Element, source_start: int,
                     source_end: int) -> None:
    """Premiere's FCP7 interchange form: source-frame opacity keyframes."""
    effect = ET.SubElement(ET.SubElement(item, "filter"), "effect")
    _xml_text(effect, "name", "Opacity")
    _xml_text(effect, "effectid", "opacity")
    _xml_text(effect, "effectcategory", "motion")
    _xml_text(effect, "effecttype", "motion")
    _xml_text(effect, "mediatype", "video")
    _xml_text(effect, "pproBypass", "false")
    parameter = ET.SubElement(effect, "parameter", authoringApp="PremierePro")
    _xml_text(parameter, "parameterid", "opacity")
    _xml_text(parameter, "name", "opacity")
    _xml_text(parameter, "valuemin", 0)
    _xml_text(parameter, "valuemax", 100)
    _xml_text(parameter, "value", 100)
    for frame, value in ((source_start, 0), (source_end, 100)):
        keyframe = ET.SubElement(parameter, "keyframe")
        _xml_text(keyframe, "when", frame)
        _xml_text(keyframe, "value", value)


def fcp7_xml(snapshot: dict[str, Any]) -> bytes:
    """Premiere-style FCP7 interchange XML with relative media references."""
    root = ET.Element("xmeml", version="4")
    sequence = ET.SubElement(root, "sequence", id="sequence-1")
    # Each export is a distinct Premiere sequence, even when the same sources
    # were selected and only their recorded cut points changed.
    _xml_text(sequence, "uuid", uuid.uuid4())
    duration = max(snapshot["total_frames"],
                   *(clip["timeline_out_frame_exclusive"] for clip in snapshot["clips"]))
    _xml_text(sequence, "duration", duration)
    _rate(sequence)
    _xml_text(sequence, "name", str(snapshot["project"].get("name") or
                                    snapshot["project"]["code"]))
    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    format_node = ET.SubElement(video, "format")
    first = snapshot["clips"][0]
    _video_sample(format_node, first["width"] or 1280,
                  first["height"] or 720, include_depth=True)
    max_video_track = max(c["track"] for c in snapshot["clips"])
    video_lanes = {
        number: sorted((c for c in snapshot["clips"] if c["track"] == number),
                       key=lambda c: c["timeline_in_frame"])
        for number in range(max_video_track + 1)
    }
    previous_by_version = {
        clip["version_id"]: snapshot["clips"][index - 1]
        for index, clip in enumerate(snapshot["clips"]) if index
    }
    lane_channels = {
        number: min(2, max((c.get("audio_channels", 0) for c in clips), default=0))
        for number, clips in video_lanes.items()
    }
    audio_lane_start: dict[int, int] = {}
    next_audio_track = 1
    for number in video_lanes:
        audio_lane_start[number] = next_audio_track
        next_audio_track += max(1, lane_channels[number])
    for track_no in range(max(c["track"] for c in snapshot["clips"]) + 1):
        track = ET.SubElement(video, "track", {"TL.SQTrackShy": "0",
                     "TL.SQTrackExpandedHeight": "25", "TL.SQTrackExpanded": "0",
                     "MZ.TrackTargeted": "1" if track_no == 0 else "0"})
        for clip_index, clip in enumerate(video_lanes[track_no], 1):
            item = ET.SubElement(track, "clipitem", id=f"clip-{clip['version_id']}")
            _xml_text(item, "masterclipid", f"masterclip-{clip['version_id']}")
            _xml_text(item, "name", f"{clip['shot']} v{clip['version']}")
            _xml_text(item, "enabled", "TRUE")
            _xml_text(item, "duration", clip["source_total_frames"])
            _rate(item)
            _xml_text(item, "start", clip["timeline_in_frame"])
            _xml_text(item, "end", clip["timeline_out_frame_exclusive"])
            _xml_text(item, "in", clip["source_in_frame"])
            _xml_text(item, "out", clip["source_out_frame_exclusive"])
            _xml_text(item, "pproTicksIn", clip["source_in_frame"] * 10584000000)
            _xml_text(item, "pproTicksOut", clip["source_out_frame_exclusive"] * 10584000000)
            _xml_text(item, "alphatype", "none")
            _xml_text(item, "pixelaspectratio", "square")
            _xml_text(item, "anamorphic", "FALSE")
            file_node = ET.SubElement(item, "file", id=f"file-{clip['version_id']}")
            _xml_text(file_node, "name", Path(clip["source_file"]).name)
            _xml_text(file_node, "pathurl", Path(clip["source_file"]).as_posix())
            _rate(file_node)
            _xml_text(file_node, "duration", clip["source_total_frames"])
            _timecode(file_node)
            fmedia = ET.SubElement(file_node, "media")
            fvideo = ET.SubElement(fmedia, "video")
            _video_sample(fvideo, clip["width"] or first["width"] or 1280,
                          clip["height"] or first["height"] or 720)
            if clip.get("audio_channels", 0):
                faudio = ET.SubElement(fmedia, "audio")
                _audio_sample(faudio, {"depth": clip["audio_depth"],
                                       "sample_rate": clip["audio_sample_rate"]})
                _xml_text(faudio, "channelcount", clip["audio_channels"])
            previous = previous_by_version.get(clip["version_id"])
            if clip["incoming_transition"] == "crossfade" and previous:
                overlap_end = min(previous["timeline_out_frame_exclusive"],
                                  clip["timeline_out_frame_exclusive"])
                overlap = overlap_end - clip["timeline_in_frame"]
                if overlap > 0:
                    _opacity_fade_in(item, clip["source_in_frame"],
                                     clip["source_in_frame"] + overlap)
            source_track = ET.SubElement(item, "sourcetrack")
            _xml_text(source_track, "mediatype", "video")
            _xml_text(source_track, "trackindex", 1)
            if clip.get("audio_channels", 0):
                _clip_link(item, f"clip-{clip['version_id']}", "video",
                           track_no + 1, clip_index)
                for channel in range(1, min(2, clip["audio_channels"]) + 1):
                    eligible = [c for c in video_lanes[track_no]
                                if c.get("audio_channels", 0) >= channel]
                    audio_index = next(index for index, c in enumerate(eligible, 1)
                                       if c["version_id"] == clip["version_id"])
                    _clip_link(item, f"clip-audio-{clip['version_id']}-{channel}",
                               "audio", audio_lane_start[track_no] + channel - 1,
                               audio_index, 1)
        _xml_text(track, "enabled", "TRUE")
        _xml_text(track, "locked", "FALSE")
    audio = ET.SubElement(media, "audio")
    audio_info = snapshot["master_audio_info"]
    channels = max(1, audio_info["channels"])
    audio_frames = min(snapshot["total_frames"], audio_info["frames"])
    _xml_text(audio, "numOutputChannels", channels)
    audio_format = ET.SubElement(audio, "format")
    _audio_sample(audio_format, audio_info)
    outputs = ET.SubElement(audio, "outputs")
    for index in range(1, channels + 1):
        group = ET.SubElement(outputs, "group")
        _xml_text(group, "index", index)
        _xml_text(group, "numchannels", 1)
        _xml_text(group, "downmix", 0)
        output_channel = ET.SubElement(group, "channel")
        _xml_text(output_channel, "index", index)
    # A1..A(max video lane) mirror V1..Vmax.  The original song is placed in
    # the next logical audio lane, never on top of a source clip's audio.
    for video_track in range(max_video_track + 1):
        lane_clips = video_lanes[video_track]
        channel_count = lane_channels[video_track]
        # Empty lanes still occupy their A-track number in the editorial layout.
        for channel in range(1, max(1, channel_count) + 1):
            atrack = ET.SubElement(audio, "track", {
                "premiereTrackType": "Stereo" if channel_count == 2 else "Mono",
                "currentExplodedTrackIndex": str(channel - 1),
                "totalExplodedTrackCount": str(max(1, channel_count)),
                "MZ.TrackTargeted": "0"})
            eligible = [c for c in lane_clips if c.get("audio_channels", 0) >= channel]
            for audio_index, clip in enumerate(eligible, 1):
                aitem = ET.SubElement(atrack, "clipitem", {
                    "id": f"clip-audio-{clip['version_id']}-{channel}",
                    "premiereChannelType": "stereo" if channel_count == 2 else "mono"})
                _xml_text(aitem, "masterclipid", f"masterclip-{clip['version_id']}")
                _xml_text(aitem, "name", f"{clip['shot']} v{clip['version']}")
                _xml_text(aitem, "enabled", "TRUE")
                _xml_text(aitem, "duration", clip["source_total_frames"])
                _rate(aitem)
                _xml_text(aitem, "start", clip["timeline_in_frame"])
                _xml_text(aitem, "end", clip["timeline_out_frame_exclusive"])
                _xml_text(aitem, "in", clip["source_in_frame"])
                _xml_text(aitem, "out", clip["source_out_frame_exclusive"])
                _xml_text(aitem, "pproTicksIn", clip["source_in_frame"] * 10584000000)
                _xml_text(aitem, "pproTicksOut", clip["source_out_frame_exclusive"] * 10584000000)
                ET.SubElement(aitem, "file", id=f"file-{clip['version_id']}")
                source_track = ET.SubElement(aitem, "sourcetrack")
                _xml_text(source_track, "mediatype", "audio")
                _xml_text(source_track, "trackindex", channel)
                video_index = next(index for index, c in enumerate(lane_clips, 1)
                                   if c["version_id"] == clip["version_id"])
                _clip_link(aitem, f"clip-{clip['version_id']}", "video",
                           video_track + 1, video_index)
                for linked_channel in range(1, min(2, clip["audio_channels"]) + 1):
                    linked_eligible = [c for c in lane_clips
                                       if c.get("audio_channels", 0) >= linked_channel]
                    linked_index = next(index for index, c in enumerate(linked_eligible, 1)
                                        if c["version_id"] == clip["version_id"])
                    _clip_link(aitem,
                               f"clip-audio-{clip['version_id']}-{linked_channel}",
                               "audio", audio_lane_start[video_track] + linked_channel - 1,
                               linked_index, 1)
            _xml_text(atrack, "enabled", "FALSE")
            _xml_text(atrack, "locked", "FALSE")
            _xml_text(atrack, "outputchannelindex", min(channel, channels))
    for index in range(1, channels + 1):
        atrack = ET.SubElement(audio, "track", {"premiereTrackType": "Stereo" if channels == 2 else "Mono",
                         "currentExplodedTrackIndex": str(index - 1),
                         "totalExplodedTrackCount": str(channels),
                         "MZ.TrackTargeted": "0"})
        aitem = ET.SubElement(atrack, "clipitem", {"id": f"master-audio-{index}",
                                                 "premiereChannelType": "stereo" if channels == 2 else "mono"})
        _xml_text(aitem, "masterclipid", "masterclip-audio")
        _xml_text(aitem, "name", Path(snapshot["master_file"]).name)
        _xml_text(aitem, "enabled", "TRUE")
        _xml_text(aitem, "duration", audio_frames)
        _rate(aitem)
        _xml_text(aitem, "start", 0)
        _xml_text(aitem, "end", audio_frames)
        _xml_text(aitem, "in", 0)
        _xml_text(aitem, "out", audio_frames)
        _xml_text(aitem, "pproTicksIn", 0)
        _xml_text(aitem, "pproTicksOut", audio_frames * 10584000000)
        afile = ET.SubElement(aitem, "file", id="file-master-audio")
        if index == 1:
            _xml_text(afile, "name", Path(snapshot["master_file"]).name)
            _xml_text(afile, "pathurl", Path(snapshot["master_file"]).as_posix())
            _rate(afile)
            _xml_text(afile, "duration", audio_frames)
            _timecode(afile)
            amedia = ET.SubElement(afile, "media")
            faudio = ET.SubElement(amedia, "audio")
            _audio_sample(faudio, audio_info)
            _xml_text(faudio, "channelcount", channels)
        source_track = ET.SubElement(aitem, "sourcetrack")
        _xml_text(source_track, "mediatype", "audio")
        _xml_text(source_track, "trackindex", index)
        for linked_index in range(1, channels + 1):
            link = ET.SubElement(aitem, "link")
            _xml_text(link, "linkclipref", f"master-audio-{linked_index}")
            _xml_text(link, "mediatype", "audio")
            _xml_text(link, "trackindex", next_audio_track + linked_index - 1)
            _xml_text(link, "clipindex", 1)
            _xml_text(link, "groupindex", 1)
        _xml_text(atrack, "enabled", "TRUE")
        _xml_text(atrack, "locked", "FALSE")
        _xml_text(atrack, "outputchannelindex", index)
    _timecode(sequence)
    # Sequence markers are editorial cues. Original media is never trimmed.
    for clip in snapshot["clips"]:
        for edge, point in (("入", clip["timeline_in_frame"]),
                            ("出", clip["timeline_out_frame_exclusive"])):
            marker = ET.SubElement(sequence, "marker")
            _xml_text(marker, "name", f"{clip['shot']} v{clip['version']} 建议{edge}点")
            _xml_text(marker, "comment", f"源片帧 {clip['source_in_frame']}–{clip['source_out_frame_exclusive']}；见 markers.csv")
            _xml_text(marker, "in", point)
            _xml_text(marker, "out", point)
    ET.indent(root, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="utf-8")


def _validate_xml_media(xml: bytes, package_root: Path) -> None:
    """Reject a broken package before publishing a downloadable ZIP."""
    root = ET.fromstring(xml)
    references = [element.text or "" for element in root.findall(".//pathurl")]
    if not references:
        raise ValueError("通用工程 XML 没有素材引用。")
    for reference in references:
        relative = Path(reference)
        if (relative.is_absolute() or "\\" in reference or
                not reference.startswith("media/") or ".." in relative.parts):
            raise ValueError(f"通用工程 XML 的素材路径不是包内相对路径：{reference}")
        if not (package_root / relative).is_file():
            raise ValueError(f"通用工程 XML 引用的素材不存在：{reference}")


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise ExportCancelled("用户取消导出。")


def _copy_file(source: Path, target: Path, cancel: threading.Event) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("wb") as writer:
        while chunk := reader.read(4 * 1024 * 1024):
            _check_cancel(cancel)
            writer.write(chunk)
    shutil.copystat(source, target)


def _movie_command(snapshot: dict[str, Any], package_root: Path,
                   ffmpeg: str) -> list[str]:
    clips = sorted(snapshot["clips"], key=lambda clip: clip["timeline_in_frame"])
    width = max(2, clips[0]["width"] // 2 * 2)
    height = max(2, clips[0]["height"] // 2 * 2)
    total = snapshot["total_frames"]
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-filter_complex_threads", "2",
               "-loglevel", "error", "-progress", "pipe:2", "-nostats"]
    for clip in clips:
        command += ["-threads", "1", "-i", str(package_root / clip["source_file"])]
    command += ["-i", str(package_root / snapshot["master_file"])]
    filters = [f"color=c=black:s={width}x{height}:r={FPS}:d={total / FPS}[base]"]
    current = "base"
    for index, clip in enumerate(clips):
        start = clip["timeline_in_frame"]
        source_in = clip["source_in_frame"]
        source_out = clip["source_out_frame_exclusive"]
        chain = (f"[{index}:v]fps={FPS},trim=start_frame={source_in}:end_frame={source_out},"
                 f"setpts=PTS-STARTPTS,scale={width}:{height}:flags=bicubic,"
                 "format=yuva420p")
        if index and clip["incoming_transition"] == "crossfade":
            previous = clips[index - 1]
            overlap = max(0, min(previous["timeline_out_frame_exclusive"],
                                 clip["timeline_out_frame_exclusive"]) - start)
            if overlap:
                chain += f",fade=t=in:st=0:d={overlap / FPS:.9f}:alpha=1"
        chain += f",setpts=PTS+{start}/({FPS}*TB)[clip{index}]"
        filters.append(chain)
        next_label = f"stack{index}"
        filters.append(f"[{current}][clip{index}]overlay=eof_action=pass:shortest=0:"
                       f"enable='gte(n,{start})'[{next_label}]")
        current = next_label
    filters.append(f"[{current}]fps={FPS},trim=end_frame={total},"
                   "setpts=PTS-STARTPTS,format=yuv420p[vout]")
    filters.append(f"[{len(clips)}:a]asetpts=PTS-STARTPTS,apad,"
                   f"atrim=duration={total / FPS:.9f}[aout]")
    command += ["-filter_complex", ";".join(filters), "-map", "[vout]",
                "-map", "[aout]", "-frames:v", str(total), "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "18", "-threads", "4",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart",
                str(package_root / "MV_无字成片.mp4")]
    return command


def _render_movie(snapshot: dict[str, Any], package_root: Path,
                  cancel: threading.Event, progress: Callable[[str, float], None],
                  ffmpeg: str) -> None:
    command = _movie_command(snapshot, package_root, ffmpeg)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", errors="replace")
    tail: list[str] = []
    lines: queue.Queue[str] = queue.Queue()
    reader_done = threading.Event()

    def read_stderr() -> None:
        try:
            assert process.stderr is not None
            for line in process.stderr:
                lines.put(line)
        finally:
            reader_done.set()

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()
    try:
        while process.poll() is None or not reader_done.is_set() or not lines.empty():
            if cancel.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise ExportCancelled("用户取消成片渲染。")
            try:
                line = lines.get(timeout=.25)
            except queue.Empty:
                continue
            tail.append(line.rstrip())
            if len(tail) > 15:
                tail.pop(0)
            match = re.search(r"frame=\s*(\d+)", line)
            if match:
                progress("正在合成无字成片", min(.92, .25 + .67 *
                         int(match.group(1)) / snapshot["total_frames"]))
        code = process.wait()
        if code != 0:
            raise RuntimeError("FFmpeg 成片失败：" + " | ".join(tail[-5:]))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        if process.stderr is not None:
            process.stderr.close()


def _zip_package(root: Path, archive: Path, cancel: threading.Event,
                 progress: Callable[[str, float], None]) -> None:
    files = [item for item in root.rglob("*") if item.is_file()]
    with zipfile.ZipFile(archive, "w", allowZip64=True) as bundle:
        for index, path in enumerate(files, 1):
            _check_cancel(cancel)
            bundle.write(path, path.relative_to(root), compress_type=zipfile.ZIP_STORED)
            progress("正在打包选中素材", .93 + .06 * index / max(1, len(files)))


def run_export(snapshot: dict[str, Any], kind: str, export_root: Path,
               export_id: str, cancel: threading.Event,
               progress: Callable[[str, float], None], ffmpeg: str = "ffmpeg") -> dict[str, str]:
    if kind not in EXPORT_KINDS:
        raise ValueError("未知的导出方案。")
    # Validate the native project before copying potentially large source
    # videos. Unsupported audio layouts then fail without wasted disk I/O.
    portable_project = premiere_project_bytes(snapshot) if kind == "fcp_xml" else None
    export_root.mkdir(parents=True, exist_ok=True)
    final = export_root / export_id
    staging = export_root / f".{export_id}.partial"
    archive = export_root / f"{export_id}.zip"
    archive_partial = export_root / f".{export_id}.zip.partial"
    if any(path.exists() for path in (final, staging, archive, archive_partial)):
        raise ValueError("导出任务编号已存在。")
    staging.mkdir()
    try:
        sources = [(Path(snapshot["master_path"]), staging / snapshot["master_file"])]
        sources += [(Path(c["source_path"]), staging / c["source_file"])
                    for c in snapshot["clips"]]
        for index, (source, target) in enumerate(sources, 1):
            _copy_file(source, target, cancel)
            progress(f"复制素材 {index}/{len(sources)}", .05 + .18 * index / len(sources))
        (staging / "selected_versions.json").write_text(
            json.dumps(public_snapshot(snapshot), ensure_ascii=False, indent=2),
            encoding="utf-8")
        (staging / "export_kind.txt").write_text(kind + "\n", encoding="ascii")
        (staging / "markers.csv").write_text("\ufeff" + markers_csv(snapshot),
                                              encoding="utf-8")
        (staging / "最终字幕.srt").write_text(subtitle_srt(snapshot["subtitles"]),
                                             encoding="utf-8")
        notes = [
            "小玥 MV 验收导出包", "",
            "此包只包含导出时验收台已选中的成功版本、每个版本的完整源片、完整原曲及正式字幕。",
            "selected_versions.json 和 markers.csv 记录 24 fps 的总轨入/出点、源片入/出点、完整源片余量和续写父版本。",
            "工程时间零点与原曲一致，无前置空白；视频轨音量为零，最终声音只使用原曲。", "",
        ]
        if kind == "movie":
            notes += ["MV_无字成片.mp4 是直接交付版，字幕未烧入，请按需导入 最终字幕.srt。"]
            progress("准备无字成片", .24)
            _render_movie(snapshot, staging, cancel, progress, ffmpeg)
        else:
            assert portable_project is not None
            notes += ["请先完整解压 ZIP，保留工程文件与 media 文件夹的相对位置。",
                      "工程文件：FCP7_通用工程.xml（FCP7 xmeml）和 MV_Edit.prproj（Premiere 原生工程）。",
                      "素材路径全部相对于 XML 所在目录，指向 media/；原始视频完整保留，但工程时间线已按选中版本的入/出帧预剪，可在 Premiere 中拉长片段找回余量。",
                      "实切与续写按所选版本和帧点接好；叠化的后段在上轨按重合帧淡入。",
                      "本机导出目录内的 MV_Edit.prproj 可直接由 Premiere 或剪映打开。",
                      "如果使用解压后的 ZIP 或搬动了工程文件夹，请先运行 Relink_MV_Project.cmd，按当前位置更新工程素材路径；脚本会保留修改前的 .bak 备份。",
                      "Premiere 不会自动解析 .prproj 中的 RelativePath，因此只搬动工程文件而不重定位会提示素材丢失。",
                      "请勿把本包复制进剪映草稿目录；在剪映中使用 PR 工程导入入口选择 MV_Edit.prproj。",
                      "FCP7 XML 可从 Premiere 的“导入”入口选择；若软件要求重新链接素材，请指向解压后的 media 文件夹。",
                      "新版 Final Cut Pro 的 FCPXML 与 FCP7 xmeml 并非同一格式，不能保证直接导入。",
                      "精确切点另见 markers.csv；两个工程都含序列标记。"]
            xml = fcp7_xml(snapshot)
            _validate_xml_media(xml, staging)
            (staging / "FCP7_通用工程.xml").write_bytes(xml)
            (staging / "MV_Edit.prproj").write_bytes(portable_project)
            asset_dir = Path(__file__).parent / "assets"
            for filename in ("relink_premiere.ps1", "Relink_MV_Project.cmd"):
                _copy_file(asset_dir / filename, staging / filename, cancel)
        (staging / "导出说明.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
        _check_cancel(cancel)
        progress("正在封装 ZIP", .93)
        _zip_package(staging, archive_partial, cancel, progress)
        _check_cancel(cancel)
        if kind == "fcp_xml":
            # The ZIP keeps a movable project plus relinker. The local output
            # folder gets a directly openable project for this known location.
            (staging / "MV_Edit.prproj").write_bytes(
                premiere_project_bytes(snapshot, final.resolve()))
        os.replace(staging, final)
        os.replace(archive_partial, archive)
        progress("导出完成", 1.0)
        return {"folder": str(final), "archive": str(archive),
                "archive_name": archive.name}
    except BaseException:
        if staging.exists():
            staging.resolve().relative_to(export_root.resolve())
            shutil.rmtree(staging)
        if archive_partial.exists():
            archive_partial.unlink()
        raise
