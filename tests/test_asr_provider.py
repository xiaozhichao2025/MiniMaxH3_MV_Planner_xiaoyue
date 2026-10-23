from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyue_asr_provider", ROOT / "asr_provider.py")
providers = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = providers
SPEC.loader.exec_module(providers)


class AsrProviderTests(unittest.TestCase):
    def test_plan_summary_contains_only_local_whisper_settings(self):
        provider = providers.build_local_whisper_provider(
            "whisper-large-v3", 32, True)
        summary = providers.public_provider_summary(provider)
        self.assertNotIn("api_key", summary["settings"])
        self.assertNotIn("endpoint", summary["settings"])
        self.assertEqual(summary["kind"], "local_whisper")

    def test_local_whisper_provider_matches_easy_media_route_options(self):
        provider = providers.build_local_whisper_provider(
            "whisper-large-v3", 32, True)
        self.assertEqual(provider["kind"], "local_whisper")
        self.assertEqual(provider["settings"], {
            "model_type": "whisper-large-v3",
            "max_sentence_length": 32,
            "unload_model": True,
        })
        self.assertTrue(provider["capabilities"]["loads_local_model"])

    def test_provider_ranges_are_validated(self):
        with self.assertRaisesRegex(ValueError, "max_sentence_length"):
            providers.build_local_whisper_provider(
                "whisper-large-v3", 0, True)

    def test_private_provider_kind_is_not_part_of_public_package(self):
        self.assertEqual(providers.ASR_PROVIDER_KINDS, {"local_whisper"})
        with self.assertRaisesRegex(ValueError, "类型不受支持"):
            providers.validate_provider({
                "schema": providers.ASR_PROVIDER_SCHEMA,
                "kind": "lan",
                "settings": {},
            })


if __name__ == "__main__":
    unittest.main()
