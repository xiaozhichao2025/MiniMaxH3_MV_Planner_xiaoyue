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

    def test_legacy_video_uses_matching_recorded_cut_after_current_cut_changes(self):
        plan = self.plan()
        plan["shots"] = plan["shots"][:1]
        plan["generation_windows"] = plan["generation_windows"][:1]
        plan["transitions"] = []
        legacy = self.version("uid_1", start=0, version_id="legacy")
        recorded = self.version("uid_1", start=0, version_id="recorded")
        for version in (legacy, recorded):
            version.update(window_id="window_0001", deliver_frames=80,
                           raw_frames=150, deliver_from_raw_frame=22)
        legacy.update(timeline_in_frame=None, timeline_out_frame_exclusive=None)
        recorded.update(number=2, timeline_in_frame=5,
                        timeline_out_frame_exclusive=85)
        state = {"shots": {"uid_1": {
            "selected_version_id": recorded["id"],
            "selection_confirmed": True,
            "versions": [legacy, recorded],
        }}}
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        shot = payload["shots"][0]
        self.assertEqual([v["number"] for v in shot["versions"]], [1, 2])
        self.assertTrue(shot["versions"][0]["playable"])
        self.assertEqual((shot["versions"][0]["timeline_in_frame"],
                          shot["versions"][0]["timeline_out_frame_exclusive"]), (5, 85))
        self.assertTrue(shot["versions"][0]["timeline_points_inferred"])
        self.assertTrue(shot["selected_playable"])

    def test_old_continuation_can_cut_with_another_parent_or_resume_original_pair(self):
        plan = self.plan()
        plan["shots"] = plan["shots"][:2]
        plan["generation_windows"] = plan["generation_windows"][:2]
        plan["transitions"] = [{"type": "hard_cut"}]
        parent_v1 = self.version("uid_1", start=0, version_id="parent_v1")
        parent_v2 = self.version("uid_1", start=0, version_id="parent_v2")
        parent_v2["number"] = 2
        child = self.version("uid_2", parent=parent_v1["id"],
                             start=100, version_id="child_v1")
        state = {"shots": {
            "uid_1": {"selected_version_id": parent_v2["id"],
                      "versions": [parent_v1, parent_v2]},
            "uid_2": {"selected_version_id": child["id"],
                      "versions": [child]},
        }}
        cut_branch = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertTrue(cut_branch["shots"][1]["selected_playable"])
        self.assertEqual(cut_branch["shots"][1]["incoming_transition"], "hard_cut")
        self.assertEqual([shot["selected_label"] for shot in cut_branch["shots"]],
                         ["01", "02"])
        selections = preview_desk.selection_chain(state, "uid_2", child["id"], plan)
        self.assertEqual(selections, {"uid_2": child["id"]})
        state["shots"]["uid_1"]["selected_version_id"] = parent_v1["id"]
        branch = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertTrue(branch["shots"][1]["selected_playable"])
        self.assertEqual(branch["shots"][1]["incoming_transition"], "long_take")
        self.assertEqual(branch["shots"][1]["track"], branch["shots"][0]["track"] + 1)
        self.assertEqual([shot["selected_label"] for shot in branch["shots"]],
                         ["01_a", "01_b"])

        plan["transitions"][0]["type"] = "long_take"
        state["shots"]["uid_1"]["selected_version_id"] = parent_v2["id"]
        strict = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertFalse(strict["shots"][1]["selected_playable"])
        self.assertEqual(preview_desk.selection_chain(
            state, "uid_2", child["id"], plan),
            {"uid_2": child["id"], "uid_1": parent_v1["id"]})

        child["independent_edit_allowed"] = True  # This v1 passed through a cut.
        unlocked = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertTrue(unlocked["shots"][1]["selected_playable"])
        self.assertEqual(unlocked["shots"][1]["incoming_transition"], "hard_cut")
        self.assertEqual(preview_desk.selection_chain(
            state, "uid_2", child["id"], plan), {"uid_2": child["id"]})

        new_child = self.version("uid_2", parent=parent_v2["id"],
                                 start=100, version_id="child_v2")
        new_child["number"] = 2
        state["shots"]["uid_2"]["versions"].append(new_child)
        state["shots"]["uid_2"]["selected_version_id"] = new_child["id"]
        state["shots"]["uid_1"]["selected_version_id"] = parent_v1["id"]
        strict_new = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertFalse(strict_new["shots"][1]["selected_playable"])
        self.assertEqual(preview_desk.selection_chain(
            state, "uid_2", new_child["id"], plan),
            {"uid_2": new_child["id"], "uid_1": parent_v2["id"]})

    def test_all_three_and_four_part_disconnect_and_reconnect_combinations(self):
        # v1 chain existed before the edit. Any seam in cut_mask was then
        # disconnected; v2 chain was made only after every seam was rejoined.
        # Enumerate every cut set and every mixed v1/v2 selection, including
        # disconnections at either end and in the middle of a four-part chain.
        for length in (3, 4):
            plan = self.plan()
            plan["shots"] = plan["shots"][:length]
            plan["generation_windows"] = plan["generation_windows"][:length]
            plan["transitions"] = [{"type": "long_take"} for _ in range(length - 1)]
            versions = {}
            for index in range(length):
                uid = f"uid_{index + 1}"
                versions[uid] = {}
                for variant in (1, 2):
                    parent = (versions[f"uid_{index}"][variant]["id"]
                              if index else "")
                    item = self.version(
                        uid, parent=parent, start=index * 100,
                        version_id=f"chain{length}_{uid}_v{variant}")
                    item["number"] = variant
                    versions[uid][variant] = item

            for cut_mask in range(1 << (length - 1)):
                for index in range(1, length):
                    versions[f"uid_{index + 1}"][1]["independent_edit_allowed"] = bool(
                        cut_mask & (1 << (index - 1)))
                for choice_mask in range(1 << length):
                    choices = [2 if choice_mask & (1 << index) else 1
                               for index in range(length)]
                    state = {"shots": {
                        uid: {"selected_version_id": versions[uid][choices[index]]["id"],
                              "selection_confirmed": True,
                              "versions": list(versions[uid].values())}
                        for index, uid in enumerate(versions)
                    }}
                    manifest = preview_desk.build_preview_manifest(
                        plan, state, self.project, self.output_root)
                    expected_playable = True
                    for index, shot in enumerate(manifest["shots"]):
                        if index:
                            parent_matches = choices[index] == choices[index - 1]
                            unlocked = (choices[index] == 1 and bool(
                                cut_mask & (1 << (index - 1))))
                            expected_mode = ("hard_cut" if unlocked and not parent_matches
                                             else "long_take")
                            expected_playable = (expected_playable and parent_matches
                                                 if expected_mode == "long_take" else True)
                            self.assertEqual(shot["incoming_transition"], expected_mode,
                                             (length, cut_mask, choices, index))
                        self.assertEqual(shot["selected_playable"], expected_playable,
                                         (length, cut_mask, choices, index))

                last_uid = f"uid_{length}"
                expected_ancestors = {}
                for index in range(length - 1, -1, -1):
                    uid = f"uid_{index + 1}"
                    expected_ancestors[uid] = versions[uid][1]["id"]
                    if index and cut_mask & (1 << (index - 1)):
                        break
                self.assertEqual(preview_desk.selection_chain(
                    state, last_uid, versions[last_uid][1]["id"], plan),
                    expected_ancestors)
                self.assertEqual(preview_desk.selection_chain(
                    state, last_uid, versions[last_uid][2]["id"], plan),
                    {f"uid_{index + 1}": versions[f"uid_{index + 1}"][2]["id"]
                     for index in range(length)})

    def test_regenerated_child_stays_strict_when_parent_is_regenerated_later(self):
        plan = self.plan()
        plan["shots"] = plan["shots"][:2]
        plan["generation_windows"] = plan["generation_windows"][:2]
        plan["transitions"] = [{"type": "long_take"}]
        a1 = self.version("uid_1", start=0, version_id="order_a1")
        b1 = self.version("uid_2", parent=a1["id"], start=100,
                          version_id="order_b1")
        b1["independent_edit_allowed"] = True  # Existing when seam broke.
        b2 = self.version("uid_2", parent=a1["id"], start=100,
                          version_id="order_b2")  # Rejoined, then B generated.
        b2["number"] = 2
        a2 = self.version("uid_1", start=0, version_id="order_a2")
        a2["number"] = 2  # A generated only after B2.
        state = {"shots": {
            "uid_1": {"selected_version_id": a2["id"],
                      "versions": [a1, a2]},
            "uid_2": {"selected_version_id": b1["id"],
                      "versions": [b1, b2]},
        }}
        manifest = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertTrue(manifest["shots"][1]["selected_playable"])
        self.assertEqual(manifest["shots"][1]["incoming_transition"], "hard_cut")
        state["shots"]["uid_2"]["selected_version_id"] = b2["id"]
        manifest = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertFalse(manifest["shots"][1]["selected_playable"])
        self.assertEqual(preview_desk.selection_chain(
            state, "uid_2", b2["id"], plan),
            {"uid_2": b2["id"], "uid_1": a1["id"]})

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

    def test_historical_virtual_and_real_versions_survive_current_mode_change(self):
        plan = self.plan()
        plan["transitions"][3]["type"] = "hard_cut"  # 05_a -> 05_b changed to real cut.
        a = self.version("uid_4", start=300, version_id="a_virtual")
        virtual_b = self.version("uid_5", start=400, parent=a["id"],
                                 version_id="b_virtual")
        virtual_b.update(editorial_label="05_b", incoming_transition="long_take")
        real_b = self.version("uid_5", start=400, version_id="b_real")
        real_b.update(editorial_label="06", incoming_transition="hard_cut", number=2)
        state = {"shots": {
            "uid_4": {"selected_version_id": a["id"], "versions": [a]},
            "uid_5": {"selected_version_id": virtual_b["id"],
                      "versions": [virtual_b, real_b]},
        }}
        self.assertEqual(preview_desk.selection_chain(state, "uid_5", virtual_b["id"], plan),
                         {"uid_5": virtual_b["id"]})
        self.assertEqual(preview_desk.selection_chain(state, "uid_5", real_b["id"], plan),
                         {"uid_5": real_b["id"]})
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertTrue(payload["shots"][4]["selected_playable"])
        self.assertEqual(payload["shots"][4]["incoming_transition"], "long_take")
        self.assertEqual(payload["shots"][4]["parent_shot_uid"], "uid_4")
        self.assertEqual(payload["shots"][4]["selected_label"], "04_b")
        state["shots"]["uid_5"]["selected_version_id"] = real_b["id"]
        plan["transitions"][3]["type"] = "long_take"  # Current mode reverses again.
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertTrue(payload["shots"][4]["selected_playable"])
        self.assertEqual(payload["shots"][4]["incoming_transition"], "hard_cut")
        self.assertEqual(payload["shots"][4]["selected_label"], "05_a")

    def test_historical_crossfade_uses_recorded_mode_not_current_mode(self):
        plan = self.plan()
        a = self.version("uid_1", start=0, version_id="a")
        a["deliver_frames"] = 110
        a["timeline_out_frame_exclusive"] = 110
        b = self.version("uid_2", start=90, version_id="b")
        b.update(incoming_transition="crossfade", editorial_label="02_old")
        state = {"shots": {
            "uid_1": {"selected_version_id": a["id"], "versions": [a]},
            "uid_2": {"selected_version_id": b["id"], "versions": [b]},
        }}
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertTrue(payload["shots"][1]["selected_playable"])
        self.assertEqual(payload["shots"][1]["incoming_transition"], "crossfade")
        self.assertGreater(payload["shots"][1]["track"], payload["shots"][0]["track"])
        self.assertEqual(payload["shots"][1]["selected_label"], "02")

    def test_old_continuations_and_new_cut_or_crossfade_stay_selectable(self):
        plan = self.plan()
        plan["transitions"][3]["type"] = "hard_cut"
        plan["transitions"][4]["type"] = "hard_cut"
        parent = self.version("uid_4", start=300, version_id="parent_v1")
        continuation_v1 = self.version(
            "uid_5", start=400, parent=parent["id"], version_id="child_v1")
        continuation_v2 = self.version(
            "uid_5", start=400, parent=parent["id"], version_id="child_v2")
        cut_v3 = self.version("uid_5", start=400, version_id="child_v3")
        fade_v4 = self.version("uid_5", start=390, version_id="child_v4")
        for number, version, mode in (
            (1, continuation_v1, "long_take"),
            (2, continuation_v2, "long_take"),
            (3, cut_v3, "hard_cut"),
            (4, fade_v4, "crossfade"),
        ):
            version.update(number=number, incoming_transition=mode)
        fade_v4.update(deliver_frames=110, timeline_out_frame_exclusive=500)
        state = {"shots": {
            "uid_4": {"selected_version_id": parent["id"], "versions": [parent]},
            "uid_5": {"selected_version_id": cut_v3["id"],
                      "versions": [continuation_v1, continuation_v2,
                                   cut_v3, fade_v4]},
        }}
        for version in (continuation_v1, continuation_v2):
            chain = preview_desk.selection_chain(state, "uid_5", version["id"], plan)
            self.assertEqual(chain, {"uid_5": version["id"]})
            state["shots"]["uid_5"]["selected_version_id"] = version["id"]
            payload = preview_desk.build_preview_manifest(
                plan, state, self.project, self.output_root)
            self.assertTrue(payload["shots"][4]["selected_playable"])
            self.assertEqual(payload["shots"][3]["selected_label"], "04_a")
            self.assertEqual(payload["shots"][4]["selected_label"], "04_b")

        for version, mode in ((cut_v3, "hard_cut"), (fade_v4, "crossfade")):
            self.assertEqual(preview_desk.selection_chain(
                state, "uid_5", version["id"], plan), {"uid_5": version["id"]})
            state["shots"]["uid_5"]["selected_version_id"] = version["id"]
            plan["transitions"][3]["type"] = (
                "crossfade" if mode == "hard_cut" else "long_take")
            payload = preview_desk.build_preview_manifest(
                plan, state, self.project, self.output_root)
            self.assertTrue(payload["shots"][4]["selected_playable"])
            self.assertEqual(payload["shots"][4]["incoming_transition"], mode)
            self.assertEqual(payload["shots"][3]["selected_label"], "04")
            self.assertEqual(payload["shots"][4]["selected_label"], "05")

    def test_current_branch_renumbers_downstream_without_rewriting_history(self):
        plan = self.plan()
        plan["shots"] = plan["shots"][:5]
        plan["generation_windows"] = plan["generation_windows"][:5]
        plan["transitions"] = [{"type": kind} for kind in
                               ("hard_cut", "long_take", "hard_cut", "hard_cut")]
        a = self.version("uid_2", start=100, version_id="a_v1")
        b_virtual = self.version("uid_3", start=200, parent=a["id"],
                                 version_id="b_virtual")
        b_virtual.update(editorial_label="02_b", incoming_transition="long_take")
        b_real = self.version("uid_3", start=200, version_id="b_real")
        b_real.update(number=2, editorial_label="03", incoming_transition="hard_cut")
        downstream = []
        for uid, label, start in (("uid_4", "03", 300), ("uid_5", "04", 400)):
            version = self.version(uid, start=start)
            version.update(editorial_label=label, incoming_transition="hard_cut")
            downstream.append(version)
        state = {"shots": {
            "uid_2": {"selected_version_id": a["id"], "versions": [a]},
            "uid_3": {"selected_version_id": b_virtual["id"],
                      "versions": [b_virtual, b_real]},
            **{uid: {"selected_version_id": version["id"], "versions": [version]}
               for uid, version in zip(("uid_4", "uid_5"), downstream)},
        }}
        virtual = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertEqual([shot["selected_label"] for shot in virtual["shots"]],
                         ["01", "02_a", "02_b", "03", "04"])

        state["shots"]["uid_3"]["selected_version_id"] = b_real["id"]
        real = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertEqual([shot["selected_label"] for shot in real["shots"]],
                         ["01", "02", "03", "04", "05"])
        self.assertEqual([shot["selected_version_id"] for shot in real["shots"][3:]],
                         [version["id"] for version in downstream])
        self.assertEqual([shot["versions"][0]["editorial_label"]
                          for shot in real["shots"][3:]], ["03", "04"])
        self.assertEqual([cue["shot"] for cue in real["subtitles"]],
                         ["01", "02", "03", "04", "05"])

        plan["shots"].pop(1)
        plan["generation_windows"].pop(1)
        plan["transitions"] = [{"type": "hard_cut"}] * 3
        deleted = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertEqual([shot["selected_label"] for shot in deleted["shots"]],
                         ["01", "02", "03", "04"])
        self.assertEqual([shot["selected_version_id"] for shot in deleted["shots"][2:]],
                         [version["id"] for version in downstream])

    def test_visible_continuation_suffix_passes_z(self):
        shots = [{"incoming_transition": "hard_cut"}]
        shots += [{"incoming_transition": "long_take"} for _ in range(27)]
        preview_desk._assign_visible_labels(shots)
        self.assertEqual(shots[0]["selected_label"], "01_a")
        self.assertEqual(shots[25]["selected_label"], "01_z")
        self.assertEqual(shots[26]["selected_label"], "01_aa")

    def test_legacy_virtual_parent_is_recovered_without_current_virtual_mode(self):
        plan = self.plan()
        plan["transitions"][3]["type"] = "hard_cut"
        a = self.version("uid_4", start=300)
        b = self.version("uid_5", start=400, parent=a["id"])
        state = {"shots": {
            "uid_4": {"selected_version_id": a["id"], "versions": [a]},
            "uid_5": {"selected_version_id": b["id"], "versions": [b]},
        }}
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertTrue(payload["shots"][4]["selected_playable"])
        self.assertEqual(payload["shots"][4]["incoming_transition"], "long_take")
        self.assertFalse(payload["shots"][4]["versions"][0]["transition_recorded"])

    def test_inserted_current_shot_does_not_reparent_old_continuation(self):
        plan = self.plan()
        a = self.version("uid_3", start=200, version_id="a")
        b = self.version("uid_5", start=400, parent=a["id"], version_id="b")
        b["incoming_transition"] = "long_take"
        b["independent_edit_allowed"] = True  # Insertion disconnected the old seam.
        state = {"shots": {
            "uid_3": {"selected_version_id": a["id"], "versions": [a]},
            "uid_5": {"selected_version_id": b["id"], "versions": [b]},
        }}
        self.assertEqual(preview_desk.selection_chain(state, "uid_5", b["id"], plan),
                         {"uid_5": b["id"]})
        payload = preview_desk.build_preview_manifest(
            plan, state, self.project, self.output_root)
        self.assertEqual(payload["shots"][4]["parent_shot_uid"], "")
        self.assertEqual(payload["shots"][4]["incoming_transition"], "hard_cut")
        self.assertTrue(payload["shots"][4]["selected_playable"])
        self.assertNotEqual(payload["shots"][4]["track"], payload["shots"][3]["track"])


if __name__ == "__main__":
    unittest.main()
