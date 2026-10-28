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
            self.assertEqual((app.cur_index, app.acc), (53, -170))
            now = app.last_tick
            with patch("pet_app.time.monotonic", side_effect=lambda: now):
                for _ in range(20):
                    now += 0.005
                    app.tick()
            self.assertEqual(app.cur_index, 53)
            app.play("dance1", with_audio=False)
            self.assertEqual(app.acc, 0)
            app.audio.play.return_value = False
            app.play("dance2", with_audio=True)
            self.assertEqual(app.acc, 0)

    def test_complete_actions_return_to_idle(self):
        for cid in ("laugh", "dance1", "dance2"):
            app = app_probe()
            app.play(cid, with_audio=True, full=True)
            self.assertEqual(app.cur_index, 0)
            app.audio.play.assert_called_once_with(cid, start_ms=0.0, end_ms=app.clips[cid].frames * app.frame_ms)
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
        with patch("pet_app.time.monotonic", return_value=901), patch("pet_app.random.choice", return_value="dance1"):
            app.tick()
        self.assertEqual((app.mode, app.cur_clip), ("playing", "dance1"))
        app.audio.play.assert_not_called()
        app.audio.stop.assert_called()

    def test_blink_plays_frames_6_to_36_and_blocks_left_interaction(self):
        from tools.test_jelly import event
        app = app_probe()
        with patch("pet_app.time.monotonic", return_value=10):
            self.assertTrue(app._maybe_blink(10, spontaneous=True))
        self.assertEqual((app.mode, app.cur_index), ("blinking", 5))
        ev = event(app)
        app._on_press(ev)
        app._on_motion(ev)
        app._on_release(ev)
        self.assertFalse(app.dragging)
        self.assertEqual(len(app._clicks), 0)
        app.audio.play.assert_not_called()
        seen = set()
        for frame in range(32):
            with patch("pet_app.time.monotonic", return_value=10 + frame * app.frame_ms / 1000):
                app.tick()
            if app.mode == "blinking":
                seen.add(app.cur_index)
        self.assertEqual(seen, set(range(5, 36)))
        self.assertEqual((app.mode, app.cur_index), ("idle", 0))

    def test_idle_and_interaction_blink_scheduling(self):
        app = app_probe()
        app._blink_deadline = 9
        with patch("pet_app.time.monotonic", return_value=10):
            app.tick()
        self.assertEqual(app.mode, "blinking")
        app = app_probe()
        with patch("pet_app.random.random", return_value=0.1):
            app._queue_blink()
        with patch("pet_app.time.monotonic", return_value=10):
            app.tick()
        self.assertEqual(app.mode, "blinking")
        app = app_probe()
        with patch("pet_app.random.random", return_value=0.9):
            app._queue_blink()
        self.assertFalse(getattr(app, "_blink_pending", False))

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
