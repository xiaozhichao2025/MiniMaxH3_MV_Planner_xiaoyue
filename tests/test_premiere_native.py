from __future__ import annotations

import gzip
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import premiere_native


class PremiereNativeTests(unittest.TestCase):
    def snapshot(self, count: int) -> dict:
        clips = []
        for index in range(count):
            start = 0 if index == 0 else index * 30 - 10
            clips.append({
                "source_file": f"media/video/clip_{index + 1:02d}.mp4",
                "source_total_frames": 288,
                "source_in_frame": 3,
                "source_out_frame_exclusive": 33,
                "timeline_in_frame": start,
                "timeline_out_frame_exclusive": start + 30,
                "incoming_transition": "crossfade" if index == 1 else "hard_cut",
                "track": index % 4,
                "width": 1280,
                "height": 736,
                "audio_channels": 2,
                "shot": f"{index + 1:02d}",
                "version": 1,
            })
        return {
            "clips": clips,
            "total_frames": clips[-1]["timeline_out_frame_exclusive"],
            "master_file": "media/audio/master.wav",
            "master_audio_info": {"channels": 2, "frames": 816},
            "project": {"name": "Synthetic MV", "code": "T"},
        }

    def read(self, count: int, package_root: Path | None = None) -> ET.Element:
        return ET.fromstring(gzip.decompress(
            premiere_native.project_bytes(self.snapshot(count), package_root)))

    def test_template_has_no_private_paths_or_media(self):
        raw = gzip.decompress(premiere_native.TEMPLATE_PATH.read_bytes())
        self.assertNotIn(b"xiaoz", raw.lower())
        self.assertNotIn(b"E:\\", raw)
        self.assertNotIn(b"D:\\", raw)
        self.assertNotIn(b"prproj_sandbox", raw)
        template = ET.fromstring(raw)
        self.assertEqual(len(template.findall("Media")), 6)
        self.assertTrue(all((media.findtext("RelativePath") or "").startswith(
            ".\\media\\") for media in template.findall("Media")))

    def test_variable_clips_tracks_and_references(self):
        for count in (1, 2, 5, 8):
            with self.subTest(count=count):
                root = self.read(count)
                self.assertEqual(len(root.findall("Media")), count + 1)
                self.assertEqual(len(root.findall("VideoClipTrackItem")), count)
                self.assertEqual(len(root.findall("AudioClipTrackItem")), count + 1)
                self.assertEqual(len(root.findall("Marker")), count * 2)
                self.assertEqual(root.findtext("./Sequence/Name"), "Synthetic MV")
                ids = [node.get("ObjectID") for node in root if node.get("ObjectID")]
                uids = [node.get("ObjectUID") for node in root if node.get("ObjectUID")]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(len(uids), len(set(uids)))
                id_set = {node.get("ObjectID") for node in root.iter()
                          if node.get("ObjectID")}
                uid_set = {node.get("ObjectUID") for node in root.iter()
                           if node.get("ObjectUID")}
                for node in root.iter():
                    if node.get("ObjectRef"):
                        self.assertIn(node.get("ObjectRef"), id_set)
                    if node.get("ObjectURef"):
                        self.assertIn(node.get("ObjectURef"), uid_set)
                lanes = min(4, count)
                self.assertEqual(len(root.findall("./VideoTrackGroup/TrackGroup/Tracks/Track")), lanes)
                audio_order = root.findall("./AudioTrackGroup/TrackGroup/Tracks/Track")
                self.assertEqual(len(audio_order), lanes + 1)
                song_uid = audio_order[-1].get("ObjectURef")
                song_track = next(node for node in root.findall("AudioClipTrack")
                                  if node.get("ObjectUID") == song_uid)
                self.assertEqual(len(song_track.findall(
                    "./ClipTrack/ClipItems/TrackItems/TrackItem")), 1)
                self.assertEqual(song_track.findtext("./ClipTrack/Track/IsMuted"),
                                 "false")
                for ref in audio_order[:-1]:
                    source_track = next(node for node in root.findall("AudioClipTrack")
                                        if node.get("ObjectUID") == ref.get("ObjectURef"))
                    self.assertEqual(source_track.findtext(
                        "./ClipTrack/Track/IsMuted"), "true")

    def test_more_than_four_video_lanes_and_portrait_sequence(self):
        snapshot = self.snapshot(7)
        for index, clip in enumerate(snapshot["clips"]):
            clip["track"] = index
            clip["width"] = 736
            clip["height"] = 1280
        root = ET.fromstring(gzip.decompress(premiere_native.project_bytes(snapshot)))
        self.assertEqual(len(root.findall(
            "./VideoTrackGroup/TrackGroup/Tracks/Track")), 7)
        self.assertEqual(len(root.findall(
            "./AudioTrackGroup/TrackGroup/Tracks/Track")), 8)
        self.assertEqual(root.findtext(
            "./Sequence/Node/Properties/MZ.Sequence.PreviewFrameSizeWidth"), "736")
        self.assertEqual(root.findtext(
            "./Sequence/Node/Properties/MZ.Sequence.PreviewFrameSizeHeight"), "1280")
        all_ids = {node.get("ObjectID") for node in root.iter() if node.get("ObjectID")}
        all_uids = {node.get("ObjectUID") for node in root.iter() if node.get("ObjectUID")}
        for node in root.iter():
            if node.get("ObjectRef"):
                self.assertIn(node.get("ObjectRef"), all_ids)
            if node.get("ObjectURef"):
                self.assertIn(node.get("ObjectURef"), all_uids)

    def test_fade_source_points_and_master_audio_order(self):
        root = self.read(8)
        ids = {node.get("ObjectID"): node for node in root if node.get("ObjectID")}
        video_group = root.find("./VideoTrackGroup/TrackGroup/Tracks")
        self.assertEqual(len(video_group.findall("Track")), 4)
        fades = root.findall("VideoFilterComponent")
        self.assertEqual(len(fades), 1)
        opacity = next(node for node in root.findall("VideoComponentParam")
                       if node.findtext("ParameterID") == "1")
        keys = opacity.findtext("Keyframes") or ""
        self.assertIn(str(3 * premiere_native.TICKS_PER_FRAME) + ",0.", keys)
        self.assertIn(str(13 * premiere_native.TICKS_PER_FRAME) + ",100.", keys)
        self.assertEqual(root.findtext("./Sequence/Node/Properties/MZ.WorkOutPoint"),
                         str(230 * premiere_native.TICKS_PER_FRAME))
        links = root.findall("./Sequence/PersistentGroupContainer/LinkContainer/Links/Link")
        self.assertEqual(len(links), 8)
        # The selected source in/out is kept on both linked clip objects.
        first_video = next(node for node in root.findall("VideoClipTrackItem")
                           if node.findtext(".//TrackItem/Start") == "0")
        first_sub = ids[first_video.find(".//SubClip").get("ObjectRef")]
        first_clip = ids[first_sub.find("Clip").get("ObjectRef")]
        self.assertEqual(first_clip.findtext("./Clip/InPoint"),
                         str(3 * premiere_native.TICKS_PER_FRAME))
        self.assertEqual(first_clip.findtext("./Clip/OutPoint"),
                         str(33 * premiere_native.TICKS_PER_FRAME))

    def test_local_paths_are_distinct_from_portable_paths(self):
        portable = self.read(2)
        self.assertTrue(all((media.findtext("FilePath") or "").startswith(
            ".\\media\\") for media in portable.findall("Media")))
        with tempfile.TemporaryDirectory() as folder:
            local = self.read(2, Path(folder))
            self.assertTrue(all(Path(media.findtext("FilePath")).is_absolute()
                                for media in local.findall("Media")))
            self.assertTrue(all((media.findtext("RelativePath") or "").startswith(
                ".\\media\\") for media in local.findall("Media")))

    def test_unverified_audio_layout_is_rejected(self):
        snapshot = self.snapshot(2)
        snapshot["clips"][1]["audio_channels"] = 1
        with self.assertRaisesRegex(ValueError, "双声道"):
            premiere_native.project_bytes(snapshot)
        snapshot["clips"][1]["audio_channels"] = 2
        snapshot["master_audio_info"]["channels"] = 1
        with self.assertRaisesRegex(ValueError, "双声道"):
            premiere_native.project_bytes(snapshot)


if __name__ == "__main__":
    unittest.main()
