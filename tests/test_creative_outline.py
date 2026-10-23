from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyue_creative_outline", ROOT / "creative_outline.py")
outline = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = outline
SPEC.loader.exec_module(outline)


def sample_plan():
    return {
        "project": {
            "description": "从孤独走向释放。",
            "overall_prompt": "统一冷蓝色电影摄影。",
            "negative_prompt": "不要画面文字。",
        },
        "generation_spec": {
            "aspect_ratio": "9:16 (Portrait Widescreen)",
            "first_pass_megapixels": 0.5,
            "second_pass_megapixels": 1.0,
            "pixel_alignment": 32,
        },
        "audio": {"timeline_frames": 480, "duration_seconds": 20.0},
        "h3": {"fps": 24},
        "analysis": {
            "bpm": 120,
            "beat_frames": list(range(12, 480, 12)),
            "protection_candidates": [{"frame": 160}, {"frame": 320}],
        },
        "assets": [{
            "id": "internal-only",
            "name": "女主",
            "category": "character",
            "declaration": "黑发蓝色外套",
            "outline_visual": True,
            "source": {
                "filename": "lead.png", "subfolder": "mv", "media_type": "image",
                "size": 1024, "modified_ms": 99,
            },
        }],
        "shots": [{
            "id": "shot_001",
            "editorial_label": "01",
            "start_frame": 0,
            "end_frame": 240,
            "keep_frames": 240,
            "subtitle_context": {"ai_dialogue_text": "风吹过旧站台"},
        }, {
            "id": "shot_002",
            "editorial_label": "02",
            "start_frame": 240,
            "end_frame": 480,
            "keep_frames": 240,
            "subtitle_context": {"ai_dialogue_text": "我向光走去"},
        }],
        "transitions": [{"type": "hard_cut"}],
    }


class CreativeOutlineTests(unittest.TestCase):
    def test_build_input_contains_timeline_and_never_exposes_asset_id(self):
        system_prompt, user_prompt, summary, fingerprint = outline.build_outline_prompts(
            sample_plan())
        self.assertIn("不写 MiniMax H3", system_prompt)
        self.assertIn("shot_001", user_prompt)
        self.assertIn("风吹过旧站台", user_prompt)
        self.assertIn("singing_performance", user_prompt)
        self.assertIn("歌曲中的段落与节奏起伏", user_prompt)
        self.assertIn("有唱词默认 true", user_prompt)
        self.assertIn("MV 演绎节奏需要空镜", user_prompt)
        self.assertIn("视觉资产 A", user_prompt)
        self.assertIn("9:16 (Portrait Widescreen)", user_prompt)
        self.assertIn('"second_pass_megapixels": 1.0', user_prompt)
        self.assertIn("完整自然语言", system_prompt)
        self.assertNotIn("internal-only", user_prompt)
        self.assertEqual(summary["shot_count"], 2)
        self.assertEqual(summary["lyric_shot_count"], 2)
        self.assertEqual(summary["visual_asset_count"], 1)
        self.assertEqual(summary["skill"]["id"], "xiaoyue-mv-visual-outline")
        self.assertEqual(summary["skill"]["version"], "0.1.1")
        self.assertIn("总分镜导演", system_prompt)
        self.assertNotIn("name: xiaoyue-mv-visual-outline", system_prompt)
        self.assertEqual(len(fingerprint), 64)

    def test_outline_skill_is_a_real_packaged_chinese_system_prompt(self):
        descriptor = outline.outline_skill_descriptor()
        self.assertEqual(descriptor["display_name"], "小月 MV 视觉镜头总纲")
        self.assertEqual(descriptor["entrypoint"], "SKILL.cn.md")
        self.assertTrue(outline.OUTLINE_SKILL_PATH.is_file())
        body = outline._load_outline_skill_body()
        self.assertIn("单镜头纲领", body)
        self.assertIn("整片连续性", body)
        self.assertIn("下一镜续写本镜", body)
        self.assertIn("绝不表示“本镜头在这里结束长镜头”", body)
        self.assertIn("逐镜决定画面是演唱", body)
        self.assertIn("creative_outline` 必须与该值一致", body)

    def test_long_take_direction_and_chain_roles_are_explicit(self):
        plan = sample_plan()
        plan["shots"] = [{
            "id": f"shot_{index + 1:03d}",
            "start_frame": index * 120,
            "end_frame": (index + 1) * 120,
            "keep_frames": 120,
        } for index in range(4)]
        plan["transitions"] = [
            {"type": "long_take"},
            {"type": "long_take"},
            {"type": "hard_cut"},
        ]

        payload = outline.build_outline_input(plan)
        rows = payload["shots"]
        self.assertEqual(rows[0]["continuation_role"], "continuation_source")
        self.assertEqual(rows[1]["continuation_role"], "continuation_middle")
        self.assertEqual(rows[2]["continuation_role"], "continuation_terminal")
        self.assertEqual(rows[3]["continuation_role"], "independent")
        self.assertIn("下一镜头必须接着本镜头", rows[0]["outgoing_connection_definition"])
        self.assertIn("不表示本镜头在这里结束长镜头", rows[0]["outgoing_connection_definition"])
        self.assertIn("从上一镜头的交付尾部无缝续写", rows[1]["incoming_connection_definition"])
        self.assertIn("只有此处才是该连续链的结束", rows[2]["continuation_role_definition"])
        self.assertIn("不是 shot[i] 的结束方式", payload["transition_semantics"]["boundary_direction"])

    def test_visual_asset_selection_is_explicit_and_limited_to_eight(self):
        plan = sample_plan()
        plan["assets"] = [{
            "id": f"asset-{index}",
            "name": f"资产{index}",
            "category": "prop",
            "declaration": "",
            "outline_visual": True,
            "source": {"filename": f"{index}.png", "media_type": "image"},
        } for index in range(9)]
        with self.assertRaisesRegex(ValueError, "最多可读取 8"):
            outline.build_outline_prompts(plan)
        plan["assets"][-1]["outline_visual"] = False
        payload = outline.build_outline_input(plan)
        self.assertEqual(len(payload["visual_assets"]), 8)
        self.assertEqual(payload["visual_assets"][-1]["label"], "视觉资产 H")

    def test_parse_fenced_json_preserves_expected_order_and_sanitizes_needs(self):
        raw = """```json
        {"overview":"完整总纲","shots":[
          {"shot_id":"shot_002","creative_outline":"第二镜","singing_performance":false,"asset_needs":[{"category":"bad","description":"雨伞"}]},
          {"shot_id":"shot_001","creative_outline":"第一镜","singing_performance":true,"asset_needs":[{"category":"character","description":"女主"}]}
        ]}
        ```"""
        parsed = outline.parse_outline_response(raw, ["shot_001", "shot_002"])
        self.assertEqual([row["shot_id"] for row in parsed["shots"]], ["shot_001", "shot_002"])
        self.assertEqual(parsed["shots"][1]["asset_needs"][0]["category"], "prop")
        self.assertTrue(parsed["shots"][0]["singing_performance"])
        self.assertFalse(parsed["shots"][1]["singing_performance"])

    def test_missing_shot_rejects_entire_result(self):
        raw = json.dumps({
            "overview": "不完整",
            "shots": [{"shot_id": "shot_001", "creative_outline": "只有一镜"}],
        }, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, "没有覆盖全部镜头"):
            outline.parse_outline_response(raw, ["shot_001", "shot_002"])


if __name__ == "__main__":
    unittest.main()

