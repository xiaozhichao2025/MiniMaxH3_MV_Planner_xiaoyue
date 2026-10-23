from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


SOURCE_SCHEMA = "xiaoyue.mvproject-sources/1.0"
DEFAULT_SKILL_TEMPLATES = {
    "visual_outline": "xiaoyue-mv-visual-outline",
    "h3_prompt": "xiaoyue-h3-mv-prompt",
}


def _skill_templates(value: Any) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    return {
        stage: str(source.get(stage) or default_id)
        for stage, default_id in DEFAULT_SKILL_TEMPLATES.items()
    }


def content_fingerprint(value: Any) -> str:
    """Return a deterministic fingerprint for project metadata."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _manifest_components(manifest: dict[str, Any]) -> dict[str, Any]:
    brief = manifest.get("project_brief")
    brief = brief if isinstance(brief, dict) else {}
    lyrics = manifest.get("lyrics")
    lyrics = lyrics if isinstance(lyrics, dict) else {}
    assets = manifest.get("assets")
    assets = assets if isinstance(assets, list) else []
    generation_spec = manifest.get("generation_spec")
    generation_spec = generation_spec if isinstance(generation_spec, dict) else {}
    return {
        "project_brief": {
            "project_name": str(manifest.get("project_name") or ""),
            "description": str(brief.get("description") or ""),
            "overall_prompt": str(brief.get("overall_prompt") or ""),
            "negative_prompt": str(brief.get("negative_prompt") or ""),
        },
        "assets": deepcopy(assets),
        "generation_spec": {
            "aspect_ratio": str(generation_spec.get("aspect_ratio") or "16:9 (Widescreen)"),
            "first_pass_megapixels": float(generation_spec.get("first_pass_megapixels") or 0.4),
            "second_pass_megapixels": float(generation_spec.get("second_pass_megapixels") or 0.9),
            "pixel_alignment": int(generation_spec.get("pixel_alignment") or 32),
        },
        "skill_templates": _skill_templates(manifest.get("skill_templates")),
        "lyrics": {
            "source_text": str(lyrics.get("source_text") or ""),
            "format": str(lyrics.get("format") or "auto"),
        },
    }


def _plan_components(plan: dict[str, Any]) -> dict[str, Any]:
    project = plan.get("project")
    project = project if isinstance(project, dict) else {}
    subtitles = plan.get("subtitles")
    subtitles = subtitles if isinstance(subtitles, dict) else {}
    assets = plan.get("assets")
    assets = assets if isinstance(assets, list) else []
    generation_spec = plan.get("generation_spec")
    generation_spec = generation_spec if isinstance(generation_spec, dict) else {}
    return {
        "project_brief": {
            "project_name": str(project.get("name") or ""),
            "description": str(project.get("description") or ""),
            "overall_prompt": str(project.get("overall_prompt") or ""),
            "negative_prompt": str(project.get("negative_prompt") or ""),
        },
        "assets": deepcopy(assets),
        "generation_spec": {
            "aspect_ratio": str(generation_spec.get("aspect_ratio") or "16:9 (Widescreen)"),
            "first_pass_megapixels": float(generation_spec.get("first_pass_megapixels") or 0.4),
            "second_pass_megapixels": float(generation_spec.get("second_pass_megapixels") or 0.9),
            "pixel_alignment": int(generation_spec.get("pixel_alignment") or 32),
        },
        "skill_templates": _skill_templates(plan.get("skill_templates")),
        "lyrics": {
            "source_text": str(plan.get("subtitle_source") or ""),
            "format": str(subtitles.get("format") or "auto"),
        },
    }


def _audio_fingerprints(manifest: dict[str, Any], master_fingerprint: str) -> tuple[str, str]:
    audio = manifest.get("audio")
    audio = audio if isinstance(audio, dict) else {}
    master = audio.get("master")
    master = master if isinstance(master, dict) else {}
    vocals = audio.get("vocals")
    vocals = vocals if isinstance(vocals, dict) else {}
    return (
        str(master_fingerprint or master.get("fingerprint") or ""),
        str(vocals.get("fingerprint") or ""),
    )


def _snapshot(project_id: str, project_code: str, master_fingerprint: str,
              vocals_fingerprint: str, components: dict[str, Any]) -> dict[str, str]:
    return {
        "schema": SOURCE_SCHEMA,
        "project_id": str(project_id or ""),
        "project_code": str(project_code or ""),
        "master_audio": str(master_fingerprint or ""),
        "vocals_audio": str(vocals_fingerprint or ""),
        "project_brief": content_fingerprint(components["project_brief"]),
        "assets": content_fingerprint(components["assets"]),
        "generation_spec": content_fingerprint(components["generation_spec"]),
        "skill_templates": content_fingerprint(components["skill_templates"]),
        "lyrics": content_fingerprint(components["lyrics"]),
    }


def _previous_snapshot(plan: dict[str, Any]) -> dict[str, str] | None:
    stored = plan.get("project_sources")
    if isinstance(stored, dict) and stored.get("project_id"):
        return {str(key): str(value or "") for key, value in stored.items()}
    project = plan.get("project")
    project = project if isinstance(project, dict) else {}
    if not project.get("id") and not plan.get("audio"):
        return None
    components = _plan_components(plan)
    audio = plan.get("audio")
    audio = audio if isinstance(audio, dict) else {}
    audio_sources = plan.get("audio_sources")
    audio_sources = audio_sources if isinstance(audio_sources, dict) else {}
    vocals = audio_sources.get("vocals")
    vocals = vocals if isinstance(vocals, dict) else {}
    return _snapshot(
        str(project.get("id") or ""),
        str(project.get("code") or ""),
        str(audio.get("fingerprint") or ""),
        str(vocals.get("fingerprint") or ""),
        components,
    )


def reconcile_project_state(
        manifest: dict[str, Any], existing: dict[str, Any] | None,
        master_fingerprint: str, *, subtitle_override: str | None = None,
) -> dict[str, Any]:
    """Merge project-entry updates with unsaved Planner edits.

    The manifest owns immutable identity. For editable metadata, whichever side
    changed since the previous Planner compile wins. If both sides changed,
    the project-entry manifest wins and the conflict is reported rather than
    silently writing stale Planner data back over the project.
    """
    original = deepcopy(existing) if isinstance(existing, dict) else {}
    previous = _previous_snapshot(original)
    project_id = str(manifest.get("project_id") or "")
    project_code = str(manifest.get("project_code") or "")
    previous_project_id = str((previous or {}).get("project_id") or "")
    switched = bool(previous_project_id and previous_project_id != project_id)
    working = {} if switched else original
    if switched:
        previous = None

    manifest_components = _manifest_components(manifest)
    plan_components = _plan_components(working) if working else deepcopy(manifest_components)
    component_sources: dict[str, str] = {}
    conflicts: list[str] = []
    selected: dict[str, Any] = {}

    for key in ("project_brief", "assets", "generation_spec", "skill_templates", "lyrics"):
        manifest_value = manifest_components[key]
        plan_value = plan_components[key]
        if key == "lyrics" and subtitle_override is not None:
            selected[key] = {
                "source_text": str(subtitle_override or ""),
                "format": "auto",
            }
            component_sources[key] = "connected_subtitle_input"
            continue
        if not working:
            selected[key] = deepcopy(manifest_value)
            component_sources[key] = "project_entry"
            continue
        base_hash = str((previous or {}).get(key) or "")
        manifest_hash = content_fingerprint(manifest_value)
        plan_hash = content_fingerprint(plan_value)
        if key in ("generation_spec", "skill_templates") and not base_hash:
            selected[key] = deepcopy(manifest_value)
            component_sources[key] = "project_entry"
            continue
        manifest_changed = bool(base_hash and manifest_hash != base_hash)
        plan_changed = bool(base_hash and plan_hash != base_hash)
        if manifest_changed:
            selected[key] = deepcopy(manifest_value)
            component_sources[key] = "project_entry"
            if plan_changed and plan_hash != manifest_hash:
                conflicts.append(key)
        else:
            selected[key] = deepcopy(plan_value)
            component_sources[key] = "planner_edit" if plan_changed else "unchanged"

    master_audio, vocals_audio = _audio_fingerprints(manifest, master_fingerprint)
    current = _snapshot(
        project_id, project_code, master_audio, vocals_audio, selected)

    changed: list[str] = []
    if switched:
        changed.append("project")
    elif previous:
        for key in ("master_audio", "vocals_audio", "lyrics", "project_brief", "assets", "generation_spec", "skill_templates"):
            if str(previous.get(key) or "") != str(current.get(key) or ""):
                changed.append(key)

    notices: list[str] = []
    pending: list[str] = []
    if switched:
        notices.append("已切换项目：旧项目的镜头、字幕和资产缓存未被带入当前项目。")
    else:
        if "master_audio" in changed:
            notices.append("检测到母带变化：已重新分析节拍与波形，并重建镜头初稿。")
        if "vocals_audio" in changed:
            notices.append("检测到干声新增、替换或移除：现有切点保留，干声分析与 ASR 对齐需要刷新。")
            pending.extend(["vocal_analysis", "subtitle_alignment"])
        if "lyrics" in changed:
            notices.append("歌词／字幕已重新载入并投影到现有镜头；不会擅自移动用户切点。")
        if "project_brief" in changed:
            notices.append("项目总控资料已同步；时间轴保留，后续提示词总纲需复核。")
            pending.append("prompt_outline_review")
        if "assets" in changed:
            notices.append("公共资产库已同步；时间轴保留，已失效的镜头资产绑定会被移除。")
            pending.append("shot_asset_binding_review")
        if "generation_spec" in changed:
            notices.append("影片画幅或双采 MP 已更新；时间轴保留，后续视频生成统一使用新规格。")
            pending.append("generation_spec_review")
        if "skill_templates" in changed:
            notices.append("阶段 Skill 模板已更新；时间轴保留，下一次生成使用新模板。")
    if conflicts:
        labels = {
            "project_brief": "项目总控",
            "assets": "公共资产",
            "generation_spec": "影片生成规格",
            "skill_templates": "阶段 Skill 模板",
            "lyrics": "歌词／字幕",
        }
        names = "、".join(labels.get(key, key) for key in conflicts)
        notices.append(f"检测到两处同时修改：{names} 采用项目工作台的最新版本，旧时间轴草稿未回写覆盖。")

    sync = {
        "changed_inputs": changed,
        "component_sources": component_sources,
        "conflicts": conflicts,
        "notices": notices,
        "pending_reviews": list(dict.fromkeys(pending)),
        "timeline_action": (
            "reset_for_project_switch" if switched else
            "replanned_for_master_change" if "master_audio" in changed else
            "preserved"
        ),
    }
    return {
        "existing": working,
        "components": selected,
        "project_sources": current,
        "project_sync": sync,
    }
