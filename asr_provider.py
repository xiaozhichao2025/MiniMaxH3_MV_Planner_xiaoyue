from __future__ import annotations

from typing import Any


ASR_PROVIDER_SCHEMA = "xiaoyue.asr-provider/1.0"
ASR_PROVIDER_TYPE = "XIAOYUE_ASR_PROVIDER"
ASR_PROVIDER_KINDS = {"local_whisper"}


def _bounded_int(value: Any, minimum: int, maximum: int, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是整数。") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{field} 必须在 {minimum}–{maximum} 之间。")
    return number


def build_local_whisper_provider(
    model_type: str,
    max_sentence_length: int,
    unload_model: bool,
) -> dict[str, Any]:
    model_type = str(model_type or "whisper-large-v3")
    if model_type != "whisper-large-v3":
        raise ValueError("本地 Whisper Provider 当前只支持 whisper-large-v3。")
    return {
        "schema": ASR_PROVIDER_SCHEMA,
        "kind": "local_whisper",
        "label": "本地 Whisper Provider",
        "settings": {
            "model_type": model_type,
            "max_sentence_length": _bounded_int(
                max_sentence_length, 1, 500, "max_sentence_length"),
            "unload_model": bool(unload_model),
        },
        "capabilities": {
            "timestamp_basis": "model_segments",
            "accepts_audio_from_planner": True,
            "loads_local_model": True,
        },
    }


def validate_provider(provider: Any) -> dict[str, Any] | None:
    if provider is None:
        return None
    if not isinstance(provider, dict):
        raise ValueError("ASR Provider 配置无效。")
    if provider.get("schema") != ASR_PROVIDER_SCHEMA:
        raise ValueError("ASR Provider 版本不受支持。")
    if provider.get("kind") not in ASR_PROVIDER_KINDS:
        raise ValueError("ASR Provider 类型不受支持。")
    if not isinstance(provider.get("settings"), dict):
        raise ValueError("ASR Provider 缺少 settings。")
    return provider


def public_provider_summary(provider: Any) -> dict[str, Any] | None:
    """Return plan-safe provider metadata without credentials or endpoints."""
    provider = validate_provider(provider)
    if provider is None:
        return None
    settings = provider["settings"]
    safe_keys = ("model_type", "max_sentence_length", "unload_model")
    return {
        "schema": provider["schema"],
        "kind": provider["kind"],
        "label": provider.get("label", ""),
        "settings": {key: settings.get(key) for key in safe_keys},
        "capabilities": dict(provider.get("capabilities") or {}),
    }
