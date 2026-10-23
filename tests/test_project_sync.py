from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyue_project_sync", ROOT / "project_sync.py")
sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync
SPEC.loader.exec_module(sync)


def manifest(*, project_id="project-a", code="PA", master="master-1",
             vocals="", lyrics="", prompt="", assets=None,
             generation_spec=None, skill_templates=None):
    audio = {"master": {"fingerprint": master}}
    if vocals:
        audio["vocals"] = {"fingerprint": vocals}
    return {
        "project_id": project_id,
        "project_code": code,
        "project_name": "MV",
        "project_brief": {
            "description": "",
            "overall_prompt": prompt,
            "negative_prompt": "",
        },
        "assets": assets or [],
        "generation_spec": generation_spec or {
            "aspect_ratio": "16:9 (Widescreen)",
            "first_pass_megapixels": 0.4,
            "second_pass_megapixels": 0.9,
            "pixel_alignment": 32,
        },
        "skill_templates": skill_templates or {
            "visual_outline": "xiaoyue-mv-visual-outline",
            "h3_prompt": "xiaoyue-h3-mv-prompt",
        },
        "lyrics": {"source_text": lyrics, "format": "auto"},
        "audio": audio,
    }


def existing_plan(result):
    components = result["components"]
    brief = components["project_brief"]
    return {
        "project": {
            "id": result["project_sources"]["project_id"],
            "code": result["project_sources"]["project_code"],
            "name": brief["project_name"],
            "description": brief["description"],
            "overall_prompt": brief["overall_prompt"],
            "negative_prompt": brief["negative_prompt"],
        },
        "assets": components["assets"],
        "generation_spec": components["generation_spec"],
        "skill_templates": components["skill_templates"],
        "subtitle_source": components["lyrics"]["source_text"],
        "subtitles": {"format": components["lyrics"]["format"]},
        "audio": {"fingerprint": result["project_sources"]["master_audio"]},
        "project_sources": result["project_sources"],
        "shots": [{"id": "shot_001"}],
    }


class ProjectSyncTests(unittest.TestCase):
    def test_project_switch_discards_old_plan_state(self):
        first = sync.reconcile_project_state(manifest(), {}, "master-1")
        plan = existing_plan(first)
        switched = sync.reconcile_project_state(
            manifest(project_id="project-b", code="PB", master="master-2"),
            plan,
            "master-2",
        )
        self.assertEqual(switched["existing"], {})
        self.assertEqual(switched["project_sync"]["changed_inputs"], ["project"])
        self.assertEqual(
            switched["project_sync"]["timeline_action"],
            "reset_for_project_switch",
        )

    def test_master_change_requests_replan_but_vocals_and_lyrics_preserve_timeline(self):
        first = sync.reconcile_project_state(manifest(), {}, "master-1")
        plan = existing_plan(first)
        updated = sync.reconcile_project_state(
            manifest(master="master-2", vocals="voice-1", lyrics="新歌词"),
            plan,
            "master-2",
        )
        changes = updated["project_sync"]["changed_inputs"]
        self.assertIn("master_audio", changes)
        self.assertIn("vocals_audio", changes)
        self.assertIn("lyrics", changes)
        self.assertEqual(
            updated["project_sync"]["timeline_action"],
            "replanned_for_master_change",
        )
        self.assertIn("vocal_analysis", updated["project_sync"]["pending_reviews"])

    def test_project_entry_update_wins_over_stale_planner_copy(self):
        first = sync.reconcile_project_state(
            manifest(prompt="原始总提示词"), {}, "master-1")
        plan = existing_plan(first)
        plan["project"]["overall_prompt"] = "Planner 中的未同步修改"
        updated = sync.reconcile_project_state(
            manifest(prompt="项目入口新提示词"), plan, "master-1")
        self.assertEqual(
            updated["components"]["project_brief"]["overall_prompt"],
            "项目入口新提示词",
        )
        self.assertIn("project_brief", updated["project_sync"]["conflicts"])

    def test_planner_only_edit_survives_when_manifest_is_unchanged(self):
        first = sync.reconcile_project_state(
            manifest(prompt="原始总提示词"), {}, "master-1")
        plan = existing_plan(first)
        plan["project"]["overall_prompt"] = "Planner 修改"
        updated = sync.reconcile_project_state(
            manifest(prompt="原始总提示词"), plan, "master-1")
        self.assertEqual(
            updated["components"]["project_brief"]["overall_prompt"],
            "Planner 修改",
        )
        self.assertEqual(
            updated["project_sync"]["component_sources"]["project_brief"],
            "planner_edit",
        )

    def test_connected_subtitle_input_has_explicit_priority(self):
        first = sync.reconcile_project_state(
            manifest(lyrics="项目歌词"), {}, "master-1")
        plan = existing_plan(first)
        updated = sync.reconcile_project_state(
            manifest(lyrics="项目歌词"),
            plan,
            "master-1",
            subtitle_override="00:00:01,000 --> 00:00:02,000\n新字幕",
        )
        self.assertIn("新字幕", updated["components"]["lyrics"]["source_text"])
        self.assertEqual(
            updated["project_sync"]["component_sources"]["lyrics"],
            "connected_subtitle_input",
        )

    def test_generation_spec_update_preserves_timeline_and_requests_review(self):
        first = sync.reconcile_project_state(manifest(), {}, "master-1")
        plan = existing_plan(first)
        updated = sync.reconcile_project_state(
            manifest(generation_spec={
                "aspect_ratio": "9:16 (Portrait Widescreen)",
                "first_pass_megapixels": 0.5,
                "second_pass_megapixels": 1.0,
                "pixel_alignment": 32,
            }),
            plan,
            "master-1",
        )
        self.assertEqual(updated["project_sync"]["timeline_action"], "preserved")
        self.assertIn("generation_spec", updated["project_sync"]["changed_inputs"])
        self.assertIn(
            "generation_spec_review",
            updated["project_sync"]["pending_reviews"],
        )

    def test_project_skill_selection_reaches_existing_plan_without_replan(self):
        first = sync.reconcile_project_state(manifest(), {}, "master-1")
        plan = existing_plan(first)
        selected = {
            "visual_outline": "alternate-outline",
            "h3_prompt": "alternate-h3",
        }
        updated = sync.reconcile_project_state(
            manifest(skill_templates=selected), plan, "master-1")
        self.assertEqual(updated["components"]["skill_templates"], selected)
        self.assertIn("skill_templates", updated["project_sync"]["changed_inputs"])
        self.assertEqual(updated["project_sync"]["timeline_action"], "preserved")


if __name__ == "__main__":
    unittest.main()
