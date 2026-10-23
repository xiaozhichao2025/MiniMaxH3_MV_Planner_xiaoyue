from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from .skill_library import (
        load_skill_body,
        selected_skill_descriptor,
        skill_template_descriptor,
    )
except ImportError:  # Direct module loading in the standalone test suite.
    from skill_library import (
        load_skill_body,
        selected_skill_descriptor,
        skill_template_descriptor,
    )


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_FENCE_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
_ASSET_CATEGORIES = {"character", "scene", "prop"}
MAX_OUTLINE_VISUAL_ASSETS = 8
OUTLINE_SKILL_ID = "xiaoyue-mv-visual-outline"
OUTLINE_SKILL_PATH = (
    Path(__file__).resolve().parent / "skills" / OUTLINE_SKILL_ID / "SKILL.cn.md"
)
OUTLINE_SKILL_META_PATH = OUTLINE_SKILL_PATH.with_name("meta.yaml")

_INCOMING_CONNECTION_DEFINITIONS = {
    "song_start": "这是整首歌的第一镜，没有上一镜头需要承接。",
    "hard_cut": "本镜头与上一镜头是独立生成的硬切关系，不继承上一镜尾部状态。",
    "long_take": (
        "本镜头必须从上一镜头的交付尾部无缝续写，保持人物姿态、构图、"
        "运镜方向、光线和动作相位连续。"
    ),
    "crossfade": "本镜头与上一镜头将在后期叠化；需要可融合的构图和运动，但不是生成续写。",
}

_OUTGOING_CONNECTION_DEFINITIONS = {
    "song_end": "这是整首歌的最后一镜，没有下一镜头需要承接。",
    "hard_cut": "下一镜头与本镜头是独立生成的硬切关系。",
    "long_take": (
        "下一镜头必须接着本镜头的交付尾部继续生成并保持无缝衔接；"
        "这表示下一镜续写本镜，不表示本镜头在这里结束长镜头。"
    ),
    "crossfade": "下一镜头与本镜头将在后期叠化；本镜尾部应提供可融合状态，但不是生成续写。",
}

_CONTINUATION_ROLE_DEFINITIONS = {
    "independent": "本镜头不在连续续写链中。",
    "continuation_source": (
        "本镜头是续写链起始段：自身不接上一段，但下一镜必须从本镜交付尾部无缝续写。"
    ),
    "continuation_middle": (
        "本镜头是续写链中间段：开头必须接住上一镜尾部，结尾还必须为下一镜保留可续写状态。"
    ),
    "continuation_terminal": (
        "本镜头是续写链末段：开头必须接住上一镜尾部；只有此处才是该连续链的结束。"
    ),
}


def _connection_type(transition: Any, fallback: str) -> str:
    value = str(transition.get("type") if isinstance(transition, dict) else fallback)
    return value if value in {"hard_cut", "long_take", "crossfade", fallback} else fallback


def _continuation_role(incoming: str, outgoing: str) -> str:
    has_incoming = incoming == "long_take"
    has_outgoing = outgoing == "long_take"
    if has_incoming and has_outgoing:
        return "continuation_middle"
    if has_outgoing:
        return "continuation_source"
    if has_incoming:
        return "continuation_terminal"
    return "independent"


def _without_frontmatter(text: str) -> str:
    source = str(text or "").lstrip("\ufeff")
    if not source.startswith("---"):
        return source.strip()
    parts = source.split("---", 2)
    return parts[2].strip() if len(parts) == 3 else source.strip()


def _load_outline_skill_body(skill_id: str = OUTLINE_SKILL_ID) -> str:
    return load_skill_body("visual_outline", skill_id)


def _simple_meta_value(text: str, key: str, default: str) -> str:
    match = re.search(
        rf"(?m)^{re.escape(key)}:\s*[\"']?([^\r\n\"']+)[\"']?\s*$",
        text,
    )
    return match.group(1).strip() if match else default


def outline_skill_descriptor(skill_id: str = OUTLINE_SKILL_ID) -> dict[str, Any]:
    return skill_template_descriptor("visual_outline", skill_id)


def _sample_frames(values: Any, limit: int) -> list[int]:
    frames = [int(value) for value in values or [] if isinstance(value, (int, float))]
    if len(frames) <= limit:
        return frames
    return [frames[round(index * (len(frames) - 1) / (limit - 1))] for index in range(limit)]


def _protection_frames(values: Any, limit: int = 160) -> list[int]:
    frames = []
    for value in values or []:
        if isinstance(value, dict):
            frame = value.get("frame", value.get("absolute_frame"))
        else:
            frame = value
        if isinstance(frame, (int, float)):
            frames.append(int(frame))
    return _sample_frames(frames, limit)


def selected_outline_assets(plan: dict[str, Any]) -> list[dict[str, Any]]:
    assets = plan.get("assets") if isinstance(plan, dict) else []
    assets = [asset for asset in assets or [] if isinstance(asset, dict)]
    has_explicit_selection = any("outline_visual" in asset for asset in assets)
    selected = [
        asset for index, asset in enumerate(assets)
        if bool(asset.get("outline_visual", index < MAX_OUTLINE_VISUAL_ASSETS))
    ] if has_explicit_selection else assets[:MAX_OUTLINE_VISUAL_ASSETS]
    if len(selected) > MAX_OUTLINE_VISUAL_ASSETS:
        raise ValueError(
            f"整片镜头纲领最多可读取 {MAX_OUTLINE_VISUAL_ASSETS} 个视觉资产。"
        )
    return selected


def build_outline_input(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("MV 纲领请求缺少有效计划。")
    shots = plan.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("MV 计划没有可规划的镜头。")
    transitions = plan.get("transitions") if isinstance(plan.get("transitions"), list) else []
    project = plan.get("project") if isinstance(plan.get("project"), dict) else {}
    generation_spec = (
        plan.get("generation_spec")
        if isinstance(plan.get("generation_spec"), dict) else {}
    )
    audio = plan.get("audio") if isinstance(plan.get("audio"), dict) else {}
    analysis = plan.get("analysis") if isinstance(plan.get("analysis"), dict) else {}
    assets = plan.get("assets") if isinstance(plan.get("assets"), list) else []
    shot_rows = []
    seen_ids = set()
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            raise ValueError("MV 计划包含无效镜头记录。")
        shot_id = str(shot.get("id") or "").strip()
        if not shot_id or shot_id in seen_ids:
            raise ValueError("MV 计划的 shot_id 缺失或重复。")
        seen_ids.add(shot_id)
        subtitle_context = shot.get("subtitle_context") \
            if isinstance(shot.get("subtitle_context"), dict) else {}
        incoming = transitions[index - 1] if index > 0 and index - 1 < len(transitions) else None
        outgoing = transitions[index] if index < len(transitions) else None
        incoming_type = _connection_type(incoming, "song_start")
        outgoing_type = _connection_type(outgoing, "song_end")
        continuation_role = _continuation_role(incoming_type, outgoing_type)
        shot_rows.append({
            "shot_id": shot_id,
            "editorial_label": str(
                shot.get("editorial_label") or shot.get("scene_code") or f"SC{index + 1:02d}"
            ),
            "start_frame": int(shot.get("start_frame") or 0),
            "end_frame": int(shot.get("end_frame") or 0),
            "keep_frames": int(shot.get("keep_frames") or 0),
            "incoming_connection": incoming_type,
            "incoming_connection_definition": _INCOMING_CONNECTION_DEFINITIONS[incoming_type],
            "outgoing_connection": outgoing_type,
            "outgoing_connection_definition": _OUTGOING_CONNECTION_DEFINITIONS[outgoing_type],
            "continuation_role": continuation_role,
            "continuation_role_definition": _CONTINUATION_ROLE_DEFINITIONS[continuation_role],
            "dialogue_or_lyrics": str(subtitle_context.get("ai_dialogue_text") or "").strip(),
            "current_singing_performance": shot.get("singing_performance")
            if isinstance(shot.get("singing_performance"), bool) else None,
        })
    selected_assets = selected_outline_assets(plan)
    visual_labels = {
        id(asset): f"视觉资产 {chr(65 + index)}"
        for index, asset in enumerate(selected_assets)
    }
    existing_assets = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        existing_assets.append({
            "name": str(asset.get("name") or "未命名资产"),
            "category": str(asset.get("category") or "prop"),
            "declaration": str(asset.get("declaration") or ""),
            "visual_evidence": visual_labels.get(id(asset), "仅文字声明"),
        })
    visual_assets = []
    for index, asset in enumerate(selected_assets):
        source = asset.get("source") if isinstance(asset.get("source"), dict) else {}
        visual_assets.append({
            "label": f"视觉资产 {chr(65 + index)}",
            "name": str(asset.get("name") or "未命名资产"),
            "category": str(asset.get("category") or "prop"),
            "declaration": str(asset.get("declaration") or ""),
            "media_type": str(source.get("media_type") or "image"),
        })
    beat_frames = analysis.get("beat_frames") or []
    protection = analysis.get("protection_candidates") or []
    return {
        "project": {
            "description": str(project.get("description") or ""),
            "overall_prompt": str(project.get("overall_prompt") or ""),
            "negative_prompt": str(project.get("negative_prompt") or ""),
        },
        "generation_spec": {
            "aspect_ratio": str(generation_spec.get("aspect_ratio") or "16:9 (Widescreen)"),
            "first_pass_megapixels": float(generation_spec.get("first_pass_megapixels") or 0.4),
            "second_pass_megapixels": float(generation_spec.get("second_pass_megapixels") or 0.9),
            "pixel_alignment": int(generation_spec.get("pixel_alignment") or 32),
        },
        "music_timeline": {
            "fps": int((plan.get("h3") or {}).get("fps") or 24),
            "total_frames": int(audio.get("timeline_frames") or 0),
            "duration_seconds": float(audio.get("duration_seconds") or 0.0),
            "bpm": float(analysis.get("bpm") or 0.0),
            "beat_count": len(beat_frames),
            "beat_frames_sample": _sample_frames(beat_frames, 240),
            "protection_point_count": len(protection),
            "protection_frames_sample": _protection_frames(protection),
        },
        "transition_semantics": {
            "boundary_direction": (
                "transitions[i] 描述 shot[i+1] 如何从 shot[i] 接入；"
                "它不是 shot[i] 的结束方式。"
            ),
            "hard_cut": "后镜与前镜独立生成，通过剪辑直接切换。",
            "long_take": (
                "后镜必须从前镜交付尾部继续生成并保持无缝衔接；"
                "long_take 出边表示下一镜续写当前镜，不表示当前镜在此结束长镜头。"
            ),
            "crossfade": "前后镜独立生成并在后期叠化，不等于生成续写。",
        },
        "shots": shot_rows,
        "available_project_assets": existing_assets,
        "visual_assets": visual_assets,
    }


def build_outline_prompts(plan: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
    outline_input = build_outline_input(plan)
    skill = selected_skill_descriptor(plan, "visual_outline")
    system_prompt = _load_outline_skill_body(skill["id"])
    schema_instruction = {
        "overview": "全片情绪曲线、叙事推进、视觉母题与连续性策略",
        "shots": [{
            "shot_id": "必须原样使用输入中的 shot_id",
            "creative_outline": "150–300 字左右的完整自然语言分镜描述；融合机位、景别、环境细节与连续性、人物、动作、运镜、光影、氛围、情绪、节奏作用及前后承接；不写字段列表",
            "singing_performance": "逐镜布尔判断：结合本镜唱词、在歌曲中的段落与节奏起伏、镜头叙事功能和画面人物表演，决定是否让可见角色实际演唱。有唱词默认 true，设计可见角色随原曲人声对口型；只有本镜明确因 MV 演绎节奏需要空镜、剧情、舞蹈或其他非演唱表演时才改为 false；无唱词时为 false。不要只凭 BPM 或背景人声虚构歌手，并让 creative_outline 与此值一致",
            "asset_needs": [{
                "category": "character|scene|prop",
                "description": "建议准备的素材条件，不写参考编号",
            }],
        }],
    }
    user_prompt = (
        "请按下面的输出结构生成整片镜头纲领：\n"
        + json.dumps(schema_instruction, ensure_ascii=False, indent=2)
        + "\n\n本次 MV 输入：\n"
        + json.dumps(outline_input, ensure_ascii=False, indent=2)
    )
    visual_source_state = [{
        "filename": str((asset.get("source") or {}).get("filename") or ""),
        "subfolder": str((asset.get("source") or {}).get("subfolder") or ""),
        "size": int((asset.get("source") or {}).get("size") or 0),
        "modified_ms": int((asset.get("source") or {}).get("modified_ms") or 0),
    } for asset in selected_outline_assets(plan)]
    source_json = json.dumps(
        {"outline": outline_input, "visual_source_state": visual_source_state},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(source_json.encode("utf-8")).hexdigest()
    summary = {
        "skill": skill,
        "shot_count": len(outline_input["shots"]),
        "duration_seconds": outline_input["music_timeline"]["duration_seconds"],
        "bpm": outline_input["music_timeline"]["bpm"],
        "lyric_shot_count": sum(
            1 for shot in outline_input["shots"] if shot["dialogue_or_lyrics"]
        ),
        "asset_count": len(outline_input["available_project_assets"]),
        "visual_asset_count": len(outline_input["visual_assets"]),
        "includes": [
            "项目整体描述／总提示词／禁用项",
            "母带总帧、时长、BPM、节拍与保护点摘要",
            "全部镜头的绝对帧位、连接类型和校正唱词",
            "项目资产声明与用户选定的视觉资产（仅用于理解，不自动绑定）",
        ],
        "excludes": [
            "H3 专业字段与 H3 Skill",
            "项目资产序号和镜头参考编号",
            "最终单镜头 H3 提示词",
        ],
    }
    return system_prompt, user_prompt, summary, fingerprint


def _decode_json_object(text: str) -> dict[str, Any]:
    cleaned = _THINK_BLOCK.sub("", str(text or "")).strip()
    fenced = _FENCE_BLOCK.search(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(cleaned):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("推理模型没有返回可解析的 JSON 对象。")


def parse_outline_response(text: str, expected_shot_ids: list[str]) -> dict[str, Any]:
    value = _decode_json_object(text)
    overview = str(value.get("overview") or "").strip()
    if not overview:
        raise ValueError("推理结果缺少非空 overview。")
    rows = value.get("shots")
    if not isinstance(rows, list):
        raise ValueError("推理结果缺少 shots 数组。")
    expected = [str(shot_id) for shot_id in expected_shot_ids]
    expected_set = set(expected)
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("推理结果包含无效镜头项。")
        shot_id = str(row.get("shot_id") or "").strip()
        if shot_id not in expected_set:
            raise ValueError(f"推理结果包含未知 shot_id：{shot_id or '空值'}。")
        if shot_id in by_id:
            raise ValueError(f"推理结果重复了 shot_id：{shot_id}。")
        creative_outline = str(row.get("creative_outline") or "").strip()
        if not creative_outline:
            raise ValueError(f"{shot_id} 缺少非空 creative_outline。")
        singing = row.get("singing_performance")
        if not isinstance(singing, bool):
            singing = None  # Older/partial model output uses the lyric default.
        needs = []
        raw_needs = row.get("asset_needs") or []
        if not isinstance(raw_needs, list):
            raise ValueError(f"{shot_id} 的 asset_needs 必须是数组。")
        for need in raw_needs:
            if not isinstance(need, dict):
                continue
            category = str(need.get("category") or "prop").strip().lower()
            description = str(need.get("description") or "").strip()
            if category not in _ASSET_CATEGORIES:
                category = "prop"
            if description:
                needs.append({"category": category, "description": description})
        by_id[shot_id] = {
            "shot_id": shot_id,
            "creative_outline": creative_outline,
            "singing_performance": singing,
            "asset_needs": needs,
        }
    missing = [shot_id for shot_id in expected if shot_id not in by_id]
    if missing:
        raise ValueError("推理结果没有覆盖全部镜头：" + "、".join(missing))
    return {"overview": overview, "shots": [by_id[shot_id] for shot_id in expected]}

