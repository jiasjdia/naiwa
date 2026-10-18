"""Product-level checks migrated from the former JavaScript verification."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.test_jelly import app_probe


class BehaviorTests(unittest.TestCase):
    def test_device_latency_holds_frame_zero_only_for_successful_manual_audio(self):
        app = app_probe()
        app.audio.output_latency_ms = 170
        app.audio.play.return_value = True
        with patch("pet_app.log"):
            app.play("laugh", with_audio=True)
            self.assertEqual((app.cur_index, app.acc), (0, -170))
            now = app.last_tick
            with patch("pet_app.time.monotonic", side_effect=lambda: now):
                for _ in range(20):
                    now += 0.005
                    app.tick()
            self.assertEqual(app.cur_index, 0)
            app.play("laugh", with_audio=False)
            self.assertEqual(app.acc, 0)
            app.audio.play.return_value = False
            app.play("laugh", with_audio=True)
            self.assertEqual(app.acc, 0)

    def test_complete_actions_return_to_idle(self):
        for cid in ("laugh",):
            app = app_probe()
            app.play(cid, with_audio=True)
            self.assertEqual(app.cur_index, 0)
            app.audio.play.assert_called_once_with(cid)
            seen = set()
            now = app.last_tick
            began = now
            with patch("pet_app.log"), patch("pet_app.time.monotonic", side_effect=lambda: now):
                while app.mode == "playing":
                    seen.add(app.cur_index)
                    now += 1 / 120
                    app.tick()
            self.assertEqual(len(seen), app.clips[cid].frames)
            self.assertEqual((app.cur_clip, app.cur_index), (app.idle_clip, app.idle_frame))
            expected = app.clips[cid].frames / 30
            self.assertAlmostEqual(now - began, expected, delta=0.02)
            self.assertGreaterEqual(app.deadline - now, 180)
            self.assertLessEqual(app.deadline - now, 240)

    def test_menu_suspends_random_and_automatic_actions_are_silent(self):
        app = app_probe()
        app.deadline = 10
        app.menu_open = True
        with patch("pet_app.time.monotonic", return_value=900):
            app.tick()
        self.assertEqual(app.mode, "idle")
        app.audio.play.assert_not_called()
        app.menu_open = False
        with patch("pet_app.time.monotonic", return_value=901), patch("pet_app.random.choice", return_value="laugh"):
            app.tick()
        self.assertEqual((app.mode, app.cur_clip), ("playing", "laugh"))
        app.audio.play.assert_not_called()
        app.audio.stop.assert_called()

    def test_manifest_files_dimensions_and_alignment(self):
        data = json.loads((ROOT / "assets/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((data["idleClip"], data["idleFrame"], data["fps"]), ("laugh", 1, 30))
        self.assertEqual([c["id"] for c in data["clips"]], ["laugh"])
        anchors = []
        for clip in data["clips"]:
            self.assertEqual(len(clip["frameFiles"]), clip["frames"])
            folder = ROOT / "assets" / clip["dir"]
            self.assertTrue(all((folder / frame).is_file() for frame in clip["frameFiles"]))
            for index in (0, clip["frames"] // 2, clip["frames"] - 1):
                with Image.open(folder / clip["frameFiles"][index]) as image:
                    self.assertEqual(image.size, (clip["window"]["store"]["w"], clip["window"]["store"]["h"]))
                    self.assertIsNotNone(image.getchannel("A").getbbox())
                    if index == 0:
                        bbox = image.getchannel("A").point(lambda a: 255 if a >= 8 else 0).getbbox()
                        anchors.append(((bbox[0] + bbox[2]) / 2, bbox[3]))
        self.assertLessEqual(max(x for x, y in anchors) - min(x for x, y in anchors), 2)
        self.assertLessEqual(max(y for x, y in anchors) - min(y for x, y in anchors), 2)


if __name__ == "__main__":
    unittest.main()
