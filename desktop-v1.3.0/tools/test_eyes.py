"""Eye direction, clipping, alpha, scaling, and state integration."""
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pet_app as pet
from pet_eyes import EyeFollow

RUN_GUI = "--gui" in sys.argv
if RUN_GUI:
    sys.argv.remove("--gui")

from tools.test_jelly import app_probe, event, scaled


class EyeTests(unittest.TestCase):
    def test_direction_limit_and_frame_rate_independence(self):
        for point in ((20000, 0), (-20000, 0), (0, 20000), (0, -20000), (20000, 20000)):
            eyes = EyeFollow((320, 305))
            for i in range(100):
                eyes.advance(point, i / 30)
            x, y = eyes.position
            self.assertLessEqual((x / 2) ** 2 + (y / 1.5) ** 2, 1.000001)
            self.assertEqual(x > 0, point[0] > eyes.center[0])
            self.assertEqual(y > 0, point[1] > eyes.center[1])
        values = []
        for fps in (30, 60):
            eyes = EyeFollow((320, 305))
            eyes.last_update = 0.0
            for i in range(1, fps + 1):
                eyes.advance((700, 700), i / fps)
            values.append(eyes.position)
        self.assertAlmostEqual(values[0][0], values[1][0], places=8)

    def test_all_sizes_change_only_eye_region_and_preserve_alpha(self):
        with Image.open(ROOT / "assets/laugh/f0001.webp") as source:
            original = source.convert("RGBA")
        for scale in pet.SCALE_PRESETS:
            size = (round(320 * scale), round(305 * scale))
            rgba = original.resize(size, Image.Resampling.LANCZOS)
            eyes = EyeFollow(size)
            original_bytes = rgba.tobytes()
            neutral = eyes.render_rgba(rgba)
            for i in range(100):
                eyes.advance((size[0] * 5, size[1] * 5), i / 30)
            moved = eyes.render_rgba(rgba)
            self.assertEqual(moved.getchannel("A").tobytes(), rgba.getchannel("A").tobytes())
            self.assertEqual(original_bytes, rgba.tobytes())
            box = ImageChops.difference(moved.convert("RGB"), rgba.convert("RGB")).getbbox()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box[0], eyes.roi[0])
            self.assertGreaterEqual(box[1], eyes.roi[1])
            self.assertLessEqual(box[2], eyes.roi[2])
            self.assertLessEqual(box[3], eyes.roi[3])
            self.assertIsNotNone(ImageChops.difference(neutral.convert("RGB"), moved.convert("RGB")).getbbox())
            stable = [eyes.advance((size[0] * 5, size[1] * 5), 4 + i / 30) for i in range(30)]
            self.assertFalse(any(stable))

    def test_window_external_pointer_and_transformed_content(self):
        app = app_probe(0.6)
        app._eyes = EyeFollow((192, 183))
        app._eye_next_frame = 0
        app.root.winfo_pointerxy = Mock(return_value=(1800, 900))
        app.root.winfo_rootx = Mock(return_value=100)
        app.root.winfo_rooty = Mock(return_value=200)
        self.assertTrue(app._update_eyes(10))
        self.assertGreater(app._eyes.position[0], 0)
        deadline = app.deadline
        app.menu_open = True
        position = app._eyes.position
        self.assertFalse(app._update_eyes(11))
        self.assertEqual(app._eyes.position, position)
        app.menu_open = False
        app.mode = "playing"
        self.assertFalse(app._update_eyes(12))
        self.assertEqual(app.deadline, deadline)

    def test_missing_assets_fail_gracefully(self):
        app = app_probe()
        app._eyes = object()
        app.group = SimpleNamespace(eyes=Mock(side_effect=OSError("missing")))
        with patch("pet_app.log"):
            app._setup_eyes()
        self.assertIsNone(app._eyes)
        app.group.eyes.assert_called_once()

    def test_face_press_warps_eye_layer_together_with_face_and_inverse_tracks_mesh(self):
        app = app_probe(1)
        app._eyes = EyeFollow((320, 305))
        app._idle_rgba = scaled(1)
        app.canvas = SimpleNamespace(itemconfigure=Mock(), coords=Mock())
        app.image_id = 1
        with patch("pet_app.time.monotonic", return_value=10):
            app._on_press(event(app, 143, 35))
        app._jelly.advance(11.5)
        eye_rgba = app._eyes.render_rgba(app._idle_rgba)
        expected = pet._chroma_frame(app._jelly.render_rgba(eye_rgba))[0]
        with patch("pet_app.ImageTk.PhotoImage", side_effect=lambda frame, **kwargs: frame.copy()):
            pet.PetApp.draw(app)
        self.assertEqual(app._jelly_photo.tobytes(), expected.tobytes())
        self.assertNotEqual(app._jelly_photo.tobytes(), pet._chroma_frame(
            app._jelly.render_rgba(app._idle_rgba))[0].tobytes())
        clip = app.clips[app.idle_clip]
        # A mesh node under the eye must map to the same original material coordinate
        # used by both the displayed texture and pointer/eye-follow input.
        node = min(range(len(app._jelly._nodes)), key=lambda n:
                   (app._jelly._nodes[n][0] - 144) ** 2 + (app._jelly._nodes[n][1] - 34) ** 2)
        x, y = app._jelly._nodes[node]
        point = app._content_point(x + clip.fx, y + clip.fy)
        self.assertEqual(point, app._jelly._points[node])
        self.assertGreater(abs(point[1] - y), 0.1)


@unittest.skipUnless(RUN_GUI, "use --gui for real Tk tracking")
class TkEyeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        settings = Path(temporary.name) / "settings.json"
        settings.write_bytes(pet.SETTINGS.read_bytes())
        audio = SimpleNamespace(prewarm=Mock(return_value=0), play=Mock(return_value=True),
                                stop=Mock(), quit=Mock())
        for context in (patch("pet_app.SETTINGS", settings), patch("pet_app.AudioPlayer", return_value=audio),
                        patch.object(pet.PetApp, "_write_geometry_file"), patch("pet_app.log")):
            context.start()
            self.addCleanup(context.stop)
        self.app = pet.PetApp()
        self.addCleanup(self.app.quit)
        self.app.root.title("Eye verification")
        self.app.root.geometry("+80+80")
        self.app.root.update()

    def test_external_pointer_settles_and_playback_uses_original_frame(self):
        app = self.app
        app.root.winfo_pointerxy = Mock(return_value=(3000, 3000))
        app.draw = Mock(wraps=app.draw)
        deadline = app.deadline
        app.tick()
        app.root.after(1150, app.root.quit)
        app.root.mainloop()
        self.assertGreater(app._eyes.position[0], 1)
        self.assertGreater(app._eyes.position[1], 0.5)
        calls = app.draw.call_count
        app.root.after(250, app.root.quit)
        app.root.mainloop()
        self.assertEqual(app.draw.call_count, calls)
        self.assertEqual(app.deadline, deadline)
        app.play("laugh", with_audio=True)
        self.assertIsNone(app._eye_photo)
        self.assertEqual(app.canvas.itemcget(app.image_id, "image"), str(app.cache[("laugh", 53)]))
        app._to_idle()
        self.assertIsNotNone(app._eye_photo)



if __name__ == "__main__":
    unittest.main()
