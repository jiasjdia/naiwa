"""Audio gating and bounded one-shot playback."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.test_jelly import app_probe


class AudioGatingTests(unittest.TestCase):
    def test_manual_and_automatic(self):
        app = app_probe()
        app.play("laugh", with_audio=True)
        app.audio.play.assert_called_once_with("laugh", start_ms=53 * app.frame_ms,
                                               end_ms=381 * app.frame_ms)
        app.audio.play.reset_mock()
        app.play("laugh")
        app.audio.play.assert_not_called()
        app.audio.stop.assert_called()
        self.assertEqual(app.cur_index, 53)

    def test_full_menu_and_explicit_stop(self):
        app = app_probe()
        app.play("laugh", with_audio=True, full=True)
        self.assertEqual(app.cur_index, 0)
        app.audio.play.assert_called_once_with("laugh", start_ms=0.0,
                                               end_ms=app.clips["laugh"].frames * app.frame_ms)
        app.stop_laugh()
        self.assertEqual(app.mode, "idle")
        app.audio.stop.assert_called()

    def test_silent_child_does_not_create_voice(self):
        source, child = app_probe(), app_probe()
        with patch("pet_app.time.monotonic", return_value=10):
            source.play("laugh")
        with patch("pet_app.time.monotonic", return_value=12):
            child.inherit_animation(source)
        child.audio.play.assert_not_called()
        self.assertEqual(child._action_start, source._action_start)


if __name__ == "__main__":
    unittest.main()
