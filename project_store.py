from __future__ import annotations

import json
import math
import os
import re
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .skill_library import normalize_skill_templates
except ImportError:  # Standalone test suite loads this module directly.
    from skill_library import normalize_skill_templates


PROJECT_SCHEMA = "xiaoyue.mvproject/1.0"
PROJECT_TYPE = "XIAOYUE_MV_PROJECT"
PROJECTS_SUBFOLDER = "xiaoyue_mv_projects"

_PROJECT_LOCK = threading.RLock()
_INVALID_FILENAME = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_ASSET_CATEGORIES = {"character", "scene", "prop"}
_ASSET_MEDIA_TYPES = {"image", "video"}
_H3_WINDOW_PRESETS = {124, 141, 158, 175, 192, 209, 226, 243, 260, 277, 294, 311, 328, 345, 362}
_ASPECT_RATIOS = {
    "1:1 (Square)",
    "2:3 (Portrait Photo)",
    "3:2 (Photo)",
    "3:4 (Portrait Standard)",
    "4:3 (Standard)",
    "9:16 (Portrait Widescreen)",
    "16:9 (Widescreen)",
    "21:9 (Ultrawide)",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _storage_roots(roots: dict[str, Path] | None = None) -> dict[str, Path]:
    if roots is not None:
        required = {"input", "output", "temp"}
        if set(roots) != required:
            raise ValueError("项目存储根目录必须同时包含 input、output 和 temp。")
        return {key: Path(value).resolve() for key, value in roots.items()}

    import folder_paths

    return {
        "input": Path(folder_paths.get_input_directory()).resolve(),
        "output": Path(folder_paths.get_output_directory()).resolve(),
        "temp": Path(folder_paths.get_temp_directory()).resolve(),
    }


def normalize_project_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError) as exc:
        raise ValueError("项目 ID 无效；请在 MV 项目工作台中重新新建或选择项目。") from exc


def _clean_label(value: str, label: str, maximum: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError(f"{label}不能为空。")
    if len(text) > maximum:
        raise ValueError(f"{label}不能超过 {maximum} 个字符。")
    return text


def normalize_project_name(value: str) -> str:
    return _clean_label(value, "项目名称", 120)


def normalize_project_code(value: str) -> str:
    raw = _clean_label(value, "项目编号", 48)
    code = _INVALID_FILENAME.sub("_", raw).replace(" ", "-").strip(" ._-")
    if not code:
        raise ValueError("项目编号不能只包含文件名禁用字符。")
    if code.upper() in _WINDOWS_RESERVED:
        raise ValueError("该项目编号是 Windows 保留名，请更换。")
    return code


def normalize_project_brief(value: dict[str, Any] | None) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    limits = {
        "description": 20000,
        "overall_prompt": 60000,
        "negative_prompt": 30000,
    }
    result = {}
    for key, maximum in limits.items():
        text = str(source.get(key) or "").strip()
        if len(text) > maximum:
            raise ValueError(f"项目字段 {key} 不能超过 {maximum} 个字符。")
        result[key] = text
    return result


def normalize_planning_settings(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    h3_max_frames = int(source.get("h3_max_frames") or 226)
    if h3_max_frames not in _H3_WINDOW_PRESETS:
        h3_max_frames = 226
    maximum_seconds = h3_max_frames / 24.0
    target_seconds = max(1.0, min(
        maximum_seconds,
        float(source.get("target_shot_seconds") or 9.0),
    ))
    return {
        "target_shot_seconds": round(target_seconds, 3),
        "h3_max_frames": h3_max_frames,
    }


def normalize_generation_spec(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    aspect_ratio = str(source.get("aspect_ratio") or "16:9 (Widescreen)")
    if aspect_ratio not in _ASPECT_RATIOS:
        raise ValueError("影片长宽比不是 ResolutionSelector 支持的官方档位。")

    def megapixels(key: str, default: float) -> float:
        requested = float(source.get(key, default))
        if not math.isfinite(requested) or requested < 0.1 or requested > 16.0:
            raise ValueError(f"{key} 必须在 0.1–16.0 MP 之间。")
        return round(requested, 3)

    first_pass = megapixels("first_pass_megapixels", 0.4)
    second_pass = megapixels("second_pass_megapixels", 0.9)
    if second_pass < first_pass:
        raise ValueError("二采目标 MP 不能低于一采目标 MP。")
    return {
        "aspect_ratio": aspect_ratio,
        "first_pass_megapixels": first_pass,
        "second_pass_megapixels": second_pass,
        "pixel_alignment": 32,
    }


def _normalize_asset_source(value: dict[str, Any]) -> dict[str, Any]:
    filename = str(value.get("filename") or "").strip()
    if not filename or Path(filename).name != filename:
        raise ValueError("项目资产文件名无效。")
    subfolder_text = str(value.get("subfolder") or "").strip().replace("\\", "/")
    subfolder = Path(subfolder_text) if subfolder_text else Path()
    if subfolder.is_absolute() or ".." in subfolder.parts:
        raise ValueError("项目资产子目录无效。")
    media_type = str(value.get("media_type") or "image")
    if media_type not in _ASSET_MEDIA_TYPES:
        raise ValueError("项目资产只能声明为图片或视频。")
    return {
        "filename": filename,
        "subfolder": subfolder.as_posix() if subfolder != Path() else "",
        "type": "input",
        "media_type": media_type,
        "size": max(0, int(value.get("size") or 0)),
        "modified_ms": max(0, int(value.get("modified_ms") or 0)),
    }


def normalize_assets(value: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("项目资产必须是列表。")
    if len(value) > 2000:
        raise ValueError("单个项目最多登记 2000 个公共资产。")
    assets = []
    used_ids = set()
    visual_count = 0
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError("项目资产条目格式无效。")
        asset_id = str(raw.get("id") or f"asset_{index + 1:03d}").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", asset_id):
            raise ValueError(f"资产 ID {asset_id!r} 无效。")
        if asset_id in used_ids:
            raise ValueError(f"资产 ID {asset_id!r} 重复。")
        used_ids.add(asset_id)
        name = " ".join(str(raw.get("name") or "").strip().split())
        if not name or len(name) > 200:
            raise ValueError("资产名称不能为空且不能超过 200 个字符。")
        category = str(raw.get("category") or "character")
        if category not in _ASSET_CATEGORIES:
            raise ValueError("资产类型只能是角色、场景或道具。")
        declaration = str(raw.get("declaration") or "").strip()
        if len(declaration) > 30000:
            raise ValueError("单个资产声明不能超过 30000 个字符。")
        source = raw.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"资产 {name} 缺少 input 来源。")
        outline_visual = bool(raw.get("outline_visual", visual_count < 8))
        if outline_visual:
            visual_count += 1
            if visual_count > 8:
                raise ValueError("整片镜头纲领最多可选 8 个视觉资产。")
        assets.append({
            "id": asset_id,
            "name": name,
            "category": category,
            "declaration": declaration,
            "outline_visual": outline_visual,
            "source": _normalize_asset_source(source),
        })
    return assets


def normalize_lyrics(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    text = str(source.get("source_text") or "")
    if len(text) > 2_000_000:
        raise ValueError("歌词／字幕文本过大。")
    return {
        "source_text": text,
        "format": str(source.get("format") or "auto"),
        "updated_at": str(source.get("updated_at") or ""),
    }


def normalize_audio(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result = {}
    for role in ("master", "vocals"):
        item = source.get(role)
        if not isinstance(item, dict) or not item:
            continue
        normalized_item = {
            "fingerprint": str(item.get("fingerprint") or ""),
            "sample_rate": max(0, int(item.get("sample_rate") or 0)),
            "total_samples": max(0, int(item.get("total_samples") or 0)),
            "duration_seconds": max(0.0, float(item.get("duration_seconds") or 0.0)),
        }
        source_descriptor = item.get("source")
        if isinstance(source_descriptor, dict) and source_descriptor.get("path"):
            relative_path = str(source_descriptor.get("path") or "").replace("\\", "/").strip("/")
            if not relative_path.startswith("source/audio/") or ".." in Path(relative_path).parts:
                raise ValueError("项目音频源路径必须位于 source/audio 目录。")
            normalized_item["source"] = {
                "path": relative_path,
                "format": str(source_descriptor.get("format") or "media"),
                "original_name": str(source_descriptor.get("original_name") or ""),
            }
        result[role] = normalized_item
    alignment = source.get("alignment")
    if isinstance(alignment, dict):
        result["alignment"] = {
            "status": str(alignment.get("status") or "not_checked"),
            "difference_samples": int(alignment.get("difference_samples") or 0),
            "difference_seconds": float(alignment.get("difference_seconds") or 0.0),
            "basis": str(alignment.get("basis") or "duration_only"),
        }
    result["master_required"] = True
    return result


def project_layout(project_id: str, roots: dict[str, Path] | None = None) -> dict[str, Path]:
    project_id = normalize_project_id(project_id)
    storage_roots = _storage_roots(roots)
    layout = {}
    for storage_type, root in storage_roots.items():
        target = (root / PROJECTS_SUBFOLDER / project_id).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"项目 {storage_type} 路径越界。") from exc
        layout[storage_type] = target
    return layout


def _portable_storage(project_id: str) -> dict[str, str]:
    relative = f"{PROJECTS_SUBFOLDER}/{project_id}"
    return {"input": relative, "output": relative, "temp": relative}


def _ensure_layout(layout: dict[str, Path]) -> None:
    folders = {
        "input": ("source", "subtitles", "plans", "assets"),
        "output": ("generations", "previews", "delivery"),
        "temp": ("waveform", "beatmap", "thumbnails", "runtime"),
    }
    for storage_type, names in folders.items():
        root = layout[storage_type]
        root.mkdir(parents=True, exist_ok=True)
        for name in names:
            (root / name).mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 必须是 JSON 对象。")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _new_manifest(project_id: str, project_name: str, project_code: str) -> dict[str, Any]:
    now = _now()
    return {
        "schema": PROJECT_SCHEMA,
        "project_id": project_id,
        "project_code": project_code,
        "project_name": project_name,
        "status": "active",
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "storage": _portable_storage(project_id),
        "project_brief": {
            "description": "",
            "overall_prompt": "",
            "negative_prompt": "",
        },
        "planning_settings": normalize_planning_settings(None),
        "generation_spec": normalize_generation_spec(None),
        "skill_templates": normalize_skill_templates(None),
        "audio": {},
        "lyrics": normalize_lyrics(None),
        "plan": {},
        "assets": [],
    }


def _validate_manifest(manifest: dict[str, Any], expected_id: str) -> None:
    if manifest.get("schema") != PROJECT_SCHEMA:
        raise ValueError(f"不支持的项目架构：{manifest.get('schema') or '缺失 schema'}")
    actual_id = normalize_project_id(str(manifest.get("project_id") or ""))
    if actual_id != expected_id:
        raise ValueError("项目目录与 project.json 的 ID 不一致。")


def project_handle(manifest: dict[str, Any], layout: dict[str, Path]) -> dict[str, Any]:
    return {
        "schema": PROJECT_SCHEMA,
        "project_id": manifest["project_id"],
        "project_code": manifest["project_code"],
        "project_name": manifest["project_name"],
        "revision": int(manifest.get("revision", 1)),
        "manifest": deepcopy(manifest),
        "paths": {key: str(value) for key, value in layout.items()},
    }


def upsert_project(project_id: str, project_name: str, project_code: str,
                   roots: dict[str, Path] | None = None, *,
                   project_brief: dict[str, Any] | None = None,
                   planning_settings: dict[str, Any] | None = None,
                   generation_spec: dict[str, Any] | None = None,
                   skill_templates: dict[str, Any] | None = None,
                   assets: list[dict[str, Any]] | None = None,
                   lyrics: dict[str, Any] | None = None,
                   audio: dict[str, Any] | None = None) -> dict[str, Any]:
    project_id = normalize_project_id(project_id)
    project_name = normalize_project_name(project_name)
    project_code = normalize_project_code(project_code)
    layout = project_layout(project_id, roots)
    manifest_path = layout["input"] / "project.json"

    with _PROJECT_LOCK:
        _ensure_layout(layout)
        existed = manifest_path.is_file()
        changed = False
        if existed:
            manifest = _read_json(manifest_path)
            _validate_manifest(manifest, project_id)
            stored_code = normalize_project_code(str(manifest.get("project_code") or ""))
            if stored_code != project_code:
                raise ValueError(
                    f"历史项目编号 {stored_code} 已锁定；更新项目资料不能改变生成文件的归属。"
                )
            changed = manifest.get("project_name") != project_name
            manifest["project_name"] = project_name
            manifest["project_code"] = stored_code
            manifest["storage"] = _portable_storage(project_id)
        else:
            manifest = _new_manifest(project_id, project_name, project_code)
        if project_brief is not None:
            normalized = normalize_project_brief(project_brief)
            changed = changed or manifest.get("project_brief") != normalized
            manifest["project_brief"] = normalized
        else:
            manifest["project_brief"] = normalize_project_brief(manifest.get("project_brief"))
        if not existed and planning_settings is not None:
            normalized = normalize_planning_settings(planning_settings)
            changed = changed or manifest.get("planning_settings") != normalized
            manifest["planning_settings"] = normalized
        elif existed and isinstance(manifest.get("planning_settings"), dict):
            # Once a project exists, its automatic-cut controls are immutable
            # from the project-entry form. A destructive timeline replan is the
            # only path that may update them, via record_plan().
            manifest["planning_settings"] = normalize_planning_settings(
                manifest.get("planning_settings"))
        else:
            manifest["planning_settings"] = normalize_planning_settings(
                planning_settings)
        if generation_spec is not None:
            normalized = normalize_generation_spec(generation_spec)
            changed = changed or manifest.get("generation_spec") != normalized
            manifest["generation_spec"] = normalized
        else:
            manifest["generation_spec"] = normalize_generation_spec(
                manifest.get("generation_spec"))
        if skill_templates is not None:
            normalized = normalize_skill_templates(skill_templates)
            changed = changed or manifest.get("skill_templates") != normalized
            manifest["skill_templates"] = normalized
        else:
            manifest["skill_templates"] = normalize_skill_templates(
                manifest.get("skill_templates"))
        if assets is not None:
            normalized = normalize_assets(assets)
            changed = changed or manifest.get("assets") != normalized
            manifest["assets"] = normalized
        else:
            manifest["assets"] = normalize_assets(manifest.get("assets", []))
        if lyrics is not None:
            normalized = normalize_lyrics(lyrics)
            previous_lyrics = normalize_lyrics(manifest.get("lyrics"))
            lyrics_content_changed = (
                previous_lyrics["source_text"] != normalized["source_text"]
                or previous_lyrics["format"] != normalized["format"]
            )
            if lyrics_content_changed:
                normalized["updated_at"] = _now()
            elif not normalized.get("updated_at"):
                normalized["updated_at"] = previous_lyrics.get("updated_at", "")
            changed = changed or previous_lyrics != normalized
            manifest["lyrics"] = normalized
        else:
            manifest["lyrics"] = normalize_lyrics(manifest.get("lyrics"))
        if audio is not None:
            normalized = normalize_audio(audio)
            previous_audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
            if isinstance(previous_audio.get("planner"), dict):
                normalized["planner"] = deepcopy(previous_audio["planner"])
                normalized.update(previous_audio["planner"])
            changed = changed or previous_audio != normalized
            manifest["audio"] = normalized
        if changed and existed:
            manifest["revision"] = max(1, int(manifest.get("revision", 1))) + 1
            manifest["updated_at"] = _now()
        _write_json_atomic(manifest_path, manifest)
    return project_handle(manifest, layout)


def load_project(project_id: str, roots: dict[str, Path] | None = None) -> dict[str, Any]:
    project_id = normalize_project_id(project_id)
    layout = project_layout(project_id, roots)
    manifest_path = layout["input"] / "project.json"
    if not manifest_path.is_file():
        raise ValueError("找不到项目；请先在 MV 项目工作台中创建或打开。")
    manifest = _read_json(manifest_path)
    _validate_manifest(manifest, project_id)
    if not isinstance(manifest.get("planning_settings"), dict):
        inferred = None
        plan_meta = manifest.get("plan") if isinstance(manifest.get("plan"), dict) else {}
        relative_plan = str(plan_meta.get("path") or "").replace("\\", "/").strip("/")
        if relative_plan:
            candidate = (layout["input"] / relative_plan).resolve()
            try:
                candidate.relative_to(layout["input"].resolve())
                stored_plan = _read_json(candidate) if candidate.is_file() else {}
                inferred = stored_plan.get("settings")
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                inferred = None
        # Legacy projects inherit the exact automatic-planning controls from
        # their project-owned plan. This is an in-memory compatibility view;
        # it is persisted only when the user explicitly saves project data.
        manifest["planning_settings"] = normalize_planning_settings(inferred)
    _ensure_layout(layout)
    return project_handle(manifest, layout)


def load_project_plan(project_id: str, roots: dict[str, Path] | None = None) -> dict[str, Any] | None:
    """Load the plan owned by a project, without consulting workflow widgets.

    A workflow is only a view/controller for the project.  Historical project
    recovery must therefore read the project-owned plan instead of combining
    it with whichever template happened to be open in ComfyUI.
    """
    project = load_project(project_id, roots)
    manifest = project["manifest"]
    plan_meta = manifest.get("plan") if isinstance(manifest.get("plan"), dict) else {}
    relative_path = str(plan_meta.get("path") or "").replace("\\", "/").strip("/")
    if not relative_path:
        return None
    layout = {key: Path(value) for key, value in project["paths"].items()}
    plan_path = (layout["input"] / relative_path).resolve()
    try:
        plan_path.relative_to(layout["input"].resolve())
    except ValueError as exc:
        raise ValueError("项目计划路径越界。") from exc
    if not plan_path.is_file():
        raise ValueError("项目记录声明了 MV 计划，但计划文件不存在。")
    plan = _read_json(plan_path)
    plan_project = plan.get("project") if isinstance(plan.get("project"), dict) else {}
    recorded_id = str(plan_project.get("id") or "")
    if recorded_id and recorded_id != project["project_id"]:
        raise ValueError("项目计划归属与当前项目 ID 不一致。")
    return plan


def update_project_audio_role(project_id: str, role: str, metadata: dict[str, Any],
                              roots: dict[str, Path] | None = None) -> dict[str, Any]:
    """Register one project-owned audio source while preserving other roles."""
    if role not in {"master", "vocals"}:
        raise ValueError("项目音频角色只能是 master 或 vocals。")
    project_id = normalize_project_id(project_id)
    layout = project_layout(project_id, roots)
    manifest_path = layout["input"] / "project.json"
    with _PROJECT_LOCK:
        if not manifest_path.is_file():
            raise ValueError("找不到项目；请先创建项目再导入声音。")
        manifest = _read_json(manifest_path)
        _validate_manifest(manifest, project_id)
        raw_previous_audio = (
            manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
        )
        previous_audio = normalize_audio(raw_previous_audio)
        if isinstance(raw_previous_audio.get("planner"), dict):
            previous_audio["planner"] = deepcopy(raw_previous_audio["planner"])
            for key in ("fingerprint", "sample_rate", "total_samples", "timeline_frames",
                        "final_audio_policy"):
                if key in raw_previous_audio:
                    previous_audio[key] = deepcopy(raw_previous_audio[key])
        role_value = normalize_audio({role: metadata}).get(role)
        if not role_value:
            raise ValueError("项目音频信息不完整。")
        next_audio = deepcopy(previous_audio)
        next_audio[role] = role_value
        master = next_audio.get("master") if isinstance(next_audio.get("master"), dict) else {}
        vocals = next_audio.get("vocals") if isinstance(next_audio.get("vocals"), dict) else {}
        if master and vocals:
            difference_seconds = float(vocals.get("duration_seconds") or 0.0) - float(
                master.get("duration_seconds") or 0.0)
            next_audio["alignment"] = {
                "status": "duration_match" if abs(difference_seconds) <= 1.0 / 24.0 else "duration_mismatch",
                "difference_samples": round(difference_seconds * int(master.get("sample_rate") or 0)),
                "difference_seconds": difference_seconds,
                "basis": "duration_only",
            }
        elif role == "master":
            next_audio.pop("alignment", None)
        next_audio = normalize_audio(next_audio)
        if isinstance(raw_previous_audio.get("planner"), dict):
            next_audio["planner"] = deepcopy(raw_previous_audio["planner"])
            for key in ("fingerprint", "sample_rate", "total_samples", "timeline_frames",
                        "final_audio_policy"):
                if key in raw_previous_audio:
                    next_audio[key] = deepcopy(raw_previous_audio[key])
        if previous_audio != next_audio:
            manifest["audio"] = next_audio
            manifest["revision"] = max(1, int(manifest.get("revision", 1))) + 1
            manifest["updated_at"] = _now()
            _write_json_atomic(manifest_path, manifest)
        return project_handle(manifest, layout)


def list_projects(roots: dict[str, Path] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    input_root = _storage_roots(roots)["input"] / PROJECTS_SUBFOLDER
    if not input_root.is_dir():
        return []
    projects = []
    for manifest_path in input_root.glob("*/project.json"):
        try:
            manifest = _read_json(manifest_path)
            project_id = normalize_project_id(str(manifest.get("project_id") or ""))
            _validate_manifest(manifest, project_id)
            projects.append({
                "schema": PROJECT_SCHEMA,
                "project_id": project_id,
                "project_code": str(manifest.get("project_code") or ""),
                "project_name": str(manifest.get("project_name") or ""),
                "revision": max(1, int(manifest.get("revision", 1))),
                "status": str(manifest.get("status") or "active"),
                "created_at": str(manifest.get("created_at") or ""),
                "updated_at": str(manifest.get("updated_at") or ""),
                "storage": deepcopy(manifest.get("storage") or _portable_storage(project_id)),
                "has_plan": bool((manifest.get("plan") or {}).get("path")),
                "has_audio": bool(
                    ((manifest.get("audio") or {}).get("master") or {}).get("fingerprint")
                    or ((manifest.get("audio") or {}).get("planner") or {}).get("fingerprint")
                    or (manifest.get("audio") or {}).get("fingerprint")
                ),
                "has_vocals": bool(
                    ((manifest.get("audio") or {}).get("vocals") or {}).get("fingerprint")
                ),
                "asset_count": len(manifest.get("assets") or []),
            })
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    projects.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return projects[:max(1, min(500, int(limit)))]


def record_plan(project: dict[str, Any], plan: dict[str, Any],
                roots: dict[str, Path] | None = None) -> dict[str, Any]:
    project_id = normalize_project_id(str(project.get("project_id") or ""))
    layout = project_layout(project_id, roots)
    manifest_path = layout["input"] / "project.json"
    plan_path = layout["input"] / "plans" / "mv_plan.json"

    with _PROJECT_LOCK:
        if not manifest_path.is_file():
            raise ValueError("项目尚未初始化，不能写入 MV 计划。")
        manifest = _read_json(manifest_path)
        _validate_manifest(manifest, project_id)
        manifest["planning_settings"] = normalize_planning_settings(
            plan.get("settings")
            if isinstance(plan.get("settings"), dict)
            else manifest.get("planning_settings")
        )
        manifest["generation_spec"] = normalize_generation_spec(
            plan.get("generation_spec")
            if isinstance(plan.get("generation_spec"), dict)
            else manifest.get("generation_spec")
        )
        manifest["skill_templates"] = normalize_skill_templates(
            plan.get("skill_templates")
            if isinstance(plan.get("skill_templates"), dict)
            else manifest.get("skill_templates")
        )
        project_snapshot = deepcopy({
            "project_name": manifest.get("project_name"),
            "project_code": manifest.get("project_code"),
            "project_brief": manifest.get("project_brief"),
            "generation_spec": manifest.get("generation_spec"),
            "skill_templates": manifest.get("skill_templates"),
            "assets": manifest.get("assets"),
            "lyrics": manifest.get("lyrics"),
        })
        audio = plan.get("audio") if isinstance(plan.get("audio"), dict) else {}
        audio_state = deepcopy(manifest.get("audio") or {})
        audio_state["planner"] = {
            "fingerprint": str(audio.get("fingerprint") or ""),
            "sample_rate": int(audio.get("sample_rate") or 0),
            "total_samples": int(audio.get("total_samples") or 0),
            "timeline_frames": int(audio.get("timeline_frames") or 0),
            "final_audio_policy": "replace_with_original_master",
        }
        audio_state.update(audio_state["planner"])
        manifest["audio"] = audio_state
        project_meta = plan.get("project") if isinstance(plan.get("project"), dict) else {}
        if str(project_meta.get("name") or "").strip():
            manifest["project_name"] = normalize_project_name(project_meta["name"])
        # project_code is an immutable identity once project.json exists.  A
        # stale or hand-edited Planner payload must never rename the project
        # or redirect downstream generation filenames.
        project_meta["code"] = manifest["project_code"]
        project_meta["id"] = manifest["project_id"]
        manifest["project_brief"] = normalize_project_brief({
            "description": project_meta.get("description", ""),
            "overall_prompt": project_meta.get("overall_prompt", ""),
            "negative_prompt": project_meta.get("negative_prompt", ""),
        })
        manifest["assets"] = normalize_assets(plan.get("assets", []))
        lyrics_source = str(plan.get("subtitle_source", ""))
        lyrics_format = str((plan.get("subtitles") or {}).get("format", "auto"))
        previous_lyrics = normalize_lyrics(manifest.get("lyrics"))
        lyrics_changed = (
            previous_lyrics["source_text"] != lyrics_source
            or previous_lyrics["format"] != lyrics_format
        )
        manifest["lyrics"] = normalize_lyrics({
            "source_text": lyrics_source,
            "format": lyrics_format,
            "updated_at": _now() if lyrics_changed else previous_lyrics["updated_at"],
        })
        manifest["plan"] = {
            "schema": str(plan.get("schema") or ""),
            "path": "plans/mv_plan.json",
            "saved_at": _now(),
        }
        current_snapshot = {
            "project_name": manifest.get("project_name"),
            "project_code": manifest.get("project_code"),
            "project_brief": manifest.get("project_brief"),
            "generation_spec": manifest.get("generation_spec"),
            "skill_templates": manifest.get("skill_templates"),
            "assets": manifest.get("assets"),
            "lyrics": manifest.get("lyrics"),
        }
        if current_snapshot != project_snapshot:
            manifest["revision"] = max(1, int(manifest.get("revision", 1))) + 1
        manifest["updated_at"] = _now()
        _write_json_atomic(plan_path, plan)
        _write_json_atomic(manifest_path, manifest)
    # A cut/crossfade makes already-generated continuation clips usable as
    # independent edits even if this seam is reconnected in a later plan.
    try:
        from .generation_store import mark_independent_edit_allowed
    except ImportError:  # Standalone tests.
        from generation_store import mark_independent_edit_allowed
    mark_independent_edit_allowed(
        project_id, plan.get("shots") or [], plan.get("transitions") or [], roots)
    return project_handle(manifest, layout)

