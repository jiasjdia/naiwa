# -*- coding: utf-8 -*-
"""全身局部蓄力与拖放回弹回归:真实素材 + 真实交互逻辑。

运行: python tools/test_jelly.py
实际 Tk 绘制和帧率: python tools/test_jelly.py --gui (音频替身、临时配置)
"""
from __future__ import annotations

import json
import gc
import math
import random
import re
import subprocess
from collections import deque
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pet_app as pet  # noqa: E402

with Image.open(ROOT / "assets/laugh/f0001.webp") as source:
    SOURCE = source.convert("RGBA")
MANIFEST = json.loads((ROOT / "tools/fixtures/probe_manifest.json").read_text(encoding="utf-8"))
RUN_GUI = "--gui" in sys.argv
if RUN_GUI:
    sys.argv.remove("--gui")


def scaled(scale=0.4):
    return SOURCE.resize((round(320 * scale), round(305 * scale)), Image.Resampling.LANCZOS)


def app_probe(scale=0.4):
    """跳过会创建窗口和预热音频的构造,保留真实的点击/命中/播放/tick 方法。"""
    app = pet.PetApp.__new__(pet.PetApp)
    app.clips = {m["id"]: pet.Clip(m, ROOT / "assets", scale) for m in MANIFEST["clips"]}
    app.idle_clip = app.cur_clip = "laugh"
    app.idle_frame = app.cur_index = 0
    app.mode = "idle"
    app.order = list(app.clips)
    app.menu_open = app.dragging = app.drag_moved = False
    app._alpha_key = app._alpha = None
    app._alpha_size = (0, 0)
    app._alpha_display = (1, 1)
    app._press_local = (0, 0)
    app._clicks = deque(maxlen=pet.LAUGH_CLICKS)
    app._click_laugh_active = False
    app.global_scale = scale
    app._action_start = None
    app._action_serial = 0
    app._action_audible = False
    app._body = None
    app._idle_remaining = None
    app._drag_body_eligible = app._press_idle = False
    app._drag_samples = deque(maxlen=128)
    app._mask_origin = (0, 0)
    app._mask_size = (0, 0)
    app.win_w, app.win_h = round(320 * scale), round(305 * scale) + 2 * round(pet.BODY_PAD * scale)
    app._jelly_next_frame = 0.0
    app._tick_after = None
    app._closed = False
    app._drop_jelly()
    app.deadline = 500.0
    app.last_tick = 10.0
    app.acc = 0.0
    app.frame_ms = 1000 / 30
    app.random_min_ms, app.random_max_ms = 180000, 240000
    app.root = ProbeRoot()
    app.audio = SimpleNamespace(play=Mock(), stop=Mock(), quit=Mock())
    app.draw = Mock()
    return app


class ProbeRoot:
    def __init__(self):
        self.x, self.y = 100, 200
        self.geometry = Mock(side_effect=self._geometry)
        self.after, self.after_cancel, self.destroy = Mock(), Mock(), Mock()
        self.update_idletasks = Mock()
        self.focus_get = Mock(return_value=None)

    def winfo_x(self):
        return self.x

    def winfo_y(self):
        return self.y

    def _geometry(self, value):
        coords = re.search(r"([+-]\d+)([+-]\d+)$", value)
        if coords:
            self.x, self.y = map(int, coords.groups())


def event(app, u=146, v=180, dx=0, dy=0):
    clip = app.clips[app.cur_clip]
    x, y = clip.fx + round(u * clip.fw / 320), clip.fy + round(v * clip.fh / 305)
    return SimpleNamespace(x=x, y=y, x_root=app.root.winfo_x() + x + dx,
                           y_root=app.root.winfo_y() + y + dy)


def click(app, ev=None):
    ev = ev or event(app)
    app._on_press(ev)
    app._on_release(ev)


class SpringTests(unittest.TestCase):
    def jelly(self, duration=0.0):
        j = pet._LocalJelly(scaled())
        j.begin_press((146 * j.sx, 180 * j.sy), 10)
        j.release(10 + duration)
        return j

    def peak(self, j, released):
        values = []
        for n in range(701):
            j.advance(released + n / 1000)
            values.append(sum(f.q for f in j.fields))
        return max(values), min(values)

    def test_short_press_retains_original_peak_timing_and_decay(self):
        j = self.jelly()
        high, low = self.peak(j, 10)
        self.assertAlmostEqual(high, 1, places=4)
        self.assertLess(low, -0.29)
        j.advance(10.701)
        self.assertFalse(j.active)

    def test_hold_is_visible_monotonic_caps_and_release_peak_doubles(self):
        peaks = []
        for duration in (0, 0.2, 0.75, 1.5, 3):
            j = pet._LocalJelly(scaled())
            j.begin_press((146 * j.sx, 180 * j.sy), 10)
            j.advance(10 + duration)
            held = j.held.q
            j.release(10 + duration)
            self.assertAlmostEqual(j.fields[0].q, held)
            high, low = self.peak(j, 10 + duration)
            if duration >= 1.5:
                self.assertAlmostEqual(held, 1.0)  # 旧版满蓄回缩为 2,现在减半。
                # 原满蓄释放的外弹峰值:从 q=2、速度为零开始的首个负峰。
                self.assertAlmostEqual(low, -2 * math.exp(-8 * math.pi / 24), places=3)
            peaks.append(high)
        self.assertTrue(all(a <= b + 1e-6 for a, b in zip(peaks, peaks[1:])), peaks)
        self.assertAlmostEqual(peaks[3] / peaks[0], 2, places=3)
        self.assertAlmostEqual(peaks[3], peaks[4], places=6)
        j = pet._LocalJelly(scaled())
        j.begin_press((146 * j.sx, 180 * j.sy), 10)
        j.advance(10.75)
        self.assertAlmostEqual(j.held.q, 0.375)  # 1.5 秒蓄满的时长与曲线不变。
        before = j.render_rgba().tobytes()
        j.advance(11.5)
        self.assertAlmostEqual(j.held.q, 1.0)
        self.assertNotEqual(before, j.render_rgba().tobytes())
        capped = j.render_rgba().tobytes()
        j.advance(100)
        self.assertEqual(capped, j.render_rgba().tobytes())

    def test_new_press_and_release_preserve_old_displacement_and_centers(self):
        j = self.jelly()
        j.advance(10.04)
        old = j.fields[0]
        before, frame = old.q, j.render_rgba().tobytes()
        j.begin_press((143 * j.sx, 35 * j.sy), 10.04)
        self.assertEqual(j.render_rgba().tobytes(), frame)
        j.release(10.04)
        self.assertEqual(old.q, before)
        self.assertEqual(old.center, (146 * j.sx, 180 * j.sy))
        self.assertEqual(j.render_rgba().tobytes(), frame)
        j.advance(10.08)
        self.assertEqual(len(j.fields), 2)
        self.assertTrue(all(abs(f.q) > 0 for f in j.fields))

    def test_same_point_rapid_pokes_stay_bounded_and_do_not_reset_pose(self):
        j = self.jelly()
        for n in range(1, 101):
            now = 10 + n / 1000
            j.advance(now)
            before = j.render_rgba().tobytes()
            j.begin_press((146 * j.sx, 180 * j.sy), now)
            j.release(now)
            self.assertEqual(j.render_rgba().tobytes(), before)
            self.assertLessEqual(abs(sum(f.q for f in j.fields)), 3.001)
        j.advance(12)
        self.assertFalse(j.active)

    def test_motion_is_frame_rate_independent_and_sleep_settles(self):
        a, b = self.jelly(), self.jelly()
        a.advance(10.123)
        for i in range(1, 124):
            b.advance(10 + i / 1000)
        self.assertAlmostEqual(a.fields[0].q, b.fields[0].q, places=12)
        self.assertAlmostEqual(a.fields[0].velocity, b.fields[0].velocity, places=12)
        a.advance(1000)
        self.assertFalse(a.active)


class ImageTests(unittest.TestCase):
    POINTS = ((143, 20), (143, 35), (177, 65), (120, 140),
              (146, 180), (205, 180), (140, 240), (177, 288))

    def test_texture_on_all_four_sides_gathers_at_click_then_rebounds_outward(self):
        center = (144, 176)  # 网格节点,观察固定中心与四周真实渲染的纹理标记。
        markers = (((120, 176), (255, 0, 0)), ((168, 176), (0, 255, 0)),
                   ((144, 152), (0, 0, 255)), ((144, 200), (255, 255, 0)),
                   (center, (255, 255, 255)))
        texture = Image.new("RGBA", SOURCE.size)
        draw = ImageDraw.Draw(texture)
        for (x, y), color in markers:
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(*color, 255))
        jelly = pet._LocalJelly(SOURCE)
        jelly.begin_press(center, 10)
        jelly.release(10)
        for now, inward in ((10.052, True), (10.16, False)):
            jelly.advance(now)
            frame = jelly.render_rgba(texture)
            pixels = frame.load()
            positions = []
            for original, color in markers:
                hits = [(x, y - jelly.pad) for y in range(frame.height) for x in range(frame.width)
                        if pixels[x, y][3] >= 110 and pixels[x, y][:3] == color]
                self.assertTrue(hits, (now, color))
                point = tuple(sum(p[k] for p in hits) / len(hits) for k in (0, 1))
                positions.append(point)
                distance = math.dist(point, center)
                if original == center:
                    self.assertLess(distance, 0.5)  # 小标记重采样后的半像素容差。
                elif inward:
                    self.assertLess(distance, math.dist(original, center) - 1)
                else:
                    self.assertGreater(distance, math.dist(original, center) + 1)
            self.assertAlmostEqual(positions[0][0] + positions[1][0], 2 * center[0], delta=1)
            self.assertAlmostEqual(positions[2][1] + positions[3][1], 2 * center[1], delta=1)

    def test_off_grid_click_center_stays_in_place_and_surroundings_contract(self):
        jelly = pet._LocalJelly(SOURCE)
        center = (146, 180)
        jelly.begin_press(center, 10)
        jelly.advance(11.5)
        jelly.render_rgba()
        self.assertLess(math.dist(jelly.inverse(*center), center), 0.25)
        for x, y in ((122, 180), (170, 180), (146, 156), (146, 204)):
            sample = jelly.inverse(x, y)
            # 反向采样向外,可见纹理才能向中心收缩;另验证没有单向平移。
            self.assertGreater(math.dist(sample, center), math.dist((x, y), center))
            self.assertLess(abs((sample[0] - x) * (y - center[1])
                                - (sample[1] - y) * (x - center[0])), 2)

    def test_all_body_parts_deform_locally_at_every_size_and_recover(self):
        for scale in pet.SCALE_PRESETS:
            original = scaled(scale)
            for u, v in self.POINTS:
                with self.subTest(scale=scale, point=(u, v)):
                    j = pet._LocalJelly(original)
                    point = (round(u * j.sx), round(v * j.sy))
                    self.assertTrue(j.mask.getpixel(point))
                    j.begin_press(point, 10)
                    j.advance(10.75)
                    warped = j.render_rgba()
                    neutral = Image.new("RGBA", j.size)
                    neutral.paste(original, (0, j.pad))
                    box = ImageChops.difference(neutral, warped).convert("RGB").getbbox()
                    self.assertIsNotNone(box)
                    self.assertLess(abs((box[1] + box[3]) / 2 - (point[1] + j.pad)), 80 * scale)
                    # Far away from the affected region stays pixel-identical.
                    distant = (0, 0, original.width, round(80 * scale)) if v > 160 else (
                        0, round(210 * scale) + j.pad, original.width, j.size[1])
                    self.assertEqual(neutral.crop(distant).tobytes(), warped.crop(distant).tobytes())
                    self.assertEqual(original.tobytes(), j.rgba.tobytes())
                    j.release(10.75)
                    j.advance(11.451)
                    self.assertEqual(j.render_rgba().tobytes(), neutral.tobytes())

    def test_strong_combined_mesh_never_folds(self):
        j = pet._LocalJelly(SOURCE)
        j.begin_press((146, 180), 10)
        j.release(11.5)
        for n in range(20):
            now = 11.5 + n * 0.01
            j.begin_press(self.POINTS[n % len(self.POINTS)], now)
            j.release(now)
        for n in range(40):
            j.advance(now + n / 60)
            j.render_rgba()
            for box, (a, b, c, d) in j._cells:
                minimum = (box[2] - box[0]) * (box[3] - box[1]) * 0.25
                self.assertGreaterEqual(j._area(j._points[a], j._points[c], j._points[b]), minimum - 1e-9)
                self.assertGreaterEqual(j._area(j._points[a], j._points[d], j._points[c]), minimum - 1e-9)

    def test_display_and_mouse_mask_agree_at_every_size(self):
        for scale in pet.SCALE_PRESETS:
            app = app_probe(scale)
            j = pet._LocalJelly(scaled(scale))
            j.begin_press((143 * j.sx, 20 * j.sy), 10)
            j.advance(11.5)
            flat, mask = pet._chroma_frame(j.render_rgba())
            app._jelly = j
            app._jelly_mask = mask.tobytes()
            clip = app.clips[app.idle_clip]
            origin = (clip.fx, clip.fy - j.pad)
            app._mask_origin, app._mask_size = origin, mask.size
            for y in range(0, flat.height, 2):
                for x in range(0, flat.width, 2):
                    self.assertEqual(app._is_opaque(x + origin[0], y + origin[1]),
                                     flat.getpixel((x, y)) != pet.CHROMA)

    def test_transparency_is_never_an_initial_hit(self):
        for scale in pet.SCALE_PRESETS:
            app = app_probe(scale)
            for u, v in self.POINTS:
                ev = event(app, u, v)
                self.assertTrue(app._is_opaque(ev.x, ev.y))
            for u, v in ((0, 0), (10, 10), (160, 245), (300, 300)):
                ev = event(app, u, v)
                self.assertFalse(app._is_opaque(ev.x, ev.y))

    def test_padding_prevents_edge_clipping_against_larger_independent_render(self):
        for scale in pet.SCALE_PRESETS:
            for u, v in ((143, 18), (177, 290), (110, 275)):
                j = pet._LocalJelly(scaled(scale))
                j.begin_press((u * j.sx, v * j.sy), 10)
                j.release(11.5)
                for now in (11.5, 11.55, 11.63, 11.75):
                    j.advance(now)
                    actual = pet._opaque_mask(j.render_rgba())
                    # Extend the actual sampling mesh into an independent larger viewport.
                    margin = 50
                    mesh = []
                    for box, corners in j._cells:
                        quad = tuple(value for node in corners for value in j._points[node])
                        mesh.append((tuple(value + margin for value in box), quad))
                    bigger = pet._opaque_mask(j.rgba.transform(
                        (j.size[0] + 2 * margin, j.size[1] + 2 * margin),
                        Image.Transform.MESH, mesh, Image.Resampling.BICUBIC))
                    self.assertEqual(actual.tobytes(), bigger.crop(
                        (margin, margin, margin + j.size[0], margin + j.size[1])).tobytes())
                    # Edge nodes are identity, and no visible pixels reach that boundary.
                    bbox = bigger.getbbox()
                    self.assertGreater(bbox[0], margin)
                    self.assertGreater(bbox[1], margin)
                    self.assertLess(bbox[2], margin + j.size[0])
                    self.assertLess(bbox[3], margin + j.size[1])


class _InteractionSetup(unittest.TestCase):
    def setUp(self):
        self.clock = patch("pet_app.time.monotonic", return_value=10.0)
        self.clock.start()
        self.quiet = patch("pet_app.log")
        self.quiet.start()
        self.timer = Mock()
        self.timer_patch = patch("pet_app._begin_jelly_timer", return_value=self.timer)
        self.begin_timer = self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.quiet.stop)


class InteractionTests(_InteractionSetup):
    def test_press_charges_and_release_counts_once_without_moving_or_sound(self):
        app = app_probe()
        ev = event(app)
        app._on_press(ev)
        self.assertIsNotNone(app._jelly.held)
        self.assertEqual(len(app._clicks), 0)
        app._on_release(ev)
        app._on_release(ev)
        self.assertIsNone(app._jelly.held)
        self.assertEqual(len(app._clicks), 1)
        self.assertTrue(app._jelly.active)
        self.assertEqual(app.deadline, 500)
        app.root.geometry.assert_not_called()
        app.audio.play.assert_not_called()
        app.audio.stop.assert_not_called()

    def test_head_feet_and_body_are_valid_but_transparency_is_not(self):
        for u, v in ((143, 40), (177, 288), (146, 180)):
            app = app_probe()
            click(app, event(app, u, v))
            self.assertTrue(app._jelly.active)
            self.assertEqual(len(app._clicks), 1)
        app = app_probe()
        click(app, event(app, 0, 0))
        self.assertIsNone(app._jelly)
        self.assertEqual(len(app._clicks), 0)

    def test_valid_press_still_releases_if_other_motion_changes_hit_mask(self):
        app = app_probe(1)
        ev = event(app, 143, 18)
        app._on_press(ev)
        with patch("pet_app.time.monotonic", return_value=11.5):
            app._jelly.advance(11.5)
            flat, mask = pet._chroma_frame(app._jelly.render_rgba())
            clip = app.clips[app.idle_clip]
            app._jelly_mask = mask.tobytes()
            app._mask_origin, app._mask_size = (0, clip.fy - app._jelly.pad), mask.size
            # 其他余弹/拖放也可能令按下处不再命中;释放只认已经记录的按压会话。
            with patch.object(app, "_is_opaque", return_value=False) as hit_test:
                app._on_release(ev)
                hit_test.assert_not_called()
        self.assertEqual(list(app._clicks), [11.5])
        self.assertIsNone(app._jelly.held)

    def test_drag_cancels_charge_preserving_visible_dent_and_starts_body_bounce(self):
        app = app_probe()
        ev = event(app)
        app._on_press(ev)
        app._jelly.advance(10.75)
        before = app._jelly.held.q
        moved = event(app, dx=8)
        with patch("pet_app.time.monotonic", return_value=10.75):
            app._on_motion(moved)
            self.assertIsNone(app._jelly.held)
            self.assertEqual(app._jelly.fields[-1].q, before)
            self.assertIsNotNone(app._body)
            app._on_release(moved)
        self.assertEqual(len(app._clicks), 0)
        self.assertEqual(app._body.phase, "landing")

    def test_far_release_without_motion_event_is_drag_not_click(self):
        app = app_probe()
        app._on_press(event(app))
        app._on_release(event(app, dx=9))
        self.assertEqual(len(app._clicks), 0)
        self.assertIsNone(app._jelly.held)
        self.assertEqual(app._body.phase, "landing")

    def test_playing_click_keeps_action_until_explicit_stop(self):
        app = app_probe()
        app.play("dance1", with_audio=True)
        click(app)
        self.assertEqual(app.mode, "playing")
        app.audio.stop.assert_not_called()
        app._to_idle()
        self.assertEqual(app.mode, "idle")
        self.assertIsNone(app._jelly)
        app.audio.stop.assert_called_once()
        click(app)
        self.assertTrue(app._jelly.active)

    def test_action_change_and_focus_loss_cancel_stale_release(self):
        for operation in ("play", "focus"):
            app = app_probe()
            ev = event(app)
            app._on_press(ev)
            if operation == "play":
                app.play("laugh")
                app._to_idle()
            else:
                app._on_focus_out()
            app._on_release(ev)
            self.assertIsNone(app._jelly)
            self.assertFalse(app.dragging)
            self.assertEqual(len(app._clicks), 0)
            self.assertIsNone(app._idle_remaining)

    def test_focus_transfer_to_child_keeps_press_but_leaving_cancels_on_idle(self):
        app = app_probe()
        queued = []
        app.root.after_idle = queued.append
        app._on_press(event(app))
        app._on_focus_out(SimpleNamespace())
        child = SimpleNamespace(winfo_toplevel=lambda: app.root)
        app.root.focus_get.return_value = child
        queued.pop()()
        self.assertIsNotNone(app._jelly.held)
        app._on_focus_out(SimpleNamespace())
        app.root.focus_get.return_value = None
        queued.pop()()
        self.assertIsNone(app._jelly)
        app._on_release(event(app))
        self.assertEqual(len(app._clicks), 0)

    def test_hold_pauses_random_countdown_and_release_resumes_remaining_time(self):
        app = app_probe()
        app.deadline = 10.5
        ev = event(app)
        app._on_press(ev)
        with patch("pet_app.time.monotonic", return_value=20):
            app.tick()
            self.assertEqual(app.mode, "idle")
            app._on_release(ev)
        self.assertEqual(len(app._clicks), 1)
        self.assertAlmostEqual(app.deadline, 20.5)
        with patch("pet_app.time.monotonic", return_value=20.51):
            app.tick()
        self.assertEqual(app.mode, "playing")
        self.assertIsNone(app._jelly)
        app.audio.play.assert_not_called()

    def test_menu_cancels_charge_and_releases_normally(self):
        app = app_probe()
        ev = event(app)
        app._on_press(ev)
        app.global_scale = 0.4
        app.scale_var = SimpleNamespace(set=Mock())
        app.menu = SimpleNamespace(tk_popup=Mock(), grab_release=Mock())
        app._on_right(ev)
        app._on_release(ev)
        self.assertIsNone(app._jelly)
        self.assertFalse(app.menu_open)
        self.assertEqual(len(app._clicks), 0)
        app.menu.grab_release.assert_called_once()

    def test_tick_settles_and_releases_timer_without_resetting_deadline(self):
        app = app_probe()
        click(app)
        with patch("pet_app.time.monotonic", return_value=10.052):
            app.tick()
        self.assertAlmostEqual(app._jelly.fields[0].q, 1, places=3)
        app.draw.assert_called_once()
        with patch("pet_app.time.monotonic", return_value=10.701):
            app.tick()
        self.assertFalse(app._jelly.active)
        self.assertIsNone(app._jelly_timer)
        self.assertIsNone(app._tap_spring)
        self.assertEqual(self.timer.timeEndPeriod.call_count, self.begin_timer.call_count)
        self.assertEqual(app.deadline, 500)

    def test_quit_and_repeated_clicks_balance_animation_timers(self):
        app = app_probe()
        for _ in range(pet.LAUGH_CLICKS - 1):
            click(app)
        self.assertEqual(self.begin_timer.call_count, 2)
        app.quit()
        app._drop_jelly()
        self.assertIsNone(app._jelly)
        self.assertIsNone(app._jelly_photo)
        self.assertIsNone(app._tap_spring)
        self.assertEqual(self.timer.timeEndPeriod.call_count, self.begin_timer.call_count)
        app.audio.quit.assert_called_once()
        app.root.destroy.assert_called_once()


class TapSpringTests(unittest.TestCase):
    def test_squash_rebound_and_exact_settling(self):
        spring = pet._TapSpring()
        spring.poke(10)
        samples = []
        for i in range(66):
            spring.advance(10 + i / 100)
            samples.append(spring.compression)
        self.assertGreater(max(samples), 0.12)
        self.assertLess(max(samples), 0.16)
        self.assertLess(min(samples), -0.035)
        self.assertFalse(spring.active)
        self.assertEqual((spring.compression, spring.velocity), (0, 0))

    def test_rapid_taps_preserve_position_and_stay_bounded(self):
        spring = pet._TapSpring()
        spring.poke(10)
        for n in range(1, 60):
            now = 10 + n * 0.025
            spring.advance(now)
            before = spring.compression
            spring.poke(now)
            self.assertEqual(spring.compression, before)
            self.assertLess(abs(spring.compression), 0.24)
        spring.advance(now + 0.651)
        self.assertFalse(spring.active)

    def test_spring_is_independent_of_frame_rate(self):
        states = []
        for fps in (30, 60, 144):
            spring = pet._TapSpring()
            spring.poke(10)
            for frame in range(1, fps + 1):
                spring.advance(10 + 0.4 * frame / fps)
            states.append((spring.compression, spring.velocity))
        for q, v in states[1:]:
            self.assertAlmostEqual(q, states[0][0])
            self.assertAlmostEqual(v, states[0][1])

    def test_render_squashes_widens_and_keeps_feet_anchored(self):
        app = app_probe(0.8)
        app._tap_spring = pet._TapSpring()
        app._tap_spring.compression = 0.16
        rgba = app._read_rgba(app.idle_clip, app.idle_frame)
        clip = app.clips[app.idle_clip]
        before = pet._opaque_mask(rgba).getbbox()
        pose = app._render_tap(rgba, (clip.fx, clip.fy))
        after = pet._opaque_mask(pose).getbbox()
        self.assertLess(after[3] - after[1], before[3] - before[1])
        self.assertGreater(after[2] - after[0], before[2] - before[0])
        self.assertAlmostEqual(after[3], before[3] + clip.fy, delta=1)
        self.assertIsNone(pose.getchannel("A").crop((0, 0, 2, 2)).getbbox())


class ClickLaughTests(_InteractionSetup):
    def test_double_click_starts_once_and_counts_toward_growth(self):
        app = app_probe(0.5)
        with patch("pet_app.time.monotonic", return_value=10):
            click(app)
        self.assertEqual(app.mode, "idle")
        with patch("pet_app.time.monotonic", return_value=10.2):
            click(app)
        self.assertEqual((app.mode, app.cur_index), ("playing", 120))
        self.assertEqual(getattr(app, "_growth_step", 0), 0)
        self.assertEqual(app._growth_click_count, 2)
        self.assertEqual(app._growth_click_last, 10.2)
        self.assertTrue(app._tap_spring.active)
        self.assertEqual(len(app._clicks), 0)
        app.audio.play.assert_called_once_with("laugh", start_ms=120 * app.frame_ms,
                                               end_ms=300 * app.frame_ms)
        self.assertEqual(app._range_end - app._range_first, 180)
        app._animate_growth = Mock()
        app._blink_pending = False
        end = app._action_start + 6
        with patch("pet_app.time.monotonic", return_value=end - 0.001):
            app.tick()
        self.assertEqual(app.cur_index, 299)
        with patch("pet_app.time.monotonic", return_value=end + 0.001):
            app.tick()
        self.assertEqual(app.mode, "idle")
        app._animate_growth.assert_called_once()

    def test_slow_clicks_do_not_start_laugh(self):
        app = app_probe()
        for now in (10, 10.6, 11.2):
            with patch("pet_app.time.monotonic", return_value=now):
                click(app)
        self.assertEqual(app.mode, "idle")
        app.audio.play.assert_not_called()

    def test_menu_open_does_not_count(self):
        app = app_probe()
        app.menu_open = True
        click(app)
        self.assertEqual(len(app._clicks), 0)


class BodyPhysicsTests(unittest.TestCase):
    def test_pickup_keeps_grip_fixed_and_conserves_apparent_volume(self):
        for scale in pet.SCALE_PRESETS:
            for u, v in ((146, 40), (146, 180), (178, 280)):
                j = pet._BodyJelly(scaled(scale))
                pin = (u * scale, v * scale)
                j.pickup(*pin, 10)
                for n in range(13):
                    j.advance(10 + n / 100)
                    self.assertAlmostEqual(j.forward(*j.grip)[0], pin[0])
                    self.assertAlmostEqual(j.forward(*j.grip)[1], pin[1])
                    sx, sy, _, _ = j.pose
                    self.assertAlmostEqual(sx * sx * sy, 1)
                j.advance(10.121)
                self.assertEqual(j.phase, "held")
                self.assertAlmostEqual(j.pose[1], 1.08)

    def test_release_is_continuous_foot_stays_fixed_and_strength_is_bounded(self):
        for speed, expected in ((0, 0.10), (1.25, 0.16), (2.5, 0.22), (1000, 0.22)):
            j = pet._BodyJelly(SOURCE)
            j.pickup(146, 180, 10)
            j.advance(10.13)
            before, floor = j.pose, j.forward(*j.foot)
            j.release(speed, 10.13)
            self.assertEqual(j.pose, before)
            positions = []
            for n in range(901):
                j.advance(10.13 + n / 1000)
                positions.append(1 - j.pose[1])
                self.assertAlmostEqual(j.forward(*j.foot)[0], floor[0])
                self.assertAlmostEqual(j.forward(*j.foot)[1], floor[1])
            self.assertAlmostEqual(max(positions), expected, places=4)
            self.assertGreaterEqual(min(positions), -0.080001)
            self.assertLessEqual(max(positions), 0.220001)
            peaks = [b for a, b, c in zip(positions, positions[1:], positions[2:])
                     if b > a and b > c and b > 0]
            self.assertGreaterEqual(len(peaks), 3)
            self.assertTrue(all(a > b for a, b in zip(peaks, peaks[1:])))
            j.advance(11.031)
            self.assertEqual(j.phase, "done")
            self.assertEqual(j.pose[:2], (1, 1))

    def test_all_sizes_have_enough_padding_at_head_and_feet(self):
        for scale in pet.SCALE_PRESETS:
            rgba = scaled(scale)
            pad = round(pet.BODY_PAD * scale)
            size = (rgba.width, rgba.height + 2 * pad)
            for u, v in ((146, 16), (146, 180), (178, 295)):
                j = pet._BodyJelly(rgba)
                j.pickup(u * scale, v * scale, 10)
                j.advance(10.13)
                self.assert_uncropped(j, size, (0, pad))
                j.release(100, 10.13)
                for n in range(60):
                    j.advance(10.13 + n / 60)
                    self.assert_uncropped(j, size, (0, pad))

    def assert_uncropped(self, j, size, origin):
        # 在更大的验证画布上独立绘制,而不是只检查已裁切图片的 bbox。
        rendered = pet._opaque_mask(j.render_rgba(j.rgba, size, origin))
        margin = 100
        reference = pet._opaque_mask(j.render_rgba(j.rgba, (size[0] + 2 * margin, size[1] + 2 * margin),
                                                  (origin[0] + margin, origin[1] + margin)))
        expected = reference.crop((margin, margin, margin + size[0], margin + size[1]))
        self.assertEqual(rendered.tobytes(), expected.tobytes())
        bb = reference.getbbox()
        self.assertTrue(bb[0] >= margin and bb[1] >= margin
                        and bb[2] <= margin + size[0] and bb[3] <= margin + size[1], (size, bb))

    def test_regrab_and_belly_handoff_start_from_current_pose(self):
        j = pet._BodyJelly(SOURCE)
        j.pickup(146, 180, 10)
        j.advance(10.13)
        j.release(2.5, 10.13)
        j.advance(10.22)
        old_pose = j.pose
        point = j.forward(146, 180)
        j.pickup(*point, 10.22)
        j.advance(10.22)
        for a, b in zip(j.pose, old_pose):
            self.assertAlmostEqual(a, b)
        j.advance(10.25)
        old_pose = j.pose
        j.handoff(10.25)
        j.advance(10.25)
        for a, b in zip(j.pose, old_pose):
            self.assertAlmostEqual(a, b)
        j.advance(10.331)
        self.assertEqual(j.phase, "done")
        self.assertEqual(j.pose[:2], (1, 1))


class DropInteractionTests(_InteractionSetup):
    def start_drag(self, app, dx=12, dy=-8, now=10.0):
        ev = event(app)
        if app._body is not None:
            clip = app.clips[app.idle_clip]
            x, y = app._body.forward(ev.x - clip.fx, ev.y - clip.fy)
            ev.x, ev.y = round(x + clip.fx), round(y + clip.fy)
            ev.x_root, ev.y_root = app.root.winfo_x() + ev.x, app.root.winfo_y() + ev.y
        with patch("pet_app.time.monotonic", return_value=now):
            app._on_press(ev)
        moved = SimpleNamespace(**vars(ev))
        moved.x_root += dx
        moved.y_root += dy
        with patch("pet_app.time.monotonic", return_value=now + 0.03):
            app._on_motion(moved)
        return moved

    def advance_app(self, app, now):
        with patch("pet_app.time.monotonic", return_value=now):
            app.tick()

    def test_release_once_is_silent_and_keeps_foot_in_world_coordinates(self):
        app = app_probe()
        ev = self.start_drag(app)
        self.advance_app(app, 10.16)
        foot = app._body.forward(*app._body.foot)
        floor = (app.root.x + foot[0], app.root.y + foot[1])
        with patch("pet_app.time.monotonic", return_value=10.18):
            app._on_release(ev)
        started = app._body.started
        app._on_release(ev)
        self.assertEqual(app._body.started, started)
        for n in range(1, 50):
            self.advance_app(app, 10.18 + n / 50)
            p = app._body.forward(*app._body.foot) if app._body else pet._BodyJelly(scaled()).foot
            self.assertAlmostEqual(app.root.x + p[0], floor[0], delta=0.51)
            self.assertAlmostEqual(app.root.y + p[1], floor[1], delta=0.51)
        self.assertIsNone(app._body)
        self.assertIsNone(app._jelly_timer)
        self.assertEqual(self.begin_timer.call_count, self.timer.timeEndPeriod.call_count)
        app.audio.play.assert_not_called()
        app.audio.stop.assert_not_called()

    def test_recent_velocity_changes_strength_but_pause_makes_release_light(self):
        def strength(dx, release_time):
            app = app_probe()
            ev = self.start_drag(app, dx=dx)
            with patch("pet_app.time.monotonic", return_value=release_time):
                app._on_release(ev)
            return app._body.strength
        self.assertLess(strength(5, 10.05), strength(90, 10.05))
        self.assertAlmostEqual(strength(90, 10.5), 0.10)

    def test_random_countdown_pauses_and_resumes_remaining_time(self):
        app = app_probe()
        app.deadline = 10.5
        ev = self.start_drag(app)
        remaining = app._idle_remaining
        self.advance_app(app, 40)
        self.assertEqual(app.mode, "idle")
        self.assertEqual(app._body.phase, "held")
        with patch("pet_app.time.monotonic", return_value=40.1):
            app._on_release(ev)
        self.advance_app(app, 40.7)
        self.assertEqual(app.mode, "idle")
        self.advance_app(app, 41.01)
        self.assertIsNone(app._body)
        self.assertAlmostEqual(app.deadline, 41.01 + remaining)

    def test_performing_drag_preserves_laugh_clock_and_audio(self):
        app = app_probe()
        app.play("laugh", with_audio=True)
        started = app._action_start
        ev = self.start_drag(app)
        self.assertEqual(app.mode, "playing")
        self.assertIsNone(app._body)
        self.assertFalse(app._drag_body_eligible)
        app.audio.stop.assert_not_called()
        with patch("pet_app.time.monotonic", return_value=10.2):
            app._on_release(ev)
        self.assertEqual(app.mode, "playing")
        self.assertEqual(app._action_start, started)
        app.audio.stop.assert_not_called()

    def test_landing_belly_click_uses_inverse_pose_and_hands_off_smoothly(self):
        app = app_probe()
        ev = self.start_drag(app)
        self.advance_app(app, 10.16)
        with patch("pet_app.time.monotonic", return_value=10.18):
            app._on_release(ev)
        self.advance_app(app, 10.25)
        body = app._body
        clip = app.clips[app.idle_clip]
        x, y = body.forward(146 * clip.fw / 320, 180 * clip.fh / 305)
        flat, mask = pet._chroma_frame(body.render_rgba(body.rgba, (app.win_w, app.win_h), (clip.fx, clip.fy)))
        app._jelly_mask = mask.tobytes()
        app._mask_origin, app._mask_size = (0, 0), mask.size
        x, y = round(x + clip.fx), round(y + clip.fy)
        self.assertTrue(app._is_opaque(x, y))
        before = body.pose
        ev = SimpleNamespace(x=x, y=y, x_root=app.root.x + x, y_root=app.root.y + y)
        with patch("pet_app.time.monotonic", return_value=10.26):
            click(app, ev)
        self.assertEqual(body.phase, "handoff")
        self.assertEqual(body.pose, before)
        self.assertTrue(app._jelly.active)
        self.assertIsNone(app._idle_remaining)
        self.advance_app(app, 10.35)
        self.assertIsNone(app._body)
        self.assertTrue(app._jelly.active)

    def test_repeated_regrabs_do_not_accumulate_offset_or_lose_grip(self):
        app = app_probe()
        now = 10.0
        for _ in range(30):
            ev = self.start_drag(app, now=now)
            for n in range(1, 9):
                self.advance_app(app, now + 0.03 + n / 60)
                body = app._body
                pin = body.forward(*body.grip)
                clip = app.clips[app.idle_clip]
                self.assertAlmostEqual(app.root.x + clip.fx + pin[0], ev.x_root, places=5)
                self.assertAlmostEqual(app.root.y + clip.fy + pin[1], ev.y_root, places=5)
                foot = body.forward(*body.foot)
                self.assertLessEqual(abs(foot[1] - body.foot[1]), 0.51)
                BodyPhysicsTests().assert_uncropped(body, (app.win_w, app.win_h), (clip.fx, clip.fy))
            with patch("pet_app.time.monotonic", return_value=now + 0.18):
                app._on_release(ev)
            self.advance_app(app, now + 0.25)
            now += 0.3

    def test_menu_action_and_quit_clean_body_and_release_timer(self):
        for operation in ("menu", "play", "quit"):
            with self.subTest(operation=operation):
                app = app_probe()
                self.start_drag(app)
                if operation == "menu":
                    app.global_scale = 0.4
                    app.scale_var = SimpleNamespace(set=Mock())
                    app.menu = SimpleNamespace(tk_popup=Mock(), grab_release=Mock())
                    app._on_right(event(app))
                elif operation == "play":
                    app.play("dance2", with_audio=True)
                else:
                    app.quit()
                self.assertIsNone(app._body)
                self.assertIsNone(app._jelly_timer)
                self.assertIsNone(app._idle_remaining)
                self.assertFalse(app._drag_body_eligible)


@unittest.skipUnless(RUN_GUI, "使用 --gui 启用真实 Tk 检查")
class TkTests(unittest.TestCase):
    def setUp(self):
        # 前面的无窗口替身含大量 Mock/回调环,先回收,避免测到它们的 GC 停顿。
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix="pet-jelly-test-")
        self.addCleanup(self.temp.cleanup)
        settings = Path(self.temp.name) / "settings.json"
        settings.write_bytes(pet.SETTINGS.read_bytes())
        fake_audio = SimpleNamespace(prewarm=Mock(return_value=0), play=Mock(),
                                     stop=Mock(), quit=Mock())
        for p in (patch("pet_app.SETTINGS", settings),
                  patch("pet_app.AudioPlayer", return_value=fake_audio),
                  patch("pet_app.log"), patch.object(pet.PetApp, "_write_geometry_file")):
            p.start()
            self.addCleanup(p.stop)
        self.app = pet.PetApp()
        # Measure deformation independently of the random blink animation.
        self.app._queue_blink = Mock()
        self.app._blink_deadline = float("inf")
        voice = self.app.audio
        self.app.audio = SimpleNamespace(play=Mock(wraps=voice.play), stop=Mock(wraps=voice.stop),
                                         quit=Mock(wraps=voice.quit), output_latency_ms=voice.output_latency_ms)
        self.addCleanup(self.app.quit)
        self.app.root.update()
        self.app.root.focus_force()
        self.app.root.update()
        self.app.draw()

    def test_laugh_click_spring_animates_current_frame_and_hit_mask(self):
        app = self.app
        app.play("laugh", with_audio=True)
        app.tick()
        click(app)
        began = app._action_start
        records = []
        draw = app.draw

        def record():
            draw()
            spring = getattr(app, "_tap_spring", None)
            if spring is not None:
                records.append((app.cur_index, spring.compression))
        app.draw = record
        app.root.after(240, app.root.quit)
        app.root.mainloop()
        self.assertGreater(len(records), 8)
        self.assertGreater(max(q for _, q in records), 0.10)
        self.assertLess(min(q for _, q in records), 0)
        self.assertGreater(len({index for index, _ in records}), 3)
        self.assertEqual(app._action_start, began)
        self.assertEqual(app.audio.play.call_count, 1)
        self.assertIsNotNone(app._jelly_photo)
        photo = app._jelly_photo._PhotoImage__photo
        for y in range(0, app.win_h, 7):
            for x in range(0, app.win_w, 7):
                self.assertEqual(app._is_opaque(x, y), photo.get(x, y) != pet.CHROMA)
        self.assertEqual(photo.get(0, 0), pet.CHROMA)
        app._on_press(event(app))
        app._on_motion(event(app, dx=20))
        self.assertIsNone(app._tap_spring)
        self.assertIsNone(app._tap_timer)

    def test_tk_photo_dimensions_hit_pixels_lifetime_cache_and_resize(self):
        app = self.app
        for scale in pet.SCALE_PRESETS:
            with self.subTest(scale=scale):
                app.set_scale(scale)
                self.assertIsNone(app._jelly)
                click(app)
                j = app._jelly
                j.advance(j.last_poke + 0.052)
                before = (app.root.winfo_x(), app.root.winfo_y())
                for _ in range(30):
                    app.draw()
                gc.collect()
                photo = app._jelly_photo
                clip = app.clips[app.idle_clip]
                self.assertEqual((photo.width(), photo.height()), (app.win_w, app.win_h))
                self.assertEqual(app.root.tk.call("image", "width", str(photo)), app.win_w)
                self.assertLessEqual(len(app.cache), 1)
                self.assertEqual((app.root.winfo_x(), app.root.winfo_y()), before)
                actual = photo._PhotoImage__photo
                for y in range(0, app.win_h, 3):
                    for x in range(0, app.win_w, 3):
                        self.assertEqual(app._is_opaque(x, y), actual.get(x, y) != pet.CHROMA)
                j.advance(j.last_poke + 0.701)
                app._tap_spring.advance(j.last_poke + 0.701)
                app.draw()
                self.assertIsNone(app._jelly_photo)
                self.assertIsNone(app._jelly_mask)
                self.assertEqual(app._load_frame(app.idle_clip, app.idle_frame).tobytes(),
                                 pet._chroma_frame(j.rgba)[0].tobytes())
        click(app)
        app.set_scale(0.4)
        self.assertIsNone(app._jelly)

    def test_real_tick_cadence_and_settling_at_min_and_max_size(self):
        app = self.app
        real_draw = app.draw
        draws, costs = [], []

        def record():
            started = time.monotonic()
            real_draw()
            # Eye tracking may redraw after the belly has settled.
            if app._jelly is not None and app._jelly.active:
                draws.append(started)
                costs.append(time.monotonic() - started)

        app.draw = record
        app.tick()
        for scale in (pet.SCALE_MIN, pet.SCALE_MAX):
            app.set_scale(scale)
            draws.clear()
            costs.clear()
            click(app)
            app.root.after(850, app.root.quit)
            app.root.mainloop()
            fps = (len(draws) - 1) / (draws[-1] - draws[0])
            print(f"\n  Tk scale={scale}: {len(draws)} frames, {fps:.1f} fps, "
                  f"mean draw={1000 * sum(costs) / len(costs):.2f}ms")
            self.assertTrue(app._jelly is None or not app._jelly.active)
            self.assertGreaterEqual(len(draws), 30, (scale, len(draws)))
            self.assertLess(draws[-1] - app._jelly.last_poke, 0.82)

    def test_threshold_laugh_renders_with_audio_and_ignores_followup_clicks(self):
        app = self.app

        def click_visible():
            ev = event(app)
            # 叠加后原坐标可能已经成为透明空白;真实点击应落在当时可见的皮肤上。
            if not app._is_opaque(ev.x, ev.y):
                width, _ = app._mask_size
                ox, oy = app._mask_origin
                _, index = min(((i % width + ox - ev.x) ** 2 + (i // width + oy - ev.y) ** 2, i)
                               for i, value in enumerate(app._jelly_mask) if value)
                ev.x, ev.y = index % width + ox, index // width + oy
                ev.x_root, ev.y_root = app.root.winfo_x() + ev.x, app.root.winfo_y() + ev.y
            click(app, ev)

        for _ in range(1):
            click_visible()
            app.draw()
            app.root.update_idletasks()
            self.assertEqual(app.mode, "idle")
            app.audio.play.assert_not_called()
        click_visible()
        app.root.update_idletasks()
        self.assertEqual((app.mode, app.cur_clip), ("playing", "laugh"),
                         (len(app._clicks), list(app._clicks), time.monotonic()))
        app.audio.play.assert_called_once()
        self.assertTrue(app._click_laugh_active)
        self.assertIsNone(app._jelly)
        self.assertTrue(app._tap_spring.active)
        self.assertIsNone(app._jelly_timer)
        for _ in range(3):
            click(app)
        self.assertEqual(app.mode, "playing")
        app.audio.play.assert_called_once()
        app.audio.stop.assert_not_called()
        self.assertEqual(app.canvas.itemcget(app.image_id, "image"), str(app._jelly_photo))

    def test_real_drop_cadence_grip_foot_mask_cache_and_resize_cleanup(self):
        app = self.app
        real_draw = app.draw
        records, errors, grip_errors, foot_errors = [], [], [], []
        expected_grip = expected_floor = None
        original_foot = None
        checked_mask = False
        app.root.report_callback_exception = lambda *exc: errors.append(str(exc[1]))

        def record():
            nonlocal checked_mask
            start = time.monotonic()
            real_draw()
            app.root.update_idletasks()
            records.append((start, time.monotonic() - start,
                            app._body.phase if app._body else "done"))
            if app._body is not None:
                body = app._body
                clip = app.clips[app.idle_clip]
                if body.phase in ("pickup", "held") and expected_grip:
                    x, y = body.forward(*body.grip)
                    grip_errors.append(math.hypot(app.root.winfo_x() + clip.fx + x - expected_grip[0],
                                                   app.root.winfo_y() + clip.fy + y - expected_grip[1]))
                if body.phase == "landing" and expected_floor:
                    x, y = body.forward(*body.foot)
                    foot_errors.append(math.hypot(app.root.winfo_x() + clip.fx + x - expected_floor[0],
                                                   app.root.winfo_y() + clip.fy + y - expected_floor[1]))
                    if body.compression > 0.08 and not checked_mask:
                        checked_mask = True
                        actual = app._jelly_photo._PhotoImage__photo
                        if (actual.width(), actual.height()) != (app.win_w, app.win_h):
                            errors.append("Full-body photo dimensions do not match the viewport")
                        for y in range(0, app.win_h, 7):
                            for x in range(0, app.win_w, 7):
                                if app._is_opaque(x, y) != (actual.get(x, y) != pet.CHROMA):
                                    errors.append(f"Display/mouse mismatch at {x},{y}")

        app.draw = record
        app.tick()
        for scale in (pet.SCALE_MIN, pet.SCALE_MAX):
            app.set_scale(scale)
            app.root.update()
            records.clear()
            grip_errors.clear()
            foot_errors.clear()
            checked_mask = False
            ev = event(app)
            app._on_press(ev)
            ev.x_root += 20
            ev.y_root -= 12
            expected_grip = None
            app._on_motion(ev)
            expected_grip = (ev.x_root, ev.y_root)
            original_foot = app._body.foot

            def release():
                nonlocal expected_floor
                app.root.update_idletasks()
                clip = app.clips[app.idle_clip]
                x, y = app._body.forward(*app._body.foot)
                expected_floor = (app.root.winfo_x() + clip.fx + x, app.root.winfo_y() + clip.fy + y)
                app._on_release(ev)

            app.root.after(200, release)
            app.root.after(1200, app.root.quit)
            app.root.mainloop()
            self.assertFalse(errors, errors)
            self.assertTrue(checked_mask)
            landing = [r for r in records if r[2] == "landing"]
            fps = (len(landing) - 1) / (landing[-1][0] - landing[0][0])
            print(f"\n  Drop Tk scale={scale}: {len(landing)} landing frames, {fps:.1f} fps, "
                  f"mean draw={1000 * sum(r[1] for r in landing) / len(landing):.2f}ms")
            self.assertGreaterEqual(len(landing), 40)
            self.assertLess(max(grip_errors), 0.01)
            self.assertLess(max(foot_errors), 0.01)
            self.assertIsNone(app._body)
            self.assertIsNone(app._jelly_timer)
            self.assertIsNone(app._idle_remaining)
            self.assertIsNone(app._jelly_photo)
            self.assertLessEqual(len(app.cache), 1)
            clip = app.clips[app.idle_clip]
            self.assertAlmostEqual(app.root.winfo_x() + clip.fx + original_foot[0], expected_floor[0], delta=0.51)
            self.assertAlmostEqual(app.root.winfo_y() + clip.fy + original_foot[1], expected_floor[1], delta=0.51)
            app._on_press(event(app))
            moved = event(app, dx=8)
            app._on_motion(moved)
            app.set_scale(0.6 if scale != 0.6 else 1.0)
            self.assertIsNone(app._body)
            self.assertIsNone(app._idle_remaining)
            self.assertIsNone(app._jelly_timer)
            app._on_release(moved)


if __name__ == "__main__":
    if RUN_GUI and len(sys.argv) == 1:
        # 像素/弹簧压力检查与实时帧率测量隔离进程,让 GUI 的调度、堆和计时器
        # 与实际启动一致;子进程仍执行全部原有 GUI 断言。
        TkTests.__unittest_skip__ = True
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise SystemExit(1)
        raise SystemExit(subprocess.call([sys.executable, __file__, "--gui", "TkTests"], cwd=ROOT))
    unittest.main(verbosity=2)
