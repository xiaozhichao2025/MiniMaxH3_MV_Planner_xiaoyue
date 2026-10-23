from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import editor_draft_store
import project_store


class EditorDraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.roots = {name: base / name for name in ("input", "output", "temp")}
        for path in self.roots.values():
            path.mkdir()
        self.project_id = "56ce9a4b-e5bc-426b-8d93-355b12a8a693"
        project_store.upsert_project(self.project_id, "测试 MV", "MV01", self.roots)

    def tearDown(self):
        self.temporary.cleanup()

    def test_small_timeline_checkpoint_is_separate_from_full_draft(self):
        plan = {"project": {"id": self.project_id}, "editor_revision": 1,
                "shots": [{"id": "shot_001", "shot_uid": "uid_a", "prompt": "long text"}]}
        editor_draft_store.save_editor_plan(self.project_id, plan, self.roots)
        checkpoint = {"base_revision": 1,
                      "shots": [{"shot_uid": "uid_a", "start_frame": 0, "end_frame": 100}],
                      "transitions": []}
        editor_draft_store.save_timeline_checkpoint(self.project_id, checkpoint, self.roots)
        editor_draft_store.save_shot_patch(self.project_id, {
            "base_revision": 1, "shot_uid": "uid_a", "prompt": "new prompt",
            "creative_outline": "new outline", "singing_performance": True,
        }, self.roots)
        state = editor_draft_store.load_editor_state(self.project_id, self.roots)
        self.assertEqual(state["draft"]["plan"]["shots"][0]["prompt"], "long text")
        self.assertEqual(state["checkpoint"]["shots"][0]["end_frame"], 100)
        self.assertNotIn("prompt", str(state["checkpoint"]))
        self.assertEqual(state["shot_patches"][0]["prompt"], "new prompt")
        next_plan = {**plan, "editor_revision": 2}
        editor_draft_store.save_editor_plan(self.project_id, next_plan, self.roots)
        with self.assertRaisesRegex(ValueError, "早于"):
            editor_draft_store.save_timeline_checkpoint(
                self.project_id, checkpoint, self.roots)
        with self.assertRaisesRegex(ValueError, "早于"):
            editor_draft_store.save_shot_patch(self.project_id, {
                "base_revision": 1, "shot_uid": "uid_a", "prompt": "stale",
                "creative_outline": "", "singing_performance": None,
            }, self.roots)
        with self.assertRaisesRegex(ValueError, "旧编辑草稿"):
            editor_draft_store.save_editor_plan(self.project_id, plan, self.roots)

    def test_new_master_audio_starts_independent_edit_revision(self):
        old = {"project": {"id": self.project_id}, "audio": {"fingerprint": "old"},
               "editor_revision": 9, "shots": []}
        editor_draft_store.save_editor_plan(self.project_id, old, self.roots)
        new = {**old, "audio": {"fingerprint": "new"}, "editor_revision": 1}
        editor_draft_store.save_editor_plan(self.project_id, new, self.roots)
        state = editor_draft_store.load_editor_state(self.project_id, self.roots)
        self.assertEqual(state["draft"]["plan"]["audio"]["fingerprint"], "new")
