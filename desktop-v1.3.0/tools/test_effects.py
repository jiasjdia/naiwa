"""Split ring texture, size-dependent expansion, alpha and window cleanup."""
from pathlib import Path
import sys
import tkinter as tk
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import ImageChops
from pet_effects import (burst_frame, burst_size, premultiply, SplitBurst,
                         BURST_DURATION, ComboBubble, combo_frame)

RUN_GUI = "--gui" in sys.argv
if RUN_GUI:
    sys.argv.remove("--gui")


class EffectTests(unittest.TestCase):
    def test_combo_dimensions_scale_with_pet_size(self):
        small = combo_frame("×3 长大 · 成长值 +3", scale=0.4 / 0.6)
        large = combo_frame("×3 长大 · 成长值 +3", scale=0.8 / 0.6)
        self.assertAlmostEqual(large.width / small.width, 2, delta=0.02)
        self.assertAlmostEqual(large.height / small.height, 2, delta=0.04)

    def test_combo_bubble_has_transparent_corners_and_room_for_result(self):
        for text in ("×2 长大 · 成长值 +2", "×6 分裂！", "×5 · 已达最大体型"):
            frame = combo_frame(text, split="分裂" in text)
            self.assertEqual(frame.height, 50)
            self.assertEqual(frame.getpixel((0, 0))[3], 0)
            self.assertGreater(frame.getchannel("A").getextrema()[1], 200)

    def test_ring_expands_with_transparent_center_and_fades_away(self):
        early, late = (burst_frame(180, t) for t in (0.05, 0.30))
        a, b = early.getchannel("A").getbbox(), late.getchannel("A").getbbox()
        self.assertGreater(b[2] - b[0], a[2] - a[0])
        self.assertGreater(b[3] - b[1], a[3] - a[1])
        for frame in (early, late):
            self.assertEqual(frame.getpixel((frame.width // 2, frame.height // 2))[3], 0)
            self.assertEqual(frame.getpixel((0, 0))[3], 0)
        self.assertIsNone(burst_frame(180, BURST_DURATION).getbbox())
        self.assertGreater(late.getchannel("A").getextrema()[1],
                           burst_frame(180, 0.55).getchannel("A").getextrema()[1])

    def test_effect_and_sparks_follow_mother_size(self):
        small, large = burst_frame(100, 0.25), burst_frame(200, 0.25)
        self.assertEqual(burst_size(200), 2 * burst_size(100))
        a, b = small.getbbox(), large.getbbox()
        self.assertAlmostEqual((b[2] - b[0]) / (a[2] - a[0]), 2, delta=0.03)
        self.assertAlmostEqual((b[3] - b[1]) / (a[3] - a[1]), 2, delta=0.03)

    def test_soft_alpha_is_premultiplied_without_opaque_background(self):
        frame = premultiply(burst_frame(150, 0.25))
        r, g, b, a = frame.split()
        for channel in (r, g, b):
            self.assertIsNone(ImageChops.subtract(channel, a).getbbox())
        self.assertEqual(frame.getpixel((0, 0)), (0, 0, 0, 0))

    def test_large_ring_keeps_thin_crisp_screen_space_stroke(self):
        widths = []
        for height in (100, 200, 400):
            frame = burst_frame(height, 0.15)
            y = frame.height // 2
            alpha = frame.getchannel("A")
            widths.append(sum(alpha.getpixel((x, y)) >= 128
                              for x in range(frame.width // 2, frame.width)))
        self.assertTrue(all(1 <= width <= 3 for width in widths), widths)
        self.assertLessEqual(max(widths) - min(widths), 1)


@unittest.skipUnless(RUN_GUI, "Use --gui for the real transparent overlay")
class WindowTests(unittest.TestCase):
    def test_combo_updates_follows_and_closes_without_taking_mouse_input(self):
        root = tk.Tk()
        root.geometry("200x200+400+400")
        root.update()
        pet = SimpleNamespace(root=root, group=SimpleNamespace(root=root),
                              visible_box=lambda: (0, 0, 160, 180),
                              foot_position=lambda: (480, 580), _closed=False)
        bubble = ComboBubble(pet)
        def center_x():
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes
                rect = wintypes.RECT()
                bubble.surface.user.GetWindowRect(bubble.surface.hwnd, ctypes.byref(rect))
                return (rect.left + rect.right) / 2
            return bubble.window.winfo_x() + bubble.frame.width / 2
        try:
            bubble.show("×2 长大 · 成长值 +2")
            root.after(80, root.quit)
            root.mainloop()
            self.assertTrue(bubble.window.winfo_ismapped())
            before = center_x()
            root.geometry("200x200+500+400")
            bubble.show("×6 分裂！", split=True)
            root.after(100, root.quit)
            root.mainloop()
            self.assertAlmostEqual(center_x() - before, 100, delta=2)
            old_height = bubble.frame.height
            pet.global_scale = 1.0
            root.after(100, root.quit)
            root.mainloop()
            self.assertGreater(bubble.frame.height, old_height)
            if sys.platform == "win32":
                style = bubble.surface.user.GetWindowLongW(bubble.surface.hwnd, -20)
                self.assertTrue(style & 0x20)
                self.assertTrue(style & 0x08000000)
            root.after(1250, root.quit)
            root.mainloop()
            self.assertTrue(bubble.closed)
            self.assertIsNone(bubble.after)
            self.assertIsNone(bubble.surface)
        finally:
            bubble.close()
            root.destroy()

    def test_click_through_window_maps_correctly_and_releases_itself(self):
        root = tk.Tk()
        root.withdraw()
        effects = set()
        errors = []
        root.report_callback_exception = lambda *exc: errors.append(str(exc[1]))
        effect = None
        try:
            effect = SplitBurst(root, (450, 350), 120, effects.discard)
            effects.add(effect)
            root.after(130, root.quit)
            root.mainloop()
            self.assertFalse(effect.closed)
            self.assertTrue(effect.window.winfo_ismapped())
            if sys.platform == "win32":
                surface = effect.surface
                self.assertEqual(surface.hwnd, surface.user.GetAncestor(effect.window.winfo_id(), 2))
                style = surface.user.GetWindowLongW(surface.hwnd, -20)
                self.assertTrue(style & 0x20)
                self.assertTrue(style & 0x08000000)
                self.assertEqual(surface.size, (effect.size, effect.size))
            root.after(700, root.quit)
            root.mainloop()
            self.assertTrue(effect.closed)
            self.assertIsNone(effect.surface)
            self.assertIsNone(effect.after)
            self.assertEqual(effects, set())
            self.assertEqual(errors, [])
        finally:
            if effect is not None:
                effect.close()
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
