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
                       place_window, REUNIFY_SEC, MAX_PETS)

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
        for scales, expected in (((0.4, 0.4), 0.8), ((0.8, 0.8), 1.2)):
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
        large = MaskPet(Image.new("L", (10, 10), 255), scale=1.2)
        small = MaskPet(Image.new("L", (4, 4), 255))
        group.pets = [source, large, small]
        group.primary = source
        self.assertFalse(group.try_merge(source))
        self.assertEqual(len(group.pets), 3)
        for pet_window in group.pets:
            pet_window.set_scale.assert_not_called()
        large.global_scale = 0.4
        source.global_scale = 1.2
        self.assertFalse(group.try_merge(source))

    def test_equal_overlap_uses_topmost_and_only_one_pair(self):
        group = bare_group()
        group.pets = [MaskPet(Image.new("L", (10, 10), 255)) for _ in range(3)]
        source, lower, upper = group.pets
        group.primary = lower
        self.assertTrue(group.try_merge(source))
        self.assertEqual(upper.global_scale, 0.8)
        self.assertEqual(lower.global_scale, 0.4)
        self.assertIs(group.primary, lower)
        self.assertEqual(len(group.pets), 2)

    def test_broadcast_single_track_members_detach_and_local_voices_stay_independent(self):
        group = bare_group()
        group.pets = [app_probe(), app_probe()]
        a, b = group.pets
        group.primary = a
        for window in group.pets:
            window.group, window.audio = group, PetVoice(group, window)
        group.play_all("laugh")
        group.audio.play.assert_called_once_with("laugh")
        self.assertEqual(group.audio_members, {a, b})
        self.assertEqual(a.last_tick, b.last_tick)
        self.assertEqual((a.acc, b.acc), (-170, -170))
        a._to_idle()
        group.audio.stop.assert_not_called()
        self.assertEqual(group.audio_members, {b})
        a.play("laugh", with_audio=True)
        group.audio.play_voice.assert_called_once_with(a, "laugh")
        self.assertEqual((b.mode, b.cur_clip), ("playing", "laugh"))
        b._to_idle()
        group.audio.stop.assert_called_once()
        a._to_idle()
        group.audio.stop_voice.assert_called_with(a)

    def test_local_ten_clicks_do_not_count_or_start_laugh_on_other_pet(self):
        group = bare_group()
        group.pets = [app_probe(), app_probe()]
        a, b = group.pets
        group.primary = a
        for window in group.pets:
            window.group, window.audio = group, PetVoice(group, window)
        for _ in range(10):
            click(a)
        self.assertTrue(a._click_laugh_active)
        self.assertEqual((b.mode, len(b._clicks)), ("idle", 0))
        group.audio.play_voice.assert_called_once_with(a, "laugh")
        group.audio.play.assert_not_called()
        for _ in range(10):
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
        with patch("pet_app.time.monotonic", return_value=900), patch("pet_app.random.choice", return_value="laugh"):
            a.tick()
            b.tick()
        self.assertEqual((a.mode, b.mode, b.cur_clip), ("idle", "playing", "laugh"))
        group.audio.play.assert_not_called()
        group.audio.play_voice.assert_not_called()
        group.audio.stop_voice.assert_called_once_with(b)


@unittest.skipUnless(RUN_GUI, "use --gui for real multi-window checks")
class TkGroupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = Path(temp.name) / "settings.json"
        self.settings.write_text(json.dumps({"displayScale": 0.8, "randomMinSec": 180,
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

    def test_menu_keyboard_copy_and_twenty_pet_limit_share_one_interpreter(self):
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
        while len(self.group.pets) < MAX_PETS:
            self.clone()
        self.assertIsNone(self.group.paste())
        self.assertEqual(len(self.group.pets), MAX_PETS)
        source = self.group.pets[-2]
        self.assertTrue(self.group.try_merge(source))
        self.assertEqual(len(self.group.pets), MAX_PETS - 1)
        self.assertEqual(self.group.paste().global_scale, 0.4)
        self.assertEqual(len(self.group.pets), MAX_PETS)
        # Creation never overwrites the saved primary size.
        self.assertEqual(json.loads(self.settings.read_text())["displayScale"], 0.8)

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
        self.assertEqual((clone.global_scale, self.app.global_scale), (0.5, 0.8))
        clone.menu.invoke("捧腹大笑")
        self.assertTrue(all(p.cur_clip == "laugh" and p.mode == "playing" for p in self.group.pets))
        self.audio.play.assert_called_once_with("laugh")

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
        target.set_scale(0.8)
        self.align(self.app, target)
        foot = target.foot_position()
        self.assertTrue(self.group.try_merge(self.app))
        self.assertIs(self.group.primary, target)
        self.assertEqual(target.global_scale, 1.2)
        self.assertLessEqual(max(abs(a - b) for a, b in zip(foot, target.foot_position())), 1)
        self.assertEqual(target._body.phase, "landing")
        self.assertTrue(self.app._closed)
        self.assertIsNone(self.app._tick_after)
        self.assertEqual(json.loads(self.settings.read_text())["displayScale"], 1.2)
        self.assertEqual(self.group.paste().global_scale, 0.4)

    def test_real_drag_release_merges_only_once_and_interrupts_only_participants(self):
        source, target, spectator = self.clone(), self.clone(), self.clone()
        place_window(target.root, 500, 300)
        place_window(source.root, 200, 300)
        place_window(spectator.root, 100, 50)
        self.group.play_all("laugh")
        self.assertTrue(self.group.audio_members)
        ev = event(source)
        source._on_press(ev)
        moved = SimpleNamespace(x=ev.x, y=ev.y, x_root=ev.x_root + 300, y_root=ev.y_root)
        source._on_motion(moved)
        self.assertEqual(source.mode, "idle")
        self.assertEqual(source._body.phase, "pickup")
        source._on_release(moved)
        self.assertTrue(source._closed)
        self.assertEqual(target.global_scale, 0.8)
        self.assertEqual((spectator.mode, spectator.cur_clip), ("playing", "laugh"))
        self.assertIn(spectator, self.group.audio_members)
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
        clone.set_scale(0.6)
        self.group.reunify()
        self.group.quit()
        self.assertTrue(self.group.closed)
        self.assertIsNone(self.group._reunify_after)
        self.assertEqual(self.group.pets, [])
        self.audio.quit.assert_called_once()
        self.group.quit()
        restarted = pet.PetApp()
        self.addCleanup(restarted.quit)
        self.assertEqual(restarted.global_scale, 0.6)
        self.assertEqual(len(restarted.group.pets), 1)
        self.assertFalse(restarted.group.copy_ready)

    def test_negative_coordinates_survive_move_resize_and_edge_paste(self):
        place_window(self.app.root, -600, -500)
        self.assertEqual((self.app.root.winfo_x(), self.app.root.winfo_y()), (-600, -500))
        area = (-1600, -900, 0, 0)
        foot = self.app.foot_position()
        with patch("pet_app.work_area", return_value=area):
            self.app.set_scale(0.6)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
