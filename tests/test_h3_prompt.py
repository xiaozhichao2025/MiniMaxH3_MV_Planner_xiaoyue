from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyue_h3_prompt", ROOT / "h3_prompt.py")
h3_prompt = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = h3_prompt
SPEC.loader.exec_module(h3_prompt)


def sample_plan():
    return {
        "project": {
            "description": "一支从孤独走向释放的都市 MV。",
            "overall_prompt": "冷蓝电影摄影，身份和服装连续。",
            "negative_prompt": "不要画面文字。",
        },
        "generation_spec": {
            "aspect_ratio": "16:9 (Widescreen)",
            "first_pass_megapixels": 0.4,
            "second_pass_megapixels": 0.9,
        },
        "creative_plan": {"overview": "雨夜车站到晨光天台的情绪推进。"},
        "h3": {"fps": 24},
        "analysis": {"bpm": 96},
        "assets": [{
            "id": "internal-character-id",
            "name": "女主",
            "category": "character",
            "declaration": "短黑发，深蓝长外套。",
            "source": {
                "filename": "lead.png", "subfolder": "mv",
                "media_type": "image", "size": 100, "modified_ms": 7,
            },
        }],
        "shots": [{
            "id": "shot_001",
            "editorial_label": "01",
            "start_frame": 0,
            "end_frame": 226,
            "keep_frames": 216,
            "creative_outline": "中景跟拍女主穿过雨夜站台，句尾停在灯下回望。",
            "prompt": "旧提示词",
            "connection_requirement": "独立硬切镜头",
            "subtitle_context": {"ai_dialogue_text": "风吹过旧站台"},
            "asset_bindings": [{
                "asset_id": "internal-character-id", "order": 1,
                "usage": "女主为前景主体，保持外套与短发。",
            }],
        }, {
            "id": "shot_002",
            "editorial_label": "02",
            "start_frame": 226,
            "end_frame": 452,
            "keep_frames": 226,
            "creative_outline": "晨光中的空镜缓慢推向天台边缘。",
            "prompt": "",
            "subtitle_context": {"ai_dialogue_text": ""},
            "asset_bindings": [],
        }],
        "transitions": [{"type": "hard_cut"}],
        "generation_windows": [{
            "id": "window_0001", "shot_id": "shot_001", "shot_window_index": 1,
            "continuation_reason": "independent", "source_start_frame": 0,
            "source_end_frame": 226, "raw_frames": 226, "raw_seconds": 9.416667,
            "context_frames": 0, "audio_handle_before_frames": 0,
            "audio_handle_after_frames": 10, "trim_leading_frames": 0,
            "deliver_frames": 216, "delivery_timeline_range": [0, 216],
            "core_timeline_range": [0, 216], "tail_padding_frames": 10,
            "h3_formula_n": 13,
        }, {
            "id": "window_0002", "shot_id": "shot_002", "shot_window_index": 1,
            "continuation_reason": "independent", "source_start_frame": 216,
            "source_end_frame": 442, "raw_frames": 226, "raw_seconds": 9.416667,
            "context_frames": 0, "audio_handle_before_frames": 10,
            "audio_handle_after_frames": 0, "trim_leading_frames": 10,
            "deliver_frames": 216, "delivery_timeline_range": [226, 442],
            "core_timeline_range": [226, 442], "tail_padding_frames": 0,
            "h3_formula_n": 13,
        }],
    }


class H3PromptTests(unittest.TestCase):
    def test_bound_asset_selects_ref_mode_and_uses_shot_local_order(self):
        system_prompt, user_prompt, summary, fingerprint = \
            h3_prompt.build_h3_prompt_prompts(sample_plan(), "shot_001")
        self.assertEqual(summary["mode"], "Ref2VA")
        self.assertEqual(summary["asset_count"], 1)
        self.assertIn("subject_definitions", system_prompt)
        self.assertIn("本镜头资产 1", user_prompt)
        self.assertIn("女主为前景主体", user_prompt)
        self.assertNotIn("existing_prompt_to_rewrite", user_prompt)
        self.assertNotIn("旧提示词", user_prompt)
        self.assertNotIn("internal-character-id", user_prompt)
        self.assertEqual(len(fingerprint), 64)

    def test_independent_shot_names_original_song_audio_attachment(self):
        payload = h3_prompt.build_h3_prompt_input(sample_plan(), "shot_002")
        self.assertEqual(payload["generation_mode"], "Ref2VA")
        self.assertEqual(payload["shot_local_assets"], [])
        self.assertEqual(payload["original_song_audio_label"], "<Audio 1>")
        self.assertIn("原 MV 歌曲声音文件", payload["shot_audio_slots"][0]["declaration"])
        self.assertNotIn("binding_state", payload["shot_audio_slots"][0])
        self.assertFalse(payload["shot"]["singing_performance"])
        self.assertEqual(
            payload["previous_shot_context"]["creative_outline"],
            "中景跟拍女主穿过雨夜站台，句尾停在灯下回望。",
        )
        self.assertEqual(
            payload["previous_shot_context"]["usage"],
            "narrative_context_only",
        )
        self.assertEqual(
            payload["previous_shot_context"]["generated_h3_prompt"], "")
        system_prompt, _user, summary, _fingerprint = \
            h3_prompt.build_h3_prompt_prompts(sample_plan(), "shot_002")
        self.assertIn("detailed_description", system_prompt)
        self.assertEqual(summary["mode"], "Ref2VA")
        self.assertIn("原 MV 歌曲声音文件", _user)
        self.assertNotIn("待绑定", _user)
        self.assertNotIn("尚未送入 Qwen", _user)

    def test_lyrics_default_to_singing_and_user_override_wins(self):
        plan = sample_plan()
        payload = h3_prompt.build_h3_prompt_input(plan, "shot_001")
        self.assertTrue(payload["shot"]["singing_performance"])
        self.assertIn("<Audio 1>", payload["original_song_audio_label"])
        _system, user, summary, _fingerprint = \
            h3_prompt.build_h3_prompt_prompts(plan, "shot_001")
        self.assertIn("嘴型、唇形开合", user)
        self.assertEqual(summary["singing_lyrics"], "风吹过旧站台")
        plan["shots"][0]["singing_performance"] = False
        payload = h3_prompt.build_h3_prompt_input(plan, "shot_001")
        self.assertFalse(payload["shot"]["singing_performance"])
        _system, user, summary, _fingerprint = \
            h3_prompt.build_h3_prompt_prompts(plan, "shot_001")
        self.assertIn("不做对口型", user)
        self.assertEqual(summary["singing_lyrics"], "")

    def test_complete_editable_lyric_track_overrides_stale_context(self):
        plan = sample_plan()
        shot = plan["shots"][0]
        shot["subtitle_track"] = {
            "leading_text": "泪光",
            "text": "在人海里远远遇见 我祝你们地久天长",
            "trailing_text": "透过窗",
        }
        shot["subtitle_context"] = {"ai_dialogue_text": "泪 光"}
        expected = "泪光 在人海里远远遇见 我祝你们地久天长 透过窗"
        payload = h3_prompt.build_h3_prompt_input(plan, "shot_001")
        self.assertEqual(payload["shot"]["dialogue_or_lyrics"], expected)
        _system, user, summary, _fingerprint = \
            h3_prompt.build_h3_prompt_prompts(plan, "shot_001")
        self.assertIn(f"必须实际唱出原词“{expected}”", user)
        self.assertEqual(summary["singing_lyrics"], expected)
        shot["subtitle_track"] = {
            "leading_text": "", "text": "", "trailing_text": ""}
        self.assertEqual(
            h3_prompt.build_h3_prompt_input(plan, "shot_001")
            ["shot"]["dialogue_or_lyrics"], "")

    def test_current_lip_sync_decision_is_explicit_and_precedes_plan_json(self):
        plan = sample_plan()
        plan["shots"][0]["singing_performance"] = True
        _system, user, summary, _fingerprint = \
            h3_prompt.build_h3_prompt_prompts(plan, "shot_001")
        self.assertTrue(user.startswith("本镜口型决定：已开启。"))
        self.assertLess(user.index("本镜口型决定"), user.index("当前镜头输入"))
        self.assertIn("必须实际唱出原词“风吹过旧站台”", user)
        self.assertIn("附件 <Audio 1>", user)
        self.assertEqual(summary["lyrics_chars"], len("风吹过旧站台"))
        plan["shots"][0]["singing_performance"] = False
        _system, user, summary, _fingerprint = \
            h3_prompt.build_h3_prompt_prompts(plan, "shot_001")
        self.assertTrue(user.startswith("本镜口型决定：已关闭。"))
        self.assertIn("画面人物只做剧情或节奏表演，不唱歌、不做对口型", user)
        self.assertFalse(summary["singing_performance"])

    def test_enabled_lip_sync_without_transcribed_lyrics_does_not_invent_words(self):
        plan = sample_plan()
        plan["shots"][1]["singing_performance"] = True
        payload = h3_prompt.build_h3_prompt_input(plan, "shot_002")
        self.assertIn("若有可听人声", payload["shot"]["singing_rule"])
        _system, user, summary, _fingerprint = \
            h3_prompt.build_h3_prompt_prompts(plan, "shot_002")
        self.assertIn("本镜口型决定：已开启", user)
        self.assertIn("不要编造歌词", user)
        self.assertEqual(summary["lyrics_chars"], 0)

    def test_user_declared_audio_precedes_reserved_song_slot(self):
        plan = sample_plan()
        plan["shots"][0]["audio_references"] = [
            {"declaration": "用户上传的环境声，仅参考质感。"},
            {"declaration": "用户上传的旁白，保持音色。"},
        ]
        payload = h3_prompt.build_h3_prompt_input(plan, "shot_001")
        self.assertEqual([slot["label"] for slot in payload["shot_audio_slots"]],
                         ["<Audio 1>", "<Audio 2>", "<Audio 3>"])
        self.assertEqual(payload["original_song_audio_label"], "<Audio 3>")

    def test_singing_prompt_requires_voice_lyrics_and_mouth_action(self):
        prompt = """subject_definitions:
<Audio 1> 是预留给本镜原曲的音频。
summary:
[audio reference] 本镜参考 <Audio 1>。
retention_analysis:
<Audio 1>: reference - 节奏和唱词来自原曲。
detailed_description:
[Shot 1] 女主在站台唱歌，嘴型随 <Audio 1> 中原曲变化。
overall_soundscape:
雨声持续。
non_diegetic_music:
原曲节奏持续。"""
        with self.assertRaisesRegex(ValueError, "缺少 \\(S1\\)"):
            h3_prompt.parse_h3_prompt_response(
                prompt, "shot_001", "Ref2VA",
                original_song_audio_label="<Audio 1>",
                singing_lyrics="风吹过旧站台")
        prompt = prompt.replace("女主在站台唱歌", "女主 (S1) 在站台唱歌，唱出 <d>[Chinese] 风吹过旧站台</d>")
        self.assertEqual(h3_prompt.parse_h3_prompt_response(
            prompt, "shot_001", "Ref2VA",
            original_song_audio_label="<Audio 1>",
            singing_lyrics="风吹过旧站台"), prompt)

    def test_reviewable_draft_can_be_kept_after_quality_retry(self):
        prompt = """subject_definitions:
<Subject 1> 是站台上的女主。
summary:
[reference generation] 女主在站台上回望。
retention_analysis:
<Subject 1>: fully_preserved - 保持人物身份。
detailed_description:
[Shot 1] 女主在站台回望，镜头缓慢推进。
overall_soundscape:
雨声持续。
non_diegetic_music:
N/A"""
        raw = json.dumps({"shot_id": "shot_001", "mode": "Ref2VA",
                          "prompt": prompt}, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, "Audio 1"):
            h3_prompt.parse_h3_prompt_response(
                raw, "shot_001", "Ref2VA",
                original_song_audio_label="<Audio 1>")
        self.assertEqual(
            h3_prompt.extract_h3_prompt_response(raw, "shot_001", "Ref2VA"),
            prompt,
        )

    def test_long_take_receives_previous_outline_and_generated_prompt(self):
        plan = sample_plan()
        plan["transitions"][0]["type"] = "long_take"
        payload = h3_prompt.build_h3_prompt_input(plan, "shot_002")
        previous = payload["previous_shot_context"]
        self.assertEqual(payload["generation_mode"], "Ref2VA")
        self.assertEqual(previous["usage"], "seamless_visual_continuation")
        self.assertEqual(previous["generated_h3_prompt"], "旧提示词")
        self.assertIn("next generated continuation", previous["continuity_instruction"])
        self.assertIn("join seamlessly", previous["continuity_instruction"])

    def test_long_take_requires_nonempty_previous_h3_prompt(self):
        plan = sample_plan()
        plan["transitions"][0]["type"] = "long_take"
        plan["shots"][0]["prompt"] = ""
        with self.assertRaisesRegex(ValueError, "请先成功生成上一镜"):
            h3_prompt.build_h3_prompt_input(plan, "shot_002")

    def test_missing_outline_or_generation_window_is_rejected_before_inference(self):
        plan = sample_plan()
        plan["shots"][0]["creative_outline"] = ""
        with self.assertRaisesRegex(ValueError, "尚未填写镜头纲领"):
            h3_prompt.build_h3_prompt_input(plan, "shot_001")
        plan = sample_plan()
        plan["generation_windows"] = []
        with self.assertRaisesRegex(ValueError, "generation_window"):
            h3_prompt.build_h3_prompt_input(plan, "shot_001")

    def test_parses_valid_ref_json_and_preserves_prompt_text(self):
        prompt = """subject_definitions:
<Subject 1> 是 <Picture 1> 中的短发女子。
summary:
[reference generation] <Subject 1> 穿过雨夜站台。
retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - 保持人物身份与服装。
detailed_description:
[Shot 1] <Subject 1> 在雨中缓慢前行，镜头小幅度跟移。
overall_soundscape:
雨声和脚步声持续贴近主体。
non_diegetic_music:
最终剪辑使用原始母带。"""
        raw = json.dumps({
            "shot_id": "shot_001", "mode": "Ref2VA", "prompt": prompt,
        }, ensure_ascii=False)
        self.assertEqual(
            h3_prompt.parse_h3_prompt_response(raw, "shot_001", "Ref2VA"),
            prompt,
        )

    def test_rejects_wrong_mode_missing_fields_and_typography(self):
        valid_base = """integrated_multimodal_description:
[Shot 1] 黎明天台保持安静，镜头缓慢推向边缘。
overall_soundscape:
轻柔风声持续流动。
non_diegetic_music:
最终剪辑使用原始母带。"""
        wrong_mode = json.dumps({
            "shot_id": "shot_002", "mode": "Ref2VA", "prompt": valid_base,
        })
        with self.assertRaisesRegex(ValueError, "错误的生成模式"):
            h3_prompt.parse_h3_prompt_response(wrong_mode, "shot_002", "T2VA")
        with self.assertRaisesRegex(ValueError, "缺少官方字段"):
            h3_prompt.parse_h3_prompt_response(
                "integrated_multimodal_description:\n[Shot 1] Dawn.",
                "shot_002", "T2VA",
            )
        with self.assertRaisesRegex(ValueError, "Typography"):
            h3_prompt.parse_h3_prompt_response(
                valid_base + "\nTypography:\nLyrics on screen.",
                "shot_002", "T2VA",
            )

    def test_rejects_english_description_with_token_chinese(self):
        english_body = """integrated_multimodal_description:
[Shot 1] A woman walks slowly through the station while the camera follows her from behind. 中文镜头描述需要完整。 The lighting changes as she reaches the platform.

overall_soundscape:
轻柔风声持续流动。

non_diegetic_music:
N/A"""
        with self.assertRaisesRegex(h3_prompt.H3PromptLanguageError,
                                    "汉字 .*英文词"):
            h3_prompt.parse_h3_prompt_response(
                english_body, "shot_002", "T2VA")

    def test_rejects_english_soundscape_when_main_description_is_chinese(self):
        english_soundscape = """integrated_multimodal_description:
[Shot 1] 黎明天台保持安静，镜头缓慢推向边缘。

overall_soundscape:
Wind blows across the rooftop as footsteps approach the camera.

non_diegetic_music:
N/A"""
        with self.assertRaisesRegex(ValueError, "overall_soundscape"):
            h3_prompt.parse_h3_prompt_response(
                english_soundscape, "shot_002", "T2VA")

    def test_retry_uses_original_input_and_requires_all_ref_fields(self):
        retry = h3_prompt.build_h3_retry_user_prompt(
            "原始镜头输入", "Ref2VA", "缺少 detailed_description")
        self.assertIn("原始镜头输入", retry)
        self.assertIn("从头生成", retry)
        self.assertIn("detailed_description、overall_soundscape、non_diegetic_music", retry)
        self.assertIn("JSON 必须闭合", retry)
        self.assertNotIn("partial H3 text", retry)

    def test_language_retry_rewrites_existing_draft_instead_of_discarding_it(self):
        draft = "detailed_description:\n[Shot 1] A woman walks through rain."
        retry = h3_prompt.build_h3_retry_user_prompt(
            "原始镜头输入", "Ref2VA", "描述不是中文",
            rejected_text=draft, language_failure=True)
        self.assertIn(draft, retry)
        self.assertIn("只把描述性英文改写为流畅简体中文", retry)
        self.assertIn("不得删除 detailed_description", retry)
        self.assertNotIn("已丢弃", retry)

    def test_user_prompt_explains_delivery_duration_as_pacing_not_new_field(self):
        _system, user, _summary, _fingerprint = \
            h3_prompt.build_h3_prompt_prompts(sample_plan(), "shot_001")
        self.assertIn("原始生成 226 帧（约 9.42 秒）", user)
        self.assertIn("可交付 216 帧（约 9.00 秒）", user)
        self.assertIn("关键表演必须在交付结束前完成", user)
        self.assertIn("不新增 duration/timing 字段", user)

    def test_timing_instruction_uses_selected_continuation_guide(self):
        prompt_input = {"music": {"fps": 24}, "generation_windows": [{
            "raw_frames": 192, "deliver_frames": 153,
            "trim_leading_frames": 39, "context_frames": 39,
            "tail_padding_frames": 0,
        }]}
        chinese = h3_prompt._performance_timing_instruction(prompt_input)
        english = h3_prompt._performance_timing_instruction(
            prompt_input, chinese_output=False)
        self.assertIn("前置续接引导 39 帧", chinese)
        self.assertIn("39 continuation guide frames", english)
        self.assertNotIn("末尾 22 帧", chinese)
        self.assertNotIn("last 22 delivered frames", english)

    def test_english_timing_and_retry_do_not_inject_chinese_output_rules(self):
        prompt_input = h3_prompt.build_h3_prompt_input(
            sample_plan(), "shot_001")
        timing = h3_prompt._performance_timing_instruction(
            prompt_input, chinese_output=False)
        retry = h3_prompt.build_h3_retry_user_prompt(
            "original shot input", "Ref2VA", "missing field",
            output_language="en")
        self.assertIn("deliverable frames", timing)
        self.assertNotIn("自然中文", timing)
        self.assertIn("Regenerate one complete JSON object", retry)
        self.assertNotIn("简体中文", retry)


if __name__ == "__main__":
    unittest.main()
