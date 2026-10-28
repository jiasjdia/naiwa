"""Group policies plus optional real multi-window integration (--gui).

Uses temporary settings and silent audio doubles; never owns the running pet's
instance guard or its production shortcuts.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pet_app as pet
from pet_group import (PetGroup, PetVoice, SharedCache, constrain_origin,
                       place_window, REUNIFY_SEC)

RUN_GUI = "--gui" in sys.argv
if RUN_GUI:
    sys.argv.remove("--gui")
from tools.test_jelly import app_probe, click, event


class SilentAudio:
    output_latency_ms = 170

    def __init__(self):
        self.prewarm = Mock(return_value=0)
        self.play = Mock(return_value=True)
        self.stop = Mock()
        self.play_voice = Mock(return_value=True)
        self.stop_voice = Mock()
        self.release_voice = Mock()
        self.quit = Mock()


def bare_group():
    group = PetGroup.__new__(PetGroup)
    group.pets = []
    group.primary = None
    group.closed = group.reunifying = group.copy_ready = False
    group.audio_members = set()
    group.cache = SharedCache(pet.FRAME_CACHE_BYTES)
    group.audio = SilentAudio()
    group.runtime = None
    group.root = SimpleNamespace(tk=SimpleNamespace(call=lambda *args: tuple(str(p.root) for p in group.pets)))
    return group


class MaskPet:
    def __init__(self, mask, x=0, y=0, scale=0.4):
        self.mask, self.x, self.y = mask, x, y
        self.global_scale = scale
        self.root = object()
        self._to_idle = Mock()
        self._save_scale = Mock()
        self._write_geometry_file = Mock()
        self._close_window = Mock()
        self.land = Mock()
        self.set_scale = Mock(side_effect=self._scale)

    def _scale(self, value, **kwargs):
        self.global_scale = value

    def screen_mask(self):
        return self.mask, self.x, self.y

    def foot_position(self):
        return self.x + self.mask.width / 2, self.y + self.mask.height


class GroupTests(unittest.TestCase):
    def test_overlap_counts_visible_pixels_and_ignores_transparent_windows(self):
        left, right = Image.new("L", (10, 10)), Image.new("L", (10, 10))
        left.paste(255, (0, 0, 4, 10))
        right.paste(255, (6, 0, 10, 10))
        a, b = MaskPet(left), MaskPet(right)
        self.assertEqual(PetGroup.overlap(a, b), 0)
        b.x = -5
        self.assertEqual(PetGroup.overlap(a, b), 30)

    def test_merge_sum_cap_identity_and_target_foot(self):
        for scales, expected in (((0.4, 0.4), 0.6), ((0.6, 0.6), 0.8), ((0.8, 0.8), 1.0)):
            for primary_index in (0, 1, 2):
                with self.subTest(scales=scales, primary=primary_index):
                    group = bare_group()
                    pets = [MaskPet(Image.new("L", (10, 10), 255), scale=s) for s in (*scales, 0.4)]
                    pets[2].x = 200
                    group.pets, group.primary = pets[:], pets[primary_index]
                    source, target, untouched = pets
                    anchor = target.foot_position()
                    self.assertTrue(group.try_merge(source))
                    self.assertEqual(group.pets, [target, untouched])
                    self.assertEqual(target.global_scale, expected)
                    target.set_scale.assert_called_once_with(expected, anchor=anchor, clamp=False)
                    source._close_window.assert_called_once()
                    target.land.assert_called_once()
                    self.assertIs(group.primary, untouched if primary_index == 2 else target)

    def test_largest_overlap_selected_before_cap_and_no_fallback(self):
        group = bare_group()
        source = MaskPet(Image.new("L", (10, 10), 255))
        large = MaskPet(Image.new("L", (10, 10), 255), scale=1.0)
        small = MaskPet(Image.new("L", (4, 4), 255))
        group.pets = [source, large, small]
        group.primary = source
        self.assertFalse(group.try_merge(source))
        self.assertEqual(len(group.pets), 3)
        for pet_window in group.pets:
            pet_window.set_scale.assert_not_called()
        large.global_scale = 0.4
        source.global_scale = 1.0
        self.assertFalse(group.try_merge(source))

    def test_equal_overlap_uses_topmost_and_only_one_pair(self):
        group = bare_group()
        group.pets = [MaskPet(Image.new("L", (10, 10), 255)) for _ in range(3)]
        source, lower, upper = group.pets
        group.primary = lower
        self.assertTrue(group.try_merge(source))
        self.assertEqual(upper.global_scale, 0.6)
        self.assertEqual(lower.global_scale, 0.4)
        self.assertIs(group.primary, lower)
        self.assertEqual(len(group.pets), 2)

    def test_broadcast_independent_voices_stop_locally(self):
        group = bare_group()
        group.pets = [app_probe(), app_probe()]
        a, b = group.pets
        group.primary = a
        for window in group.pets:
            window.group, window.audio = group, PetVoice(group, window)
        group.play_all("dance1")
        group.audio.play.assert_not_called()
        self.assertEqual(group.audio.play_voice.call_count, 2)
        self.assertEqual(a.last_tick, b.last_tick)
        self.assertEqual((a.acc, b.acc), (-170, -170))
        a._to_idle()
        group.audio.stop.assert_not_called()
        a.play("laugh", with_audio=True)
        self.assertEqual(group.audio.play_voice.call_args.args, (a, "laugh"))
        self.assertEqual((b.mode, b.cur_clip), ("playing", "dance1"))
        b._to_idle()
        group.audio.stop.assert_not_called()
        a._to_idle()
        group.audio.stop_voice.assert_called_with(a)

    def test_local_ten_clicks_do_not_count_or_start_laugh_on_other_pet(self):
        group = bare_group()
        group.pets = [app_probe(), app_probe()]
        a, b = group.pets
        group.primary = a
        for window in group.pets:
            window.group, window.audio = group, PetVoice(group, window)
        for _ in range(2):
            click(a)
        self.assertTrue(a._click_laugh_active)
        self.assertEqual((b.mode, len(b._clicks)), ("idle", 0))
        self.assertEqual(group.audio.play_voice.call_count, 1)
        group.audio.play.assert_not_called()
        for _ in range(2):
            click(b)
        self.assertEqual(group.audio.play_voice.call_count, 2)
        self.assertTrue(a._click_laugh_active and b._click_laugh_active)

    def test_shared_cache_budget_eviction_and_negative_monitor_clamp(self):
        cache = SharedCache(20)
        cache.put("a", object(), 10)
        cache.put("b", object(), 10)
        a = cache.get("a")
        cache.put("c", object(), 10)
        self.assertIs(cache.get("a"), a)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.bytes, 20)
        cache.put("oversized", object(), 100)
        self.assertEqual(cache.bytes, 20)
        area = (-1600, -900, 0, 0)
        self.assertEqual(constrain_origin(-100, -50, (10, 10, 110, 110), area), (-110, -110))
        self.assertEqual(constrain_origin(-1800, -1000, (10, 10, 110, 110), area), (-1610, -910))

    def test_automatic_performance_is_local_and_silent(self):
        group = bare_group()
        a, b = app_probe(), app_probe()
        group.pets, group.primary = [a, b], a
        for window in group.pets:
            window.group, window.audio = group, PetVoice(group, window)
        a.deadline, b.deadline = 1000, 899
        with patch("pet_app.time.monotonic", return_value=900), patch("pet_app.random.choice", return_value="dance1"):
            a.tick()
            b.tick()
        self.assertEqual((a.mode, b.mode, b.cur_clip), ("idle", "playing", "dance1"))
        group.audio.play.assert_not_called()
        group.audio.play_voice.assert_not_called()
        group.audio.stop_voice.assert_called_once_with(b)


@unittest.skipUnless(RUN_GUI, "use --gui for real multi-window checks")
class TkGroupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = Path(temp.name) / "settings.json"
        self.settings.write_text(json.dumps({"displayScale": 1.0, "randomMinSec": 180,
                                             "randomMaxSec": 240, "fps": None}), encoding="utf-8")
        self.audio = SilentAudio()
        for context in (patch("pet_app.SETTINGS", self.settings), patch("pet_app.AudioPlayer", return_value=self.audio),
                        patch.object(pet.PetApp, "_write_geometry_file"), patch("pet_app.log")):
            context.start()
            self.addCleanup(context.stop)
        self.app = pet.PetApp()
        self.group = self.app.group
        self.addCleanup(self.group.quit)
        self.errors = []
        self.group.root.report_callback_exception = lambda *exc: self.errors.append(str(exc[1]))
        self.app.draw()
        self.group.root.update()

    def pump(self, duration):
        self.group.root.after(round(duration * 1000), self.group.root.quit)
        self.group.root.mainloop()
        self.assertEqual(self.errors, [])

    def clone(self):
        return self.group.duplicate(self.app)

    def align(self, source, target):
        source._drop_jelly()
        target._drop_jelly()
        sb, tb = source.visible_box(), target.visible_box()
        x = target.root.winfo_x() + (tb[0] + tb[2] - sb[0] - sb[2]) / 2
        y = target.root.winfo_y() + (tb[1] + tb[3] - sb[1] - sb[3]) / 2
        place_window(source.root, x, y)
        source.draw()
        target.draw()

    def test_nearby_laughter_spreads_in_waves_on_shared_clock(self):
        app = self.app
        app.set_scale(0.4)
        targets = [self.group.spawn(source=app, scale=0.4) for _ in range(3)]
        for pet, x in zip([app] + targets, (400, 540, 680, 1100)):
            pet._queue_blink = Mock()
            pet._blink_deadline = float("inf")
            place_window(pet.root, x, 300)
        app.play_short_laugh(with_audio=False)
        app.tick()
        self.pump(0.55)
        self.assertTrue(all(p.mode == "idle" for p in targets))
        self.pump(0.35)
        self.assertEqual(targets[0].mode, "playing")
        self.assertEqual(targets[1].mode, "idle")
        self.pump(0.75)
        self.assertEqual(targets[1].mode, "playing")
        self.assertEqual(targets[2].mode, "idle")
        self.assertGreater(targets[1]._action_start, targets[0]._action_start + 0.65)
        for pet in targets[:2]:
            self.assertEqual((pet._range_first, pet._range_end), (120, 300))
            self.assertFalse(pet._action_audible)
            self.assertEqual(pet._growth_step, 0)
        self.audio.play_voice.assert_not_called()

    def test_show_existing_instance_recovers_offscreen_pet_and_stops_escape(self):
        app = self.app
        app.set_scale(0.4)
        self.group.throw(app, (1000, 1000))
        self.group.physics.particles[app].body.position = (-3500, 32767)
        app.mode = "thrown"
        place_window(app.root, -3500, 32767)
        self.group.runtime = SimpleNamespace(pending=Mock(return_value=["show"]))
        with patch("pet_group.work_area", return_value=(0, 0, 1000, 800)):
            self.group.dispatch()
        box = app.visible_box()
        x, y = app.root.winfo_x(), app.root.winfo_y()
        self.assertGreaterEqual(x + box[0], -1)
        self.assertGreaterEqual(y + box[1], -1)
        self.assertLessEqual(x + box[2], 1001)
        self.assertLessEqual(y + box[3], 801)
        self.assertFalse(self.group.physics.is_dynamic(app))
        self.assertEqual(app.mode, "idle")
        self.assertEqual(self.group.pets, [app])
        self.group.runtime = None

    def test_split_gold_ring_follows_size_outlives_parent_and_cleans_up(self):
        heights = []
        for scale in (0.4, 0.8):
            source = self.group.primary
            source.set_scale(scale)
            source.play_short_laugh(with_audio=False)
            self.group.request_split(source)
            self.group._emit_due(source._split_began + pet.SPLIT_DURATION_SEC)
            self.assertTrue(source._closed)
            self.assertEqual(len(self.group.split_effects), 1)
            effect = next(iter(self.group.split_effects))
            heights.append(effect.height)
            self.assertFalse(effect.closed)
            self.assertNotIn(effect, self.group.pets)
            self.pump(0.75)
            self.assertTrue(effect.closed)
            self.assertEqual(self.group.split_effects, set())
        self.assertAlmostEqual(heights[1] / heights[0], 2, delta=0.03)
        self.group._show_split_effect((400, 300), 150)
        effect = next(iter(self.group.split_effects))
        self.group.quit()
        self.assertTrue(effect.closed)

    def test_growth_split_once_and_throw_real_windows(self):
        app = self.app
        app.set_scale(0.4)
        app.play("laugh", with_audio=True)
        app.tick()
        start = app._action_start
        for _ in range(5):
            app._complete_split_click(time.monotonic())
            self.assertEqual(app.global_scale, 0.4)
        self.pump(0.8)
        self.assertAlmostEqual(app.global_scale, 0.4 + 5 / 30)
        self.assertEqual(app._action_start, start)
        self.assertEqual(json.loads(self.settings.read_text())["displayScale"], 0.4)
        for _ in range(7):
            app._complete_split_click(time.monotonic())
        self.assertEqual(len(self.group.pending_bursts), 1)
        self.pump(0.3)
        self.assertEqual(len(app._split_ghost_layers), 2)
        self.assertEqual(len(app.canvas.find_withtag("split_ghost")), 0)
        self.assertEqual((app.root.winfo_x(), app.root.winfo_y()), app._split_origin)
        box = app.visible_box()
        cx, py = (box[0] + box[2]) / 2, box[3] - (box[3] - box[1]) / 3
        offset = (box[2] - box[0]) / 4
        self.assertEqual(app._split_anchors, ((cx - offset, py), (cx + offset, py)))
        self.assertEqual(app.canvas.coords(app.image_id), [0.0, 0.0])
        self.pump(1.4)
        self.assertEqual(len(self.group.pets), 2)
        self.assertTrue(app._closed)
        self.assertNotIn(app, self.group.pets)
        self.assertIn(self.group.primary, self.group.pets)
        for child in self.group.pets:
            self.assertEqual(child.global_scale, 0.4)
            self.assertEqual(child._growth_step, 0)
            self.assertEqual(child._action_start, start)
            self.assertTrue(self.group.physics.is_dynamic(child))
        child = self.group.pets[0]
        self.group.throw(child, (500, -700))
        velocity = self.group.physics.particles[child].body.velocity
        self.assertEqual(tuple(velocity), (900, -1260))

    def test_batch_growth_bounces_crosses_tier_and_small_tier_splits(self):
        app = self.app
        app.set_scale(0.8)
        app.play("laugh")
        app.tick()
        sizes = []
        resize = app.set_scale
        def record(scale, **kwargs):
            if kwargs.get("growing"):
                sizes.append(scale)
            return resize(scale, **kwargs)
        app.set_scale = record
        for _ in range(3):
            app._complete_split_click(time.monotonic())
        self.pump(0.20)
        self.assertEqual(app.global_scale, 0.8)
        self.pump(0.65)
        self.assertAlmostEqual(app.global_scale, 0.9)
        self.assertGreater(max(sizes), 0.9)
        self.assertEqual(app._growth_step, 3)
        for _ in range(6):
            app._complete_split_click(time.monotonic())
        self.pump(0.85)
        self.assertNotIn(app, self.group.pets)
        self.assertTrue(all(p.global_scale == 0.8 for p in self.group.pets))

    def test_growth_keeps_window_fixed_and_rebuilds_resources_only_at_end(self):
        app = self.app
        app.set_scale(0.8)
        app.play("laugh")
        app.tick()
        foot = app.foot_position()
        with patch.object(app, "_setup_eyes", wraps=app._setup_eyes) as eyes, \
             patch.object(self.group, "resize_physics", wraps=self.group.resize_physics) as physics:
            app._animate_growth(1.0, time.monotonic())
            surface_size = (app.win_w, app.win_h)
            origin = app.root.winfo_x(), app.root.winfo_y()
            self.pump(0.20)
            self.assertEqual((app.win_w, app.win_h), surface_size)
            self.assertEqual((app.root.winfo_x(), app.root.winfo_y()), origin)
            self.assertEqual(eyes.call_count, 0)
            self.assertEqual(physics.call_count, 1)
            mask, x, y = app.screen_mask()
            box = mask.getbbox()
            self.assertIsNotNone(box)
            # The actual transformed image, not its former size, owns hit testing.
            self.assertFalse(app._is_opaque(0, 0))
            pixel = next((i % mask.width, i // mask.width)
                         for i, value in enumerate(mask.tobytes()) if value)
            self.assertTrue(app._is_opaque(x - app.root.winfo_x() + pixel[0],
                                           y - app.root.winfo_y() + pixel[1]))
            self.pump(0.3)
            self.assertIsNone(app._growth_surface)
            self.assertIsNone(app._growth_timer)
            self.assertEqual(eyes.call_count, 1)
            self.assertEqual(physics.call_count, 2)
        self.assertAlmostEqual(app.global_scale, 1.0)
        self.assertLessEqual(max(abs(a - b) for a, b in zip(foot, app.foot_position())), 1)

    def test_maximum_puff_has_unclipped_native_frame_and_returns_to_one(self):
        app = self.app
        app.set_scale(1.0)
        app.play("laugh")
        for n in range(5):
            app._complete_split_click(10 + n * 0.03)
        app._settle_growth_clicks(11)
        app._advance_growth(11.16)
        self.assertGreater(app.global_scale, 1.0)
        box = app._growth_surface["mask"].getbbox()
        self.assertGreater(box[0], 0)
        self.assertGreater(box[1], 0)
        self.assertLess(box[2], app.win_w)
        self.assertLess(box[3], app.win_h)
        app._advance_growth(11.41)
        self.assertEqual(app.global_scale, 1.0)
        self.assertIsNone(app._growth_surface)
        self.assertEqual(app._native_surface.size, (app.win_w, app.win_h))

    def test_growth_animation_has_dense_frames_and_manual_resize_restores_window(self):
        app = self.app
        app.set_scale(0.8)
        app.play("laugh")
        app.tick()
        renders = []
        resize = app.set_scale
        def record(scale, **kwargs):
            if kwargs.get("growing"):
                renders.append((time.perf_counter(), scale))
            return resize(scale, **kwargs)
        app.set_scale = record
        app._animate_growth(1.0, time.monotonic())
        self.pump(0.5)
        self.assertGreaterEqual(len(renders), 24)
        app._animate_growth(1.1, time.monotonic())
        self.pump(0.08)
        app.set_scale(0.8)
        self.assertIsNone(app._growth_surface)
        self.assertIsNone(app._growth_anim)
        self.assertIsNone(app._growth_timer)
        self.assertEqual(app.win_w, round(app.base_w * 0.8))

    def test_spawn_has_complete_first_frame_before_showing_window(self):
        move = place_window
        observed = []
        def check_move(window, x, y, size=None, **kwargs):
            child = next((p for p in self.group.pets if p.root is window and p is not self.app), None)
            if child is not None:
                name = child.canvas.itemcget(child.image_id, "image")
                self.assertTrue(name)
                self.assertGreater(int(child.root.tk.call("image", "width", name)), 0)
                observed.append(child)
            return move(window, x, y, size, **kwargs)
        self.app.play("laugh")
        with patch("pet_group.place_window", side_effect=check_move):
            self.group.spawn(source=self.app)
            self.group.spawn(source=self.app, emitted=True)
        self.assertEqual(len(observed), 2)

    def test_dynamic_frames_stay_immutable_with_transparent_corners(self):
        import gc
        from PIL import ImageTk, ImageChops
        app = self.app
        app.set_scale(0.8)
        app.play("laugh")
        app._animate_growth(1.0, time.monotonic())
        retained = []
        for frame in range(20):
            app.global_scale = 0.8 + frame * 0.005
            app.draw()
            gc.collect()
            app.root.update_idletasks()
            name = str(app._display_photo)
            self.assertEqual(app.canvas.itemcget(app.image_id, "image"), name)
            self.assertEqual(int(app.root.tk.call("image", "width", name)), app.win_w)
            image = ImageTk.getimage(app._display_photo).convert("RGB")
            self.assertTrue(all(image.getpixel(point) == pet.CHROMA for point in (
                (0, 0), (image.width - 1, 0), (0, image.height - 1),
                (image.width - 1, image.height - 1))))
            retained.append((app._display_photo, image))
        for photo, expected in retained:
            self.assertIsNone(ImageChops.difference(ImageTk.getimage(photo).convert("RGB"), expected).getbbox())
        app.set_scale(0.8)
        retained.clear()
        self.group.request_split(app)
        for progress in (0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 1):
            app.draw_split_ghosts(progress)
            gc.collect()
            name = str(app._display_photo)
            self.assertEqual(app.canvas.itemcget(app.image_id, "image"), name)
            retained.append((app._display_photo, ImageTk.getimage(app._display_photo).convert("RGB")))
        for photo, expected in retained:
            self.assertIsNone(ImageChops.difference(ImageTk.getimage(photo).convert("RGB"), expected).getbbox())

    @unittest.skipUnless(sys.platform == "win32", "native transparent layer")
    def test_native_growth_updates_alpha_size_and_position_together(self):
        import ctypes as C
        from ctypes import wintypes as W
        from PIL import ImageChops
        app = self.app
        app.set_scale(0.4)
        place_window(app.root, 180, 180)
        app.play("laugh")
        app._animate_growth(0.8, time.monotonic())
        surface = app._native_surface
        user = surface.user
        user.WindowFromPoint.argtypes = [W.POINT]
        user.WindowFromPoint.restype = W.HWND
        for scale in (0.4, 0.56, 0.7, 0.84, 0.8):
            app.global_scale = scale
            app.draw()
            app.root.update()
            self.assertEqual(surface.size, (app.root.winfo_width(), app.root.winfo_height()))
            native = Image.frombytes("RGBA", surface.size,
                C.string_at(surface.bits, surface.size[0] * surface.size[1] * 4), "raw", "BGRA")
            alpha = native.getchannel("A")
            self.assertEqual(alpha.getpixel((0, 0)), 0)
            self.assertEqual(native.getpixel((0, 0)), (0, 0, 0, 0))
            transparent = alpha.point(lambda v: 255 if v == 0 else 0)
            for band in native.split()[:3]:
                self.assertIsNone(ImageChops.multiply(band, transparent).getbbox())
            # Windows hit-testing must follow the visible sprite, not its rectangle.
            x, y = app.root.winfo_x(), app.root.winfo_y()
            outside = user.WindowFromPoint(W.POINT(x, y))
            self.assertNotEqual(user.GetAncestor(outside, 2), surface.hwnd)
            C.WinDLL("dwmapi").DwmFlush()
            index = min((i for i, value in enumerate(alpha.tobytes()) if value == 255),
                        key=lambda i: (i % native.width - native.width / 2) ** 2
                                      + (i // native.width - native.height / 2) ** 2)
            point = W.POINT(x + index % native.width, y + index // native.width)
            inside = user.WindowFromPoint(point)
            root_hit = user.GetAncestor(inside, 2)
            if root_hit != surface.hwnd:
                title = C.create_unicode_buffer(256)
                user.GetWindowTextW(W.HWND(root_hit), title, 256)
                rect = W.RECT()
                user.GetWindowRect(surface.hwnd, C.byref(rect))
                self.fail(f"hit={root_hit}:{title.value}, expected={surface.hwnd}, "
                          f"point={point.x, point.y}, rect={rect.left, rect.top, rect.right, rect.bottom}, "
                          f"current={user.GetAncestor(app.root.winfo_id(), 2)}")
        app._growth_anim = None
        app.set_scale(0.8, growing=True)
        self.assertEqual(app._native_surface.size, (app.win_w, app.win_h))
        self.assertIsNone(app._growth_surface)

    @unittest.skipUnless(sys.platform == "win32", "native GDI ownership")
    def test_native_surface_resizes_without_leaking_bitmaps(self):
        import ctypes as C
        from ctypes import wintypes as W
        surface = self.app._native_surface
        kernel = C.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = W.HANDLE
        user = surface.user
        user.GetGuiResources.argtypes = [W.HANDLE, W.DWORD]
        user.GetGuiResources.restype = W.DWORD
        process = kernel.GetCurrentProcess()
        before = user.GetGuiResources(process, 0)
        for step in range(40):
            # A native resize must replace and release its former DIB.
            frame = Image.new("RGBA", (120 + step, 150 + step))
            frame.paste((240, 180, 20, 255), (30, 30, 80, 110))
            surface.present(frame, (180, 180))
        after = user.GetGuiResources(process, 0)
        self.assertLessEqual(after, before + 2)
        self.app.draw()

    def test_resize_binds_complete_frame_before_window_redraw(self):
        app = self.app
        move = pet.place_window
        seen = []
        def check_move(window, x, y, size=None, **kwargs):
            if window is app.root and size is not None:
                name = app.canvas.itemcget(app.image_id, "image")
                self.assertTrue(name)
                ox, oy = app.canvas.coords(app.image_id)
                self.assertEqual((int(app.root.tk.call("image", "width", name)) + 2 * ox,
                                  int(app.root.tk.call("image", "height", name)) + 2 * oy), size)
                seen.append(size)
            return move(window, x, y, size, **kwargs)
        app.play("laugh")
        with patch("pet_app.place_window", side_effect=check_move):
            app.set_scale(0.8)
            app._animate_growth(1.0, time.monotonic())
            app._growth_anim = None
            app.set_scale(1.0, growing=True)
        self.assertEqual(len(seen), 3)

    def test_shake_reuses_recent_poses_for_trails(self):
        app = self.app
        app.play("laugh")
        self.group.request_split(app)
        with patch.object(app, "_split_body_pose", wraps=app._split_body_pose) as rotate:
            for frame in range(5):
                app.draw_split_ghosts(frame / (60 * 0.75))
            # Five main poses plus only the initial two reconstructed trails.
            self.assertLessEqual(rotate.call_count, 7)
        self.assertEqual(len(app._split_ghost_layers), 2)
        self.group.cancel_emission(app)
        self.assertIsNone(app._split_pose_history)

    def test_shake_is_one_transparent_image_without_rectangular_layers(self):
        from PIL import ImageTk, ImageChops
        app = self.app
        app.play("laugh")
        self.group.request_split(app)
        for progress in (0.85, 0.93, 1):
            app.draw_split_ghosts(progress)
            rendered = ImageTk.getimage(app._display_photo).convert("RGB")
            self.assertTrue(all(rendered.getpixel(point) == pet.CHROMA for point in (
                (0, 0), (rendered.width - 1, 0), (0, rendered.height - 1),
                (rendered.width - 1, rendered.height - 1))))
            self.assertEqual(len(app.canvas.find_all()), 1)
            rgba = app._read_rgba(app.cur_clip, app.cur_index)
            clip = app.clips[app.cur_clip]
            stage = Image.new("RGBA", rendered.size)
            stage.alpha_composite(rgba, (clip.fx, clip.fy))
            pose = app._split_body_pose(stage, progress)
            outside_body = ImageChops.subtract(app._split_ghost_layers[1].getchannel("A"),
                                               pet._opaque_mask(pose))
            self.assertIsNotNone(outside_body.getbbox())
            # Ghost pixels remain visible even inside the main image's rectangle.
            visible = ImageChops.difference(rendered, Image.new("RGB", rendered.size, pet.CHROMA))
            self.assertIsNotNone(ImageChops.multiply(visible.convert("L"), outside_body).getbbox())

    def test_next_numbered_tier_real_children_and_short_charge(self):
        for scale, expected in ((1.0, 0.8), (0.8, 0.6), (0.6, 0.4), (0.4, 0.4)):
            source = self.group.primary
            source.set_scale(scale)
            source.play("laugh")
            before = set(self.group.pets)
            self.group.request_split(source)
            self.pump(0.2)
            self.assertIn(source, self.group.pets)
            self.assertLessEqual(abs(source._split_angle), 24)
            self.pump(0.6)
            children = set(self.group.pets) - before
            self.assertNotIn(source, self.group.pets)
            self.assertEqual(len(children), 2)
            self.assertTrue(all(p.global_scale == expected and p._growth_base == expected
                                and p._growth_step == 0 for p in children))

    def test_growth_manual_reset_and_laugh_end_during_charge(self):
        app = self.app
        app.set_scale(0.4)
        app.play("laugh")
        app._complete_split_click(time.monotonic())
        app.set_scale(0.4)
        self.assertEqual(app._growth_step, 0)
        self.group.request_split(app)
        app._to_idle()
        self.pump(1.65)
        self.assertEqual(len(self.group.pets), 2)
        self.assertTrue(all(p.mode == "idle" for p in self.group.pets))
        self.assertTrue(all(p.global_scale == 0.4 for p in self.group.pets))

    def test_menu_keyboard_copy_beyond_forty_share_one_interpreter(self):
        self.assertIsNone(self.group.paste())
        self.app.menu.invoke("复制分身")
        clone = self.group.pets[-1]
        self.assertIs(clone.root.tk, self.app.root.tk)
        self.assertIs(clone.root.tk, self.group.root.tk)
        self.assertEqual((clone.global_scale, clone.mode, len(clone._clicks)), (0.4, "idle", 0))
        self.assertIs(self.group.primary, self.app)
        self.audio.prewarm.assert_called_once()
        self.group.root.winfo_pointerxy = Mock(return_value=(500, 350))
        self.group.runtime = SimpleNamespace(pending=Mock(return_value=["copy", "paste"]), close=Mock())
        self.group.dispatch()
        self.group.runtime.pending.return_value = []
        pasted = self.group.pets[-1]
        box = pasted.visible_box()
        self.assertAlmostEqual(pasted.root.winfo_x() + (box[0] + box[2]) / 2, 500, delta=1)
        while len(self.group.pets) < 41:
            self.clone()
        self.assertIsNotNone(self.group.paste())
        self.assertEqual(len(self.group.pets), 42)
        source = self.group.pets[-2]
        self.assertTrue(self.group.try_merge(source))
        self.assertEqual(len(self.group.pets), 41)
        self.assertEqual(self.group.paste().global_scale, 0.4)
        self.assertEqual(len(self.group.pets), 42)
        # Creation never overwrites the saved primary size.
        self.assertEqual(json.loads(self.settings.read_text())["displayScale"], 1.0)

    def test_right_click_changes_primary_even_when_menu_dismissed_and_size_is_local(self):
        clone = self.clone()
        self.audio.prewarm.assert_called_once_with("laugh")
        for window in (self.app, clone):
            self.assertEqual(list(window.clips), ["laugh"])
            self.assertEqual(window.order, ["laugh"])
            labels = [window.menu.entrycget(i, "label") for i in range(window.menu.index("end") + 1)
                      if window.menu.type(i) != "separator"]
            self.assertIn("捧腹大笑", labels)
            self.assertNotIn("卡牌摇", labels)
            self.assertNotIn("小苹果", labels)
            self.assertFalse(window.root.bind("<Key-2>"))
            self.assertFalse(window.root.bind("<Key-3>"))
            window.play("dance1", with_audio=True)
            window.play("dance2", with_audio=True)
            self.assertEqual(window.mode, "idle")
        with patch.object(clone.menu, "tk_popup"):
            clone._on_right(event(clone))
        self.assertIs(self.group.primary, clone)
        self.assertEqual(json.loads(self.settings.read_text())["displayScale"], 0.4)
        clone.size_menu.invoke("变大  ＋")
        self.assertEqual((clone.global_scale, self.app.global_scale), (0.6, 1.0))
        clone.menu.invoke("捧腹大笑")
        self.assertEqual(self.app.mode, "idle")
        self.assertEqual(clone.mode, "playing")
        clone.menu.invoke("全部大笑")
        self.assertTrue(all(p.cur_clip == "laugh" and p.mode == "playing" for p in self.group.pets))
        self.audio.play.assert_not_called()
        self.assertEqual(self.audio.play_voice.call_count, 3)

    def test_frames_and_eye_assets_shared_but_eye_state_and_current_photos_survive_eviction(self):
        a, b = self.clone(), self.clone()
        self.assertIs(a._read_rgba("laugh", 0), b._read_rgba("laugh", 0))
        self.assertIs(a._frame_image("laugh", 0), b._frame_image("laugh", 0))
        self.assertIs(a._eyes.static, b._eyes.static)
        a._eyes.advance((999, 999), time.monotonic())
        self.assertNotEqual(a._eyes.position, b._eyes.position)
        a.play("laugh")
        current = a._display_photo
        self.group.cache.clear()
        self.assertEqual(a.root.tk.call("image", "width", str(current)), a.clips["laugh"].fw)
        self.assertEqual(a.canvas.itemcget(a.image_id, "image"), str(current))

    def test_merge_preserves_target_foot_and_primary_identity_then_paste_still_works(self):
        target = self.clone()
        self.app.set_scale(0.8)
        target.set_scale(0.8)
        self.align(self.app, target)
        foot = target.foot_position()
        self.assertTrue(self.group.try_merge(self.app))
        self.assertIs(self.group.primary, target)
        self.assertEqual(target.global_scale, 1.0)
        self.assertLessEqual(max(abs(a - b) for a, b in zip(foot, target.foot_position())), 1)
        self.assertEqual(target._body.phase, "landing")
        self.assertTrue(self.app._closed)
        self.assertIsNone(self.app._tick_after)
        self.assertEqual(json.loads(self.settings.read_text())["displayScale"], 1.0)
        self.assertEqual(self.group.paste().global_scale, 0.4)

    def test_real_drag_release_merges_only_once_and_interrupts_only_participants(self):
        source, target, spectator = self.clone(), self.clone(), self.clone()
        place_window(target.root, 500, 300)
        place_window(source.root, 200, 300)
        place_window(spectator.root, 100, 50)
        self.group.play_all("laugh")
        self.assertEqual(self.audio.play_voice.call_count, 4)
        ev = event(source)
        source._on_press(ev)
        moved = SimpleNamespace(x=ev.x, y=ev.y, x_root=ev.x_root + 300, y_root=ev.y_root)
        source._on_motion(moved)
        self.assertEqual(source.mode, "playing")
        self.assertIsNone(source._body)
        self.pump(0.15)  # Gentle placement after the swipe has stopped.
        source._on_release(moved)
        self.assertTrue(source._closed)
        self.assertEqual(target.global_scale, 0.6)
        self.assertEqual((spectator.mode, spectator.cur_clip), ("playing", "laugh"))
        self.assertTrue(spectator._action_audible)
        self.audio.stop.assert_not_called()

    def test_reunify_locks_interaction_returns_all_and_keeps_primary_size(self):
        first, second = self.clone(), self.clone()
        self.group.set_primary(second)
        original_size, original_foot = second.global_scale, second.foot_position()
        began = time.monotonic()
        self.group.reunify()
        self.assertTrue(self.group.reunifying)
        self.assertEqual(first._body.phase, "pickup")
        self.assertIsNone(self.group.paste())
        self.assertFalse(self.group.try_merge(first))
        self.group.set_primary(first)
        self.assertIs(self.group.primary, second)
        self.pump(REUNIFY_SEC + 0.12)
        self.assertGreaterEqual(time.monotonic() - began, REUNIFY_SEC)
        self.assertEqual(self.group.pets, [second])
        self.assertFalse(self.group.reunifying)
        self.assertEqual(second.global_scale, original_size)
        self.assertEqual(second.foot_position(), original_foot)
        self.assertEqual(second._body.phase, "landing")
        self.assertIsNone(first._tick_after)
        self.assertEqual(self.group.paste().global_scale, 0.4)

    def test_exit_during_reunify_closes_all_and_restart_restores_only_primary_size(self):
        clone = self.clone()
        self.group.set_primary(clone)
        clone.set_scale(0.8)
        self.group.reunify()
        self.group.quit()
        self.assertTrue(self.group.closed)
        self.assertIsNone(self.group._reunify_after)
        self.assertEqual(self.group.pets, [])
        self.audio.quit.assert_called_once()
        self.group.quit()
        restarted = pet.PetApp()
        self.addCleanup(restarted.quit)
        self.assertEqual(restarted.global_scale, 0.8)
        self.assertEqual(len(restarted.group.pets), 1)
        self.assertFalse(restarted.group.copy_ready)

    def test_negative_coordinates_survive_move_resize_and_edge_paste(self):
        place_window(self.app.root, -600, -500)
        self.assertEqual((self.app.root.winfo_x(), self.app.root.winfo_y()), (-600, -500))
        area = (-1600, -900, 0, 0)
        foot = self.app.foot_position()
        with patch("pet_app.work_area", return_value=area):
            self.app.set_scale(0.8)
        self.assertLess(self.app.root.winfo_x(), 0)
        self.assertLess(self.app.root.winfo_y(), 0)
        self.assertLessEqual(max(abs(a - b) for a, b in zip(foot, self.app.foot_position())), 1)
        self.group.copy()
        self.group.root.winfo_pointerxy = Mock(return_value=(-1, -1))
        with patch("pet_group.work_area", return_value=area):
            clone = self.group.paste()
        box = clone.visible_box()
        self.assertLessEqual(clone.root.winfo_x() + box[2], 0)
        self.assertLessEqual(clone.root.winfo_y() + box[3], 0)

    def save_scene(self, name):
        from pet_group import work_area
        left, top, right, bottom = work_area(self.app.foot_position())
        scene = Image.new("RGBA", (right - left, bottom - top), "#e8eef5")
        for window in self.group.pets:
            clip = window.clips[window.cur_clip]
            frame = window._read_rgba(window.cur_clip, window.cur_index)
            scene.alpha_composite(frame, (window.root.winfo_x() + clip.fx - left,
                                          window.root.winfo_y() + clip.fy - top))
        (ROOT / "reports").mkdir(exist_ok=True)
        scene.convert("RGB").save(ROOT / "reports" / name)

    def test_recursive_split_children_finish_once_and_stop_locally(self):
        self.app.set_scale(1.0)
        self.app.play("laugh", with_audio=True)
        self.app.tick()
        for _ in range(6):
            click(self.app)
        self.pump(1.65)
        children = list(self.group.pets)
        self.assertEqual(len(children), 2)
        self.assertTrue(all(p._range_first == 53 for p in children))
        children[0].stop_laugh()
        self.assertEqual(children[0].mode, "idle")
        self.assertEqual(children[1].mode, "playing")
        for _ in range(6):
            click(children[1])
        self.pump(0.85)
        self.assertEqual(len(self.group.pets), 3)
        self.assertNotIn(children[1], self.group.pets)
        self.group.stop_all_laughs()
        self.assertTrue(all(p.mode == "idle" for p in self.group.pets))

    def test_forty_emitted_windows_and_exit_clear_world_and_queue(self):
        samples = []
        advance = self.group.physics.advance
        def measured_advance(elapsed):
            began = time.perf_counter()
            advance(elapsed)
            samples.append((elapsed, time.perf_counter() - began))
        self.group.physics.advance = measured_advance
        self.app.set_scale(1.0)
        place_window(self.app.root, 600, 250)
        self.app.play("laugh", with_audio=True)
        self.app.tick()
        for _ in range(39):
            self.group.spawn(source=self.app, scale=0.4, emitted=True)
        self.assertEqual(self.group.request_split(self.app), 2)
        self.assertEqual(len(self.group.pets), 40)
        self.assertEqual(len(self.group.physics.particles), 40)
        self.assertEqual(self.audio.play_voice.call_count, 40)
        self.pump(2.5)
        self.assertEqual(len(self.group.pets), 41)
        self.app = self.group.primary
        (ROOT / "reports").mkdir(exist_ok=True)
        (ROOT / "reports" / "split_timing.json").write_text(json.dumps({
            "ticks": len(samples), "elapsedSeconds": sum(s[0] for s in samples),
            "maxTickInterval": max(s[0] for s in samples),
            "physicsCostSeconds": sum(s[1] for s in samples),
            "positionsY": [e.body.position.y for e in self.group.physics.particles.values()],
        }, indent=2), encoding="utf-8")
        self.save_scene("split_forty.png")
        for window, entry in self.group.physics.particles.items():
            if self.group.physics.is_dynamic(window):
                x, y = entry.body.position.x - entry.offset[0], entry.body.position.y - entry.offset[1]
                self.assertAlmostEqual(window.root.winfo_x(), x, delta=2)
                self.assertAlmostEqual(window.root.winfo_y(), y, delta=2)
        self.group.quit()
        self.assertEqual(self.group.physics.particles, {})
        self.assertEqual(self.group.pending_bursts, [])
        self.assertIsNone(self.group._physics_after)
        self.assertIsNone(self.group._physics_timer)

    def test_merge_and_reunify_stop_bounded_performance(self):
        self.app.play("laugh", with_audio=True)
        target = self.group.spawn(source=self.app, scale=0.8, emitted=True)
        place_window(target.root, 500, 300)
        source = self.clone()
        source.set_scale(0.8)
        self.align(source, target)
        self.assertTrue(self.group.try_merge(source))
        self.assertEqual(target.global_scale, 1.0)
        self.assertEqual(target.mode, "idle")
        self.group.set_primary(target)
        self.group.reunify()
        self.pump(REUNIFY_SEC + 0.1)
        self.assertEqual(self.group.pets, [target])
        self.assertEqual(target.mode, "idle")


if __name__ == "__main__":
    unittest.main(verbosity=2)
