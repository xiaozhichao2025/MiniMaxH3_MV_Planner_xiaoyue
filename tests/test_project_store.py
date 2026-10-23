from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyue_project_store", ROOT / "project_store.py")
store = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = store
SPEC.loader.exec_module(store)


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.roots = {
            "input": base / "input",
            "output": base / "output",
            "temp": base / "temp",
        }
        for path in self.roots.values():
            path.mkdir(parents=True)
        self.project_id = "d34c06e6-5f1c-4e50-82ef-cc939e254de0"

    def tearDown(self):
        self.temporary.cleanup()

    def test_project_initialization_creates_complete_three_root_layout(self):
        project = store.upsert_project(
            self.project_id, "天长地久 MV", "TC99", self.roots)
        layout = {key: Path(value) for key, value in project["paths"].items()}
        for relative in ("source", "subtitles", "plans", "assets"):
            self.assertTrue((layout["input"] / relative).is_dir())
        for relative in ("generations", "previews", "delivery"):
            self.assertTrue((layout["output"] / relative).is_dir())
        for relative in ("waveform", "beatmap", "thumbnails", "runtime"):
            self.assertTrue((layout["temp"] / relative).is_dir())
        manifest = json.loads((layout["input"] / "project.json").read_text("utf-8"))
        self.assertEqual(manifest["schema"], store.PROJECT_SCHEMA)
        self.assertEqual(manifest["project_id"], self.project_id)
        self.assertEqual(manifest["project_code"], "TC99")
        self.assertEqual(manifest["revision"], 1)
        self.assertEqual(manifest["generation_spec"], {
            "aspect_ratio": "16:9 (Widescreen)",
            "first_pass_megapixels": 0.4,
            "second_pass_megapixels": 0.9,
            "pixel_alignment": 32,
        })

    def test_opening_same_project_is_idempotent_but_display_name_change_increments_revision(self):
        first = store.upsert_project(
            self.project_id, "天长地久 MV", "TC99", self.roots)
        opened = store.upsert_project(
            self.project_id, "天长地久 MV", "TC99", self.roots)
        renamed = store.upsert_project(
            self.project_id, "天长地久 · 新剪辑", "TC99", self.roots)
        self.assertEqual(first["revision"], opened["revision"])
        self.assertEqual(renamed["revision"], first["revision"] + 1)

    def test_existing_project_code_is_immutable(self):
        store.upsert_project(
            self.project_id, "天长地久 MV", "TC99", self.roots)
        with self.assertRaisesRegex(ValueError, "历史项目编号 TC99 已锁定"):
            store.upsert_project(
                self.project_id, "天长地久 MV", "OTHER", self.roots)
        loaded = store.load_project(self.project_id, self.roots)
        self.assertEqual(loaded["project_code"], "TC99")

    def test_plan_payload_cannot_rename_existing_project_code(self):
        project = store.upsert_project(
            self.project_id, "天长地久 MV", "TC99", self.roots)
        plan = {
            "schema": "xiaoyue.mvplan/1.0",
            "project": {
                "id": self.project_id,
                "code": "WRONG",
                "name": "天长地久 MV",
            },
            "audio": {},
            "assets": [],
            "subtitle_source": "",
            "subtitles": {"format": "none"},
        }
        saved = store.record_plan(project, plan, self.roots)
        self.assertEqual(saved["project_code"], "TC99")
        self.assertEqual(plan["project"]["code"], "TC99")

    def test_project_listing_and_plan_record_use_portable_paths(self):
        project = store.upsert_project(
            self.project_id, "天长地久 MV", "TC99", self.roots)
        plan = {
            "schema": "xiaoyue.mvplan/1.0",
            "audio": {
                "fingerprint": "abc123",
                "sample_rate": 48000,
                "total_samples": 96000,
                "timeline_frames": 48,
            },
            "shots": [],
        }
        saved = store.record_plan(project, plan, self.roots)
        manifest = saved["manifest"]
        self.assertEqual(manifest["plan"]["path"], "plans/mv_plan.json")
        self.assertEqual(manifest["audio"]["fingerprint"], "abc123")
        self.assertNotIn(str(Path(self.temporary.name)), json.dumps(manifest))
        projects = store.list_projects(self.roots)
        self.assertEqual(len(projects), 1)
        self.assertTrue(projects[0]["has_plan"])
        self.assertTrue(projects[0]["has_audio"])

    def test_historical_plan_is_loaded_from_project_storage(self):
        project = store.upsert_project(
            self.project_id, "天长地久 MV", "TC99", self.roots)
        plan = {
            "schema": "xiaoyue.mvplan/1.0",
            "project": {"id": self.project_id, "code": "TC99"},
            "audio": {"fingerprint": "master-1"},
            "shots": [{"id": "shot_001", "start_frame": 0, "end_frame": 100}],
        }
        store.record_plan(project, plan, self.roots)
        restored = store.load_project_plan(self.project_id, self.roots)
        self.assertEqual(restored["project"]["id"], self.project_id)
        self.assertEqual(restored["shots"][0]["id"], "shot_001")

    def test_legacy_project_infers_planning_settings_from_its_owned_plan(self):
        project = store.upsert_project(
            self.project_id, "天长地久 MV", "TC99", self.roots)
        plan = {
            "schema": "xiaoyue.mvplan/1.0",
            "project": {"id": self.project_id, "code": "TC99"},
            "audio": {"fingerprint": "master-1"},
            "settings": {"target_shot_seconds": 9.0, "h3_max_frames": 243},
            "shots": [{"id": "shot_001", "start_frame": 0, "end_frame": 100}],
        }
        store.record_plan(project, plan, self.roots)
        manifest_path = Path(project["paths"]["input"]) / "project.json"
        legacy_manifest = json.loads(manifest_path.read_text("utf-8"))
        legacy_manifest.pop("planning_settings", None)
        manifest_path.write_text(
            json.dumps(legacy_manifest, ensure_ascii=False, indent=2), "utf-8")

        reopened = store.load_project(self.project_id, self.roots)

        self.assertEqual(
            reopened["manifest"]["planning_settings"],
            {"target_shot_seconds": 9.0, "h3_max_frames": 243},
        )
        self.assertNotIn(
            "planning_settings",
            json.loads(manifest_path.read_text("utf-8")),
        )

    def test_project_planning_settings_and_owned_audio_source_are_persisted(self):
        project = store.upsert_project(
            self.project_id,
            "天长地久 MV",
            "TC99",
            self.roots,
            planning_settings={"target_shot_seconds": 9.0, "h3_max_frames": 243},
        )
        self.assertEqual(project["manifest"]["planning_settings"]["h3_max_frames"], 243)
        updated = store.update_project_audio_role(
            self.project_id,
            "master",
            {
                "fingerprint": "master-owned",
                "sample_rate": 48000,
                "total_samples": 480000,
                "duration_seconds": 10,
                "source": {
                    "path": "source/audio/master_owned.wav",
                    "format": "media",
                    "original_name": "母带.wav",
                },
            },
            self.roots,
        )
        master = updated["manifest"]["audio"]["master"]
        self.assertEqual(master["fingerprint"], "master-owned")
        self.assertEqual(master["source"]["path"], "source/audio/master_owned.wav")

    def test_existing_project_planning_settings_only_change_through_recorded_replan(self):
        project = store.upsert_project(
            self.project_id,
            "天长地久 MV",
            "TC99",
            self.roots,
            planning_settings={"target_shot_seconds": 9.0, "h3_max_frames": 226},
        )
        reopened = store.upsert_project(
            self.project_id,
            "天长地久 MV",
            "TC99",
            self.roots,
            planning_settings={"target_shot_seconds": 5.0, "h3_max_frames": 124},
        )
        self.assertEqual(
            reopened["manifest"]["planning_settings"],
            {"target_shot_seconds": 9.0, "h3_max_frames": 226},
        )

        plan = {
            "schema": "xiaoyue.mvplan/1.0",
            "project": {"id": self.project_id, "code": "TC99"},
            "settings": {"target_shot_seconds": 5.0, "h3_max_frames": 124},
            "audio": {"fingerprint": "master-1"},
            "shots": [],
        }
        saved = store.record_plan(project, plan, self.roots)
        self.assertEqual(
            saved["manifest"]["planning_settings"],
            {"target_shot_seconds": 5.0, "h3_max_frames": 124},
        )

    def test_generation_spec_is_validated_and_persisted(self):
        project = store.upsert_project(
            self.project_id,
            "天长地久 MV",
            "TC99",
            self.roots,
            generation_spec={
                "aspect_ratio": "9:16 (Portrait Widescreen)",
                "first_pass_megapixels": 0.5,
                "second_pass_megapixels": 1.2,
            },
        )
        self.assertEqual(
            project["manifest"]["generation_spec"]["aspect_ratio"],
            "9:16 (Portrait Widescreen)",
        )
        self.assertEqual(project["manifest"]["generation_spec"]["second_pass_megapixels"], 1.2)

        with self.assertRaisesRegex(ValueError, "ResolutionSelector"):
            store.upsert_project(
                "9d31aaca-259d-4a0a-91ec-3ca4d9a23f42",
                "无效画幅",
                "BAD-AR",
                self.roots,
                generation_spec={"aspect_ratio": "5:4"},
            )

    def test_skill_templates_are_saved_with_project_and_plan(self):
        expected = {
            "visual_outline": "xiaoyue-mv-visual-outline",
            "h3_prompt": "xiaoyue-h3-mv-prompt",
        }
        project = store.upsert_project(
            self.project_id, "天长地久 MV", "TC99", self.roots,
            skill_templates=expected,
        )
        self.assertEqual(project["manifest"]["skill_templates"], expected)
        plan = {
            "project": {"id": self.project_id, "code": "TC99"},
            "skill_templates": expected,
            "shots": [],
        }
        recorded = store.record_plan(project, plan, self.roots)
        self.assertEqual(recorded["manifest"]["skill_templates"], expected)
        reopened = store.load_project(self.project_id, self.roots)
        self.assertEqual(reopened["manifest"]["skill_templates"], expected)
        with self.assertRaisesRegex(ValueError, "二采目标 MP"):
            store.upsert_project(
                "4c794b05-15d5-4c80-b819-b2b8f885f32a",
                "无效双采",
                "BAD-MP",
                self.roots,
                generation_spec={
                    "aspect_ratio": "16:9 (Widescreen)",
                    "first_pass_megapixels": 1.0,
                    "second_pass_megapixels": 0.5,
                },
            )

    def test_project_audio_source_cannot_escape_project_directory(self):
        store.upsert_project(
            self.project_id, "天长地久 MV", "TC99", self.roots)
        with self.assertRaisesRegex(ValueError, "source/audio"):
            store.update_project_audio_role(
                self.project_id,
                "master",
                {
                    "fingerprint": "bad",
                    "sample_rate": 48000,
                    "total_samples": 48000,
                    "duration_seconds": 1,
                    "source": {
                        "path": "../outside.wav",
                        "format": "media",
                        "original_name": "outside.wav",
                    },
                },
                self.roots,
            )

    def test_invalid_project_id_cannot_escape_storage_root(self):
        with self.assertRaises(ValueError):
            store.upsert_project("../escape", "MV", "MV01", self.roots)

    def test_initialization_persists_brief_lyrics_audio_and_assets(self):
        project = store.upsert_project(
            self.project_id,
            "天长地久 MV",
            "TC99",
            self.roots,
            project_brief={
                "description": "跨越四季的重逢。",
                "overall_prompt": "统一冷暖交替与电影摄影。",
                "negative_prompt": "避免角色漂移。",
            },
            lyrics={"source_text": "[00:01.00]第一句", "format": "lrc"},
            assets=[{
                "id": "asset_lead",
                "name": "女主角",
                "category": "character",
                "declaration": "黑色长发，蓝色外套。",
                "source": {
                    "filename": "lead.webp",
                    "subfolder": f"{store.PROJECTS_SUBFOLDER}/{self.project_id}/assets",
                    "type": "input",
                    "media_type": "image",
                },
            }],
            audio={
                "master": {
                    "fingerprint": "master-1",
                    "sample_rate": 48000,
                    "total_samples": 480000,
                    "duration_seconds": 10,
                },
                "alignment": {"status": "not_provided", "basis": "duration_only"},
            },
        )
        manifest = project["manifest"]
        self.assertEqual(manifest["project_brief"]["overall_prompt"], "统一冷暖交替与电影摄影。")
        self.assertEqual(manifest["lyrics"]["source_text"], "[00:01.00]第一句")
        self.assertEqual(manifest["assets"][0]["id"], "asset_lead")
        self.assertEqual(manifest["audio"]["master"]["fingerprint"], "master-1")
        listed = store.list_projects(self.roots)[0]
        self.assertTrue(listed["has_audio"])
        self.assertEqual(listed["asset_count"], 1)

    def test_asset_descriptor_cannot_escape_input(self):
        with self.assertRaises(ValueError):
            store.upsert_project(
                self.project_id,
                "MV",
                "MV01",
                self.roots,
                assets=[{
                    "id": "asset_escape",
                    "name": "越界",
                    "category": "prop",
                    "source": {
                        "filename": "escape.png",
                        "subfolder": "../outside",
                        "type": "input",
                        "media_type": "image",
                    },
                }],
            )


if __name__ == "__main__":
    unittest.main()

