from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyue_reasoning_provider", ROOT / "reasoning_provider.py")
provider = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provider
SPEC.loader.exec_module(provider)


class FakeModel:
    settings = {"family": "Qwen3.8-VL", "model": "test.gguf"}


class FakeVisualModel:
    settings = {
        "family": "Qwen3.8-VL",
        "model": "vision.gguf",
        "mmproj": "mmproj-vision.gguf",
    }


class FakeInfer:
    last_kwargs = None

    def run(self, **kwargs):
        FakeInfer.last_kwargs = kwargs
        return ("{\"overview\":\"ok\"}",)


class FakeLlm:
    def create_chat_completion(self, **_kwargs):
        return {
            "choices": [{
                "message": {"content": "private reply"},
                "finish_reason": "length",
            }],
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 4096,
                "total_tokens": 5296,
            },
        }


class FakeInferWithCompletion:
    def run(self, **kwargs):
        kwargs["qwen模型"].llm.create_chat_completion(messages=[])
        return ("partial H3 text",)


class ReasoningProviderTests(unittest.TestCase):
    def test_public_summary_never_contains_token_or_model_object(self):
        registered = provider.register_qwen_te_provider(
            FakeModel(), max_tokens=4096, temperature=0.3, top_p=0.8,
            top_k=20, repeat_penalty=1.05, seed=7,
            unload_after_generate=False,
        )
        public = provider.public_reasoning_provider_summary(registered)
        client = provider.client_reasoning_provider_descriptor(registered)
        self.assertNotIn("token", public)
        self.assertNotIn("model", public)
        self.assertTrue(client["available"])
        self.assertTrue(client["token"])

    def test_runner_delegates_to_qwen_te_text_mode(self):
        registered = provider.register_qwen_te_provider(
            FakeModel(), max_tokens=4096, temperature=0.3, top_p=0.8,
            top_k=20, repeat_penalty=1.05, seed=7,
            unload_after_generate=True,
        )
        previous = sys.modules.get("nodes")
        sys.modules["nodes"] = types.SimpleNamespace(
            NODE_CLASS_MAPPINGS={"QwenTE_ImageInfer": FakeInfer},
        )
        try:
            text, model = provider.run_qwen_te_provider(
                registered["token"], "system", "user")
        finally:
            if previous is None:
                sys.modules.pop("nodes", None)
            else:
                sys.modules["nodes"] = previous
        self.assertEqual(text, '{"overview":"ok"}')
        self.assertEqual(model["model_name"], "test.gguf")
        self.assertEqual(FakeInfer.last_kwargs["输入模式"], "文本")
        self.assertEqual(FakeInfer.last_kwargs["系统提示词"], "system")
        self.assertTrue(FakeInfer.last_kwargs["生成后自动卸载模型"])

    def test_runner_passes_selected_assets_to_real_image_inputs(self):
        registered = provider.register_qwen_te_provider(
            FakeVisualModel(), max_tokens=4096, temperature=0.3, top_p=0.8,
            top_k=20, repeat_penalty=1.05, seed=7,
            unload_after_generate=False,
        )
        previous = sys.modules.get("nodes")
        sys.modules["nodes"] = types.SimpleNamespace(
            NODE_CLASS_MAPPINGS={"QwenTE_ImageInfer": FakeInfer},
        )
        first, second = object(), object()
        try:
            _text, model = provider.run_qwen_te_provider(
                registered["token"], "system", "user", images=[first, second])
        finally:
            if previous is None:
                sys.modules.pop("nodes", None)
            else:
                sys.modules["nodes"] = previous
        self.assertEqual(FakeInfer.last_kwargs["输入模式"], "图片")
        self.assertIs(FakeInfer.last_kwargs["图片"], first)
        self.assertIs(FakeInfer.last_kwargs["图片2"], second)
        self.assertTrue(registered["visual_capable"])
        self.assertEqual(model["mode"], "vision")
        self.assertEqual(model["visual_asset_count"], 2)

    def test_text_only_model_rejects_visual_assets_without_silent_fallback(self):
        registered = provider.register_qwen_te_provider(
            FakeModel(), max_tokens=4096, temperature=0.3, top_p=0.8,
            top_k=20, repeat_penalty=1.05, seed=7,
            unload_after_generate=False,
        )
        with self.assertRaisesRegex(RuntimeError, "mmproj"):
            provider.run_qwen_te_provider(
                registered["token"], "system", "user", images=[object()])

    def test_runner_captures_only_completion_metadata_and_restores_model(self):
        model = FakeModel()
        model.llm = FakeLlm()
        original = model.llm.create_chat_completion
        registered = provider.register_qwen_te_provider(
            model, max_tokens=4096, temperature=0.3, top_p=0.8,
            top_k=20, repeat_penalty=1.05, seed=7,
            unload_after_generate=False,
        )
        previous = sys.modules.get("nodes")
        sys.modules["nodes"] = types.SimpleNamespace(
            NODE_CLASS_MAPPINGS={"QwenTE_ImageInfer": FakeInferWithCompletion},
        )
        try:
            text, metadata = provider.run_qwen_te_provider(
                registered["token"], "system", "user")
        finally:
            if previous is None:
                sys.modules.pop("nodes", None)
            else:
                sys.modules["nodes"] = previous
        self.assertEqual(text, "partial H3 text")
        self.assertEqual(metadata["finish_reason"], "length")
        self.assertEqual(metadata["completion_tokens"], 4096)
        self.assertEqual(metadata["max_tokens"], 4096)
        self.assertNotIn("private reply", str(metadata))
        self.assertIs(model.llm.create_chat_completion.__func__, original.__func__)
        self.assertNotIn("create_chat_completion", vars(model.llm))


if __name__ == "__main__":
    unittest.main()

