from __future__ import annotations

import importlib.util
import sys
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("xiaoyue_planner_core", ROOT / "planner_core.py")
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


def silent_analysis(total_frames: int, bpm: float = 120.0):
    beat_step = round(core.FPS * 60 / bpm)
    return {
        "bpm": bpm,
        "bpm_confidence": 0.8,
        "beat_frames": list(range(beat_step, total_frames, beat_step)),
        "frame_energy": [0.2] * (total_frames + 1),
        "section_change_frames": [],
    }


def compile_seconds(seconds: float, existing=None, settings=None):
    sample_rate = 48000
    samples = round(seconds * sample_rate)
    total_frames = round(seconds * core.FPS)
    return core.compile_plan(
        audio_fingerprint="test-audio",
        sample_rate=sample_rate,
        total_samples=samples,
        analysis=silent_analysis(total_frames),
        settings=settings,
        subtitle_source="",
        existing=existing,
    )


def compile_with_subtitles(seconds: float, subtitle_source: str, existing=None):
    sample_rate = 48000
    samples = round(seconds * sample_rate)
    total_frames = round(seconds * core.FPS)
    return core.compile_plan(
        audio_fingerprint="test-audio",
        sample_rate=sample_rate,
        total_samples=samples,
        analysis=silent_analysis(total_frames),
        subtitle_source=subtitle_source,
        existing=existing,
    )


class H3MathTests(unittest.TestCase):
    def test_legal_lengths(self):
        expected = {1: 5, 5: 5, 6: 22, 120: 124, 192: 192, 216: 226, 240: 243}
        for requested, legal in expected.items():
            self.assertEqual(core.legal_h3_frames(requested), legal)
            self.assertEqual((legal - 5) % 17, 0)

    def test_legal_window_presets_cover_five_to_fifteen_seconds(self):
        self.assertEqual(core.H3_DEFAULT_MAX_FRAMES, 226)
        self.assertEqual(core.H3_WINDOW_PRESETS[0], 124)
        self.assertEqual(core.H3_WINDOW_PRESETS[-1], 362)
        self.assertTrue(all((frames - 5) % 17 == 0 for frames in core.H3_WINDOW_PRESETS))
        self.assertGreaterEqual(core.H3_WINDOW_PRESETS[0] / core.FPS, 5)
        self.assertGreaterEqual(core.H3_WINDOW_PRESETS[-1] / core.FPS, 15)

    def test_absolute_sample_clock_has_no_accumulated_drift(self):
        sample_rate = 44100
        boundaries = [0, 37, 192, 419, 1001, 4319]
        pieces = [
            core.frame_to_sample(right, sample_rate) - core.frame_to_sample(left, sample_rate)
            for left, right in zip(boundaries, boundaries[1:])
        ]
        self.assertEqual(sum(pieces), core.frame_to_sample(boundaries[-1], sample_rate))


class SubtitleTests(unittest.TestCase):
    def test_srt(self):
        parsed = core.parse_timed_text(
            "1\n00:00:01,000 --> 00:00:02,500\n第一句\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n第二句"
        )
        self.assertEqual(parsed["format"], "srt")
        self.assertEqual(len(parsed["cues"]), 2)
        self.assertEqual(parsed["cues"][0]["end_frame"], 60)

    def test_lrc(self):
        parsed = core.parse_timed_text("[00:01.00]第一句\n[00:04.50]第二句")
        self.assertEqual(parsed["format"], "lrc")
        self.assertEqual(parsed["cues"][0]["end_seconds"], 4.5)

    def test_plain_lyrics_are_not_fake_timed(self):
        parsed = core.parse_timed_text("没有时间戳的歌词")
        self.assertEqual(parsed["format"], "plain")
        self.assertEqual(parsed["cues"], [])
        self.assertTrue(parsed["untimed_text"])

    def test_master_cue_has_stable_absolute_frame_and_sample_identity(self):
        source = "1\n00:00:01,000 --> 00:00:02,500\n原始字幕"
        first = compile_with_subtitles(6, source)
        second = compile_with_subtitles(6, source, existing=first)
        cue = second["subtitles"]["master_cues"][0]
        self.assertEqual(cue["id"], "cue_f0000024_f0000060")
        self.assertEqual(cue["absolute_start_frame"], 24)
        self.assertEqual(cue["absolute_end_frame"], 60)
        self.assertEqual(cue["absolute_start_sample"], 48000)
        self.assertEqual(cue["absolute_end_sample"], 120000)
        self.assertNotIn("cues", second["subtitles"])

    def test_one_master_cue_projects_into_two_shots_with_relative_frames(self):
        source = "1\n00:00:03,500 --> 00:00:04,500\n跨切点字幕"
        base = compile_with_subtitles(8, source)
        template = base["shots"][0]
        base["shots"] = [
            {**template, "id": "shot_001", "start_frame": 0, "end_frame": 96},
            {**template, "id": "shot_002", "start_frame": 96, "end_frame": 192},
        ]
        base["transitions"] = [{
            "type": "hard_cut", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        }]
        plan = compile_with_subtitles(8, source, existing=base)
        cue_id = plan["subtitles"]["master_cues"][0]["id"]
        left = plan["shots"][0]["subtitle_projections"][0]
        right = plan["shots"][1]["subtitle_projections"][0]
        self.assertEqual(left["source_cue_id"], cue_id)
        self.assertEqual(right["source_cue_id"], cue_id)
        self.assertEqual((left["relative_start_frame"], left["relative_end_frame"]), (84, 96))
        self.assertEqual((right["relative_start_frame"], right["relative_end_frame"]), (0, 12))
        self.assertTrue(left["is_partial"])
        self.assertTrue(right["is_partial"])

    def test_text_correction_survives_boundary_move_and_updates_every_projection(self):
        source = "1\n00:00:03,500 --> 00:00:04,500\n识别错字"
        base = compile_with_subtitles(8, source)
        template = base["shots"][0]
        base["shots"] = [
            {**template, "id": "shot_001", "start_frame": 0, "end_frame": 96},
            {**template, "id": "shot_002", "start_frame": 96, "end_frame": 192},
        ]
        base["transitions"] = [{
            "type": "hard_cut", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        }]
        initial = compile_with_subtitles(8, source, existing=base)
        master = initial["subtitles"]["master_cues"][0]
        master["corrected_text"] = "用户修正文字"
        master["revision"] = 1
        initial["shots"][0]["end_frame"] = 108
        initial["shots"][1]["start_frame"] = 108
        moved = compile_with_subtitles(8, source, existing=initial)
        updated = moved["subtitles"]["master_cues"][0]
        self.assertEqual(updated["corrected_text"], "用户修正文字")
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(updated["absolute_start_frame"], 84)
        self.assertEqual(updated["absolute_end_frame"], 108)
        self.assertEqual(len(moved["shots"][0]["subtitle_projections"]), 1)
        self.assertEqual(len(moved["shots"][1]["subtitle_projections"]), 0)
        self.assertEqual(
            moved["shots"][0]["subtitle_projections"][0]["relative_end_frame"],
            108,
        )

    def test_final_shot_subtitle_tracks_survive_boundary_move(self):
        source = "1\n00:00:03,500 --> 00:00:04,500\n母版参考"
        base = compile_with_subtitles(8, source)
        template = base["shots"][0]
        left = deepcopy(template)
        right = deepcopy(template)
        left.update({"id": "shot_001", "start_frame": 0, "end_frame": 96})
        right.update({"id": "shot_002", "start_frame": 96, "end_frame": 192})
        left["subtitle_track"] = {
            "leading_text": "左镜前扩",
            "text": "前镜头最终文字",
            "trailing_text": "左镜后扩",
            "revision": 3,
        }
        right["subtitle_track"] = {
            "leading_text": "右镜前扩",
            "text": "后镜头最终文字",
            "trailing_text": "右镜后扩",
            "revision": 4,
        }
        base["shots"] = [left, right]
        base["transitions"] = [{
            "type": "hard_cut", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        }]
        initial = compile_with_subtitles(8, source, existing=base)
        initial["shots"][0]["end_frame"] = 108
        initial["shots"][1]["start_frame"] = 108

        moved = compile_with_subtitles(8, source, existing=initial)

        self.assertEqual(moved["shots"][0]["subtitle_track"]["text"], "前镜头最终文字")
        self.assertEqual(moved["shots"][1]["subtitle_track"]["text"], "后镜头最终文字")
        self.assertEqual(moved["shots"][0]["subtitle_track"]["leading_text"], "左镜前扩")
        self.assertEqual(moved["shots"][0]["subtitle_track"]["trailing_text"], "左镜后扩")
        self.assertEqual(moved["shots"][1]["subtitle_track"]["leading_text"], "右镜前扩")
        self.assertEqual(moved["shots"][1]["subtitle_track"]["trailing_text"], "右镜后扩")
        self.assertEqual(moved["shots"][0]["subtitle_track"]["revision"], 3)
        self.assertEqual(moved["shots"][1]["subtitle_track"]["revision"], 4)
        self.assertEqual(
            (
                moved["shots"][0]["subtitle_track"]["absolute_start_frame"],
                moved["shots"][0]["subtitle_track"]["absolute_end_frame"],
                moved["shots"][0]["subtitle_track"]["relative_end_frame"],
            ),
            (0, 108, 108),
        )
        self.assertEqual(
            (
                moved["shots"][1]["subtitle_track"]["absolute_start_frame"],
                moved["shots"][1]["subtitle_track"]["absolute_end_frame"],
                moved["shots"][1]["subtitle_track"]["relative_end_frame"],
            ),
            (108, 192, 84),
        )
        self.assertEqual(
            moved["subtitles"]["final_text_authority"],
            "shots[].subtitle_track.text",
        )

    def test_ai_dialogue_context_combines_leading_main_and_trailing_words(self):
        source = (
            "1\n00:00:04,500 --> 00:00:04,900\n前扩词\n\n"
            "2\n00:00:06,000 --> 00:00:06,500\n母版正式参考\n\n"
            "3\n00:00:10,100 --> 00:00:10,500\n后扩词"
        )
        base = compile_with_subtitles(15, source)
        template = base["shots"][0]
        points = [0, 120, 240, 360]
        base["shots"] = [
            {
                **deepcopy(template),
                "id": f"shot_{index + 1:03d}",
                "start_frame": left,
                "end_frame": right,
                "manual_splice_frames": [],
                "subtitle_track": {
                    "leading_text": "用户前扩词" if index == 1 else "",
                    "text": "正式校正词" if index == 1 else "",
                    "trailing_text": "用户后扩词" if index == 1 else "",
                    "revision": 1 if index == 1 else 0,
                },
            }
            for index, (left, right) in enumerate(zip(points, points[1:]))
        ]
        base["transitions"] = [{
            "type": "hard_cut", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        } for _ in range(2)]

        plan = compile_with_subtitles(15, source, existing=base)
        context = plan["shots"][1]["subtitle_context"]

        self.assertLess(context["source_range"][0], 120)
        self.assertGreater(context["source_range"][1], 240)
        self.assertEqual(context["leading_extension"]["text"], "用户前扩词")
        self.assertEqual(
            context["leading_extension"]["text_authority"],
            "shots[].subtitle_track.leading_text",
        )
        self.assertEqual(context["main"]["text"], "正式校正词")
        self.assertTrue(context["main"]["is_final_subtitle_output"])
        self.assertEqual(context["trailing_extension"]["text"], "用户后扩词")
        self.assertEqual(
            context["trailing_extension"]["text_authority"],
            "shots[].subtitle_track.trailing_text",
        )
        self.assertEqual(
            context["ai_dialogue_text"],
            "用户前扩词 正式校正词 用户后扩词",
        )
        self.assertEqual(
            context["ai_dialogue_policy"],
            "text_only_no_shot_relative_timestamps",
        )
        self.assertEqual(
            plan["subtitles"]["ai_dialogue_authority"],
            "shots[].subtitle_context.ai_dialogue_text",
        )
        self.assertEqual(plan["shots"][0]["subtitle_context"]["leading_extension"]["frames"], 0)
        self.assertEqual(plan["shots"][2]["subtitle_context"]["trailing_extension"]["frames"], 0)

    def test_long_take_owns_overlap_only_as_next_shot_leading_extension(self):
        source = "1\n00:00:04,300 --> 00:00:04,900\n虚接重复词"
        base = compile_with_subtitles(10, source)
        template = base["shots"][0]
        base["shots"] = [
            {
                **deepcopy(template),
                "id": "shot_001",
                "start_frame": 0,
                "end_frame": 120,
                "manual_splice_frames": [],
                "subtitle_track": {"text": "左段正式词", "revision": 1},
            },
            {
                **deepcopy(template),
                "id": "shot_002",
                "start_frame": 120,
                "end_frame": 240,
                "manual_splice_frames": [],
                "subtitle_track": {
                    "leading_text": "虚接重复词",
                    "text": "右段正式词",
                    "trailing_text": "",
                    "revision": 1,
                },
            },
        ]
        base["transitions"] = [{
            "type": "long_take", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        }]

        plan = compile_with_subtitles(10, source, existing=base)
        left, right = plan["shots"]
        left_window = next(
            window for window in plan["generation_windows"]
            if window["shot_id"] == left["id"]
        )
        right_window = next(
            window for window in plan["generation_windows"]
            if window["shot_id"] == right["id"]
        )

        self.assertEqual(left_window["audio_handle_after_frames"], 0)
        self.assertEqual(left["subtitle_context"]["trailing_extension"]["frames"], 0)
        self.assertEqual(right_window["context_frames"], 22)
        self.assertEqual(right["subtitle_context"]["leading_extension"]["frames"], 22)
        self.assertEqual(
            right["subtitle_context"]["leading_extension"]["text"],
            "虚接重复词",
        )

    def test_long_take_chain_middle_has_only_leading_extension_and_tail_has_both(self):
        base = compile_with_subtitles(20, "")
        template = base["shots"][0]
        points = [0, 120, 240, 360, 480]
        base["shots"] = [
            {
                **deepcopy(template),
                "id": f"shot_{index + 1:03d}",
                "start_frame": left,
                "end_frame": right,
                "manual_splice_frames": [],
            }
            for index, (left, right) in enumerate(zip(points, points[1:]))
        ]
        base["transitions"] = [
            {
                "type": "long_take", "context_frames": 22,
                "duration_frames": 12, "safety_frames": 5,
            },
            {
                "type": "long_take", "context_frames": 22,
                "duration_frames": 12, "safety_frames": 5,
            },
            {
                "type": "hard_cut", "context_frames": 22,
                "duration_frames": 12, "safety_frames": 5,
            },
        ]

        plan = compile_with_subtitles(20, "", existing=base)
        first, middle, tail, _ = plan["shots"]
        windows = {
            window["shot_id"]: window
            for window in plan["generation_windows"]
        }

        self.assertEqual(windows[first["id"]]["audio_handle_after_frames"], 0)
        self.assertEqual(first["subtitle_context"]["trailing_extension"]["frames"], 0)

        self.assertEqual(windows[middle["id"]]["audio_handle_before_frames"], 0)
        self.assertGreater(windows[middle["id"]]["audio_handle_after_frames"], 0)
        self.assertEqual(windows[middle["id"]]["continuation_tail_bleed_frames"],
                         windows[middle["id"]]["audio_handle_after_frames"])
        self.assertGreater(middle["subtitle_context"]["leading_extension"]["frames"], 0)
        self.assertGreater(middle["subtitle_context"]["trailing_extension"]["frames"], 0)

        self.assertGreater(tail["subtitle_context"]["leading_extension"]["frames"], 0)
        self.assertGreater(tail["subtitle_context"]["trailing_extension"]["frames"], 0)
        self.assertTrue(all(
            (window["raw_frames"] - core.H3_OFFSET) % core.H3_STEP == 0
            for window in plan["generation_windows"]
        ))

    def test_long_take_chain_rule_scales_beyond_three_segments(self):
        base = compile_with_subtitles(30, "")
        template = base["shots"][0]
        points = list(range(0, 721, 120))
        base["shots"] = [
            {
                **deepcopy(template),
                "id": f"shot_{index + 1:03d}",
                "start_frame": left,
                "end_frame": right,
                "manual_splice_frames": [],
            }
            for index, (left, right) in enumerate(zip(points, points[1:]))
        ]
        long_take = {
            "type": "long_take", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        }
        hard_cut = {
            "type": "hard_cut", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        }
        base["transitions"] = [deepcopy(long_take) for _ in range(4)] + [hard_cut]

        plan = compile_with_subtitles(30, "", existing=base)
        chain = plan["shots"][:5]
        windows = {
            window["shot_id"]: window
            for window in plan["generation_windows"]
        }

        self.assertEqual(windows[chain[0]["id"]]["audio_handle_after_frames"], 0)
        for shot in chain[1:-1]:
            with self.subTest(shot=shot["id"]):
                self.assertEqual(windows[shot["id"]]["audio_handle_before_frames"], 0)
                self.assertGreater(windows[shot["id"]]["audio_handle_after_frames"], 0)
                self.assertGreater(shot["subtitle_context"]["trailing_extension"]["frames"], 0)
        for shot in chain[1:-1]:
            with self.subTest(leading=shot["id"]):
                self.assertGreater(shot["subtitle_context"]["leading_extension"]["frames"], 0)

        tail = chain[-1]
        self.assertGreater(tail["subtitle_context"]["leading_extension"]["frames"], 0)
        self.assertGreater(tail["subtitle_context"]["trailing_extension"]["frames"], 0)

    def test_middle_continuations_put_only_alignment_padding_after_delivery(self):
        base = compile_with_subtitles(480 / 24, "")
        template = base["shots"][0]
        points = [0, 144, 269, 380, 480]
        base["shots"] = [
            {**deepcopy(template), "id": f"shot_{index + 1:03d}",
             "start_frame": left, "end_frame": right,
             "manual_splice_frames": []}
            for index, (left, right) in enumerate(zip(points, points[1:]))
        ]
        base["transitions"] = [
            {"type": "long_take", "context_frames": 22,
             "duration_frames": 12, "safety_frames": 5},
            {"type": "long_take", "context_frames": 22,
             "duration_frames": 12, "safety_frames": 5},
            {"type": "hard_cut", "context_frames": 22,
             "duration_frames": 12, "safety_frames": 5},
        ]
        plan = compile_with_subtitles(480 / 24, "", existing=base)
        self.assertEqual(plan["h3"]["continuation_window_rule_revision"], 3)
        a, b, c = plan["generation_windows"][:3]
        self.assertEqual((a["raw_frames"], a["deliver_frames"],
                          a["audio_handle_before_frames"],
                          a["audio_handle_after_frames"]), (175, 144, 31, 0))
        self.assertEqual((b["raw_frames"], b["context_frames"],
                          b["deliver_frames"], b["audio_handle_before_frames"],
                          b["continuation_tail_bleed_frames"]),
                         (158, 22, 125, 0, 11))
        self.assertEqual((c["raw_frames"], c["context_frames"],
                          c["deliver_frames"], c["audio_handle_before_frames"],
                          c["continuation_tail_bleed_frames"]),
                         (158, 22, 111, 0, 0))
        self.assertEqual(c["audio_handle_after_frames"], 25)
        self.assertEqual(c["tail_padding_frames"], 25)
        self.assertEqual(c["source_end_frame"], 405)
        self.assertEqual(b["source_start_frame"], a["delivery_timeline_range"][1] - 22)
        self.assertEqual(c["source_start_frame"], b["delivery_timeline_range"][1] - 22)
        self.assertEqual(b["deliver_from_raw_frame"] + b["deliver_frames"] - 22, 125)
        self.assertEqual(b["raw_frames"] - b["deliver_from_raw_frame"] - b["deliver_frames"], 11)

    def test_exact_h3_continuation_ends_on_hard_cut_without_tail_padding(self):
        base = compile_with_subtitles(320 / 24, "")
        template = base["shots"][0]
        points = [0, 152, 220, 320]
        base["shots"] = [
            {**deepcopy(template), "id": f"shot_{index + 1:03d}",
             "start_frame": left, "end_frame": right,
             "manual_splice_frames": []}
            for index, (left, right) in enumerate(zip(points, points[1:]))
        ]
        base["transitions"] = [
            {"type": "long_take", "context_frames": 22,
             "duration_frames": 12, "safety_frames": 5},
            {"type": "hard_cut", "context_frames": 22,
             "duration_frames": 12, "safety_frames": 5},
        ]
        plan = compile_with_subtitles(320 / 24, "", existing=base)
        continuation = plan["generation_windows"][1]
        self.assertEqual((continuation["context_frames"],
                          continuation["deliver_frames"],
                          continuation["raw_frames"]), (22, 68, 90))
        self.assertEqual(continuation["source_end_frame"], 220)
        self.assertEqual(continuation["audio_handle_after_frames"], 0)
        self.assertEqual(continuation["continuation_tail_bleed_frames"], 0)

    def test_crossfade_keeps_extensions_on_both_sides(self):
        base = compile_with_subtitles(10, "")
        template = base["shots"][0]
        base["shots"] = [
            {**deepcopy(template), "id": "shot_001", "start_frame": 0,
             "end_frame": 120, "manual_splice_frames": []},
            {**deepcopy(template), "id": "shot_002", "start_frame": 120,
             "end_frame": 240, "manual_splice_frames": []},
        ]
        base["transitions"] = [{
            "type": "crossfade", "context_frames": 22,
            "duration_frames": 24, "safety_frames": 5,
        }]

        plan = compile_with_subtitles(10, "", existing=base)

        self.assertGreater(
            plan["shots"][0]["subtitle_context"]["trailing_extension"]["frames"],
            0,
        )
        self.assertGreater(
            plan["shots"][1]["subtitle_context"]["leading_extension"]["frames"],
            0,
        )


class PlanTests(unittest.TestCase):
    def test_selected_window_preset_controls_auto_plan_and_compilation(self):
        plan = compile_seconds(40, settings={
            "target_shot_seconds": 12.0,
            "h3_max_frames": 294,
        })
        self.assertEqual(plan["h3"]["max_window_frames"], 294)
        self.assertTrue(all(shot["keep_frames"] <= 294 for shot in plan["shots"]))
        self.assertTrue(all(window["raw_frames"] <= 294 for window in plan["generation_windows"]))

    def test_replan_resets_automatic_transitions_to_hard_cut(self):
        base = compile_seconds(20)
        base["transitions"][0]["type"] = "long_take"
        if len(base["transitions"]) > 1:
            base["transitions"][1]["type"] = "crossfade"
            base["transitions"][1]["duration_frames"] = 36
        plan = core.compile_plan(
            audio_fingerprint="test-audio",
            sample_rate=48000,
            total_samples=20 * 48000,
            analysis=silent_analysis(20 * core.FPS),
            settings={"h3_max_frames": 226},
            existing=base,
            request_replan=True,
        )
        self.assertTrue(all(item["type"] == "hard_cut" for item in plan["transitions"]))
        self.assertTrue(all(item["duration_frames"] == 12 for item in plan["transitions"]))

    def test_changed_auto_plan_settings_wait_for_destructive_confirmation(self):
        base = compile_seconds(60, settings={
            "target_shot_seconds": 7.5,
            "h3_max_frames": 226,
        })
        old_boundaries = [shot["end_frame"] for shot in base["shots"]]
        pending = compile_seconds(60, existing=base, settings={
            "target_shot_seconds": 9.0,
            "h3_max_frames": 243,
        })
        self.assertEqual(
            [shot["end_frame"] for shot in pending["shots"]],
            old_boundaries,
        )
        self.assertEqual(
            pending["pending_replan"]["reason"],
            "automatic_settings_changed",
        )
        self.assertEqual(pending["pending_replan"]["requested"]["h3_max_frames"], 243)

        confirmed = core.compile_plan(
            audio_fingerprint="test-audio",
            sample_rate=48000,
            total_samples=60 * 48000,
            analysis=silent_analysis(60 * core.FPS),
            settings={"target_shot_seconds": 9.0, "h3_max_frames": 243},
            existing=pending,
            request_replan=True,
        )
        self.assertNotEqual(
            [shot["end_frame"] for shot in confirmed["shots"]],
            old_boundaries,
        )
        self.assertNotIn("pending_replan", confirmed)

    def test_legacy_plan_without_auto_basis_requires_one_time_confirmation(self):
        base = compile_seconds(30, settings={
            "target_shot_seconds": 9.0,
            "h3_max_frames": 243,
        })
        base.pop("automatic_plan_basis")
        pending = compile_seconds(30, existing=base, settings={
            "target_shot_seconds": 9.0,
            "h3_max_frames": 243,
        })
        self.assertEqual(
            pending["pending_replan"]["reason"],
            "legacy_plan_basis_unverified",
        )
        self.assertEqual(
            [shot["end_frame"] for shot in pending["shots"]],
            [shot["end_frame"] for shot in base["shots"]],
        )

    def test_confirmed_replan_clears_shot_data_but_keeps_project_assets(self):
        base = compile_seconds(30)
        base["project"] = {
            "id": "project-1",
            "code": "DEMO",
            "name": "测试 MV",
            "overall_prompt": "全片风格",
        }
        base["assets"] = [{
            "id": "asset-1",
            "name": "主角",
            "category": "character",
            "declaration": "统一角色",
        }]
        base["creative_plan"] = {
            "overview": "旧整片镜头纲领",
            "revision": 2,
            "source_fingerprint": "old",
            "model": "reasoner",
        }
        base["shots"][0]["creative_outline"] = "旧镜头大体内容"
        base["shots"][0]["prompt"] = "旧镜头提示词"
        base["shots"][0]["asset_bindings"] = [{
            "asset_id": "asset-1", "order": 1, "usage": "旧镜头绑定",
        }]
        base["shots"][0]["material"] = {
            "kind": "video", "reference": "old.mp4", "note": "旧参数",
        }
        base["shots"][0]["subtitle_track"] = {
            "text": "旧镜头最终字幕", "revision": 7,
        }
        rebuilt = core.compile_plan(
            audio_fingerprint="test-audio",
            sample_rate=48000,
            total_samples=30 * 48000,
            analysis=silent_analysis(30 * core.FPS),
            settings={"target_shot_seconds": 9.0, "h3_max_frames": 243},
            existing=base,
            request_replan=True,
        )
        self.assertEqual(rebuilt["project"]["code"], "DEMO")
        self.assertEqual(rebuilt["assets"][0]["id"], "asset-1")
        self.assertFalse(rebuilt["creative_plan"]["overview"])
        self.assertTrue(all(not shot["creative_outline"] for shot in rebuilt["shots"]))
        self.assertTrue(all(not shot["prompt"] for shot in rebuilt["shots"]))
        self.assertTrue(all(not shot["asset_bindings"] for shot in rebuilt["shots"]))
        self.assertTrue(all(not shot["material"]["reference"] for shot in rebuilt["shots"]))
        self.assertTrue(all(not shot["subtitle_track"]["text"] for shot in rebuilt["shots"]))

    def test_auto_plan_stays_inside_real_shot_limit(self):
        plan = compile_seconds(35)
        self.assertGreater(len(plan["shots"]), 1)
        self.assertTrue(all(shot["keep_frames"] <= 221 for shot in plan["shots"]))
        self.assertTrue(all(window["raw_frames"] <= 226 for window in plan["generation_windows"]))
        self.assertTrue(all((window["raw_frames"] - 5) % 17 == 0
                            for window in plan["generation_windows"]))

    def test_long_take_uses_prefix_and_splits_when_needed(self):
        base = compile_seconds(18)
        base["shots"] = [
            {**base["shots"][0], "id": "shot_001", "start_frame": 0, "end_frame": 216},
            {**base["shots"][0], "id": "shot_002", "start_frame": 216, "end_frame": 432},
        ]
        base["transitions"] = [{
            "type": "long_take", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        }]
        plan = compile_seconds(18, existing=base)
        second = [window for window in plan["generation_windows"] if window["shot_id"] == "shot_002"]
        self.assertEqual(second[0]["context_frames"], 22)
        self.assertEqual(second[0]["source_start_frame"], 194)
        self.assertEqual(len(second), 2)
        self.assertTrue(all(window["raw_frames"] <= 226 for window in second))

    def test_crossfade_adds_active_handles_and_safety(self):
        base = compile_seconds(16)
        base["shots"] = [
            {**base["shots"][0], "id": "shot_001", "start_frame": 0, "end_frame": 192},
            {**base["shots"][0], "id": "shot_002", "start_frame": 192, "end_frame": 384},
        ]
        base["transitions"] = [{
            "type": "crossfade", "context_frames": 22,
            "duration_frames": 24, "safety_frames": 5,
        }]
        plan = compile_seconds(16, existing=base)
        transition = plan["transitions"][0]
        self.assertEqual(transition["active_range"], [180, 204])
        first = next(window for window in plan["generation_windows"] if window["shot_id"] == "shot_001")
        second = next(window for window in plan["generation_windows"] if window["shot_id"] == "shot_002")
        self.assertGreater(first["delivery_timeline_range"][1], 192)
        self.assertLess(second["delivery_timeline_range"][0], 192)

    def test_crossfade_next_to_long_take_keeps_safety_and_guide_separate(self):
        for guide in (5, 22, 39, 56, 73):
            for transition_types in (
                ("long_take", "crossfade", "hard_cut"),
                ("hard_cut", "crossfade", "long_take"),
                ("long_take", "crossfade", "long_take"),
            ):
                base = compile_seconds(400 / 24)
                template = base["shots"][0]
                base["shots"] = [
                    {**deepcopy(template), "id": f"shot_{index + 1:03d}",
                     "start_frame": index * 100,
                     "end_frame": (index + 1) * 100,
                     "manual_splice_frames": []}
                    for index in range(4)
                ]
                base["transitions"] = [
                    {"type": kind, "context_frames": guide,
                     "duration_frames": 24, "safety_frames": 0}
                    for kind in transition_types
                ]
                plan = compile_seconds(400 / 24, existing=base)
                windows = plan["generation_windows"]
                fade = plan["transitions"][1]
                left, right = windows[1:3]
                self.assertEqual(fade["active_range"], [188, 212])
                self.assertEqual(fade["after_safety_frames"],
                                 7 if transition_types[0] == "long_take" else 12)
                self.assertEqual(fade["before_safety_frames"], 12)
                self.assertEqual(left["delivery_timeline_range"][1],
                                 fade["previous_required_until_frame"])
                self.assertEqual(right["delivery_timeline_range"][0],
                                 fade["next_required_from_frame"])
                self.assertEqual(left["continuation_tail_bleed_frames"], 0)
                self.assertEqual(left["context_frames"],
                                 guide if transition_types[0] == "long_take" else 0)
                self.assertEqual(right["context_frames"], 0)
                self.assertFalse(right.get("continuity_parent_label"))
                if transition_types[0] == "long_take":
                    self.assertEqual(left["source_start_frame"],
                                     windows[0]["delivery_timeline_range"][1] - guide)
                    self.assertEqual(left["continuity_parent_label"],
                                     windows[0]["editorial_label"])
                if transition_types[2] == "long_take":
                    self.assertEqual(windows[3]["source_start_frame"],
                                     right["delivery_timeline_range"][1] - guide)
                    self.assertEqual(windows[3]["continuity_parent_label"],
                                     right["editorial_label"])
                self.assertTrue(all((window["raw_frames"] - 5) % 17 == 0
                                    for window in windows))

    def test_crossfade_odd_split_and_adjacent_shot_limit(self):
        base = compile_seconds(3)
        template = base["shots"][0]
        base["shots"] = [
            {**template, "id": "shot_001", "start_frame": 0, "end_frame": 60},
            {**template, "id": "shot_002", "start_frame": 60, "end_frame": 72},
        ]
        base["transitions"] = [{
            "type": "crossfade", "context_frames": 22,
            "duration_frames": 23, "safety_frames": 5,
        }]
        odd = compile_seconds(3, existing=base)["transitions"][0]
        self.assertEqual(odd["active_range"], [49, 72])
        self.assertEqual(odd["after_safety_frames"], 1)
        self.assertEqual(odd["before_safety_frames"], 16)
        self.assertEqual(odd["previous_required_until_frame"], 73)
        self.assertEqual(odd["next_required_from_frame"], 33)
        self.assertEqual(odd["safety_mode"], "auto_17n5")

        base["transitions"][0]["duration_frames"] = 95
        limited_plan = compile_seconds(3, existing=base)
        limited = limited_plan["transitions"][0]
        self.assertEqual(limited["duration_frames"], 24)
        self.assertEqual(limited["active_range"], [48, 72])
        self.assertEqual(limited["after_safety_frames"], 1)
        self.assertEqual(limited["before_safety_frames"], 15)
        self.assertTrue(all((window["raw_frames"] - 5) % 17 == 0
                            for window in limited_plan["generation_windows"]))

    def test_hard_cuts_compile_real_handles_on_both_sides(self):
        base = compile_seconds(15)
        template = base["shots"][0]
        points = [0, 120, 240, 360]
        base["shots"] = [
            {**template, "id": f"shot_{index + 1:03d}",
             "start_frame": left, "end_frame": right,
             "manual_splice_frames": []}
            for index, (left, right) in enumerate(zip(points, points[1:]))
        ]
        base["transitions"] = [{
            "type": "hard_cut", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        } for _ in range(2)]
        plan = compile_seconds(15, existing=base)
        first, middle, last = plan["generation_windows"]

        self.assertEqual(first["audio_handle_before_frames"], 0)
        self.assertGreater(first["audio_handle_after_frames"], 0)
        self.assertGreater(middle["audio_handle_before_frames"], 0)
        self.assertGreater(middle["audio_handle_after_frames"], 0)
        self.assertGreater(last["audio_handle_before_frames"], 0)
        self.assertEqual(last["audio_handle_after_frames"], 0)
        self.assertEqual(
            middle["trim_leading_frames"],
            middle["audio_handle_before_frames"],
        )
        for window in (first, middle, last):
            self.assertEqual((window["raw_frames"] - 5) % 17, 0)
            self.assertEqual(
                window["raw_frames"],
                window["audio_handle_before_frames"] +
                window["context_frames"] +
                window["deliver_frames"] +
                window["audio_handle_after_frames"],
            )

    def test_edit_points_store_absolute_master_positions(self):
        base = compile_seconds(15)
        template = base["shots"][0]
        points = [0, 120, 240, 360]
        base["shots"] = [
            {**template, "id": f"shot_{index + 1:03d}",
             "start_frame": left, "end_frame": right,
             "manual_splice_frames": []}
            for index, (left, right) in enumerate(zip(points, points[1:]))
        ]
        base["transitions"] = [{
            "type": "hard_cut", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        } for _ in range(2)]
        plan = compile_seconds(15, existing=base)

        self.assertEqual(
            [point["absolute_frame"] for point in plan["edit_points"]],
            points,
        )
        self.assertEqual(plan["edit_points"][1]["absolute_sample"], 240000)
        self.assertNotIn("absolute_seconds", plan["edit_points"][1])
        self.assertEqual(plan["edit_points"][1]["left_shot_id"], "shot_001")
        self.assertEqual(plan["edit_points"][1]["right_shot_id"], "shot_002")

    def test_more_than_three_long_take_sections_warns(self):
        base = compile_seconds(24)
        points = [0, 144, 288, 432, 576]
        template = base["shots"][0]
        base["shots"] = [
            {**template, "id": f"shot_{i + 1:03d}", "start_frame": left, "end_frame": right}
            for i, (left, right) in enumerate(zip(points, points[1:]))
        ]
        base["transitions"] = [{
            "type": "long_take", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        } for _ in range(3)]
        plan = compile_seconds(24, existing=base)
        self.assertTrue(any("超过实测 3 段舒适区" in warning for warning in plan["warnings"]))

    def test_manual_splice_survives_recompile_and_controls_window_boundary(self):
        base = compile_seconds(18)
        base["shots"] = [{
            **base["shots"][0],
            "id": "shot_001",
            "start_frame": 0,
            "end_frame": 432,
            "manual_splice_frames": [210],
        }]
        base["transitions"] = []
        plan = compile_seconds(18, existing=base)
        self.assertEqual(plan["shots"][0]["manual_splice_frames"], [210])
        windows = plan["generation_windows"]
        self.assertEqual(windows[0]["delivery_timeline_range"][1], 210)
        self.assertEqual(windows[1]["continuation_reason"], "manual_splice")
        self.assertEqual(windows[1]["source_start_frame"], 210 - 22)
        self.assertTrue(all(window["raw_frames"] <= 226 for window in windows))

    def test_missing_manual_splice_still_keeps_backend_windows_safe(self):
        base = compile_seconds(18)
        base["shots"] = [{
            **base["shots"][0],
            "id": "shot_001",
            "start_frame": 0,
            "end_frame": 432,
            "manual_splice_frames": [],
        }]
        base["transitions"] = []
        plan = compile_seconds(18, existing=base)
        self.assertTrue(all(window["raw_frames"] <= 226 for window in plan["generation_windows"]))
        self.assertTrue(any("用剃刀切成独立镜头" in warning for warning in plan["warnings"]))

    def test_legacy_allow_stitch_recovers_splices_from_old_windows(self):
        base = compile_seconds(18)
        first_shot = base["shots"][0]
        old_break = first_shot["start_frame"] + 96
        first_shot["allow_stitch"] = True
        first_shot.pop("manual_splice_frames", None)
        base["generation_windows"] = [{
            "shot_id": first_shot["id"],
            "delivery_timeline_range": [first_shot["start_frame"], old_break],
        }, {
            "shot_id": first_shot["id"],
            "delivery_timeline_range": [old_break, first_shot["end_frame"]],
        }]
        plan = compile_seconds(18, existing=base)
        self.assertIn(old_break, plan["shots"][0]["manual_splice_frames"])
        self.assertNotIn("allow_stitch", plan["shots"][0])

    def test_editorial_labels_versions_and_continuity_cascade(self):
        base = compile_seconds(18)
        base["shots"] = [
            {**base["shots"][0], "id": "shot_001", "start_frame": 0,
             "end_frame": 216, "manual_splice_frames": []},
            {**base["shots"][0], "id": "shot_002", "start_frame": 216,
             "end_frame": 432, "manual_splice_frames": []},
        ]
        base["transitions"] = [{
            "type": "long_take", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        }]
        plan = compile_seconds(18, existing=base)
        windows = plan["generation_windows"]
        labels = [window["editorial_label"] for window in windows]
        self.assertEqual(labels, ["01_a", "01_b", "01_c"])
        self.assertEqual(windows[0]["clip_name"], "SC01_a_v1")
        self.assertEqual(windows[0]["clip_name_template"], "{project_code}_SC01_a_v1")
        self.assertEqual(windows[1]["continuity_parent_label"], "01_a")
        self.assertEqual(windows[0]["downstream_regeneration_labels"], ["01_b", "01_c"])
        self.assertEqual(
            plan["continuity_chains"][0]["regeneration_policy"],
            "upstream_change_invalidates_downstream",
        )

    def test_hard_cuts_receive_new_scene_numbers_without_part_suffix(self):
        base = compile_seconds(12)
        base["shots"] = [
            {**base["shots"][0], "id": "shot_001", "start_frame": 0,
             "end_frame": 144, "manual_splice_frames": []},
            {**base["shots"][0], "id": "shot_002", "start_frame": 144,
             "end_frame": 288, "manual_splice_frames": []},
        ]
        base["transitions"] = [{
            "type": "hard_cut", "context_frames": 22,
            "duration_frames": 12, "safety_frames": 5,
        }]
        plan = compile_seconds(12, existing=base)
        self.assertEqual(
            [window["scene_code"] for window in plan["generation_windows"]],
            ["SC01", "SC02"],
        )
        self.assertEqual(plan["generation_windows"][1]["clip_name_template"],
                         "{project_code}_SC02_v1")
        self.assertEqual(plan["continuity_chains"], [])

    def test_saved_editor_ui_state_survives_audio_reload(self):
        base = compile_seconds(12)
        base["ui_state"] = {
            "timeline_zoom": 7,
            "playback_mode": "shot",
            "selected_shot_index": 1,
        }
        plan = compile_seconds(12, existing=base)
        self.assertEqual(plan["ui_state"], base["ui_state"])

    def test_project_assets_and_shot_bindings_survive_recompile(self):
        base = compile_seconds(12)
        base["project"] = {
            "code": "MV01",
            "name": "地久天长",
            "description": "一支有完整情绪曲线的 MV。",
            "overall_prompt": "全片统一色彩与镜头语言。",
            "negative_prompt": "避免人物外观漂移。",
        }
        base["generation_spec"] = {
            "aspect_ratio": "21:9 (Ultrawide)",
            "first_pass_megapixels": 0.6,
            "second_pass_megapixels": 1.4,
            "pixel_alignment": 32,
        }
        base["assets"] = [{
            "id": "asset_lead",
            "name": "女主角",
            "category": "character",
            "declaration": "黑色长发，蓝色外套。",
            "source": {
                "filename": "lead.webp",
                "subfolder": "mv01",
                "type": "input",
                "media_type": "image",
            },
        }]
        base["creative_plan"] = {
            "overview": "先做情绪递进，再在副歌释放。",
            "revision": 3,
            "source_fingerprint": "creative-3",
            "model": "local-reasoner",
        }
        base["shots"][0]["creative_outline"] = "主角进入空旷站台，建立孤独情绪。"
        base["shots"][0]["creative_asset_needs"] = [{
            "category": "scene", "description": "空旷旧站台",
        }]
        base["shots"][0]["asset_bindings"] = [
            {"asset_id": "asset_lead", "order": 8, "usage": "前景主体"},
            {"asset_id": "missing_asset", "order": 9, "usage": "不应保留"},
        ]
        plan = compile_seconds(12, existing=base)
        self.assertEqual(plan["project"], base["project"])
        self.assertEqual(plan["generation_spec"], base["generation_spec"])
        self.assertEqual(plan["creative_plan"], base["creative_plan"])
        self.assertEqual(plan["assets"], base["assets"])
        self.assertEqual(
            plan["shots"][0]["creative_outline"],
            "主角进入空旷站台，建立孤独情绪。",
        )
        self.assertEqual(plan["shots"][0]["creative_asset_needs"], [{
            "category": "scene", "description": "空旷旧站台",
        }])
        self.assertEqual(plan["shots"][0]["asset_bindings"], [{
            "asset_id": "asset_lead", "order": 1, "usage": "前景主体",
        }])
        self.assertFalse(plan["asset_binding_policy"]["global_library_order_is_generation_order"])


if __name__ == "__main__":
    unittest.main()
