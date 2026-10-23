from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generation_store as versions
import editorial_export
import project_store
import shot_workflow


class ShotGenerationStoreTests(unittest.TestCase):
    def test_independent_single_jobs_can_queue_but_chains_and_batches_cannot(self):
        parent_by_id = {"a": "", "b": "a", "c": "b", "d": ""}
        running = [{"project_id": "project", "state": "running", "shot_ids": ["a"]}]
        self.assertEqual(versions.generation_job_conflict(
            running, "project", {"d"}, parent_by_id), "")
        self.assertIn("续写链", versions.generation_job_conflict(
            running, "project", {"b"}, parent_by_id))
        self.assertIn("续写链", versions.generation_job_conflict(
            running, "project", {"c"}, parent_by_id))
        self.assertIn("续写链", versions.generation_job_conflict(
            running, "project", {"a"}, parent_by_id))
        self.assertIn("批量", versions.generation_job_conflict(
            running, "project", {"b", "d"}, parent_by_id))
        self.assertEqual(versions.generation_job_conflict(
            running, "other", {"a"}, parent_by_id), "")
        running[0]["shot_ids"] = ["b"]
        self.assertIn("续写链", versions.generation_job_conflict(
            running, "project", {"a"}, parent_by_id))
        running[0]["shot_ids"] = ["a", "b"]
        self.assertIn("批量", versions.generation_job_conflict(
            running, "project", {"d"}, parent_by_id))
        running = [{"project_id": "project", "state": "running", "shot_ids": [shot_id]}
                   for shot_id in ("a", "d")]
        self.assertIn("续写链", versions.generation_job_conflict(
            running, "project", {"b"}, parent_by_id))

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

    def test_stable_uid_does_not_attach_deleted_shot_versions_to_renumbered_neighbor(self):
        old = versions.begin_version(
            self.project_id, "uid_deleted", editorial_label="02",
            window_id="window_0002", prompt_fingerprint="old",
            window_fingerprint="old-cut", deliver_frames=100,
            roots=self.roots)
        versions.update_version(self.project_id, "uid_deleted", old["id"],
                                status="completed", output={"filename": "old.mp4"},
                                roots=self.roots)
        current = versions.begin_version(
            self.project_id, "uid_survivor", editorial_label="03",
            window_id="window_0003", prompt_fingerprint="current",
            window_fingerprint="same-cut", deliver_frames=100,
            roots=self.roots)
        versions.update_version(self.project_id, "uid_survivor", current["id"],
                                status="completed", output={"filename": "current.mp4"},
                                roots=self.roots)
        state = versions.load_versions(self.project_id, self.roots)
        self.assertEqual(state["shots"]["uid_survivor"]["versions"][0]["id"], current["id"])
        self.assertNotIn(old["id"], [row["id"] for row in
                         state["shots"]["uid_survivor"]["versions"]])
        self.assertIn("uid_deleted", state["shots"])

    def test_fingerprint_ignores_visual_number_but_tracks_parent_identity(self):
        window = {"shot_id": "shot_003", "source_start_frame": 100,
                  "source_end_frame": 222, "raw_frames": 122,
                  "context_frames": 22, "deliver_from_raw_frame": 22,
                  "deliver_frames": 100, "delivery_timeline_range": [100, 200],
                  "core_timeline_range": [100, 200]}
        original = shot_workflow.window_fingerprint(window, {}, "audio", parent_uid="uid_a")
        renumbered = {**window, "shot_id": "shot_002"}
        self.assertEqual(original, shot_workflow.window_fingerprint(
            renumbered, {}, "audio", parent_uid="uid_a"))
        self.assertNotEqual(original, shot_workflow.window_fingerprint(
            renumbered, {}, "audio", parent_uid="uid_other"))
        self.assertNotEqual(original, shot_workflow.window_fingerprint(
            renumbered, {}, "audio", parent_uid=""))

    def test_legacy_manifest_migrates_to_non_positional_key(self):
        path = versions._manifest_path(self.project_id, self.roots)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema": versions.LEGACY_SCHEMA, "project_id": self.project_id,
            "shots": {"shot_001": {"selected_version_id": "old",
                                     "versions": [{"id": "old", "shot_id": "shot_001",
                                                   "status": "completed",
                                                   "window_fingerprint": "legacy-cut"}]}},
        }), encoding="utf-8")
        migrated = versions.load_versions(self.project_id, self.roots)
        self.assertEqual(migrated["schema"], versions.SCHEMA)
        self.assertNotIn("shot_001", migrated["shots"])
        self.assertEqual(migrated["shots"]["legacy_shot_001"]["versions"][0]["shot_uid"],
                         "legacy_shot_001")
        flagged = versions.with_stale_flags(migrated, {
            "legacy_shot_001": {"current": "new-cut", "legacy": "legacy-cut"}})
        self.assertFalse(flagged["shots"]["legacy_shot_001"]["versions"][0]["stale"])

    def test_versions_append_and_selection_marks_old_descendant_stale(self):
        first = versions.begin_version(
            self.project_id, "shot_001", editorial_label="01_a",
            window_id="window_0001", prompt_fingerprint="a",
            window_fingerprint="plan-a", deliver_frames=180,
            roots=self.roots)
        versions.update_version(self.project_id, "shot_001", first["id"],
                                status="completed", output={"filename": "a.mp4"},
                                roots=self.roots)
        child = versions.begin_version(
            self.project_id, "shot_002", editorial_label="01_b",
            window_id="window_0002", prompt_fingerprint="b",
            window_fingerprint="plan-b", deliver_frames=190,
            parent_version_id=first["id"], roots=self.roots)
        versions.update_version(self.project_id, "shot_002", child["id"],
                                status="completed", output={"filename": "b.mp4"},
                                roots=self.roots)
        second = versions.begin_version(
            self.project_id, "shot_001", editorial_label="01_a",
            window_id="window_0001", prompt_fingerprint="c",
            window_fingerprint="plan-a", deliver_frames=180,
            roots=self.roots)
        versions.update_version(self.project_id, "shot_001", second["id"],
                                status="completed", output={"filename": "c.mp4"},
                                roots=self.roots)
        unconfirmed = versions.load_versions(self.project_id, self.roots)
        self.assertFalse(unconfirmed["shots"]["shot_001"].get("selection_confirmed"))
        with self.assertRaisesRegex(ValueError, "明确点选"):
            versions.require_confirmed_selection(unconfirmed, "shot_001", "01_a")
        state = versions.select_version(self.project_id, "shot_001", second["id"], self.roots)
        self.assertTrue(state["shots"]["shot_001"]["selection_confirmed"])
        versions.require_confirmed_selection(state, "shot_001", "01_a")
        flagged = versions.with_stale_flags(state)
        self.assertTrue(flagged["shots"]["shot_002"]["versions"][0]["stale"])
        self.assertEqual(len(flagged["shots"]["shot_001"]["versions"]), 2)
        self.assertTrue(versions.with_stale_flags(state, {"shot_001": "new-plan"})
                        ["shots"]["shot_002"]["versions"][0]["stale"])
        historical = versions.select_version(
            self.project_id, "shot_001", first["id"], self.roots)
        self.assertEqual(historical["shots"]["shot_001"]["selected_version_id"],
                         first["id"])
        with self.assertRaises(ValueError):
            versions.update_version(self.project_id, "shot_001", first["id"],
                                    status="failed", roots=self.roots)

    def test_cannot_choose_unfinished_version(self):
        pending = versions.begin_version(
            self.project_id, "shot_001", editorial_label="01",
            window_id="window_0001", prompt_fingerprint="a",
            window_fingerprint="plan-a", deliver_frames=100,
            raw_frames=124, deliver_from_raw_frame=12, seed=42,
            roots=self.roots)
        self.assertEqual(pending["frame_space"], "raw")
        self.assertEqual(pending["seed"], 42)
        with self.assertRaises(ValueError):
            versions.select_version(self.project_id, "shot_001", pending["id"], self.roots)
        recovered = versions.recover_interrupted_versions(self.project_id, self.roots)
        self.assertEqual(recovered["shots"]["shot_001"]["versions"][0]["status"], "failed")

    def test_each_version_snapshots_its_own_absolute_timeline_cut_points(self):
        first = versions.begin_version(
            self.project_id, "shot_005", editorial_label="05_b",
            window_id="window_0005", prompt_fingerprint="a",
            window_fingerprint="old-plan", deliver_frames=125,
            raw_frames=158, deliver_from_raw_frame=22,
            timeline_in_frame=144, timeline_out_frame_exclusive=269,
            roots=self.roots)
        versions.update_version(self.project_id, "shot_005", first["id"],
                                status="completed", output={"filename": "first.mp4"},
                                roots=self.roots)
        second = versions.begin_version(
            self.project_id, "shot_005", editorial_label="05_b",
            window_id="window_0005", prompt_fingerprint="b",
            window_fingerprint="new-plan", deliver_frames=125,
            raw_frames=158, deliver_from_raw_frame=22,
            timeline_in_frame=145, timeline_out_frame_exclusive=270,
            roots=self.roots)
        self.assertEqual((first["timeline_in_frame"], first["timeline_out_frame_exclusive"]),
                         (144, 269))
        self.assertEqual((second["timeline_in_frame"], second["timeline_out_frame_exclusive"]),
                         (145, 270))
        state = versions.load_versions(self.project_id, self.roots)
        self.assertEqual(state["shots"]["shot_005"]["versions"][0]["timeline_in_frame"], 144)
        with self.assertRaisesRegex(ValueError, "总轨记录点"):
            versions.begin_version(
                self.project_id, "shot_006", editorial_label="06",
                window_id="window_0006", prompt_fingerprint="x",
                window_fingerprint="plan", deliver_frames=125,
                timeline_in_frame=145, timeline_out_frame_exclusive=269,
                roots=self.roots)

    def test_failed_cancelled_and_interrupted_attempts_do_not_consume_video_number(self):
        def begin():
            return versions.begin_version(
                self.project_id, "shot_001", editorial_label="01",
                window_id="window_0001", prompt_fingerprint="a",
                window_fingerprint="plan-a", deliver_frames=100,
                roots=self.roots)

        failed = begin()
        versions.update_version(self.project_id, "shot_001", failed["id"],
                                status="failed", error="CUDA out of memory",
                                roots=self.roots)
        cancelled = begin()
        versions.update_version(self.project_id, "shot_001", cancelled["id"],
                                status="cancelled", error="用户取消",
                                roots=self.roots)
        interrupted = begin()
        versions.recover_interrupted_versions(self.project_id, self.roots)
        successful = begin()
        completed = versions.update_version(
            self.project_id, "shot_001", successful["id"],
            status="completed", output={"filename": "v1.mp4"},
            roots=self.roots)
        self.assertEqual([failed["attempt_number"], cancelled["attempt_number"],
                          interrupted["attempt_number"], successful["attempt_number"]],
                         [1, 2, 3, 4])
        self.assertEqual(completed["number"], 1)
        state = versions.load_versions(self.project_id, self.roots)
        rows = state["shots"]["shot_001"]["versions"]
        self.assertEqual([row["number"] for row in rows], [None, None, None, 1])
        self.assertEqual(rows[0]["error"], "CUDA out of memory")
        self.assertEqual(rows[2]["status"], "failed")
        self.assertEqual(rows[2]["error"], "ComfyUI 服务重启，旧生成任务已中断；可重新生成。")

    def test_recover_old_failed_output_uses_next_unused_success_number(self):
        first = versions.begin_version(
            self.project_id, "shot_001", editorial_label="01",
            window_id="window_0001", prompt_fingerprint="a",
            window_fingerprint="plan-a", deliver_frames=100,
            roots=self.roots)
        versions.update_version(self.project_id, "shot_001", first["id"],
                                status="running", prompt_id="prompt-1",
                                roots=self.roots)
        versions.update_version(
            self.project_id, "shot_001", first["id"], status="failed",
            error="工作流已结束，但 SaveVideo 没有返回视频文件。", roots=self.roots)
        second = versions.begin_version(
            self.project_id, "shot_001", editorial_label="01",
            window_id="window_0001", prompt_fingerprint="b",
            window_fingerprint="plan-a", deliver_frames=100,
            roots=self.roots)
        completed = versions.update_version(
            self.project_id, "shot_001", second["id"],
            status="completed", output={"filename": "v1.mp4"}, roots=self.roots)
        self.assertEqual(completed["number"], 1)
        recovered = versions.recover_misclassified_output(
            self.project_id, "shot_001", first["id"], prompt_id="prompt-1",
            output={"filename": "recovered.mp4"}, roots=self.roots)
        self.assertEqual(recovered["number"], 2)

    def test_new_successful_alternative_requires_fresh_choice(self):
        first = versions.begin_version(
            self.project_id, "shot_001", editorial_label="01",
            window_id="window_0001", prompt_fingerprint="a",
            window_fingerprint="plan-a", deliver_frames=100,
            roots=self.roots)
        versions.update_version(self.project_id, "shot_001", first["id"],
                                status="completed", output={"filename": "a.mp4"},
                                roots=self.roots)
        versions.select_version(self.project_id, "shot_001", first["id"], self.roots)
        second = versions.begin_version(
            self.project_id, "shot_001", editorial_label="01",
            window_id="window_0001", prompt_fingerprint="b",
            window_fingerprint="plan-a", deliver_frames=100,
            roots=self.roots)
        versions.update_version(self.project_id, "shot_001", second["id"],
                                status="completed", output={"filename": "b.mp4"},
                                roots=self.roots)
        state = versions.load_versions(self.project_id, self.roots)
        self.assertFalse(state["shots"]["shot_001"]["selection_confirmed"])
        with self.assertRaisesRegex(ValueError, "明确点选"):
            versions.require_confirmed_selection(state, "shot_001", "01")

    def test_batch_regeneration_selects_fresh_parents_through_three_part_chain(self):
        def complete(shot_id, label, parent_id="", preserve_existing_selection=False):
            item = versions.begin_version(
                self.project_id, shot_id, editorial_label=label,
                window_id=f"window_{shot_id}", prompt_fingerprint="prompt",
                window_fingerprint=f"cut_{shot_id}", deliver_frames=100,
                parent_version_id=parent_id, roots=self.roots)
            return versions.update_version(
                self.project_id, shot_id, item["id"], status="completed",
                output={"filename": f"{item['id']}.mp4"},
                preserve_existing_selection=preserve_existing_selection,
                roots=self.roots)

        old_a = complete("shot_001", "01_a")
        old_b = complete("shot_002", "01_b", old_a["id"])
        old_c = complete("shot_003", "01_c", old_b["id"])
        new_a = complete("shot_001", "01_a", preserve_existing_selection=True)
        middle_state = versions.with_stale_flags(
            versions.load_versions(self.project_id, self.roots))
        self.assertEqual(versions.resolve_parent_version(
            middle_state, "shot_001", "01_a", new_a)["id"], new_a["id"])
        new_b = complete("shot_002", "01_b", new_a["id"],
                         preserve_existing_selection=True)
        self.assertEqual(versions.resolve_parent_version(
            versions.with_stale_flags(versions.load_versions(
                self.project_id, self.roots)), "shot_002", "01_b", new_b)["id"],
            new_b["id"])
        new_c = complete("shot_003", "01_c", new_b["id"])
        parent_by_id = {"shot_002": "shot_001", "shot_003": "shot_002"}
        shot_uid_by_id = {shot_id: shot_id for shot_id in (
            "shot_001", "shot_002", "shot_003")}
        fresh = {"shot_001": new_a, "shot_002": new_b, "shot_003": new_c}
        self.assertEqual(versions.completed_batch_parent_selections(
            list(fresh), parent_by_id, shot_uid_by_id, fresh),
            {"shot_001": new_a["id"], "shot_002": new_b["id"]})
        before_commit = versions.load_versions(self.project_id, self.roots)
        self.assertEqual(before_commit["shots"]["shot_001"]["selected_version_id"],
                         old_a["id"])
        self.assertEqual(before_commit["shots"]["shot_002"]["selected_version_id"],
                         old_b["id"])
        self.assertTrue(before_commit["shots"]["shot_001"]["selection_confirmed"])
        self.assertTrue(before_commit["shots"]["shot_002"]["selection_confirmed"])
        selections = versions.completed_batch_parent_selections(
            list(fresh), parent_by_id, shot_uid_by_id, fresh)
        versions.select_versions(self.project_id, selections, self.roots)
        state = versions.with_stale_flags(
            versions.load_versions(self.project_id, self.roots))
        self.assertEqual(state["shots"]["shot_001"]["selected_version_id"], new_a["id"])
        self.assertEqual(state["shots"]["shot_002"]["selected_version_id"], new_b["id"])
        self.assertTrue(state["shots"]["shot_001"]["selection_confirmed"])
        self.assertTrue(state["shots"]["shot_002"]["selection_confirmed"])
        self.assertEqual(state["shots"]["shot_003"]["selected_version_id"], old_c["id"])
        self.assertFalse(state["shots"]["shot_003"]["selection_confirmed"])
        self.assertTrue(state["shots"]["shot_002"]["versions"][0]["stale"])
        self.assertTrue(state["shots"]["shot_003"]["versions"][0]["stale"])
        self.assertFalse(state["shots"]["shot_003"]["versions"][1]["stale"])
        with self.assertRaisesRegex(ValueError, "明确点选"):
            versions.resolve_parent_version(state, "shot_003", "01_c")
        self.assertEqual(versions.resolve_parent_version(
            state, "shot_003", "01_c", new_c)["id"], new_c["id"])

    def test_failed_batch_descendant_preserves_previous_final_chain(self):
        selected = ["shot_001", "shot_002", "shot_003"]
        parents = {"shot_002": "shot_001", "shot_003": "shot_002"}
        uids = {shot_id: shot_id for shot_id in selected}
        partial = {
            "shot_001": {"id": "new_a", "status": "completed"},
            "shot_002": {"id": "new_b", "status": "completed"},
        }
        self.assertEqual(versions.completed_batch_parent_selections(
            selected, parents, uids, partial), {})
        partial["shot_003"] = {"id": "new_c", "status": "completed"}
        self.assertEqual(versions.completed_batch_parent_selections(
            selected, parents, uids, partial),
            {"shot_001": "new_a", "shot_002": "new_b"})

    def test_unselected_batch_parent_still_requires_explicit_version_choice(self):
        first = versions.begin_version(
            self.project_id, "shot_001", editorial_label="01_a",
            window_id="window_0001", prompt_fingerprint="one",
            window_fingerprint="cut", deliver_frames=100, roots=self.roots)
        versions.update_version(self.project_id, "shot_001", first["id"],
                                status="completed", output={"filename": "one.mp4"},
                                roots=self.roots)
        second = versions.begin_version(
            self.project_id, "shot_001", editorial_label="01_a",
            window_id="window_0001", prompt_fingerprint="two",
            window_fingerprint="cut", deliver_frames=100, roots=self.roots)
        fresh = versions.update_version(
            self.project_id, "shot_001", second["id"], status="completed",
            output={"filename": "two.mp4"}, roots=self.roots)
        state = versions.with_stale_flags(
            versions.load_versions(self.project_id, self.roots))
        with self.assertRaisesRegex(ValueError, "明确点选"):
            versions.resolve_parent_version(state, "shot_001", "01_a")
        self.assertEqual(versions.resolve_parent_version(
            state, "shot_001", "01_a", fresh)["id"], fresh["id"])
        state = versions.select_version(
            self.project_id, "shot_001", second["id"], self.roots)
        self.assertEqual(versions.resolve_parent_version(
            state, "shot_001", "01_a")["id"], second["id"])

    def test_recover_savevideo_output_misclassified_as_missing(self):
        version = versions.begin_version(
            self.project_id, "shot_001", editorial_label="01",
            window_id="window_0001", prompt_fingerprint="a",
            window_fingerprint="plan-a", deliver_frames=100,
            roots=self.roots)
        versions.update_version(self.project_id, "shot_001", version["id"],
                                status="running", prompt_id="prompt-1",
                                roots=self.roots)
        versions.update_version(
            self.project_id, "shot_001", version["id"], status="failed",
            error="工作流已结束，但 SaveVideo 没有返回视频文件。",
            roots=self.roots)
        with self.assertRaises(ValueError):
            versions.recover_misclassified_output(
                self.project_id, "shot_001", version["id"],
                prompt_id="wrong", output={"filename": "shot.mp4"},
                roots=self.roots)
        fixed = versions.recover_misclassified_output(
            self.project_id, "shot_001", version["id"],
            prompt_id="prompt-1", output={"filename": "shot.mp4"},
            roots=self.roots)
        self.assertEqual(fixed["status"], "completed")
        self.assertEqual(fixed["output"]["filename"], "shot.mp4")
        state = versions.load_versions(self.project_id, self.roots)
        self.assertEqual(state["shots"]["shot_001"]["selected_version_id"],
                         version["id"])


class WorkflowConversionTests(unittest.TestCase):
    def test_savevideo_mp4_in_images_is_a_video_result(self):
        history = {"outputs": {"5": {"images": [{
            "filename": "SC01_v1_00001_.mp4", "type": "output",
            "subfolder": "project/generations",
            "metadata": {"kind": "video", "frame_count": 209}}]}}}
        self.assertEqual(
            shot_workflow.history_video_descriptor(history)["filename"],
            "SC01_v1_00001_.mp4")

    def test_savevideo_does_not_accept_still_image_as_video(self):
        history = {"outputs": {"5": {"images": [{
            "filename": "preview.png", "type": "output"}]}}}
        self.assertIsNone(shot_workflow.history_video_descriptor(history))

    def test_linked_widgets_keep_widget_order_and_unrelated_nodes_are_pruned(self):
        graph = {
            "nodes": [
                {"id": 2, "type": "Text", "inputs": [{"name": "value", "link": None}],
                 "widgets_values": ["hello"]},
                {"id": 3, "type": "Encoder", "inputs": [
                    {"name": "prompt", "link": 1}, {"name": "length", "link": None}],
                 "widgets_values": ["stale text", 209]},
                {"id": 5, "type": "SaveVideo", "inputs": [
                    {"name": "video", "link": 2}, {"name": "filename_prefix", "link": None}],
                 "widgets_values": ["video/old"]},
                {"id": 99, "type": "MissingUnused", "inputs": []},
            ],
            "links": [[1, 2, 0, 3, 0, "STRING"], [2, 3, 0, 5, 0, "VIDEO"]],
        }
        info = {
            "Text": {"input": {"required": {"value": ["STRING", {}]}}},
            "Encoder": {"input": {"required": {
                "prompt": ["STRING", {}], "length": ["INT", {}]}}},
            "SaveVideo": {"input": {"required": {
                "video": ["VIDEO", {}], "filename_prefix": ["STRING", {}]}}},
        }
        api = shot_workflow.ui_to_api_workflow(graph, info)
        self.assertNotIn("99", api)
        self.assertEqual(api["3"]["inputs"], {"prompt": ["2", 0], "length": 209})
        self.assertEqual(api["5"]["inputs"]["filename_prefix"], "video/old")

    def test_user_bypassed_passthrough_node_is_not_executed(self):
        graph = {"nodes": [
            {"id": 2, "type": "Text", "inputs": [
                {"name": "value", "link": None}], "widgets_values": ["hello"]},
            {"id": 4, "type": "PassThrough", "mode": 4, "inputs": [
                {"name": "source", "type": "STRING", "link": 1}],
             "outputs": [{"type": "STRING"}]},
            {"id": 5, "type": "SaveVideo", "mode": 2, "inputs": [
                {"name": "video", "link": 2}]},
        ], "links": [[1, 2, 0, 4, 0, "STRING"],
                     [2, 4, 0, 5, 0, "STRING"]]}
        info = {
            "Text": {"input": {"required": {"value": ["STRING", {}]}}},
            "SaveVideo": {"input": {"required": {"video": ["STRING", {}]}}},
        }
        api = shot_workflow.ui_to_api_workflow(graph, info)
        self.assertNotIn("4", api)
        self.assertEqual(api["5"]["inputs"]["video"], ["2", 0])


class ShotWorkflowSpliceTests(unittest.TestCase):
    def test_visible_template_is_the_live_backbone_source(self):
        template = (Path(__file__).resolve().parents[1] / "workflows" /
                    "MiniMaxH3_MV_Planner_xiaoyue_MV工作台v1.0.json")
        canvas = json.loads(template.read_text(encoding="utf-8"))
        self.assertNotIn(1008, {group["id"] for group in canvas["groups"]})
        self.assertFalse({2149, 2150} & {node["id"] for node in canvas["nodes"]})
        canvas_links = {int(link[0]): link for link in canvas["links"]}
        self.assertEqual(canvas_links[1065][1:5], [25, 0, 34, 0])
        self.assertEqual(canvas_links[1067][1:5], [2145, 1, 34, 1])
        self.assertFalse({1066, 1068} & set(canvas_links))
        notes = "\n".join(str(node.get("widgets_values", "")) for node in canvas["nodes"]
                          if node["type"] == "MarkdownNote")
        self.assertIn("最终合成由工作台验收剪辑台", notes)
        self.assertNotIn("裁切节点保持", notes)
        self.assertEqual(sum(node["type"] == "MiniMaxH3_MV_Planner_xiaoyue"
                             for node in canvas["nodes"]), 1)
        output = next(node for node in canvas["nodes"]
                      if (node.get("properties") or {}).get(
                          shot_workflow.BACKBONE_NODE_PROPERTY) == 5)
        self.assertEqual(output["type"], "SaveVideo")
        self.assertEqual(output["mode"], 2)
        normalized = shot_workflow.canvas_backbone(canvas)
        self.assertIn(5, {node["id"] for node in normalized["nodes"]})
        nodes_by_id = {node["id"]: node for node in normalized["nodes"]}
        self.assertEqual(nodes_by_id[130]["outputs"][6]["name"], "raw_frames")
        links = {int(link[0]): link for link in normalized["links"]}
        for h3_id in (11, 71):
            length = next(item for item in nodes_by_id[h3_id]["inputs"]
                          if item["name"] == "length")
            self.assertEqual(links[length["link"]][1:3], [130, 6])
        preview = next(item for item in nodes_by_id[12]["inputs"]
                       if item["name"] == "preview_frames")
        self.assertEqual(links[preview["link"]][1:3], [130, 6])
        self.assertNotIn(19, nodes_by_id)
        self.assertNotIn(17, nodes_by_id)
        self.assertNotIn(135, nodes_by_id)
        self.assertNotIn(136, nodes_by_id)
        self.assertNotIn("MiniMaxH3_MV_Planner_xiaoyue",
                         {node["type"] for node in normalized["nodes"]})
        self.assertEqual(nodes_by_id[130]["type"], "Xiaoyue_MV_ShotInput")
        self.assertEqual(nodes_by_id[130]["widgets_values"], [-1])
        self.assertNotIn(1002, {node["id"] for node in normalized["nodes"]})
        self.assertEqual([entry["name"] for entry in nodes_by_id[130]["inputs"]],
                         ["seed"])
        self.assertNotIn(1001, {link[0] for link in normalized["links"]})
        node_info = {node["type"]: {"input": {"required": {}}}
                     for node in normalized["nodes"]}
        graph = shot_workflow.ui_to_api_workflow(normalized, node_info)
        self.assertEqual(graph["11"]["inputs"]["length"], ["130", 6])
        self.assertEqual(graph["5"]["inputs"]["filename_prefix"], ["130", 8])
        self.assertNotIn("MiniMaxH3_MV_Planner_xiaoyue",
                         {entry["class_type"] for entry in graph.values()})
        queued = shot_workflow.compose_shot_workflow(
            normalized, node_info, prompt="test shot",
            window={"raw_frames": 226, "deliver_from_raw_frame": 0,
                    "deliver_frames": 204, "context_frames": 0},
            generation_spec={"aspect_ratio": "16:9 (Widescreen)",
                             "first_pass_megapixels": 0.4,
                             "second_pass_megapixels": 0.9},
            audio_input_name="project/audio/shot.wav", visual_inputs=[],
            output_prefix="project/video/shot_v1", seed=42)
        self.assertEqual(queued["130"]["class_type"], "Xiaoyue_MV_RuntimeShot")
        self.assertEqual(queued["130"]["inputs"]["seed"], 42)
        self.assertNotIn("1002", queued)
        self.assertTrue({130, 131, 132, 133, 134, 137}.issubset(
            {node["id"] for node in normalized["nodes"]}))
        self.assertEqual(len(shot_workflow.canvas_workflow_fingerprint(normalized)), 64)

    def test_merged_workbench_reads_live_seed_and_keeps_old_adapter(self):
        template = (Path(__file__).resolve().parents[1] / "workflows" /
                    "MiniMaxH3_MV_Planner_xiaoyue_MV工作台v1.0.json")
        canvas = json.loads(template.read_text(encoding="utf-8"))
        main = next(node for node in canvas["nodes"] if node["id"] == 1002)
        main["widgets_values"][5] = 42
        main["widgets_values_named"]["seed"] = -1
        normalized = shot_workflow.canvas_backbone(canvas)
        self.assertEqual(shot_workflow.shot_seed_setting(normalized), 42)

    def test_canvas_requires_a_marked_visible_output(self):
        with self.assertRaisesRegex(ValueError, "画布里没有"):
            shot_workflow.canvas_backbone({"nodes": [], "links": []})

    def test_visible_seed_control_supports_random_and_fixed(self):
        ui = {"nodes": [{"id": 130, "inputs": [
            {"name": "seed", "widget": {"name": "seed"}, "link": None}],
            "widgets_values": [-1]}]}
        self.assertEqual(shot_workflow.shot_seed_setting(ui), -1)
        ui["nodes"][0]["widgets_values"][0] = 42
        self.assertEqual(shot_workflow.shot_seed_setting(ui), 42)
        ui["nodes"][0]["inputs"].insert(0, {
            "name": "shot_id", "widget": {"name": "shot_id"}, "link": None})
        ui["nodes"][0]["widgets_values"] = ["old_shot", 43]
        self.assertEqual(shot_workflow.shot_seed_setting(ui), 43)
        ui["nodes"][0]["inputs"].pop(0)
        ui["nodes"][0]["inputs"][0]["link"] = 9
        with self.assertRaisesRegex(ValueError, "外接种子"):
            shot_workflow.shot_seed_setting(ui)

    def test_new_user_node_in_backbone_chain_is_retained(self):
        marker = shot_workflow.BACKBONE_NODE_PROPERTY
        canvas = {"nodes": [
            {"id": 82, "type": "Text", "properties": {marker: 2},
             "inputs": []},
            {"id": 400, "type": "CustomFilter", "inputs": [
                {"name": "source", "link": 10}]},
            {"id": 501, "type": "SaveVideo", "properties": {marker: 5},
             "inputs": [{"name": "video", "link": 11}]},
            {"id": 999, "type": "MiniMaxH3_MV_Planner_xiaoyue", "inputs": []},
        ], "links": [
            [10, 82, 0, 400, 0, "STRING"],
            [11, 400, 0, 501, 0, "VIDEO"],
        ]}
        normalized = shot_workflow.canvas_backbone(canvas)
        self.assertEqual({node["id"] for node in normalized["nodes"]},
                         {2, 5, 20000})
        self.assertEqual(normalized["links"][1][1:4], [20000, 0, 5])
        baseline = shot_workflow.canvas_workflow_fingerprint(normalized)
        normalized["nodes"][0]["pos"] = [400, 300]
        self.assertEqual(shot_workflow.canvas_workflow_fingerprint(normalized), baseline)

    def test_marked_custom_saver_can_be_the_output_boundary(self):
        marker = shot_workflow.BACKBONE_NODE_PROPERTY
        canvas = {"nodes": [
            {"id": 130, "type": "Xiaoyue_MV_ShotInput",
             "properties": {marker: 130}, "inputs": []},
            {"id": 888, "type": "UserSaveVideo",
             "properties": {marker: 5}, "inputs": [
                 {"name": "directory", "link": 1},
                 {"name": "filename", "link": 2}]},
        ], "links": [[1, 130, 17, 888, 0, "STRING"],
                     [2, 130, 18, 888, 1, "STRING"]]}
        normalized = shot_workflow.canvas_backbone(canvas)
        self.assertEqual({item["id"]: item["type"]
                          for item in normalized["nodes"]}[5], "UserSaveVideo")

    @staticmethod
    def backbone_stub():
        ids = (2, 3, 4, 5, 9, 11, 16, 17, 20, 21, 25, 29, 31, 32,
               34, 69, 71, 72, 74, 99, 101, 130, 131, 132,
               133, 134, 137, 20000)
        graph = {str(node_id): {"class_type": "Stub", "inputs": {}}
                 for node_id in ids}
        graph["5"]["inputs"]["video"] = ["34", 0]
        graph["5"]["inputs"]["filename_prefix"] = ["130", 8]
        graph["11"]["class_type"] = "MiniMaxH3ReferenceToVideo"
        graph["71"]["class_type"] = "MiniMaxH3ReferenceToVideo"
        graph["137"] = {"class_type": "Xiaoyue_MV_ReferenceSlots",
                        "inputs": {"reference_manifest_json": ["130", 9],
                                   "shot_audio": ["130", 1]}}
        graph["11"]["inputs"].update({"prompt": ["130", 0],
                                       "length": ["130", 6],
                                       "ref_images.ref_image_0": ["137", 0],
                                       "ref_audios.ref_audio_0": ["137", 12]})
        graph["71"]["inputs"].update({"prompt": ["74", 0],
                                       "length": ["130", 6],
                                       "ref_images.ref_image_0": ["137", 0],
                                       "ref_audios.ref_audio_0": ["137", 12]})
        graph["74"]["inputs"]["text2"] = ["130", 0]
        graph["130"]["inputs"]["mv_plan"] = ["20000", 0]
        graph["132"]["inputs"].update({
            "video": ["130", 12],
            "frame_load_cap": ["130", 13],
            "skip_first_frames": ["130", 14],
        })
        graph["131"]["inputs"].update({"av_latent": ["11", 1],
                                        "source_audio": ["130", 1],
                                        "audio_vae": ["4", 0]})
        graph["21"]["inputs"]["latent_image"] = ["131", 0]
        graph["21"]["inputs"]["guider"] = ["29", 0]
        graph["133"]["inputs"].update({"positive": ["69", 0],
                                        "latent": ["131", 0],
                                        "image": ["132", 0]})
        graph["29"]["inputs"]["conditioning"] = ["133", 0]
        graph["134"]["inputs"].update({"positive": ["72", 0],
                                        "latent": ["99", 0],
                                        "image": ["132", 0]})
        graph["32"]["inputs"]["conditioning"] = ["134", 0]
        graph["16"]["inputs"].update({"guider": ["32", 0],
                                       "latent_image": ["99", 0]})
        graph["99"]["inputs"]["highres_latent"] = ["71", 1]
        graph["99"]["inputs"]["lowres_latent"] = ["21", 0]
        graph["25"]["inputs"]["samples"] = ["16", 0]
        graph["34"]["inputs"].update({"images": ["25", 0],
                                       "audio": ["131", 1]})
        return graph

    def compose(self, *, context=0, rewired=False,
                parent_lead=22, parent_deliver=204):
        deliver = min(204, 226 - context)
        window = {
            "raw_frames": 226,
            "deliver_from_raw_frame": context,
            "deliver_frames": deliver,
            "context_frames": context,
        }
        spec = {
            "aspect_ratio": "16:9 (Widescreen)",
            "first_pass_megapixels": 0.4,
            "second_pass_megapixels": 0.9,
        }
        backbone = self.backbone_stub()
        if not context:
            backbone["29"]["inputs"]["conditioning"] = ["69", 0]
            backbone["32"]["inputs"]["conditioning"] = ["72", 0]
        if rewired:
            backbone["131"]["inputs"]["av_latent"] = ["20020", 0]
            backbone["133"]["inputs"]["positive"] = ["20021", 0]
            backbone["134"]["inputs"]["positive"] = ["20022", 0]
            backbone["134"]["inputs"]["latent"] = ["20023", 0]
            backbone["34"]["inputs"]["images"] = ["20024", 0]
            upstream = {20020: ["11", 1], 20021: ["69", 0],
                        20022: ["72", 0], 20023: ["99", 0],
                        20024: ["25", 0]}
            for node_id in range(20020, 20025):
                backbone[str(node_id)] = {"class_type": "UserNode", "inputs": {
                    "source": upstream[node_id]}}
        with patch.object(shot_workflow, "ui_to_api_workflow",
                          return_value=copy.deepcopy(backbone)):
            return shot_workflow.compose_shot_workflow(
                {}, {}, prompt="singing performance", window=window,
                generation_spec=spec, audio_input_name="shot.wav",
                visual_inputs=[{"media_type": "image", "input_name": "hero.png"}],
                output_prefix="project/shot_v1",
                parent_video_path="parent.mp4" if context else "",
                parent_deliver_frames=parent_deliver if context else 0,
                parent_deliver_from_raw_frame=parent_lead if context else 0)

    def test_audio_drives_both_passes_and_saves_full_h3_window(self):
        graph = self.compose()
        self.assertEqual(graph["11"]["inputs"]["ref_audios.ref_audio_0"], ["137", 12])
        self.assertEqual(graph["71"]["inputs"]["ref_audios.ref_audio_0"], ["137", 12])
        self.assertEqual(graph["130"]["class_type"], "Xiaoyue_MV_RuntimeShot")
        self.assertEqual(graph["130"]["inputs"]["shot_audio"], ["10000", 0])
        self.assertEqual(graph["21"]["inputs"]["latent_image"], ["131", 0])
        self.assertEqual(graph["131"]["inputs"]["av_latent"], ["11", 1])
        self.assertEqual(graph["131"]["inputs"]["source_audio"], ["130", 1])
        self.assertEqual(graph["11"]["inputs"]["ref_images.ref_image_0"], ["137", 0])
        self.assertEqual(graph["71"]["inputs"]["ref_images.ref_image_0"], ["137", 0])
        self.assertEqual(graph["34"]["inputs"]["images"], ["25", 0])
        self.assertEqual(graph["34"]["inputs"]["audio"], ["131", 1])
        self.assertNotIn("135", graph)
        self.assertNotIn("136", graph)
        self.assertNotIn("132", graph)
        self.assertIn("130", graph)
        self.assertNotIn("2", graph)
        self.assertNotIn("20000", graph)

    def test_router_identifies_only_the_connected_h3_pass(self):
        single = self.backbone_stub()
        single["99"]["inputs"]["highres_latent"] = ["11", 1]
        single["11"]["inputs"]["length"] = ["130", 6]
        single["71"]["inputs"]["length"] = 999  # Detached pass is irrelevant.
        with patch.object(shot_workflow, "ui_to_api_workflow",
                          return_value=copy.deepcopy(single)):
            graph = shot_workflow.compose_shot_workflow(
                {}, {}, prompt="one pass", window={"raw_frames": 226,
                "deliver_from_raw_frame": 0, "deliver_frames": 204,
                "context_frames": 0}, generation_spec={
                "aspect_ratio": "16:9 (Widescreen)",
                "first_pass_megapixels": 0.4,
                "second_pass_megapixels": 0.9},
                audio_input_name="project/runtime_audio/shot.wav",
                visual_inputs=[], output_prefix="project/generations/shot_v1",
                seed=42)
        self.assertIn("11", graph)
        self.assertNotIn("71", graph)
        self.assertEqual(graph["130"]["inputs"]["input_subfolder"],
                         "project/runtime_audio")
        self.assertEqual(graph["130"]["inputs"]["shot_audio_input_name"],
                         "project/runtime_audio/shot.wav")
        self.assertEqual(graph["130"]["inputs"]["output_subfolder"],
                         "project/generations")
        self.assertEqual(graph["130"]["inputs"]["output_filename_stem"],
                         "shot_v1")

    def test_two_routed_passes_are_each_checked_without_fixed_node_ids(self):
        backbone = self.backbone_stub()
        backbone["71"]["inputs"]["length"] = 999
        with patch.object(shot_workflow, "ui_to_api_workflow",
                          return_value=copy.deepcopy(backbone)):
            with self.assertRaisesRegex(ValueError, "H3 节点 71.*原始帧数"):
                shot_workflow.compose_shot_workflow(
                    {}, {}, prompt="two passes", window={"raw_frames": 226,
                    "deliver_from_raw_frame": 0, "deliver_frames": 204,
                    "context_frames": 0}, generation_spec={
                    "aspect_ratio": "16:9 (Widescreen)",
                    "first_pass_megapixels": 0.4,
                    "second_pass_megapixels": 0.9},
                    audio_input_name="shot.wav", visual_inputs=[],
                    output_prefix="project/shot", seed=42)

    def test_custom_generator_and_output_keep_explicit_user_wires(self):
        backbone = {
            "5": {"class_type": "UserSaveVideo", "inputs": {
                "video": ["300", 0], "directory": ["130", 17],
                "filename": ["130", 18]}},
            "300": {"class_type": "UserOnePassGenerator", "inputs": {
                "prompt": ["130", 0], "frames": ["130", 6],
                "audio": ["130", 1]}},
            "130": {"class_type": "Xiaoyue_MV_ShotInput", "inputs": {}},
        }
        with patch.object(shot_workflow, "ui_to_api_workflow",
                          return_value=copy.deepcopy(backbone)):
            graph = shot_workflow.compose_shot_workflow(
                {}, {}, prompt="custom", window={"raw_frames": 226,
                "deliver_from_raw_frame": 0, "deliver_frames": 204,
                "context_frames": 0}, generation_spec={
                "aspect_ratio": "16:9 (Widescreen)",
                "first_pass_megapixels": 0.4,
                "second_pass_megapixels": 0.9},
                audio_input_name="project/audio/shot.wav", visual_inputs=[],
                output_prefix="project/videos/shot_v1", seed=42)
        self.assertEqual(graph["5"]["inputs"]["directory"], ["130", 17])
        self.assertEqual(graph["5"]["inputs"]["filename"], ["130", 18])
        self.assertEqual(graph["300"]["inputs"]["frames"], ["130", 6])

    def test_output_without_workbench_naming_wire_is_rejected(self):
        backbone = self.backbone_stub()
        backbone["5"]["inputs"].pop("filename_prefix")
        with patch.object(shot_workflow, "ui_to_api_workflow",
                          return_value=copy.deepcopy(backbone)):
            with self.assertRaisesRegex(ValueError, "视频输出节点必须"):
                shot_workflow.compose_shot_workflow(
                    {}, {}, prompt="shot", window={"raw_frames": 226,
                    "deliver_from_raw_frame": 0, "deliver_frames": 204,
                    "context_frames": 0}, generation_spec={
                    "aspect_ratio": "16:9 (Widescreen)",
                    "first_pass_megapixels": 0.4,
                    "second_pass_megapixels": 0.9},
                    audio_input_name="shot.wav", visual_inputs=[],
                    output_prefix="project/shot", seed=42)

    def test_rejects_absolute_or_parent_traversal_path_ports(self):
        for candidate in ("../shot", "/tmp/shot", "C:/tmp/shot"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    shot_workflow._relative_parts(candidate, "输出前缀")

    def test_continuation_guides_use_delivery_tail_inside_raw_parent(self):
        graph = self.compose(context=22)
        self.assertEqual(graph["132"]["inputs"]["video"], ["130", 12])
        self.assertEqual(graph["132"]["inputs"]["frame_load_cap"], ["130", 13])
        self.assertEqual(graph["132"]["inputs"]["skip_first_frames"], ["130", 14])
        self.assertEqual(graph["130"]["inputs"]["continuation_video_path"], "parent.mp4")
        self.assertEqual(graph["130"]["inputs"]["continuation_frame_count"], 22)
        self.assertEqual(graph["130"]["inputs"]["continuation_start_frame"], 204)
        self.assertEqual(graph["133"]["inputs"]["image"], ["132", 0])
        self.assertEqual(graph["134"]["inputs"]["image"], ["132", 0])
        self.assertEqual(graph["29"]["inputs"]["conditioning"], ["133", 0])
        self.assertEqual(graph["32"]["inputs"]["conditioning"], ["134", 0])
        self.assertNotIn("135", graph)
        self.assertNotIn("136", graph)

    def test_middle_segment_guide_skips_discarded_tail_bleed(self):
        # Parent B: 22 guide + 125 delivered + 11 discarded = 158 raw.
        graph = self.compose(context=22, parent_lead=22, parent_deliver=125)
        self.assertEqual(graph["130"]["inputs"]["continuation_start_frame"], 125)
        self.assertEqual(graph["130"]["inputs"]["continuation_frame_count"], 22)

    def test_user_selected_continuation_guide_lengths_read_parent_delivery(self):
        for guide in (5, 22, 39, 56, 73):
            with self.subTest(guide=guide):
                graph = self.compose(context=guide)
                self.assertEqual(graph["130"]["inputs"]["continuation_frame_count"], guide)
                self.assertEqual(graph["130"]["inputs"]["continuation_start_frame"],
                                 22 + 204 - guide)
        with self.assertRaisesRegex(ValueError, "至少有 73 帧"):
            self.compose(context=73, parent_deliver=56)

    def test_continuation_rejects_hidden_loader_parameters(self):
        backbone = self.backbone_stub()
        backbone["132"]["inputs"]["skip_first_frames"] = 22
        with patch.object(shot_workflow, "ui_to_api_workflow",
                          return_value=copy.deepcopy(backbone)):
            with self.assertRaisesRegex(ValueError, "从本镜输入连线"):
                shot_workflow.compose_shot_workflow(
                    {}, {}, prompt="continuation", window={
                        "raw_frames": 226, "deliver_from_raw_frame": 22,
                        "deliver_frames": 204, "context_frames": 22,
                    }, generation_spec={
                        "aspect_ratio": "16:9 (Widescreen)",
                        "first_pass_megapixels": 0.4,
                        "second_pass_megapixels": 0.9,
                    }, audio_input_name="shot.wav", visual_inputs=[],
                    output_prefix="project/shot", parent_video_path="parent.mp4",
                    parent_deliver_frames=204, parent_deliver_from_raw_frame=22)

    def test_user_inserted_processing_is_preserved_at_splice_points(self):
        graph = self.compose(context=22, rewired=True)
        self.assertEqual(graph["131"]["inputs"]["av_latent"], ["20020", 0])
        self.assertEqual(graph["133"]["inputs"]["positive"], ["20021", 0])
        self.assertEqual(graph["134"]["inputs"]["positive"], ["20022", 0])
        self.assertEqual(graph["134"]["inputs"]["latent"], ["20023", 0])
        self.assertEqual(graph["34"]["inputs"]["images"], ["20024", 0])

    def test_legacy_parent_tail_still_uses_delivered_video_coordinates(self):
        graph = self.backbone_stub()
        with patch.object(shot_workflow, "ui_to_api_workflow",
                          return_value=copy.deepcopy(graph)):
            result = shot_workflow.compose_shot_workflow(
                {}, {}, prompt="continuation", window={
                    "raw_frames": 226, "deliver_from_raw_frame": 22,
                    "deliver_frames": 204, "context_frames": 22,
                }, generation_spec={
                    "aspect_ratio": "16:9 (Widescreen)",
                    "first_pass_megapixels": 0.4,
                    "second_pass_megapixels": 0.9,
                }, audio_input_name="shot.wav", visual_inputs=[],
                output_prefix="project/shot", parent_video_path="legacy.mp4",
                parent_deliver_frames=204)
        self.assertEqual(result["130"]["inputs"]["continuation_start_frame"], 182)

    def test_legacy_seconds_formula_requires_visible_frame_wire(self):
        backbone = self.backbone_stub()
        backbone["11"]["inputs"]["length"] = ["19", 1]
        backbone["71"]["inputs"]["length"] = ["19", 1]
        backbone["11"]["inputs"]["model"] = ["12", 0]
        backbone["12"] = {"class_type": "ModelPreviewOverrideKJ", "inputs": {
            "preview_frames": ["19", 1]}}
        backbone["19"] = {"class_type": "Stub", "inputs": {
            "values.a": ["17", 0]}}
        with patch.object(shot_workflow, "ui_to_api_workflow",
                          return_value=copy.deepcopy(backbone)):
            with self.assertRaisesRegex(ValueError, "必须连本镜原始帧数"):
                shot_workflow.compose_shot_workflow(
                {}, {}, prompt="shot", window={"raw_frames": 226,
                "deliver_from_raw_frame": 0, "deliver_frames": 204,
                "context_frames": 0}, generation_spec={
                "aspect_ratio": "16:9 (Widescreen)",
                "first_pass_megapixels": 0.4,
                "second_pass_megapixels": 0.9},
                audio_input_name="shot.wav", visual_inputs=[],
                output_prefix="project/shot", seed=123)

    def test_visible_attachment_slots_feed_both_h3_passes_without_hidden_loaders(self):
        backbone = self.backbone_stub()
        backbone["137"] = {"class_type": "Xiaoyue_MV_ReferenceSlots",
                            "inputs": {"reference_manifest_json": ["130", 9],
                                       "shot_audio": ["130", 1]}}
        for node_id in ("11", "71"):
            for index in range(9):
                backbone[str(node_id)]["inputs"][f"ref_images.ref_image_{index}"] = ["137", index]
            for index in range(3):
                backbone[str(node_id)]["inputs"][f"ref_videos.ref_video_{index}"] = ["137", 9 + index]
                backbone[str(node_id)]["inputs"][f"ref_audios.ref_audio_{index}"] = ["137", 12 + index]
        with patch.object(shot_workflow, "ui_to_api_workflow",
                          return_value=copy.deepcopy(backbone)):
            graph = shot_workflow.compose_shot_workflow(
                {}, {}, prompt="shot", window={"raw_frames": 226,
                "deliver_from_raw_frame": 0, "deliver_frames": 204,
                "context_frames": 0}, generation_spec={
                "aspect_ratio": "16:9 (Widescreen)",
                "first_pass_megapixels": 0.4,
                "second_pass_megapixels": 0.9},
                audio_input_name="shot.wav", visual_inputs=[{
                    "media_type": "image", "input_name": "hero.png",
                    "absolute_path": "C:/input/hero.png"}],
                output_prefix="project/shot", seed=42)
        self.assertEqual(graph["11"]["inputs"]["ref_images.ref_image_0"], ["137", 0])
        self.assertEqual(graph["71"]["inputs"]["ref_images.ref_image_0"], ["137", 0])
        self.assertEqual(graph["137"]["inputs"]["reference_manifest_json"], ["130", 9])
        self.assertNotIn("10020", graph)


class EditorialExportTests(unittest.TestCase):
    def test_raw_and_legacy_selected_versions_export_correct_source_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_input = root / "input"
            project_output = root / "output" / "xiaoyue_mv_projects" / "project"
            (project_input / "source" / "audio").mkdir(parents=True)
            (project_input / "source" / "audio" / "master.wav").touch()
            output_dir = project_output / "generations"
            output_dir.mkdir(parents=True)
            (output_dir / "a.mp4").touch()
            (output_dir / "b.mp4").touch()
            plan = {"project": {"id": "project"},
                    "shots": [{"id": "a", "shot_uid": "uid_a"},
                              {"id": "b", "shot_uid": "uid_b"}],
                    "transitions": [{"type": "long_take"}],
                    "generation_windows": [
                        {"id": "wa", "shot_id": "a", "shot_uid": "uid_a", "raw_frames": 226,
                         "deliver_from_raw_frame": 11, "deliver_frames": 204,
                         "delivery_timeline_range": [0, 204]},
                        {"id": "wb", "shot_id": "b", "shot_uid": "uid_b", "raw_frames": 226,
                         "deliver_from_raw_frame": 22, "deliver_frames": 204,
                         "delivery_timeline_range": [204, 408]}]}
            state = {"shots": {
                "uid_a": {"selected_version_id": "va", "versions": [{
                    "id": "va", "number": 1, "status": "completed",
                    "window_id": "wa", "deliver_frames": 204,
                    "raw_frames": 226, "deliver_from_raw_frame": 11,
                    "timeline_in_frame": 12,
                    "timeline_out_frame_exclusive": 216,
                    "frame_space": "raw", "output": {
                        "filename": "a.mp4", "subfolder":
                        "xiaoyue_mv_projects/project/generations"}}]},
                "uid_b": {"selected_version_id": "vb", "versions": [{
                    "id": "vb", "number": 2, "status": "completed",
                    "window_id": "wb", "deliver_frames": 204,
                    "output": {"filename": "b.mp4", "subfolder":
                               "xiaoyue_mv_projects/project/generations"}}]},
            }}
            project = {"paths": {"input": str(project_input),
                                 "output": str(project_output)},
                       "manifest": {"audio": {"master": {"source": {
                           "path": "source/audio/master.wav"}}}}}
            payload = editorial_export.build_cut_list(
                plan, state, root / "output", project)
            self.assertEqual([(row["source_in_frame"], row["source_out_frame_exclusive"])
                              for row in payload["shots"]], [(11, 215), (0, 204)])
            self.assertEqual([(row["timeline_in_frame"], row["timeline_out_frame_exclusive"])
                              for row in payload["shots"]], [(12, 216), (204, 408)])
            self.assertIn("long_take", editorial_export.cut_list_csv(payload))
            state["shots"]["uid_b"]["versions"][0]["stale"] = True
            with self.assertRaisesRegex(ValueError, "最终版本"):
                editorial_export.build_cut_list(plan, state, root / "output", project)


if __name__ == "__main__":
    unittest.main()
