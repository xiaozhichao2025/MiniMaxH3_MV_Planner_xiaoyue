"""Read-only, frame-accurate source map for the MV acceptance desk.

The desk never trims, transcodes, or copies media.  It describes which portion
of each immutable generation belongs at which absolute master-audio frame.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any


FPS = 24
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov"}


def _shot_uid(shot: dict[str, Any]) -> str:
    shot_id = str(shot.get("id") or "")
    return str(shot.get("shot_uid") or f"legacy_{shot_id}")


def _project_file(root: Path, relative: str) -> Path:
    relative_path = Path(str(relative).replace("\\", "/"))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("工程素材路径无效。")
    path = (root / relative_path).resolve()
    path.relative_to(root.resolve())
    if not path.is_file():
        raise ValueError(f"工程素材不存在：{path.name}")
    return path


def _master_descriptor(project: dict[str, Any]) -> dict[str, str]:
    project_id = str(project["project_id"])
    source = (((project.get("manifest") or {}).get("audio") or {})
              .get("master") or {}).get("source") or {}
    relative = str(source.get("path") or "")
    if not relative.replace("\\", "/").startswith("source/audio/"):
        raise ValueError("工程缺少完整原曲母带。")
    _project_file(Path(project["paths"]["input"]), relative)
    relative_path = Path(relative.replace("\\", "/"))
    return {
        "type": "input",
        "subfolder": f"xiaoyue_mv_projects/{project_id}/{relative_path.parent.as_posix()}",
        "filename": relative_path.name,
    }


def _video_descriptor(project: dict[str, Any], output_root: Path,
                      output: dict[str, Any]) -> dict[str, str] | None:
    filename = str(output.get("filename") or "")
    subfolder = str(output.get("subfolder") or "").replace("\\", "/")
    if (not filename or Path(filename).name != filename
            or Path(filename).suffix.lower() not in VIDEO_SUFFIXES):
        return None
    project_id = str(project["project_id"])
    expected_prefix = f"xiaoyue_mv_projects/{project_id}/generations"
    if subfolder != expected_prefix and not subfolder.startswith(expected_prefix + "/"):
        return None
    try:
        path = _project_file(output_root, f"{subfolder}/{filename}")
        path.relative_to((Path(project["paths"]["output"]) / "generations").resolve())
    except (ValueError, OSError):
        return None
    return {"type": "output", "subfolder": subfolder, "filename": filename}


def _version_entry(version: dict[str, Any], window: dict[str, Any],
                   project: dict[str, Any], output_root: Path) -> dict[str, Any]:
    current_range = window.get("delivery_timeline_range") or [0, 0]
    deliver = int(version.get("deliver_frames") or 0)
    timeline_in = version.get("timeline_in_frame")
    timeline_out = version.get("timeline_out_frame_exclusive")
    if timeline_in is None or timeline_out is None:
        timeline_in, timeline_out = current_range
    timeline_in, timeline_out = int(timeline_in), int(timeline_out)
    source_in = (int(version.get("deliver_from_raw_frame") or 0)
                 if version.get("frame_space") == "raw" else 0)
    source_out = source_in + deliver
    output = version.get("output") or {}
    source = (_video_descriptor(project, output_root, output)
              if version.get("status") == "completed" else None)
    frame_count = int(output.get("frame_count") or 0)
    playable = bool(source and deliver > 0 and source_in >= 0
                    and timeline_in >= 0 and timeline_out - timeline_in == deliver
                    and (frame_count <= 0 or source_out <= frame_count))
    return {
        "id": str(version.get("id") or ""),
        "number": version.get("number"),
        "status": str(version.get("status") or ""),
        "stale": bool(version.get("stale")),
        "playable": playable,
        "parent_version_id": str(version.get("parent_version_id") or ""),
        "timeline_in_frame": timeline_in,
        "timeline_out_frame_exclusive": timeline_out,
        "source_in_frame": source_in,
        "source_out_frame_exclusive": source_out,
        "source_frames": frame_count,
        "timeline_points_recorded": (version.get("timeline_in_frame") is not None
                                     and version.get("timeline_out_frame_exclusive") is not None),
        "source": source if playable else None,
        "error": str(version.get("error") or ""),
    }


def final_subtitles(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Only the shot's formal subtitle cell is delivery text; handles are not."""
    result = []
    for shot in plan.get("shots") or []:
        track = shot.get("subtitle_track") or {}
        text = str(track.get("text") or "").strip()
        start = int(shot.get("start_frame") or 0)
        end = int(shot.get("end_frame") or 0)
        if text and end > start:
            result.append({
                "shot_uid": _shot_uid(shot),
                "shot": str(shot.get("editorial_label") or shot.get("id") or ""),
                "start_frame": start,
                "end_frame_exclusive": end,
                "text": text,
            })
    return sorted(result, key=lambda item: (item["start_frame"], item["shot"]))


def _srt_time(frame: int) -> str:
    milliseconds = round(frame * 1000 / FPS)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def subtitle_srt(rows: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"{index}\n{_srt_time(row['start_frame'])} --> "
        f"{_srt_time(row['end_frame_exclusive'])}\n{row['text']}"
        for index, row in enumerate(rows, 1)
    ) + ("\n" if rows else "")


def subtitle_csv(rows: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=(
        "shot", "shot_uid", "start_frame", "end_frame_exclusive", "text"))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def continuation_parents(plan: dict[str, Any]) -> dict[str, str]:
    shots = plan.get("shots") or []
    transitions = plan.get("transitions") or []
    return {
        _shot_uid(shots[index]): _shot_uid(shots[index - 1])
        for index in range(1, len(shots))
        if index - 1 < len(transitions)
        and (transitions[index - 1] or {}).get("type") == "long_take"
    }


def _track_label(index: int) -> str:
    """Keep the presentation numeric without limiting continuation depth."""
    return f"{index + 1:02d}"


def selection_chain(state: dict[str, Any], shot_uid: str, version_id: str,
                    plan: dict[str, Any]) -> dict[str, str]:
    """Select the recorded ancestor branch, independent of later cut revisions."""
    records = state.get("shots") or {}
    parent_by_uid = continuation_parents(plan)
    by_id = {
        str(version.get("id")): (uid, version)
        for uid, record in records.items()
        for version in record.get("versions", [])
        if version.get("id")
    }
    selections: dict[str, str] = {}
    current_uid, current_id = shot_uid, version_id
    while current_id:
        if current_uid in selections:
            raise ValueError("续写版本链存在循环。")
        found = by_id.get(current_id)
        if not found or found[0] != current_uid:
            raise ValueError("所选镜头版本的续写祖先不存在。")
        version = found[1]
        if version.get("status") != "completed":
            raise ValueError("只能选用已成功生成的视频版本。")
        selections[current_uid] = current_id
        expected_parent_uid = parent_by_uid.get(current_uid, "")
        parent_id = str(version.get("parent_version_id") or "")
        if bool(expected_parent_uid) != bool(parent_id):
            raise ValueError("续写版本与当前镜头连接关系不一致。")
        current_uid, current_id = expected_parent_uid, parent_id
    return selections


def build_preview_manifest(plan: dict[str, Any], state: dict[str, Any],
                           project: dict[str, Any], output_root: Path) -> dict[str, Any]:
    project_id = str(project["project_id"])
    if str((plan.get("project") or {}).get("id") or "") != project_id:
        raise ValueError("剪辑台连入的计划与工程不一致。")
    shots = plan.get("shots") or []
    windows = plan.get("generation_windows") or []
    if not shots or len(windows) != len(shots):
        raise ValueError("请先在主 MV 工作台保存并编译完整镜头计划。")
    windows_by_uid = {
        str(window.get("shot_uid") or f"legacy_{window.get('shot_id')}"): window
        for window in windows
    }
    if len(windows_by_uid) != len(shots):
        raise ValueError("生成窗口与镜头数量不一致。")
    transitions = plan.get("transitions") or []
    records = state.get("shots") or {}
    arranged = []
    placed_intervals: list[tuple[int, int, int]] = []
    previous_track = -1
    normal_preference = 0
    for index, shot in enumerate(shots):
        uid = _shot_uid(shot)
        window = windows_by_uid.get(uid)
        if not window:
            raise ValueError(f"镜头 {shot.get('editorial_label') or uid} 缺少生成窗口。")
        incoming = (transitions[index - 1] or {}).get("type") if index else ""
        record = records.get(uid) or {}
        versions = [_version_entry(item, window, project, output_root)
                    for item in record.get("versions", [])]
        selected_id = str(record.get("selected_version_id") or "")
        selected = next((item for item in versions if item["id"] == selected_id), None)
        planned_start = int(shot.get("start_frame") or 0)
        planned_end = int(shot.get("end_frame") or 0)
        interval_start = selected["timeline_in_frame"] if selected else planned_start
        interval_end = selected["timeline_out_frame_exclusive"] if selected else planned_end

        def track_is_free(candidate: int) -> bool:
            return all(candidate != used_track or interval_end <= used_start
                       or interval_start >= used_end
                       for used_track, used_start, used_end in placed_intervals)

        if incoming == "long_take":
            # Every continuation rises above its direct predecessor, with no fixed track cap.
            track = previous_track + 1
            while not track_is_free(track):
                track += 1
        else:
            # Independent groups normally alternate A/B. Never place adjacent
            # groups on the same track, even when a chain ended on that track.
            track = next((candidate for candidate in
                          (normal_preference, 1 - normal_preference)
                          if candidate != previous_track and track_is_free(candidate)), None)
            if track is None:
                track = 2
                while track == previous_track or not track_is_free(track):
                    track += 1
            normal_preference = 1 - normal_preference
        previous_track = track
        placed_intervals.append((track, interval_start, interval_end))
        successful = sum(item["status"] == "completed" for item in versions)
        if incoming == "long_take":
            parent = arranged[-1]
            lineage_ok = bool(selected and parent["selected_playable"]
                              and selected["parent_version_id"] == parent["selected_version_id"])
        else:
            lineage_ok = bool(selected and not selected["parent_version_id"])
        selected_playable = bool(selected and selected["playable"] and lineage_ok)
        arranged.append({
            "shot_id": str(shot.get("id") or ""),
            "shot_uid": uid,
            "label": str(shot.get("editorial_label") or shot.get("id") or ""),
            "track": track,
            "incoming_transition": incoming or "hard_cut",
            "planned_start_frame": planned_start,
            "planned_end_frame_exclusive": planned_end,
            "selected_version_id": selected_id,
            "selection_confirmed": bool(record.get("selection_confirmed")) or successful <= 1,
            "selected_playable": selected_playable,
            "selection_issue": ("" if selected_playable or not selected else
                                "所选续写版本与前段选版不匹配" if selected["playable"] else
                                "所选版本的素材或交付帧无效"),
            "versions": versions,
        })
    total_frames = int((plan.get("audio") or {}).get("timeline_frames") or 0)
    if total_frames <= 0:
        total_frames = max(item["planned_end_frame_exclusive"] for item in arranged)
    return {
        "schema": "xiaoyue.mv.preview-desk.v1",
        "project": {"id": project_id,
                    "name": str(project.get("project_name") or ""),
                    "code": str(project.get("project_code") or "")},
        "fps": FPS,
        "total_frames": total_frames,
        "master_audio": _master_descriptor(project),
        "audio_policy": "mute_all_shot_videos_use_full_master",
        "tracks": [_track_label(index) for index in
                   range(max(item["track"] for item in arranged) + 1)],
        "shots": arranged,
        "subtitles": final_subtitles(plan),
    }
