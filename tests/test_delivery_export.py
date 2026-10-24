from __future__ import annotations

import json
import gzip
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delivery_export


class DeliveryExportTests(unittest.TestCase):
    def test_srt_to_webvtt_preserves_exported_cues(self):
        srt = ("\ufeff1\r\n00:00:00,000 --> 00:00:01,042\r\n"
               "第一句歌词\r\n\r\n2\r\n00:00:01,042 --> 00:00:02,000\r\n"
               "第二句歌词\r\n")
        self.assertEqual(
            delivery_export.srt_to_webvtt(srt),
            "WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.042\n第一句歌词\n\n"
            "2\n00:00:01.042 --> 00:00:02.000\n第二句歌词\n",
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project_id = "11111111-2222-3333-4444-555555555555"
        self.input_root = root / "input" / "xiaoyue_mv_projects" / self.project_id
        self.output_root = root / "output"
        self.project_output = self.output_root / "xiaoyue_mv_projects" / self.project_id
        audio = self.input_root / "source" / "audio" / "master.wav"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"audio")
        self.project = {
            "project_id": self.project_id,
            "paths": {"input": self.input_root, "output": self.project_output},
        }
        subfolder = f"xiaoyue_mv_projects/{self.project_id}/generations"
        self.versions = []
        for index in range(2):
            name = f"v{index + 1}.mp4"
            path = self.output_root / subfolder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"video")
            self.versions.append({
                "id": f"version-{index + 1}", "number": 1, "playable": True,
                "parent_version_id": "", "source_in_frame": 0 if index == 0 else 10,
                "source_out_frame_exclusive": 24 if index == 0 else 34,
                "timeline_in_frame": 0 if index == 0 else 16,
                "timeline_out_frame_exclusive": 24 if index == 0 else 40,
                "timeline_points_recorded": True,
                "source": {"type": "output", "subfolder": subfolder, "filename": name},
            })
        self.manifest = {
            "schema": "xiaoyue.mv.preview-desk.v1",
            "project": {"id": self.project_id, "code": "T01", "name": "测试"},
            "total_frames": 40,
            "master_audio": {"type": "input",
                             "subfolder": f"xiaoyue_mv_projects/{self.project_id}/source/audio",
                             "filename": "master.wav"},
            "shots": [
                {"shot_uid": "uid-1", "label": "01", "selected_version_id": "version-1",
                 "selection_confirmed": True, "selected_playable": True,
                 "incoming_transition": "hard_cut", "versions": [self.versions[0]]},
                {"shot_uid": "uid-2", "label": "02", "selected_version_id": "version-2",
                 "selection_confirmed": True, "selected_playable": True,
                 "incoming_transition": "crossfade", "versions": [self.versions[1]]},
            ],
            "subtitles": [{"shot": "01", "shot_uid": "uid-1",
                           "start_frame": 0, "end_frame_exclusive": 24,
                           "text": "正式歌词"}],
        }

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        with patch.object(delivery_export, "probe_video",
                          return_value={"nb_frames": 48, "width": 128, "height": 72,
                                        "audio_channels": 2, "audio_sample_rate": 48000,
                                        "audio_depth": 16}), \
             patch.object(delivery_export, "probe_audio",
                          return_value={"frames": 48, "sample_rate": 48000,
                                        "channels": 2, "depth": 16}):
            return delivery_export.build_export_snapshot(
                self.manifest, self.project, self.output_root)

    def test_selected_edits_keep_full_sources_and_markers(self):
        snapshot = self.snapshot()
        self.assertEqual([clip["full_clip_start_frame"] for clip in snapshot["clips"]],
                         [0, 6])
        self.assertNotEqual(snapshot["clips"][0]["track"], snapshot["clips"][1]["track"])
        self.assertEqual(snapshot["clips"][1]["source_total_frames"], 48)
        self.assertIn("timeline_points_recorded", delivery_export.markers_csv(snapshot))
        self.assertNotIn("source_path", json.dumps(delivery_export.public_snapshot(snapshot)))
        xml = delivery_export.fcp7_xml(snapshot)
        root = ET.fromstring(xml.split(b"<!DOCTYPE xmeml>\n", 1)[1])
        self.assertEqual(root.find("./sequence/name").text, "测试")
        self.assertEqual(root.find("./sequence/media/audio/numOutputChannels").text, "2")
        audio_tracks = root.findall("./sequence/media/audio/track")
        self.assertEqual(len(audio_tracks), 6)  # V1/A1, V2/A2, then original song A3.
        self.assertEqual([track.findtext("enabled") for track in audio_tracks],
                         ["FALSE"] * 4 + ["TRUE"] * 2)
        self.assertEqual([track.findtext("clipitem/name") for track in audio_tracks],
                         ["01 v1", "01 v1", "02 v1", "02 v1", "master.wav", "master.wav"])
        self.assertEqual(len(root.findall(".//sequence/marker")), 4)
        self.assertEqual(len(root.findall(".//video/track")), 2)
        self.assertFalse(root.findall(".//video/track/name"))
        video_items = root.findall("./sequence/media/video/track/clipitem")
        self.assertEqual(
            [(item.findtext("name"), item.findtext("start"), item.findtext("end"),
              item.findtext("in"), item.findtext("out"), item.findtext("duration"))
             for item in video_items],
            [("01 v1", "0", "24", "0", "24", "48"),
             ("02 v1", "16", "40", "10", "34", "48")])
        self.assertEqual(
            [(item.findtext("name"), item.findtext("start"), item.findtext("end"),
              item.findtext("in"), item.findtext("out"))
             for item in root.findall("./sequence/media/audio/track/clipitem")[:-2]],
            [("01 v1", "0", "24", "0", "24")] * 2 +
            [("02 v1", "16", "40", "10", "34")] * 2)
        self.assertTrue(all((item.text or "").startswith("media/")
                            for item in root.findall(".//pathurl")))
        self.assertIn("正式歌词", delivery_export.subtitle_srt(snapshot["subtitles"]))

    def test_unconfirmed_or_broken_chain_blocks_export(self):
        self.manifest["shots"][0]["selection_confirmed"] = False
        with self.assertRaisesRegex(ValueError, "尚未明确确认"):
            self.snapshot()
        self.manifest["shots"][0]["selection_confirmed"] = True
        self.manifest["shots"][1]["incoming_transition"] = "long_take"
        with self.assertRaisesRegex(ValueError, "父版本链不匹配"):
            self.snapshot()

    def test_export_uses_selected_historical_label_and_parent_connection(self):
        self.versions[1]["parent_version_id"] = self.versions[0]["id"]
        self.manifest["shots"][1].update(
            label="03", selected_label="02_b", incoming_transition="long_take",
            parent_shot_uid="uid-1")
        snapshot = self.snapshot()
        self.assertEqual(snapshot["clips"][1]["shot"], "02_b")
        self.assertEqual(snapshot["clips"][1]["parent_shot_uid"], "uid-1")
        self.assertEqual(snapshot["clips"][1]["incoming_transition"], "long_take")

    def test_old_continuation_source_can_export_as_independent_cut(self):
        self.versions[1]["parent_version_id"] = "original-parent-version"
        self.manifest["shots"][1].update(
            incoming_transition="hard_cut", parent_shot_uid="")
        snapshot = self.snapshot()
        self.assertEqual(snapshot["clips"][1]["incoming_transition"], "hard_cut")
        self.assertEqual(snapshot["clips"][1]["parent_version_id"],
                         "original-parent-version")
        self.assertEqual(snapshot["clips"][1]["parent_shot_uid"], "")

    def test_movie_command_blends_only_selected_crossfade(self):
        snapshot = self.snapshot()
        command = delivery_export._movie_command(snapshot, self.project_output, "ffmpeg")
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("fade=t=in:", graph)
        snapshot["clips"][1]["incoming_transition"] = "hard_cut"
        command = delivery_export._movie_command(snapshot, self.project_output, "ffmpeg")
        graph = command[command.index("-filter_complex") + 1]
        self.assertNotIn("fade=t=in:", graph)

    def test_pr_sample_style_crossfade_uses_upper_track_and_source_frame_opacity(self):
        snapshot = self.snapshot()
        self.assertGreater(snapshot["clips"][1]["track"], snapshot["clips"][0]["track"])
        root = ET.fromstring(delivery_export.fcp7_xml(snapshot))
        upper = next(item for item in root.findall("./sequence/media/video/track/clipitem")
                     if item.findtext("name") == "02 v1")
        effect = upper.find("./filter/effect")
        self.assertEqual(effect.findtext("effectid"), "opacity")
        parameter = effect.find("parameter")
        self.assertEqual(parameter.attrib.get("authoringApp"), "PremierePro")
        self.assertEqual(parameter.findtext("parameterid"), "opacity")
        self.assertEqual([(int(key.findtext("when")), int(key.findtext("value")))
                          for key in parameter.findall("keyframe")],
                         [(10, 0), (18, 100)])

        snapshot["clips"][1]["incoming_transition"] = "hard_cut"
        root = ET.fromstring(delivery_export.fcp7_xml(snapshot))
        cut = next(item for item in root.findall("./sequence/media/video/track/clipitem")
                   if item.findtext("name") == "02 v1")
        self.assertIsNone(cut.find("./filter/effect"))

    def test_pr_sequence_combines_crossfade_hard_cut_and_continuation(self):
        for index, (incoming, source_in, timeline_in, parent_id) in enumerate((
                ("hard_cut", 3, 40, ""),
                ("long_take", 4, 64, "version-3")), 3):
            filename = f"v{index}.mp4"
            source = (self.output_root / self.versions[0]["source"]["subfolder"]
                      / filename)
            source.write_bytes(b"video")
            version = {
                "id": f"version-{index}", "number": 1, "playable": True,
                "parent_version_id": parent_id,
                "source_in_frame": source_in,
                "source_out_frame_exclusive": source_in + 24,
                "timeline_in_frame": timeline_in,
                "timeline_out_frame_exclusive": timeline_in + 24,
                "timeline_points_recorded": True,
                "source": {"type": "output",
                           "subfolder": self.versions[0]["source"]["subfolder"],
                           "filename": filename},
            }
            self.manifest["shots"].append({
                "shot_uid": f"uid-{index}", "label": f"{index:02d}",
                "selected_version_id": version["id"],
                "selection_confirmed": True, "selected_playable": True,
                "incoming_transition": incoming,
                "parent_shot_uid": f"uid-{index - 1}" if parent_id else "",
                "versions": [version],
            })
        self.manifest["total_frames"] = 88
        snapshot = self.snapshot()
        clips = snapshot["clips"]
        self.assertEqual([clip["track"] for clip in clips], [0, 1, 0, 1])
        root = ET.fromstring(delivery_export.fcp7_xml(snapshot))
        self.assertEqual(root.findtext("./sequence/duration"), "88")
        self.assertEqual([item.findtext("start") for item in
                          root.findall("./sequence/media/audio/track")[-2:]
                          for item in item.findall("clipitem")], ["0", "0"])
        by_name = {item.findtext("name"): item for item in
                   root.findall("./sequence/media/video/track/clipitem")}
        expected = {
            "01 v1": (0, 24, 0, 24),
            "02 v1": (16, 40, 10, 34),
            "03 v1": (40, 64, 3, 27),
            "04 v1": (64, 88, 4, 28),
        }
        for name, points in expected.items():
            item = by_name[name]
            self.assertEqual(tuple(int(item.findtext(tag)) for tag in
                                   ("start", "end", "in", "out")), points)
            self.assertEqual(item.findtext("duration"), "48")
            self.assertEqual(int(item.findtext("pproTicksIn")), points[2] * 10584000000)
            self.assertEqual(int(item.findtext("pproTicksOut")), points[3] * 10584000000)
        self.assertIsNotNone(by_name["02 v1"].find("./filter/effect"))
        self.assertIsNone(by_name["03 v1"].find("./filter/effect"))
        self.assertIsNone(by_name["04 v1"].find("./filter/effect"))
        self.assertEqual(len(root.findall("./sequence/marker")), 8)

    def test_original_song_follows_highest_video_audio_lane(self):
        snapshot = self.snapshot()
        snapshot["clips"][1]["track"] = 4  # Continuation chains may exceed A/B/C.
        root = ET.fromstring(delivery_export.fcp7_xml(snapshot))
        tracks = root.findall("./sequence/media/audio/track")
        self.assertEqual(len(tracks), 9)  # Five logical source lanes, then stereo A6.
        self.assertTrue(all(track.findtext("enabled") == "FALSE"
                            for track in tracks[:-2]))
        self.assertEqual([track.findtext("clipitem/name") for track in tracks[-2:]],
                         ["master.wav", "master.wav"])
        self.assertEqual([link.findtext("trackindex")
                          for link in tracks[-1].findall("clipitem/link")], ["8", "9"])

    def test_edit_tracks_rise_through_continuation_chain(self):
        clips = [
            {"shot_uid": "a", "parent_shot_uid": "", "incoming_transition": "hard_cut",
             "timeline_in_frame": 0, "timeline_out_frame_exclusive": 100},
            {"shot_uid": "b", "parent_shot_uid": "a", "incoming_transition": "long_take",
             "timeline_in_frame": 100, "timeline_out_frame_exclusive": 150},
            {"shot_uid": "c", "parent_shot_uid": "b", "incoming_transition": "long_take",
             "timeline_in_frame": 150, "timeline_out_frame_exclusive": 200},
            {"shot_uid": "d", "parent_shot_uid": "", "incoming_transition": "hard_cut",
             "timeline_in_frame": 200, "timeline_out_frame_exclusive": 300},
        ]
        delivery_export._allocate_edit_tracks(clips)
        self.assertEqual([clip["track"] for clip in clips], [0, 1, 2, 1])

    def test_source_handle_before_song_zero_does_not_offset_edit(self):
        version = self.versions[0]
        version["source_in_frame"] = 30
        version["source_out_frame_exclusive"] = 54
        with patch.object(delivery_export, "probe_video",
                          return_value={"nb_frames": 60, "width": 128, "height": 72}), \
             patch.object(delivery_export, "probe_audio",
                          return_value={"frames": 48, "sample_rate": 48000,
                                        "channels": 2, "depth": 16}):
            snapshot = delivery_export.build_export_snapshot(
                self.manifest, self.project, self.output_root)
        self.assertEqual(snapshot["project_offset_frames"], 0)
        self.assertEqual(snapshot["clips"][0]["full_clip_start_frame"], -30)
        root = ET.fromstring(delivery_export.fcp7_xml(snapshot).split(
            b"<!DOCTYPE xmeml>\n", 1)[1])
        self.assertEqual(root.find(".//sequence/marker/in").text, "0")
        first = root.find("./sequence/media/video/track/clipitem")
        self.assertEqual([first.findtext(key) for key in ("start", "end", "in", "out")],
                         ["0", "24", "30", "54"])

    def test_cancel_keeps_no_incomplete_package(self):
        snapshot = self.snapshot()
        root = self.project_output / "delivery" / "exports"
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(delivery_export.ExportCancelled):
            delivery_export.run_export(snapshot, "fcp_xml", root, "c" * 32,
                                       cancel, lambda *_: None)
        self.assertEqual(list(root.iterdir()), [])

    def test_pr_audio_layout_is_rejected_before_copying_sources(self):
        snapshot = self.snapshot()
        snapshot["clips"][0]["audio_channels"] = 1
        root = self.project_output / "delivery" / "exports"
        with self.assertRaisesRegex(ValueError, "双声道"):
            delivery_export.run_export(snapshot, "fcp_xml", root, "a" * 32,
                                       threading.Event(), lambda *_: None)
        self.assertFalse(root.exists())

    def test_fcp_package_resolves_every_media_reference_inside_zip(self):
        snapshot = self.snapshot()
        result = delivery_export.run_export(snapshot, "fcp_xml",
            self.project_output / "delivery" / "exports", "d" * 32,
            threading.Event(), lambda *_: None)
        with zipfile.ZipFile(result["archive"]) as archive:
            names = set(archive.namelist())
            self.assertIn("FCP7_通用工程.xml", names)
            self.assertIn("MV_Edit.prproj", names)
            self.assertIn("Relink_MV_Project.cmd", names)
            self.assertIn("relink_premiere.ps1", names)
            self.assertIn("markers.csv", names)
            self.assertIn("最终字幕.srt", names)
            root = ET.fromstring(archive.read("FCP7_通用工程.xml"))
            references = [element.text for element in root.findall(".//pathurl")]
            self.assertEqual(len(references), 3)
            self.assertTrue(all(path in names and path.startswith("media/")
                                for path in references))
            self.assertNotIn("Premiere_工程.xml", names)
            self.assertFalse(any(path.endswith("draft_content.json") for path in names))
            portable = ET.fromstring(gzip.decompress(archive.read("MV_Edit.prproj")))
            packaged_paths = [media.findtext("FilePath") for media in
                              portable.findall("Media")]
            self.assertEqual(len(packaged_paths), 3)
            self.assertTrue(all(path.startswith(".\\media\\") for path in packaged_paths))
            local = ET.fromstring(gzip.decompress(
                (Path(result["folder"]) / "MV_Edit.prproj").read_bytes()))
            local_paths = [media.findtext("FilePath") for media in local.findall("Media")]
            self.assertTrue(all(Path(path).is_file() for path in local_paths))
            notes = archive.read("导出说明.txt").decode("utf-8")
            self.assertIn("相对于 XML 所在目录", notes)
            self.assertIn("Relink_MV_Project.cmd", notes)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"),
                         "Windows PowerShell is required for native project relocation")
    def test_prproj_can_be_relocated_after_unpacking(self):
        snapshot = self.snapshot()
        result = delivery_export.run_export(snapshot, "fcp_xml",
            self.project_output / "delivery" / "exports", "e" * 32,
            threading.Event(), lambda *_: None)
        relocated = Path(self.temp.name) / "moved-package"
        with zipfile.ZipFile(result["archive"]) as archive:
            archive.extractall(relocated)
        project = relocated / "MV_Edit.prproj"
        original = ET.fromstring(gzip.decompress(project.read_bytes()))
        self.assertTrue(all((media.findtext("FilePath") or "").startswith(
            ".\\media\\") for media in original.findall("Media")))
        run = subprocess.run([
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(relocated / "relink_premiere.ps1"), "-ProjectFile", str(project),
        ], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        updated = ET.fromstring(gzip.decompress(project.read_bytes()))
        self.assertTrue(all(Path(media.findtext("FilePath")).is_file()
                            for media in updated.findall("Media")))
        self.assertEqual(len(list(relocated.glob("MV_Edit.prproj.before-relink-*.bak"))), 1)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg is required for movie integration test")
    def test_hard_cut_at_fractional_second_has_no_black_frame(self):
        self.versions[0].update(source_out_frame_exclusive=1,
                                timeline_out_frame_exclusive=1)
        self.versions[1].update(source_in_frame=0,
                                source_out_frame_exclusive=3,
                                timeline_in_frame=1,
                                timeline_out_frame_exclusive=4)
        self.manifest["shots"][1]["incoming_transition"] = "hard_cut"
        self.manifest["total_frames"] = 4
        for index, version in enumerate(self.versions):
            source = (self.output_root / version["source"]["subfolder"] /
                      version["source"]["filename"])
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"color=c={'red' if index == 0 else 'blue'}:s=128x72:r=24:d=2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                           check=True, capture_output=True)
        audio = self.input_root / "source" / "audio" / "master.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=48000:duration=2", str(audio)],
                       check=True, capture_output=True)
        snapshot = delivery_export.build_export_snapshot(
            self.manifest, self.project, self.output_root)
        result = delivery_export.run_export(snapshot, "movie",
            self.project_output / "delivery" / "exports", "b" * 32,
            threading.Event(), lambda *_: None)
        movie = Path(result["folder"]) / "MV_无字成片.mp4"
        frame = subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(movie), "-vf", "select=eq(n\\,1)",
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True).stdout
        center = (36 * 128 + 64) * 3
        self.assertGreater(frame[center + 2], 100)  # Incoming blue starts on frame 1.
        self.assertLess(frame[center], 50)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg is required for movie integration test")
    def test_movie_package_has_exact_frames_and_original_audio(self):
        for index, version in enumerate(self.versions):
            source = (self.output_root / version["source"]["subfolder"] /
                      version["source"]["filename"])
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"color=c={'red' if index == 0 else 'blue'}:s=128x72:r=24:d=2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                           check=True, capture_output=True)
        audio = self.input_root / "source" / "audio" / "master.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=48000:duration=2", str(audio)],
                       check=True, capture_output=True)
        snapshot = delivery_export.build_export_snapshot(
            self.manifest, self.project, self.output_root)
        result = delivery_export.run_export(snapshot, "movie",
            self.project_output / "delivery" / "exports", "a" * 32,
            threading.Event(), lambda *_: None)
        movie = Path(result["folder"]) / "MV_无字成片.mp4"
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=nb_frames", "-of", "json",
                                str(movie)], capture_output=True, text=True, check=True)
        self.assertEqual(int(json.loads(probe.stdout)["streams"][0]["nb_frames"]), 40)
        blended = subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(movie), "-vf", "select=eq(n\\,20)",
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True).stdout
        center = (36 * 128 + 64) * 3
        self.assertGreater(blended[center], 30)  # Previous red remains visible.
        self.assertGreater(blended[center + 2], 30)  # Incoming blue has begun.
        with zipfile.ZipFile(result["archive"]) as archive:
            names = archive.namelist()
            self.assertIn("最终字幕.srt", names)
            self.assertEqual(len([name for name in names if name.startswith("media/video/")]), 2)


if __name__ == "__main__":
    unittest.main()
