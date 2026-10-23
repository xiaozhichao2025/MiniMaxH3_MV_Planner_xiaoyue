"""Project-owned, append-only H3 shot attempt and selection records."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from .project_store import load_project, normalize_project_id
except ImportError:  # Standalone tests.
    from project_store import load_project, normalize_project_id


_LOCK = threading.RLock()
SCHEMA = "xiaoyue.mv.shot-generations.v2"
LEGACY_SCHEMA = "xiaoyue.mv.shot-generations.v1"


def _manifest_path(project_id: str, roots: dict[str, Path] | None = None) -> Path:
    project = load_project(normalize_project_id(project_id), roots)
    root = Path(project["paths"]["output"]).resolve()
    path = (root / "generations" / "shot_versions.json").resolve()
    path.relative_to(root)
    return path


def _read(path: Path, project_id: str) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": SCHEMA, "project_id": project_id, "shots": {}}
    with path.open("r", encoding="utf-8") as source:
        state = json.load(source)
    if (not isinstance(state, dict) or state.get("schema") not in {SCHEMA, LEGACY_SCHEMA}
            or state.get("project_id") != project_id
            or not isinstance(state.get("shots"), dict)):
        raise ValueError("镜头生成版本记录与当前项目不匹配。")
    if state["schema"] == LEGACY_SCHEMA:
        migrated: dict[str, Any] = {}
        for old_key, record in state["shots"].items():
            stable_key = (f"legacy_{old_key}" if str(old_key).startswith("shot_")
                          else str(old_key))
            if stable_key in migrated:
                raise ValueError("旧镜头版本记录存在重复身份，需人工检查后才能迁移。")
            for version in record.get("versions", []):
                version.setdefault("shot_uid", stable_key)
            migrated[stable_key] = record
        state["shots"] = migrated
        state["schema"] = SCHEMA
    return state


def _write(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(state, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_versions(project_id: str, roots: dict[str, Path] | None = None) -> dict[str, Any]:
    project_id = normalize_project_id(project_id)
    with _LOCK:
        return _read(_manifest_path(project_id, roots), project_id)


def require_confirmed_selection(state: dict[str, Any], shot_id: str,
                                label: str) -> None:
    """A default first version is not a user's choice once alternatives exist."""
    record = (state.get("shots") or {}).get(shot_id) or {}
    completed = [version for version in record.get("versions", [])
                 if version.get("status") == "completed"]
    if len(completed) > 1 and not record.get("selection_confirmed"):
        raise ValueError(
            f"续写前段 {label} 有 {len(completed)} 个成功版本；"
            "请先在该镜头的“镜头”页明确点选一个最终使用版本，再生成续写镜头。"
        )


def resolve_parent_version(state: dict[str, Any], shot_id: str,
                           label: str,
                           fresh_version: dict[str, Any] | None = None) -> dict[str, Any]:
    """Use this batch's completed parent, otherwise require a chosen old version."""
    if fresh_version is not None:
        if fresh_version.get("status") != "completed":
            raise ValueError(f"本批次续写前段 {label} 未成功，不能生成后续镜头。")
        return fresh_version
    require_confirmed_selection(state, shot_id, label)
    record = (state.get("shots") or {}).get(shot_id) or {}
    selected_id = record.get("selected_version_id")
    parent = next((item for item in record.get("versions", [])
                   if item.get("id") == selected_id), None)
    if not parent or parent.get("status") != "completed":
        raise ValueError(f"续写前段 {label} 没有可用的最终版本；请先生成并选定它。")
    if parent.get("stale"):
        raise ValueError(
            f"续写前段 {label} 的版本切点或祖先选版已改变；请重新生成前段。")
    return parent


def completed_batch_parent_selections(
        selected_ids: list[str], parent_by_id: dict[str, str],
        shot_uid_by_id: dict[str, str],
        fresh: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Select new parents only when their entire selected continuation chain succeeds."""
    selected = set(selected_ids)
    children: dict[str, list[str]] = {}
    for child_id in selected_ids:
        parent_id = parent_by_id.get(child_id, "")
        if parent_id in selected:
            children.setdefault(parent_id, []).append(child_id)

    def chain_completed(shot_id: str, seen: set[str]) -> bool:
        version = fresh.get(shot_id)
        if shot_id in seen or not version or version.get("status") != "completed":
            return False
        return all(chain_completed(child, seen | {shot_id})
                   for child in children.get(shot_id, []))

    return {
        shot_uid_by_id[parent_id]: fresh[parent_id]["id"]
        for parent_id in children
        if parent_id in shot_uid_by_id and chain_completed(parent_id, set())
    }


def recover_interrupted_versions(project_id: str,
                                 roots: dict[str, Path] | None = None) -> dict[str, Any]:
    """After a server restart, unfinished records are no longer live jobs."""
    project_id = normalize_project_id(project_id)
    with _LOCK:
        path = _manifest_path(project_id, roots)
        state = _read(path, project_id)
        changed = False
        for shot in state["shots"].values():
            for version in shot.get("versions", []):
                if version.get("status") in {"queued", "running"}:
                    version.update({
                        "status": "failed",
                        "error": "ComfyUI 服务重启，旧生成任务已中断；可重新生成。",
                        "finished_at": time.time(),
                    })
                    changed = True
        if changed:
            _write(path, state)
        return state


def generation_job_conflict(active_jobs: list[dict[str, Any]], project_id: str,
                            requested_ids: set[str],
                            parent_by_id: dict[str, str]) -> str:
    """Reserve independent shots while keeping continuation chains deterministic."""
    def is_ancestor(ancestor: str, child: str) -> bool:
        seen: set[str] = set()
        while child and child not in seen:
            seen.add(child)
            child = parent_by_id.get(child, "")
            if child == ancestor:
                return True
        return False

    for job in active_jobs:
        if job.get("project_id") != project_id or job.get("state") != "running":
            continue
        busy_ids = {str(value) for value in job.get("shot_ids") or []}
        if not busy_ids or len(requested_ids) > 1 or len(busy_ids) > 1:
            return "批量顺序任务与其它镜头任务不能同时启动；请等待当前任务结束。"
        for requested_id in requested_ids:
            for busy_id in busy_ids:
                if (requested_id == busy_id
                        or is_ancestor(requested_id, busy_id)
                        or is_ancestor(busy_id, requested_id)):
                    return "同一镜头或其续写链已有生成任务；请等待它结束。"
    return ""


def with_stale_flags(state: dict[str, Any],
                     expected_windows: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark descendant versions whose parent is no longer the chosen cut."""
    import copy

    result = copy.deepcopy(state)
    selected = {}
    expected = expected_windows or {}
    for shot in result.get("shots", {}).values():
        if not isinstance(shot, dict):
            continue
        chosen = next((version for version in shot.get("versions", [])
                       if version.get("id") == shot.get("selected_version_id")), None)
        if chosen:
            selected[chosen["id"]] = chosen

    def stale(version: dict[str, Any], seen: set[str]) -> bool:
        shot_id = str(version.get("shot_uid") or version.get("shot_id") or "")
        matching = expected.get(shot_id)
        if isinstance(matching, dict):
            matching = matching.get(
                "current" if version.get("fingerprint_revision") == 2 else "legacy")
        if matching is not None and version.get("window_fingerprint") != matching:
            return True
        parent = str(version.get("parent_version_id") or "")
        if not parent:
            return False
        if parent in seen or parent not in selected:
            return True
        return stale(selected[parent], seen | {parent})

    for shot in result.get("shots", {}).values():
        for version in shot.get("versions", []):
            version["stale"] = stale(version, {str(version.get("id") or "")})
    return result


def begin_version(project_id: str, shot_id: str, *, editorial_label: str,
                  window_id: str, prompt_fingerprint: str,
                  window_fingerprint: str,
                  deliver_frames: int,
                  raw_frames: int = 0,
                  deliver_from_raw_frame: int = 0,
                  timeline_in_frame: int | None = None,
                  timeline_out_frame_exclusive: int | None = None,
                  seed: int | None = None,
                  workflow_fingerprint: str = "",
                  parent_version_id: str = "",
                  roots: dict[str, Path] | None = None) -> dict[str, Any]:
    project_id = normalize_project_id(project_id)
    if not shot_id or not window_id:
        raise ValueError("镜头或生成窗口 ID 为空。")
    if ((timeline_in_frame is None) != (timeline_out_frame_exclusive is None)
            or (timeline_in_frame is not None and (
                int(timeline_in_frame) < 0 or
                int(timeline_out_frame_exclusive) - int(timeline_in_frame)
                != int(deliver_frames)))):
        raise ValueError("镜头版本的总轨记录点必须与交付帧数一致。")
    with _LOCK:
        path = _manifest_path(project_id, roots)
        state = _read(path, project_id)
        shot = state["shots"].setdefault(shot_id, {"selected_version_id": "", "versions": []})
        if any(version.get("status") in {"queued", "running"}
               for version in shot["versions"]):
            raise ValueError("当前镜头已有生成中的版本。")
        # A failed/cancelled run is an attempt, not a published video version.
        # Legacy rows used number for both; retain their published numbers and
        # use the old value as the attempt sequence when migrating in place.
        attempt_number = max((int(version.get("attempt_number")
                                  or version.get("number") or 0)
                              for version in shot["versions"]), default=0) + 1
        pending_number = max((int(version.get("number") or 0)
                              for version in shot["versions"]
                              if version.get("status") == "completed"), default=0) + 1
        version = {
            "id": uuid.uuid4().hex,
            "attempt_number": attempt_number,
            "pending_number": pending_number,
            "number": None,
            "shot_id": shot_id,
            "shot_uid": shot_id,
            "editorial_label": editorial_label,
            "window_id": window_id,
            "deliver_frames": int(deliver_frames),
            "raw_frames": int(raw_frames),
            "deliver_from_raw_frame": int(deliver_from_raw_frame),
            "timeline_in_frame": (int(timeline_in_frame)
                                  if timeline_in_frame is not None else None),
            "timeline_out_frame_exclusive": (
                int(timeline_out_frame_exclusive)
                if timeline_out_frame_exclusive is not None else None),
            "frame_space": "raw" if raw_frames else "delivery",
            "seed": seed,
            "prompt_fingerprint": prompt_fingerprint,
            "window_fingerprint": window_fingerprint,
            "fingerprint_revision": 2,
            "workflow_fingerprint": workflow_fingerprint,
            "parent_version_id": parent_version_id,
            "status": "queued",
            "created_at": time.time(),
            "output": None,
            "error": "",
        }
        shot["versions"].append(version)
        _write(path, state)
        return version.copy()


def update_version(project_id: str, shot_id: str, version_id: str, *,
                   status: str, output: dict[str, Any] | None = None,
                   error: str = "", prompt_id: str = "",
                   preserve_existing_selection: bool = False,
                   roots: dict[str, Path] | None = None) -> dict[str, Any]:
    if status not in {"running", "completed", "failed", "cancelled"}:
        raise ValueError("无效的镜头生成状态。")
    project_id = normalize_project_id(project_id)
    with _LOCK:
        path = _manifest_path(project_id, roots)
        state = _read(path, project_id)
        shot = state["shots"].get(shot_id) or {}
        version = next((item for item in shot.get("versions", [])
                        if item.get("id") == version_id), None)
        if version is None:
            raise ValueError("镜头版本不存在。")
        if version["status"] in {"completed", "failed", "cancelled"}:
            raise ValueError("已结束的生成版本不可覆盖。")
        version["status"] = status
        if prompt_id:
            version["prompt_id"] = prompt_id
        if status in {"completed", "failed", "cancelled"}:
            version["finished_at"] = time.time()
            version["error"] = str(error or "")
        if status == "completed":
            if not isinstance(output, dict) or not output.get("filename"):
                raise ValueError("完成的镜头版本必须有可预览视频。")
            version["number"] = int(version.get("pending_number") or 0) or (
                max((int(item.get("number") or 0) for item in shot["versions"]
                     if item.get("status") == "completed" and item is not version),
                    default=0) + 1)
            version["output"] = output
            if not shot.get("selected_version_id"):
                shot["selected_version_id"] = version_id
            prior_completed = [item for item in shot["versions"]
                               if item is not version and item.get("status") == "completed"]
            if prior_completed:
                if preserve_existing_selection:
                    # A failed later batch segment must not invalidate the
                    # previously usable final chain merely by adding a parent.
                    if (len(prior_completed) == 1
                            and shot.get("selected_version_id") == prior_completed[0]["id"]):
                        shot["selection_confirmed"] = True
                else:
                    shot["selection_confirmed"] = False
        _write(path, state)
        return version.copy()


def recover_misclassified_output(project_id: str, shot_id: str,
                                 version_id: str, *, prompt_id: str,
                                 output: dict[str, Any],
                                 roots: dict[str, Path] | None = None) -> dict[str, Any]:
    """Reconcile a saved video rejected by an older history parser."""
    if not isinstance(output, dict) or not output.get("filename"):
        raise ValueError("恢复镜头版本必须提供已验证的视频文件。")
    project_id = normalize_project_id(project_id)
    with _LOCK:
        path = _manifest_path(project_id, roots)
        state = _read(path, project_id)
        shot = state["shots"].get(shot_id) or {}
        version = next((item for item in shot.get("versions", [])
                        if item.get("id") == version_id), None)
        if (version is None or version.get("status") != "failed"
                or version.get("prompt_id") != prompt_id
                or version.get("error") != "工作流已结束，但 SaveVideo 没有返回视频文件。"):
            raise ValueError("该版本不是可恢复的 SaveVideo 结果识别错误。")
        version["status"] = "completed"
        if not version.get("number"):
            # A later attempt may already have claimed the originally pending
            # number. Recovery publishes the next unused success number.
            version["number"] = max(
                (int(item.get("number") or 0) for item in shot["versions"]
                 if item.get("status") == "completed" and item is not version),
                default=0) + 1
        version["output"] = output
        version["error"] = ""
        version["recovered_at"] = time.time()
        if not shot.get("selected_version_id"):
            shot["selected_version_id"] = version_id
        if sum(item.get("status") == "completed" for item in shot["versions"]) > 1:
            shot["selection_confirmed"] = False
        _write(path, state)
        return version.copy()


def select_versions(project_id: str, selections: dict[str, str],
                    roots: dict[str, Path] | None = None) -> dict[str, Any]:
    """Commit a completed batch's parent choices in one manifest write."""
    if not selections:
        raise ValueError("没有要选用的镜头版本。")
    project_id = normalize_project_id(project_id)
    with _LOCK:
        path = _manifest_path(project_id, roots)
        state = _read(path, project_id)
        for shot_id, version_id in selections.items():
            shot = state["shots"].get(shot_id) or {}
            version = next((item for item in shot.get("versions", [])
                            if item.get("id") == version_id), None)
            if version is None or version.get("status") != "completed":
                raise ValueError("只能选用已成功生成的视频版本。")
        for shot_id, version_id in selections.items():
            state["shots"][shot_id]["selected_version_id"] = version_id
            state["shots"][shot_id]["selection_confirmed"] = True
        _write(path, state)
        return state


def select_version(project_id: str, shot_id: str, version_id: str,
                   roots: dict[str, Path] | None = None) -> dict[str, Any]:
    return select_versions(project_id, {shot_id: version_id}, roots)
