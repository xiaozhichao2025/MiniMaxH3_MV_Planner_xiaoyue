"""Build an editable native Premiere project for the selected MV timeline.

The bundled skeleton contains only synthetic test media. A historical user
project must never be copied into a current export.
"""

from __future__ import annotations

import copy
import gzip
import json
import re
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET


TICKS_PER_FRAME = 10_584_000_000
TEMPLATE_PATH = Path(__file__).parent / "assets" / "premiere_synthetic_template.prproj"


def project_bytes(snapshot: dict, package_root: Path | None = None) -> bytes:
    """Serialize the selected edits, with portable or local media paths."""
    if int(snapshot["master_audio_info"]["channels"]) != 2:
        raise ValueError("原生 Premiere 工程目前要求原曲为双声道；FCP7 XML 不受此限制。")
    non_stereo = [clip["shot"] for clip in snapshot["clips"]
                  if int(clip.get("audio_channels", 0)) != 2]
    if non_stereo:
        raise ValueError("原生 Premiere 工程目前要求视频内音轨为双声道：" +
                         "、".join(non_stereo[:5]))
    template = TEMPLATE_PATH.read_bytes()
    return build(template, snapshot, package_root)


def _obj(root: ET.Element, tag: str) -> ET.Element:
    matches = root.findall(tag)
    if len(matches) != 1:
        raise ValueError(f"Expected one {tag}, got {len(matches)}")
    return matches[0]


def _set(node: ET.Element, path: str, value: object) -> None:
    target = node.find(path)
    if target is None:
        raise ValueError(f"Missing {node.tag}/{path}")
    target.text = str(value)


def _refs(node: ET.Element, ids: dict[str, ET.Element],
          uids: dict[str, ET.Element]):
    for sub in node.iter():
        if ref := sub.get("ObjectRef"):
            if ref in ids:
                yield ids[ref]
        if ref := sub.get("ObjectURef"):
            if ref in uids:
                yield uids[ref]


def _closure(starts: list[ET.Element], ids: dict[str, ET.Element],
             uids: dict[str, ET.Element],
             stop_tags: frozenset[str] = frozenset()) -> list[ET.Element]:
    queue = list(starts)
    seen: set[int] = set()
    result = []
    while queue:
        node = queue.pop()
        if id(node) in seen or node.tag in stop_tags:
            continue
        seen.add(id(node))
        result.append(node)
        queue.extend(_refs(node, ids, uids))
    return result


def _index(root: ET.Element):
    ids = {node.get("ObjectID"): node for node in root if node.get("ObjectID")}
    uids = {node.get("ObjectUID"): node for node in root if node.get("ObjectUID")}
    return ids, uids


def _bundle(root: ET.Element, master: ET.Element, ids: dict[str, ET.Element],
            uids: dict[str, ET.Element]) -> list[ET.Element]:
    uid = master.get("ObjectUID")
    projects = [node for node in root.findall("ClipProjectItem")
                if node.find("MasterClip") is not None
                and node.find("MasterClip").get("ObjectURef") == uid]
    sub_ids = {node.get("ObjectID") for node in root.findall("SubClip")
               if node.find("MasterClip") is not None
               and node.find("MasterClip").get("ObjectURef") == uid}
    items = [node for node in root
             if node.tag in {"VideoClipTrackItem", "AudioClipTrackItem"}
             and node.find(".//SubClip") is not None
             and node.find(".//SubClip").get("ObjectRef") in sub_ids]
    item_ids = {node.get("ObjectID") for node in items}
    links = [node for node in root.findall("Link")
             if any(ref.get("ObjectID") in item_ids for ref in _refs(node, ids, uids))]
    if len(projects) != 1 or len(items) != 2 or len(links) != 1:
        raise ValueError("Unexpected clip source graph")
    return _closure([master, *projects, *items, *links], ids, uids)


def _clone(nodes: list[ET.Element], next_id: list[int]) -> list[ET.Element]:
    copied = [copy.deepcopy(node) for node in nodes]
    id_map: dict[str, str] = {}
    uid_map: dict[str, str] = {}
    for node in copied:
        if old := node.get("ObjectID"):
            next_id[0] += 1
            id_map[old] = str(next_id[0])
        if old := node.get("ObjectUID"):
            uid_map[old] = str(uuid.uuid4())
    for node in copied:
        for sub in node.iter():
            for attr, mapping in (("ObjectID", id_map), ("ObjectRef", id_map),
                                  ("ObjectUID", uid_map), ("ObjectURef", uid_map)):
                old = sub.get(attr)
                if old in mapping:
                    sub.set(attr, mapping[old])
    # Some project relationships are stored as text GUIDs rather than refs.
    guid_map: dict[str, str] = {}
    for node in copied:
        for sub in node.iter():
            if sub.tag in {"ClipID", "DefMappingID", "FileKey", "ID"}:
                old = sub.text or ""
                if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", old):
                    sub.text = guid_map.setdefault(old, str(uuid.uuid4()))
    return copied


def _append_ref_list(parent: ET.Element, child_tag: str, attr: str,
                     refs: list[str]) -> None:
    for old in list(parent):
        parent.remove(old)
    for index, ref in enumerate(refs):
        ET.SubElement(parent, child_tag, {"Index": str(index), attr: ref})


def _configure_source(bundle: list[ET.Element], clip: dict, overlap: int,
                      package_root: Path | None) -> dict:
    by_tag: dict[str, list[ET.Element]] = {}
    for node in bundle:
        by_tag.setdefault(node.tag, []).append(node)
    if len(by_tag.get("VideoClipTrackItem", [])) != 1 or len(by_tag.get("Media", [])) != 1:
        raise ValueError("Unexpected cloned clip graph")
    old_name = by_tag["MasterClip"][0].findtext("Name") or ""
    filename = Path(clip["source_file"]).name
    relative = ".\\" + clip["source_file"].replace("/", "\\")
    for node in bundle:
        for sub in node.iter():
            if sub.text and old_name in sub.text:
                sub.text = sub.text.replace(old_name, filename)
    media = by_tag["Media"][0]
    _set(media, "RelativePath", relative)
    media_path = str(package_root / clip["source_file"]) if package_root else relative
    for field in ("FilePath", "ActualMediaFilePath"):
        _set(media, field, media_path)
    _set(media, "Title", filename)
    source_duration = int(clip["source_total_frames"]) * TICKS_PER_FRAME
    source_in = int(clip["source_in_frame"]) * TICKS_PER_FRAME
    source_out = int(clip["source_out_frame_exclusive"]) * TICKS_PER_FRAME
    start = int(clip["timeline_in_frame"]) * TICKS_PER_FRAME
    end = int(clip["timeline_out_frame_exclusive"]) * TICKS_PER_FRAME
    width, height = int(clip["width"]), int(clip["height"])
    for node in by_tag["VideoMediaSource"] + by_tag["AudioMediaSource"]:
        _set(node, "OriginalDuration", source_duration)
    for node in by_tag["VideoStream"] + by_tag["AudioStream"]:
        _set(node, "Duration", source_duration)
    for node in by_tag["VideoStream"]:
        _set(node, "FrameRect", f"0,0,{width},{height}")
    for node in by_tag["ClipLoggingInfo"]:
        _set(node, "MediaOutPoint", source_duration)
        _set(node, "ClipName", filename)
    for node in by_tag["VideoClipTrackItem"] + by_tag["AudioClipTrackItem"]:
        _set(node, ".//TrackItem/Start", start)
        _set(node, ".//TrackItem/End", end)
    _set(by_tag["VideoClipTrackItem"][0], "FrameRect", f"0,0,{width},{height}")
    # The imported master clips span the full source; timeline copies carry
    # the selected, frame-accurate source in/out points.
    master = by_tag["MasterClip"][0]
    imported_refs = {child.get("ObjectRef") for child in master.findall("./Clips/Clip")}
    for node in by_tag["VideoClip"] + by_tag["AudioClip"]:
        if node.get("ObjectID") in imported_refs:
            # Imported source clips in Premiere omit explicit trim fields.
            if node.find("./Clip/InPoint") is not None:
                _set(node, "./Clip/InPoint", 0)
            if node.find("./Clip/OutPoint") is not None:
                _set(node, "./Clip/OutPoint", source_duration)
        else:
            _set(node, "./Clip/InPoint", source_in)
            _set(node, "./Clip/OutPoint", source_out)
    if overlap:
        if len(by_tag.get("VideoComponentParam", [])) != 3:
            raise ValueError("Fade graph missing opacity params")
        opacity = next(node for node in by_tag["VideoComponentParam"]
                       if node.findtext("ParameterID") == "1")
        fade_end = source_in + overlap * TICKS_PER_FRAME
        _set(opacity, "StartKeyframe", "-91445760000000000,100.,0,0,0,0,0,0")
        _set(opacity, "Keyframes", (
            f"{source_in},0.,0,0,0,0,0,0;"
            f"{fade_end},100.,0,0,0,0,0,0;"
            f"{source_out},100.,0,0,0,0,0,0;"))
    return {
        "video": by_tag["VideoClipTrackItem"][0].get("ObjectID"),
        "audio": by_tag["AudioClipTrackItem"][0].get("ObjectID"),
        "link": by_tag["Link"][0].get("ObjectID"),
        "project": by_tag["ClipProjectItem"][0].get("ObjectUID"),
    }


def build(template: bytes, snapshot: dict, package_root: Path | None = None) -> bytes:
    root = ET.fromstring(gzip.decompress(template))
    project_name = snapshot["project"].get("name") or snapshot["project"]["code"]
    for node in root.iter():
        if node.text and "Xiaoyue Native Test" in node.text:
            node.text = node.text.replace("Xiaoyue Native Test", project_name)
        if node.tag == "project.settings.lastknowngoodprojectpath":
            node.text = (str(package_root / "MV_Edit.prproj") if package_root
                         else "MV_Edit.prproj")
    ids, uids = _index(root)
    video_masters = [node for node in root.findall("MasterClip")
                     if (node.findtext("Name") or "").endswith(".mp4")]
    if len(video_masters) != 5:
        raise ValueError("Unexpected Premiere template")
    source_bundles = [_bundle(root, node, ids, uids) for node in video_masters]
    fade_template = source_bundles[1]
    normal_template = source_bundles[2]
    remove = {id(node) for bundle in source_bundles for node in bundle}
    for node in list(root):
        if id(node) in remove:
            root.remove(node)
    next_id = [max(int(node.get("ObjectID")) for node in root.iter()
                   if (node.get("ObjectID") or "").isdigit())]

    clips = sorted(snapshot["clips"], key=lambda c: c["timeline_in_frame"])
    built = []
    for index, clip in enumerate(clips):
        overlap = 0
        if index and clip["incoming_transition"] == "crossfade":
            previous = clips[index - 1]
            overlap = max(0, min(previous["timeline_out_frame_exclusive"],
                                 clip["timeline_out_frame_exclusive"])
                          - clip["timeline_in_frame"])
        clone = _clone(fade_template if overlap else normal_template, next_id)
        refs = _configure_source(clone, clip, overlap, package_root)
        root.extend(clone)
        built.append((clip, refs))

    # Rebuild project-bin membership, sequence clip links, and per-lane items.
    video_bin = next(node for node in root.findall("BinProjectItem")
                     if node.findtext("./ProjectItem/Name") == "video")
    _append_ref_list(video_bin.find("./ProjectItemContainer/Items"), "Item",
                     "ObjectURef", [refs["project"] for _, refs in built])
    sequence = _obj(root, "Sequence")
    _set(sequence, "Name", project_name)
    sequence_width = int(clips[0]["width"])
    sequence_height = int(clips[0]["height"])
    _set(sequence, "./Node/Properties/MZ.Sequence.PreviewFrameSizeWidth", sequence_width)
    _set(sequence, "./Node/Properties/MZ.Sequence.PreviewFrameSizeHeight", sequence_height)
    links = sequence.find("./PersistentGroupContainer/LinkContainer/Links")
    _append_ref_list(links, "Link", "ObjectRef", [refs["link"] for _, refs in built])

    max_lane = max(int(clip["track"]) for clip in clips)
    video_tracks = root.findall("VideoClipTrack")
    audio_tracks = root.findall("AudioClipTrack")
    song_track, source_tracks = audio_tracks[0], audio_tracks[1:]
    while len(video_tracks) <= max_lane:
        clone = _clone([video_tracks[-1]], next_id)[0]
        root.append(clone)
        video_tracks.append(clone)
    while len(source_tracks) <= max_lane:
        # Audio track effects are a separate reference graph from source clips.
        ids, uids = _index(root)
        graph = _closure([source_tracks[-1]], ids, uids,
                         frozenset({"AudioClipTrackItem"}))
        cloned = _clone(graph, next_id)
        root.extend(cloned)
        source_tracks.append(next(node for node in cloned if node.tag == "AudioClipTrack"))
    used_video = video_tracks[:max_lane + 1]
    used_source_audio = source_tracks[:max_lane + 1]
    # Keep generated-video audio editable, but never mix it with the song by
    # default. The song owns its own final lane above all source-audio lanes.
    for track in used_source_audio:
        _set(track, "./ClipTrack/Track/IsMuted", "true")
    _set(song_track, "./ClipTrack/Track/IsMuted", "false")
    # Premiere keeps some track-inlet references outside TrackGroup. Preserve
    # unused template track objects, but detach their former clip items.
    for node in [*video_tracks[max_lane + 1:], *source_tracks[max_lane + 1:]]:
        items = node.find("./ClipTrack/ClipItems/TrackItems")
        if items is None:
            continue
        for old in list(items):
            items.remove(old)
    for lane in range(max_lane + 1):
        for tag, track, field in (("VideoClipTrack", used_video[lane], "video"),
                                  ("AudioClipTrack", used_source_audio[lane], "audio")):
            _set(track, "./ClipTrack/Track/Index", lane)
            _set(track, "./ClipTrack/Track/ID", lane + 1)
            items = track.find("./ClipTrack/ClipItems/TrackItems")
            if items is None:
                items = ET.SubElement(track.find("./ClipTrack/ClipItems"),
                                      "TrackItems", {"Version": "1"})
            lane_refs = [refs[field] for clip, refs in built if int(clip["track"]) == lane]
            _append_ref_list(items, "TrackItem", "ObjectRef", lane_refs)
            _set(track, "./ClipTrack/ClipItems/Index", lane)
            _set(track, "./ClipTrack/TransitionItems/Index", lane)
    _set(song_track, "./ClipTrack/Track/Index", max_lane + 1)
    _set(song_track, "./ClipTrack/Track/ID", max_lane + 2)
    _set(song_track, "./ClipTrack/ClipItems/Index", max_lane + 1)
    _set(song_track, "./ClipTrack/TransitionItems/Index", max_lane + 1)
    for group_tag, tracks in (("VideoTrackGroup", used_video),
                              ("AudioTrackGroup", [*used_source_audio, song_track])):
        group = _obj(root, group_tag)
        track_list = group.find("./TrackGroup/Tracks")
        _append_ref_list(track_list, "Track", "ObjectURef",
                         [track.get("ObjectUID") for track in tracks])
        _set(group, "./TrackGroup/NextTrackID", len(tracks) + 1)
    _set(_obj(root, "VideoTrackGroup"), "FrameRect",
         f"0,0,{sequence_width},{sequence_height}")

    total = int(snapshot["total_frames"]) * TICKS_PER_FRAME
    for path in ("./Node/Properties/MZ.WorkOutPoint",
                 "./Node/Properties/MZ.EditLine",
                 "./Node/Properties/Monitor.ZoomOut"):
        target = sequence.find(path)
        if target is not None:
            target.text = str(total)
    for node in root.findall("VideoSequenceSource") + root.findall("AudioSequenceSource"):
        for target in node.findall(".//Duration"):
            target.text = str(total)

    # Adapt the original song clip and media, keeping it on the last audio lane.
    master_file = snapshot["master_file"]
    master_name = Path(master_file).name
    for node in root:
        if node.tag not in {"MasterClip", "ClipProjectItem", "SubClip",
                            "ClipLoggingInfo", "Media"}:
            continue
        for sub in node.iter():
            if sub.text and "master.wav" in sub.text:
                sub.text = sub.text.replace("master.wav", master_name)
    master_media = next(node for node in root.findall("Media")
                        if node.findtext("Title") == master_name)
    relative = ".\\" + master_file.replace("/", "\\")
    _set(master_media, "RelativePath", relative)
    song_path = str(package_root / master_file) if package_root else relative
    for field in ("FilePath", "ActualMediaFilePath"):
        _set(master_media, field, song_path)
    song_duration = max(int(snapshot["total_frames"]),
                        int(snapshot.get("master_audio_info", {}).get("frames", 0))) * TICKS_PER_FRAME
    media_ids, _ = _index(root)
    for stream_ref in master_media.findall("AudioStream"):
        _set(media_ids[stream_ref.get("ObjectRef")], "Duration", song_duration)
    for source in root.findall("AudioMediaSource"):
        media_ref = source.find("./MediaSource/Media")
        if media_ref is not None and media_ref.get("ObjectURef") == master_media.get("ObjectUID"):
            _set(source, "OriginalDuration", song_duration)
    for logging in root.findall("ClipLoggingInfo"):
        if logging.findtext("ClipName") == master_name:
            _set(logging, "MediaOutPoint", song_duration)
    _set(master_media, "Title", master_name)
    song_ref = song_track.find("./ClipTrack/ClipItems/TrackItems/TrackItem").get("ObjectRef")
    ids, _ = _index(root)
    song_item = ids[song_ref]
    _set(song_item, ".//TrackItem/Start", 0)
    _set(song_item, ".//TrackItem/End", total)
    song_sub = ids[song_item.find(".//SubClip").get("ObjectRef")]
    song_clip = ids[song_sub.find("Clip").get("ObjectRef")]
    _set(song_clip, "./Clip/InPoint", 0)
    _set(song_clip, "./Clip/OutPoint", total)

    # Recreate sequence markers in timeline frame units.
    marker_holder = ids[sequence.find("./MarkerOwner/Markers").get("ObjectRef")]
    marker_list = marker_holder.find("Markers")
    for old in list(marker_list):
        marker_list.remove(old)
    for node in list(root.findall("Marker")):
        root.remove(node)
    marker_template = ET.Element("Marker", {
        "ObjectID": "0", "ClassID": "a45508e0-3ff7-4d04-90a7-2e0dfff4c910",
        "Version": "3"})
    ET.SubElement(marker_template, "DVAMarker")
    markers = []
    for clip, _ in built:
        for label, frame in (("入点", clip["timeline_in_frame"]),
                             ("出点", clip["timeline_out_frame_exclusive"])):
            marker = _clone([marker_template], next_id)[0]
            marker.find("DVAMarker").text = json.dumps({"DVAMarker": {
                "mMarkerID": str(uuid.uuid4()),
                "mStartTime": {"ticks": int(frame) * TICKS_PER_FRAME},
                "mType": "Comment", "mName": f"{clip['shot']} v{clip['version']} {label}"}},
                ensure_ascii=False, separators=(",", ":"))
            root.append(marker)
            markers.append(marker.get("ObjectID"))
    for index, ref in enumerate(markers):
        entry = ET.SubElement(marker_list, "Marker", {"Version": "1", "Index": str(index)})
        ET.SubElement(entry, "First").text = str(uuid.uuid4())
        ET.SubElement(entry, "Second", {"ObjectRef": ref})

    output = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    check = ET.fromstring(output)
    all_ids = {node.get("ObjectID") for node in check.iter() if node.get("ObjectID")}
    all_uids = {node.get("ObjectUID") for node in check.iter() if node.get("ObjectUID")}
    missing = [(node.tag, key, value) for node in check.iter()
               for key, value in node.attrib.items()
               if (key == "ObjectRef" and value not in all_ids)
               or (key == "ObjectURef" and value not in all_uids)]
    if missing:
        raise ValueError(f"Dangling Premiere refs: {missing[:10]}")
    return gzip.compress(output, mtime=0)
