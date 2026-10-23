from __future__ import annotations

import json
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

    def test_selected_full_sources_and_markers(self):
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
        self.assertEqual([item.text for item in root.findall(".//video/track/clipitem/in")],
                         ["0", "0"])
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

    def test_full_source_tracks_rise_through_continuation_chain(self):
        clips = [
            {"shot_uid": "a", "parent_shot_uid": "", "incoming_transition": "hard_cut",
             "full_clip_start_frame": 0, "full_clip_end_frame_exclusive": 100},
            {"shot_uid": "b", "parent_shot_uid": "a", "incoming_transition": "long_take",
             "full_clip_start_frame": 50, "full_clip_end_frame_exclusive": 150},
            {"shot_uid": "c", "parent_shot_uid": "b", "incoming_transition": "long_take",
             "full_clip_start_frame": 100, "full_clip_end_frame_exclusive": 200},
            {"shot_uid": "d", "parent_shot_uid": "", "incoming_transition": "hard_cut",
             "full_clip_start_frame": 100, "full_clip_end_frame_exclusive": 200},
        ]
        delivery_export._allocate_full_tracks(clips)
        self.assertEqual([clip["track"] for clip in clips], [0, 1, 2, 0])

    def test_negative_full_source_start_adds_global_offset(self):
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
        self.assertEqual(snapshot["project_offset_frames"], 30)
        self.assertEqual(snapshot["clips"][0]["full_clip_start_frame"], 0)
        root = ET.fromstring(delivery_export.fcp7_xml(snapshot).split(
            b"<!DOCTYPE xmeml>\n", 1)[1])
        self.assertEqual(root.find(".//sequence/marker/in").text, "30")

    def test_cancel_keeps_no_incomplete_package(self):
        snapshot = self.snapshot()
        root = self.project_output / "delivery" / "exports"
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(delivery_export.ExportCancelled):
            delivery_export.run_export(snapshot, "fcp_xml", root, "c" * 32,
                                       cancel, lambda *_: None)
        self.assertEqual(list(root.iterdir()), [])

    def test_fcp_package_resolves_every_media_reference_inside_zip(self):
        snapshot = self.snapshot()
        result = delivery_export.run_export(snapshot, "fcp_xml",
            self.project_output / "delivery" / "exports", "d" * 32,
            threading.Event(), lambda *_: None)
        with zipfile.ZipFile(result["archive"]) as archive:
            names = set(archive.namelist())
            self.assertIn("FCP7_通用工程.xml", names)
            self.assertIn("markers.csv", names)
            self.assertIn("最终字幕.srt", names)
            root = ET.fromstring(archive.read("FCP7_通用工程.xml"))
            references = [element.text for element in root.findall(".//pathurl")]
            self.assertEqual(len(references), 3)
            self.assertTrue(all(path in names and path.startswith("media/")
                                for path in references))
            self.assertNotIn("Premiere_工程.xml", names)
            self.assertFalse(any(path.endswith("draft_content.json") for path in names))
            notes = archive.read("导出说明.txt").decode("utf-8")
            self.assertIn("相对于 XML 所在目录", notes)

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
