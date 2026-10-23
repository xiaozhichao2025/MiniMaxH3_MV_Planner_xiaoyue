from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import preview_desk


class PreviewDeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project_id = "56ce9a4b-e5bc-426b-8d93-355b12a8a693"
        self.input_root = root / "input" / "xiaoyue_mv_projects" / self.project_id
        self.output_root = root / "output"
        self.project_output = self.output_root / "xiaoyue_mv_projects" / self.project_id
        audio = self.input_root / "source" / "audio" / "master.wav"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"audio")
        self.project = {
            "project_id": self.project_id,
            "project_code": "MV01",
            "project_name": "测试 MV",
            "paths": {"input": self.input_root, "output": self.project_output},
            "manifest": {"audio": {"master": {"source": {
                "path": "source/audio/master.wav"}}}},
        }

    def tearDown(self):
        self.temp.cleanup()

    def plan(self):
        labels = ["01", "02", "03", "05_a", "05_b", "05_c"]
        shots = []
        windows = []
        for index, label in enumerate(labels):
            start = index * 100
            shot_id = f"shot_{index + 1:03d}"
            uid = f"uid_{index + 1}"
            shots.append({
                "id": shot_id, "shot_uid": uid, "editorial_label": label,
                "start_frame": start, "end_frame": start + 100,
                "subtitle_track": {"leading_text": "不要导出", "text": f"正式{label}",
                                   "trailing_text": "也不要导出"},
            })
            windows.append({
                "shot_id": shot_id, "shot_uid": uid,
                "delivery_timeline_range": [start, start + 100],
            })
        return {
            "project": {"id": self.project_id},
            "audio": {"timeline_frames": 600},
            "shots": shots,
            "generation_windows": windows,
            "transitions": [{"type": kind} for kind in
                            ("hard_cut", "hard_cut", "hard_cut", "long_take", "long_take")],
        }

    def version(self, uid, *, parent="", start=0, lead=22, version_id=None):
        version_id = version_id or f"version_{uid}"
        subfolder = f"xiaoyue_mv_projects/{self.project_id}/generations"
        target = self.output_root / subfolder / f"{version_id}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video")
        return {
            "id": version_id, "number": 1, "status": "completed",
            "shot_uid": uid, "window_fingerprint": f"fingerprint_{uid}",
            "fingerprint_revision": 2, "parent_version_id": parent,
            "deliver_frames": 100, "frame_space": "raw",
            "deliver_from_raw_frame": lead,
            "timeline_in_frame": start, "timeline_out_frame_exclusive": start + 100,
            "output": {"filename": f"{version_id}.mp4", "subfolder": subfolder,
                       "frame_count": 150},
        }

    def test_tracks_version_points_and_trimmed_source(self):
        plan = self.plan()
        state = {"shots": {}}
        parent = ""
        for index in range(6):
            uid = f"uid_{index + 1}"
            version = self.version(uid, parent=parent if index >= 4 else "",
                                   start=index * 100, lead=22 + index)
            state["shots"][uid] = {
                "selected_version_id": version["id"], "versions": [version],
            }
            parent = version["id"]
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertEqual([shot["track"] for shot in payload["shots"]],
                         [0, 1, 0, 1, 2, 3])
        chosen = payload["shots"][4]["versions"][0]
        self.assertEqual(chosen["source_in_frame"], 26)
        self.assertEqual(chosen["source_out_frame_exclusive"], 126)
        self.assertEqual(chosen["timeline_in_frame"], 400)
        self.assertTrue(chosen["playable"])
        self.assertEqual(payload["audio_policy"],
                         "mute_all_shot_videos_use_full_master")

    def test_independent_shot_after_continuation_uses_another_track(self):
        plan = self.plan()
        plan["transitions"] = [{"type": kind} for kind in
                               ("long_take", "hard_cut", "hard_cut",
                                "long_take", "long_take")]
        payload = preview_desk.build_preview_manifest(
            plan, {"shots": {}}, self.project, self.output_root)
        self.assertEqual([shot["track"] for shot in payload["shots"]],
                         [0, 1, 0, 1, 2, 3])
        self.assertEqual(payload["tracks"], ["01", "02", "03", "04"])

    def test_deep_continuation_has_no_three_track_ceiling(self):
        plan = self.plan()
        plan["transitions"] = [{"type": "long_take"} for _ in range(5)]
        payload = preview_desk.build_preview_manifest(
            plan, {"shots": {}}, self.project, self.output_root)
        self.assertEqual([shot["track"] for shot in payload["shots"]],
                         [0, 1, 2, 3, 4, 5])
        self.assertEqual(payload["tracks"], ["01", "02", "03", "04", "05", "06"])
        self.assertEqual(preview_desk._track_label(26), "27")

    def test_tracks_do_not_overlap_after_version_point_shift(self):
        plan = self.plan()
        delayed = self.version("uid_1", start=150)
        state = {"shots": {"uid_1": {"selected_version_id": delayed["id"],
                                     "versions": [delayed]}}}
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertEqual([shot["track"] for shot in payload["shots"][:3]],
                         [0, 1, 2])

    def test_missing_media_is_not_playable(self):
        plan = self.plan()
        version = self.version("uid_1")
        (self.output_root / version["output"]["subfolder"] /
         version["output"]["filename"]).unlink()
        state = {"shots": {"uid_1": {"selected_version_id": version["id"],
                                      "versions": [version]}}}
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertFalse(payload["shots"][0]["versions"][0]["playable"])

    def test_final_subtitles_exclude_generation_extensions(self):
        rows = preview_desk.final_subtitles(self.plan())
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["text"], "正式01")
        self.assertNotIn("不要导出", preview_desk.subtitle_srt(rows))
        self.assertIn("00:00:00,000 --> 00:00:04,167", preview_desk.subtitle_srt(rows))
        self.assertIn("start_frame,end_frame_exclusive", preview_desk.subtitle_csv(rows))

    def test_child_selection_picks_exact_ancestor_branch(self):
        plan = self.plan()
        a = self.version("uid_4", start=300)
        b = self.version("uid_5", start=400, parent=a["id"])
        c = self.version("uid_6", start=500, parent=b["id"])
        state = {"shots": {
            uid: {"versions": [version], "selected_version_id": ""}
            for uid, version in (("uid_4", a), ("uid_5", b), ("uid_6", c))
        }}
        self.assertEqual(preview_desk.selection_chain(
            state, "uid_6", c["id"], plan),
            {"uid_6": c["id"], "uid_5": b["id"], "uid_4": a["id"]})
        b["parent_version_id"] = "missing"
        with self.assertRaisesRegex(ValueError, "祖先不存在"):
            preview_desk.selection_chain(state, "uid_6", c["id"], plan)

    def test_historical_cut_chain_stays_previewable_by_recorded_parent(self):
        plan = self.plan()
        failed_a = self.version("uid_4", start=300, version_id="failed_a")
        failed_a.update(status="failed", number=1)
        a = self.version("uid_4", start=310, version_id="a_v2")
        a.update(number=2)
        b = self.version("uid_5", start=410, parent=a["id"], version_id="b_v1")
        other_b = self.version("uid_5", start=415, parent=a["id"], version_id="b_v2")
        other_b.update(number=2)
        c = self.version("uid_6", start=510, parent=b["id"], version_id="c_v1")
        other_c = self.version("uid_6", start=515, parent=other_b["id"], version_id="c_v2")
        other_c.update(number=2)
        state = {"shots": {
            "uid_4": {"selected_version_id": a["id"], "versions": [failed_a, a]},
            "uid_5": {"selected_version_id": b["id"], "versions": [b, other_b]},
            "uid_6": {"selected_version_id": c["id"], "versions": [c, other_c]},
        }}
        for uid in ("uid_5", "uid_6"):
            for version in state["shots"][uid]["versions"]:
                version["stale"] = True
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        chain = payload["shots"][3:]
        self.assertEqual([shot["selected_playable"] for shot in chain],
                         [True, True, True])
        self.assertEqual([next(version["timeline_in_frame"] for version in shot["versions"]
                               if version["id"] == shot["selected_version_id"])
                          for shot in chain],
                         [310, 410, 510])
        self.assertFalse(chain[0]["versions"][0]["playable"])
        self.assertEqual(preview_desk.selection_chain(state, "uid_6", c["id"], plan),
                         {"uid_6": c["id"], "uid_5": b["id"], "uid_4": a["id"]})
        self.assertEqual(preview_desk.selection_chain(state, "uid_6", other_c["id"], plan),
                         {"uid_6": other_c["id"], "uid_5": other_b["id"],
                          "uid_4": a["id"]})
        self.assertEqual(chain[1]["versions"][1]["timeline_in_frame"], 415)
        self.assertEqual(chain[2]["versions"][1]["timeline_in_frame"], 515)

    def test_selected_continuation_requires_its_recorded_parent_choice(self):
        plan = self.plan()
        a = self.version("uid_4", start=300, version_id="a_v2")
        other_a = self.version("uid_4", start=300, version_id="a_v3")
        b = self.version("uid_5", start=400, parent=a["id"])
        state = {"shots": {
            "uid_4": {"selected_version_id": other_a["id"],
                      "versions": [a, other_a]},
            "uid_5": {"selected_version_id": b["id"], "versions": [b]},
        }}
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertFalse(payload["shots"][4]["selected_playable"])
        self.assertEqual(preview_desk.selection_chain(state, "uid_5", b["id"], plan),
                         {"uid_5": b["id"], "uid_4": a["id"]})


if __name__ == "__main__":
    unittest.main()
