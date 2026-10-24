"""Small editor checkpoints, separate from the compiled plan and video records."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

try:
    from .project_store import load_project, normalize_project_id
    from .generation_store import mark_independent_edit_allowed
except ImportError:  # Standalone tests.
    from project_store import load_project, normalize_project_id
    from generation_store import mark_independent_edit_allowed


_LOCK = threading.RLock()


def _paths(project_id: str, roots: dict[str, Path] | None = None) -> tuple[Path, Path]:
    project = load_project(normalize_project_id(project_id), roots)
    base = (Path(project["paths"]["input"]) / "plans").resolve()
    base.relative_to(Path(project["paths"]["input"]).resolve())
    return base / "editor_draft.json", base / "timeline_checkpoint.json"


def _require_current_audio(project_id: str, fingerprint: str,
                           roots: dict[str, Path] | None = None) -> None:
    manifest = load_project(project_id, roots)["manifest"]
    current = str((manifest.get("audio") or {}).get("fingerprint") or "")
    if current and current != fingerprint:
        raise ValueError("项目母带已变化；旧编辑页面不能覆盖当前项目草稿。")


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as source:
        result = json.load(source)
    if not isinstance(result, dict):
        raise ValueError("编辑草稿格式无效。")
    return result


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_editor_state(project_id: str,
                      roots: dict[str, Path] | None = None) -> dict[str, Any]:
    project_id = normalize_project_id(project_id)
    with _LOCK:
        draft_path, checkpoint_path = _paths(project_id, roots)
        draft = _read(draft_path)
        checkpoint = _read(checkpoint_path)
        if draft and (draft.get("schema") != "xiaoyue.mv.editor-draft.v1"
                      or draft.get("project_id") != project_id):
            raise ValueError("编辑草稿与当前项目不匹配。")
        if checkpoint and (checkpoint.get("schema") != "xiaoyue.mv.timeline-checkpoint.v1"
                           or checkpoint.get("project_id") != project_id):
            raise ValueError("时间轴检查点与当前项目不匹配。")
        shot_dir = draft_path.parent / "shot_drafts"
        patches = []
        if shot_dir.is_dir():
            for path in shot_dir.glob("*.json"):
                try:
                    patch = _read(path)
                except (OSError, ValueError):
                    continue  # One damaged sidecar must not hide other edits.
                if (patch and patch.get("schema") == "xiaoyue.mv.shot-draft.v1"
                        and patch.get("project_id") == project_id):
                    patches.append(patch)
        return {"draft": draft, "checkpoint": checkpoint, "shot_patches": patches}


def save_editor_plan(project_id: str, plan: dict[str, Any],
                     roots: dict[str, Path] | None = None) -> None:
    project_id = normalize_project_id(project_id)
    if (not isinstance(plan, dict)
            or str((plan.get("project") or {}).get("id") or "") != project_id
            or not isinstance(plan.get("shots"), list)):
        raise ValueError("编辑草稿与项目不匹配或缺少镜头。")
    revision = int(plan.get("editor_revision") or 0)
    _require_current_audio(project_id, str((plan.get("audio") or {}).get("fingerprint") or ""), roots)
    with _LOCK:
        path, _ = _paths(project_id, roots)
        existing = _read(path)
        same_audio = (str(((existing or {}).get("plan") or {}).get("audio", {}).get("fingerprint") or "")
                      == str((plan.get("audio") or {}).get("fingerprint") or ""))
        if existing and same_audio and revision <= int(existing.get("revision") or 0):
            if (revision == int(existing.get("revision") or 0)
                    and existing.get("plan") == plan):
                return
            raise ValueError("旧编辑草稿不能覆盖较新的保存。")
        _write(path, {"schema": "xiaoyue.mv.editor-draft.v1",
                      "project_id": project_id, "revision": revision,
                      "plan": plan})
    mark_independent_edit_allowed(
        project_id, plan["shots"], plan.get("transitions") or [], roots)


def save_timeline_checkpoint(project_id: str, checkpoint: dict[str, Any],
                             roots: dict[str, Path] | None = None) -> None:
    project_id = normalize_project_id(project_id)
    if (not isinstance(checkpoint, dict)
            or not isinstance(checkpoint.get("shots"), list)
            or not isinstance(checkpoint.get("transitions"), list)):
        raise ValueError("时间轴检查点格式无效。")
    shots = checkpoint["shots"]
    if (not shots or len(checkpoint["transitions"]) != len(shots) - 1
            or len({row.get("shot_uid") for row in shots if isinstance(row, dict)}) != len(shots)
            or any(not isinstance(row, dict) or not isinstance(row.get("shot_uid"), str)
                   or not row["shot_uid"] or not isinstance(row.get("start_frame"), int)
                   or not isinstance(row.get("end_frame"), int)
                   or row["end_frame"] <= row["start_frame"]
                   for row in shots)
            or any(shots[index]["end_frame"] != shots[index + 1]["start_frame"]
                   for index in range(len(shots) - 1))):
        raise ValueError("时间轴检查点的镜头范围不连续。")
    base_revision = int(checkpoint.get("base_revision") or 0)
    audio_fingerprint = str(checkpoint.get("audio_fingerprint") or "")
    _require_current_audio(project_id, audio_fingerprint, roots)
    with _LOCK:
        draft_path, path = _paths(project_id, roots)
        draft = _read(draft_path)
        same_audio = (str((((draft or {}).get("plan") or {}).get("audio") or {}).get("fingerprint") or "")
                      == audio_fingerprint)
        if draft and same_audio and base_revision < int(draft.get("revision") or 0):
            raise ValueError("时间轴检查点早于已保存的编辑草稿。")
        _write(path, {"schema": "xiaoyue.mv.timeline-checkpoint.v1",
                      "project_id": project_id, "base_revision": base_revision,
                      "audio_fingerprint": audio_fingerprint,
                      "shots": checkpoint["shots"],
                      "transitions": checkpoint["transitions"]})
    mark_independent_edit_allowed(
        project_id, checkpoint["shots"], checkpoint["transitions"], roots)


def save_shot_patch(project_id: str, patch: dict[str, Any],
                    roots: dict[str, Path] | None = None) -> None:
    project_id = normalize_project_id(project_id)
    uid = str(patch.get("shot_uid") or "") if isinstance(patch, dict) else ""
    if (not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", uid)
            or not isinstance(patch.get("prompt"), str)
            or not isinstance(patch.get("creative_outline"), str)
            or patch.get("singing_performance") not in {True, False, None}):
        raise ValueError("镜头草稿格式无效。")
    base_revision = int(patch.get("base_revision") or 0)
    audio_fingerprint = str(patch.get("audio_fingerprint") or "")
    _require_current_audio(project_id, audio_fingerprint, roots)
    with _LOCK:
        draft_path, _ = _paths(project_id, roots)
        draft = _read(draft_path)
        same_audio = (str((((draft or {}).get("plan") or {}).get("audio") or {}).get("fingerprint") or "")
                      == audio_fingerprint)
        if draft and same_audio and base_revision < int(draft.get("revision") or 0):
            raise ValueError("镜头草稿早于已保存的编辑计划。")
        _write(draft_path.parent / "shot_drafts" / f"{uid}.json", {
            "schema": "xiaoyue.mv.shot-draft.v1", "project_id": project_id,
            "base_revision": base_revision, "shot_uid": uid,
            "audio_fingerprint": audio_fingerprint,
            "prompt": patch["prompt"],
            "creative_outline": patch["creative_outline"],
            "singing_performance": patch.get("singing_performance"),
        })
