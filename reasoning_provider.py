from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from functools import wraps
from typing import Any


REASONING_PROVIDER_TYPE = "XIAOYUE_MV_REASONING_PROVIDER"
_MAX_REGISTERED_PROVIDERS = 8
_PROVIDERS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_REGISTRY_LOCK = threading.RLock()
_INFERENCE_LOCK = threading.Lock()


def _model_identity(model: Any) -> tuple[str, str]:
    settings = getattr(model, "settings", {})
    if not isinstance(settings, dict):
        settings = {}
    family = str(settings.get("family") or "QwenTE").strip()
    model_name = str(settings.get("model") or type(model).__name__).strip()
    return family, model_name


def _model_supports_vision(model: Any) -> bool:
    settings = getattr(model, "settings", {})
    if not isinstance(settings, dict):
        return False
    mmproj = str(settings.get("mmproj") or "").strip()
    return bool(mmproj and mmproj.casefold() not in {"none", "null", "无"}
                and not mmproj.startswith("（"))


def register_qwen_te_provider(
        model: Any,
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int,
        repeat_penalty: float,
        seed: int,
        unload_after_generate: bool,
) -> dict[str, Any]:
    if model is None:
        raise ValueError("QwenTE 推理 Provider 没有收到 qwen_model。")
    family, model_name = _model_identity(model)
    config = {
        "max_tokens": max(512, min(32768, int(max_tokens))),
        "temperature": max(0.0, min(2.0, float(temperature))),
        "top_p": max(0.0, min(1.0, float(top_p))),
        "top_k": max(0, min(200, int(top_k))),
        "repeat_penalty": max(0.5, min(2.0, float(repeat_penalty))),
        "seed": max(0, int(seed)),
        "unload_after_generate": bool(unload_after_generate),
    }
    with _REGISTRY_LOCK:
        reusable_token = next((
            token for token, entry in _PROVIDERS.items()
            if entry.get("model") is model and entry.get("config") == config
        ), None)
        token = reusable_token or uuid.uuid4().hex
        _PROVIDERS[token] = {
            "model": model,
            "config": config,
            "family": family,
            "model_name": model_name,
            "visual_capable": _model_supports_vision(model),
        }
        _PROVIDERS.move_to_end(token)
        while len(_PROVIDERS) > _MAX_REGISTERED_PROVIDERS:
            _PROVIDERS.popitem(last=False)
    return {
        "kind": "qwen_te",
        "token": token,
        "label": (
            "QwenTE 本地视觉推理"
            if _model_supports_vision(model)
            else "QwenTE 本地文本推理"
        ),
        "family": family,
        "model_name": model_name,
        "visual_capable": _model_supports_vision(model),
        "config": dict(config),
    }


def public_reasoning_provider_summary(provider: Any) -> dict[str, Any]:
    if not isinstance(provider, dict) or provider.get("kind") != "qwen_te":
        return {"connected": False, "kind": "none", "label": "未连接推理 Provider"}
    config = provider.get("config") if isinstance(provider.get("config"), dict) else {}
    return {
        "connected": True,
        "kind": "qwen_te",
        "label": str(provider.get("label") or "QwenTE 本地文本推理"),
        "family": str(provider.get("family") or "QwenTE"),
        "model_name": str(provider.get("model_name") or "未知模型"),
        "visual_capable": bool(provider.get("visual_capable", False)),
        "max_tokens": int(config.get("max_tokens") or 0),
        "unload_after_generate": bool(config.get("unload_after_generate", False)),
    }


def client_reasoning_provider_descriptor(provider: Any) -> dict[str, Any]:
    summary = public_reasoning_provider_summary(provider)
    token = str(provider.get("token") or "") if isinstance(provider, dict) else ""
    with _REGISTRY_LOCK:
        available = bool(token and token in _PROVIDERS)
    return {**summary, "available": available, "token": token if available else ""}


def _qwen_infer_class():
    try:
        import nodes as comfy_nodes
    except Exception as exc:  # pragma: no cover - only available inside ComfyUI
        raise RuntimeError("当前环境无法读取 ComfyUI 节点注册表。") from exc
    infer_class = getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {}).get("QwenTE_ImageInfer")
    if infer_class is None:
        raise RuntimeError(
            "没有找到 QwenTE_ImageInfer。请安装或更新 comfyUI-llama-TE，重启 ComfyUI 后重跑工作流。"
        )
    return infer_class


def _completion_diagnostics(response: Any) -> dict[str, Any]:
    """Keep only non-content completion metadata; never retain prompts or replies."""
    if not isinstance(response, dict):
        return {}
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    usage = response.get("usage")
    result: dict[str, Any] = {}
    if isinstance(choice, dict):
        reason = choice.get("finish_reason")
        if reason is not None:
            result["finish_reason"] = str(reason)
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                result[key] = value
    return result


def run_qwen_te_provider(
        token: str,
        system_prompt: str,
        user_prompt: str,
        *,
        images: list[Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    token = str(token or "").strip()
    with _REGISTRY_LOCK:
        entry = _PROVIDERS.get(token)
        if entry is not None:
            _PROVIDERS.move_to_end(token)
    if entry is None:
        raise RuntimeError("推理 Provider 已失效；请重新运行包含 QwenTE 适配器的工作流。")
    if not _INFERENCE_LOCK.acquire(blocking=False):
        raise RuntimeError("已有 MV 推理任务正在运行，请等待完成后再试。")
    try:
        config = entry["config"]
        visual_inputs = list(images or [])
        if len(visual_inputs) > 8:
            raise ValueError("QwenTE 单次最多支持 8 个视觉资产。")
        if visual_inputs and not entry.get("visual_capable"):
            raise RuntimeError(
                "当前 QwenTE 模型没有加载视觉投影 mmproj。"
                "请在模型加载器中选择与主模型匹配的 mmproj，重新运行工作流后再生成。"
            )
        infer = _qwen_infer_class()()
        infer_kwargs = {
            "qwen模型": entry["model"],
            "输入模式": "图片" if visual_inputs else "文本",
            "提示词": str(user_prompt or ""),
            "系统提示词": str(system_prompt or ""),
            "最多帧数": 24,
            "最大边长": 1024,
            "最大生成token": config["max_tokens"],
            "温度": config["temperature"],
            "top_p": config["top_p"],
            "top_k": config["top_k"],
            "重复惩罚": config["repeat_penalty"],
            "频率惩罚": 0.0,
            "存在惩罚": 0.0,
            "seed": config["seed"],
            "输出think块": False,
            "生成后自动卸载模型": config["unload_after_generate"],
        }
        for index, image in enumerate(visual_inputs):
            infer_kwargs["图片" if index == 0 else f"图片{index + 1}"] = image
        # QwenTE returns text only, discarding llama.cpp's finish_reason and
        # usage. Observe this one call without changing its arguments/result.
        completion: dict[str, Any] = {}
        llm = getattr(entry["model"], "llm", None)
        original_completion = getattr(llm, "create_chat_completion", None)
        instance_attributes = getattr(llm, "__dict__", None)
        had_instance_override = (
            isinstance(instance_attributes, dict)
            and "create_chat_completion" in instance_attributes
        )
        observing = False
        if callable(original_completion):
            @wraps(original_completion)
            def observed_completion(*args: Any, **kwargs: Any) -> Any:
                response = original_completion(*args, **kwargs)
                completion.update(_completion_diagnostics(response))
                return response

            try:
                llm.create_chat_completion = observed_completion
                observing = True
            except (AttributeError, TypeError):
                pass  # Unsupported llama wrapper: inference still works.
        try:
            result = infer.run(**infer_kwargs)
        finally:
            if observing:
                if had_instance_override:
                    llm.create_chat_completion = original_completion
                else:
                    try:
                        del llm.create_chat_completion
                    except (AttributeError, TypeError):
                        llm.create_chat_completion = original_completion
        text = result[0] if isinstance(result, (tuple, list)) and result else result
        text = str(text or "").strip()
        if not text:
            raise RuntimeError("QwenTE 返回了空内容；现有内容没有改变。")
        return text, {
            "family": entry["family"],
            "model_name": entry["model_name"],
            "label": "QwenTE 本地视觉推理" if visual_inputs else "QwenTE 本地文本推理",
            "mode": "vision" if visual_inputs else "text",
            "visual_asset_count": len(visual_inputs),
            "max_tokens": config["max_tokens"],
            **completion,
        }
    finally:
        _INFERENCE_LOCK.release()

