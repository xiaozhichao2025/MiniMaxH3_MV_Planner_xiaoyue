"""Non-destructive edit decisions for selected, full-length H3 shot versions."""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any


FPS = 24
CSV_FIELDS = (
    "shot", "version", "source_file", "source_in_frame",
    "source_out_frame_exclusive", "timeline_in_frame",
    "timeline_out_frame_exclusive", "fps", "transition_to_next",
    "master_audio_file", "audio_policy",
)


def _contained_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    if not path.is_file():
        raise ValueError(f"剪切表源文件不存在：{path.name}")
    return path


def build_cut_list(plan: dict[str, Any], state: dict[str, Any],
                   output_root: Path, project: dict[str, Any]) -> dict[str, Any]:
    """Require one current selected version per shot; never modify source media."""
    shots = plan.get("shots") or []
    windows = plan.get("generation_windows") or []
    if not shots or len(windows) != len(shots):
        raise ValueError("剪切表需要每个镜头都有一个已编译的生成窗口。")
    window_by_shot = {str(item.get("shot_id") or ""): item for item in windows}
    if len(window_by_shot) != len(shots):
        raise ValueError("剪切表遇到重复或缺失的镜头生成窗口。")
    source = ((((project.get("manifest") or {}).get("audio") or {}).get("master") or {})
              .get("source") or {}).get("path")
    if not source:
        raise ValueError("项目缺少完整原曲母带文件。")
    master_path = _contained_file(Path(project["paths"]["input"]), str(source))
    project_outputs = (Path(project["paths"]["output"]) / "generations").resolve()
    result = []
    for index, shot in enumerate(shots):
        shot_id = str(shot.get("id") or "")
        window = window_by_shot.get(shot_id)
        if not window:
            raise ValueError(f"镜头 {shot_id} 没有生成窗口。")
        shot_uid = str(shot.get("shot_uid") or f"legacy_{shot_id}")
        if str(window.get("shot_uid") or f"legacy_{shot_id}") != shot_uid:
            raise ValueError(f"镜头 {shot_id} 的窗口身份与计划不一致。")
        record = (state.get("shots") or {}).get(shot_uid) or {}
        if (sum(item.get("status") == "completed" for item in record.get("versions", [])) > 1
                and not record.get("selection_confirmed")):
            raise ValueError(f"镜头 {shot.get('editorial_label') or shot_id} 有多个成功版本，请先确认最终版本。")
        selected_id = record.get("selected_version_id")
        version = next((item for item in record.get("versions", [])
                        if item.get("id") == selected_id), None)
        if (not version or version.get("status") != "completed"
                or version.get("stale")):
            raise ValueError(f"镜头 {shot.get('editorial_label') or shot_id} 尚无当前规划可用的最终版本。")
        descriptor = version.get("output") or {}
        filename = str(descriptor.get("filename") or "")
        subfolder = str(descriptor.get("subfolder") or "").replace("\\", "/")
        if not filename or Path(filename).name != filename:
            raise ValueError(f"镜头 {shot_id} 的输出文件记录无效。")
        source_path = _contained_file(output_root, str(Path(subfolder) / filename))
        source_path.relative_to(project_outputs)
        deliver = int(window["deliver_frames"])
        lead = int(window["deliver_from_raw_frame"])
        raw = int(window["raw_frames"])
        if (deliver <= 0 or lead < 0 or lead + deliver > raw
                or int(version.get("deliver_frames") or 0) != deliver):
            raise ValueError(f"镜头 {shot_id} 的裁切帧范围与当前规划不一致。")
        frame_space = version.get("frame_space") or "delivery"
        if frame_space == "raw":
            if (int(version.get("raw_frames") or 0) != raw
                    or int(version.get("deliver_from_raw_frame", -1)) != lead):
                raise ValueError(f"镜头 {shot_id} 的原始窗口与当前规划不一致。")
            source_in = lead
        elif frame_space == "delivery":
            source_in = 0  # Versions created before raw-window preservation.
        else:
            raise ValueError(f"镜头 {shot_id} 的视频帧坐标类型无效。")
        timeline = window.get("delivery_timeline_range") or []
        if (len(timeline) != 2 or int(timeline[1]) - int(timeline[0]) != deliver):
            raise ValueError(f"镜头 {shot_id} 的时间线范围无效。")
        version_timeline_in = version.get("timeline_in_frame")
        version_timeline_out = version.get("timeline_out_frame_exclusive")
        if version_timeline_in is None or version_timeline_out is None:
            # Existing versions predate per-version absolute timeline markers.
            version_timeline_in, version_timeline_out = timeline
        version_timeline_in = int(version_timeline_in)
        version_timeline_out = int(version_timeline_out)
        if (version_timeline_in < 0 or
                version_timeline_out - version_timeline_in != deliver):
            raise ValueError(f"镜头 {shot_id} 的版本总轨记录点无效。")
        transitions = plan.get("transitions") or []
        transition = transitions[index] if index < len(transitions) else {}
        result.append({
            "shot": str(window.get("editorial_label") or shot.get("editorial_label") or shot_id),
            "shot_id": shot_id,
            "shot_uid": shot_uid,
            "version": int(version.get("number") or 0),
            "version_id": str(version["id"]),
            "source_file": str(source_path),
            "source_in_frame": source_in,
            "source_out_frame_exclusive": source_in + deliver,
            "timeline_in_frame": version_timeline_in,
            "timeline_out_frame_exclusive": version_timeline_out,
            "fps": FPS,
            "transition_to_next": str(transition.get("type") or "")
                if isinstance(transition, dict) else "",
            "master_audio_file": str(master_path),
            "audio_policy": "mute_shot_audio_use_full_master",
        })
    return {
        "schema": "xiaoyue.mv.editorial-cut-list.v1",
        "project_id": str((plan.get("project") or {}).get("id") or ""),
        "fps": FPS,
        "master_audio_file": str(master_path),
        "audio_policy": "mute_shot_audio_use_full_master",
        "out_frame_semantics": "exclusive",
        "shots": result,
    }


def cut_list_csv(payload: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(payload["shots"])
    return output.getvalue()


