from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent / "skills"
SKILL_STAGE_DEFAULTS = {
    "visual_outline": "xiaoyue-mv-visual-outline",
    "h3_prompt": "xiaoyue-h3-mv-prompt",
}


def _simple_meta_value(text: str, key: str, default: str = "") -> str:
    match = re.search(
        rf"(?m)^{re.escape(key)}:\s*[\"']?([^\r\n\"']+)[\"']?\s*$",
        text,
    )
    return match.group(1).strip() if match else default


def skill_template_folder() -> str:
    """Show the MV plugin folder used for defaults and user-added Skills."""
    return str(SKILL_ROOT.resolve())


def _skill_descriptor(file_path: Path, root: Path, skill_id: str,
                      source: str) -> dict[str, Any] | None:
    try:
        body = file_path.read_text(encoding="utf-8-sig")
        meta_path = file_path.parent / "meta.yaml"
        metadata = meta_path.read_text(encoding="utf-8-sig") \
            if meta_path.is_file() else ""
    except (OSError, UnicodeError):
        return None
    frontmatter = body.split("---", 2)[1] \
        if body.startswith("---") and body.count("---") >= 2 else ""
    display_name = (
        _simple_meta_value(metadata, "display-name-zh")
        or _simple_meta_value(frontmatter, "name")
        or file_path.stem
    )
    return {
        "id": skill_id,
        "display_name": display_name,
        "version": _simple_meta_value(metadata, "version", "unknown"),
        "entrypoint": file_path.name,
        "skill_path": file_path.relative_to(root).as_posix(),
        "output_language": _simple_meta_value(
            metadata, "output-language",
            "zh-CN" if file_path.name == "SKILL.cn.md" else "en",
        ),
        "summary": _simple_meta_value(metadata, "summary-cn")
        or _simple_meta_value(frontmatter, "description"),
        "declared_stage": _simple_meta_value(metadata, "xiaoyue-stage"),
        "source": source,
        "embedded": source == "bundled",
    }


def _scan_skills(root: Path, source: str) -> dict[str, dict[str, Any]]:
    catalogue: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return catalogue
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != "references")
        names = sorted(
            name for name in filenames
            if Path(name).suffix.lower() in {".md", ".txt"}
        )
        relative_dir = Path(current).relative_to(root).as_posix()
        legacy_dir = relative_dir != "." and bool(
            re.fullmatch(r"[A-Za-z0-9._-]+", relative_dir))
        primary = "SKILL.cn.md" if "SKILL.cn.md" in names else \
            "SKILL.md" if "SKILL.md" in names else None
        if legacy_dir and primary:
            descriptor = _skill_descriptor(
                Path(current) / primary, root, relative_dir, source)
            if descriptor:
                catalogue[relative_dir] = descriptor
        for name in names:
            if legacy_dir and primary and name in {"SKILL.md", "SKILL.cn.md"}:
                continue
            path = Path(current) / name
            skill_id = path.relative_to(root).as_posix()
            descriptor = _skill_descriptor(path, root, skill_id, source)
            if descriptor:
                catalogue[skill_id] = descriptor
    return catalogue


def skill_template_library() -> dict[str, list[dict[str, Any]]]:
    """Discover only this plugin's Skills on each request, without TE lookup."""
    catalogue = _scan_skills(SKILL_ROOT, "local")
    for default_id in SKILL_STAGE_DEFAULTS.values():
        if default_id in catalogue:
            catalogue[default_id] = {
                **catalogue[default_id], "source": "bundled", "embedded": True,
            }
    library = {stage: [] for stage in SKILL_STAGE_DEFAULTS}
    for stage, default_id in SKILL_STAGE_DEFAULTS.items():
        values = [
            {**descriptor, "stage": stage,
             "recommended": descriptor["id"] == default_id
             or descriptor["declared_stage"] == stage}
            for descriptor in catalogue.values()
        ]
        values.sort(key=lambda item: (
            item["id"] != default_id,
            not item["recommended"],
            item["display_name"].casefold(),
            item["id"].casefold(),
        ))
        library[stage] = values
    return library


def normalize_skill_templates(value: Any) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    library = skill_template_library()
    normalized: dict[str, str] = {}
    for stage, default_id in SKILL_STAGE_DEFAULTS.items():
        available = {item["id"] for item in library.get(stage, [])}
        requested = str(source.get(stage) or default_id)
        if requested not in available:
            raise ValueError(
                f"阶段 {stage} 选择的 Skill {requested} 已不存在；"
                "请刷新模板列表并重新选择。"
            )
        normalized[stage] = requested
    return normalized


def skill_template_descriptor(stage: str, skill_id: str | None = None) -> dict[str, Any]:
    if stage not in SKILL_STAGE_DEFAULTS:
        raise ValueError(f"未知 Skill 阶段：{stage}。")
    requested = str(skill_id or SKILL_STAGE_DEFAULTS[stage])
    for descriptor in skill_template_library().get(stage, []):
        if descriptor["id"] == requested:
            return descriptor
    raise ValueError(f"阶段 {stage} 找不到 Skill 模板：{requested}。")


def selected_skill_descriptor(plan: Any, stage: str) -> dict[str, Any]:
    selected = normalize_skill_templates(
        plan.get("skill_templates") if isinstance(plan, dict) else None)
    return skill_template_descriptor(stage, selected[stage])


def load_skill_body(stage: str, skill_id: str | None = None) -> str:
    descriptor = skill_template_descriptor(stage, skill_id)
    root = SKILL_ROOT
    path = (root / descriptor["skill_path"]).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError("Skill 路径超出模板目录。")
    try:
        text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    except OSError as exc:
        raise RuntimeError(f"无法读取 Skill 模板：{path}") from exc
    if text.startswith("---"):
        parts = text.split("---", 2)
        text = parts[2] if len(parts) == 3 else text
    text = text.strip()
    if not text:
        raise RuntimeError(f"Skill 模板为空：{descriptor['id']}。")
    return text
