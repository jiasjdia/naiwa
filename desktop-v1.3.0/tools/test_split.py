"""Recursive emission, phase inheritance, capacity reservations and real physics."""
from __future__ import annotations

from pathlib import Path
import random
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pet_physics import PhysicsWorld, STEP, TIERS, GROWTH_INCREMENT, snap_scale, tier_of
from tools.test_group import bare_group
from tools.test_jelly import app_probe, click


class SplitTests(unittest.TestCase):
    def test_combo_text_reports_actual_growth_cap_split_and_capacity(self):
        import tkinter as tk
        group, pet = self.group(0.8)
        group.root = Mock(spec=tk.Misc)
        with patch("pet_effects.ComboBubble") as bubble_class:
            bubble = bubble_class.return_value
            bubble.closed = False
            pet._show_combo(3)
            bubble.show.assert_called_with("×3 长大 · 成长值 +3", split=False)
            pet._growth_base, pet._growth_step = 0.8, 5
            pet._show_combo(5)
            bubble.show.assert_called_with("×5 长大 · 成长值 +1", split=False)
            pet._show_combo(5, added=0)
            bubble.show.assert_called_with("×5 · 已达最大体型", split=False)
            pet._show_combo(6, split=True)
            bubble.show.assert_called_with("×6 分裂！", split=True)
            pet._show_combo(6, blocked=True)
            bubble.show.assert_called_with("×6 · 暂时无法分裂", split=False)
            bubble_class.assert_called_once_with(pet)

    def group(self, scale=0.4):
        group = bare_group()
        group.pending_bursts = []
        pet = app_probe(scale)
        pet.group = group
        pet.canvas = Mock()
        pet._growth_base, pet._growth_step = scale, 0
        pet.draw_split_ghosts = Mock()
        group.pets, group.primary = [pet], pet
        with patch("pet_app.time.monotonic", return_value=10):
            pet.play("laugh", with_audio=True)
        return group, pet

    def test_startup_clicks_join_growth_and_split_batch(self):
        for count in (2, 3, 5, 6, 7):
            with self.subTest(count=count):
                group, pet = self.group()
                pet._animate_growth = Mock()
                with patch("pet_app.time.monotonic", return_value=10):
                    pet._to_idle()
                for n in range(count):
                    with patch("pet_app.time.monotonic", return_value=11 + n * 0.05):
                        click(pet)
                pet._settle_growth_clicks(12)
                if count < 6:
                    pet._animate_growth.assert_called_once()
                    self.assertAlmostEqual(pet._animate_growth.call_args.args[0],
                                           0.4 + count * GROWTH_INCREMENT)
                    self.assertEqual(group.pending_bursts, [])
                else:
                    pet._animate_growth.assert_not_called()
                    self.assertEqual(len(group.pending_bursts), 1)
                    self.assertTrue(pet._splitting)

    def test_split_holds_compression_until_children_rebound_even_after_laugh_ends(self):
        for ended in (False, True):
            with self.subTest(ended=ended), patch("pet_app._begin_jelly_timer", return_value=Mock()):
                group, source = self.group()
                source._poke_tap(10.95)
                group.request_split(source, 11)
                spring = source._tap_spring
                state = (spring.compression, spring.velocity)
                self.assertGreater(state[0], 0.1)
                self.assertTrue(spring.paused)
                self.assertIsNone(source._tap_timer)
                if ended:
                    with patch("pet_app.time.monotonic", return_value=11.2):
                        source.stop_laugh()
                for now in (11.1, 11.3, 11.75):
                    spring.advance(now)
                    self.assertEqual((spring.compression, spring.velocity), state)
                children = [app_probe(0.4), app_probe(0.4)]
                with patch("pet_app.time.monotonic", return_value=11.75):
                    for child in children:
                        child.inherit_animation(source)
                        self.assertEqual(child.mode, "idle" if ended else "playing")
                        self.assertFalse(child._tap_spring.paused)
                        self.assertEqual((child._tap_spring.compression, child._tap_spring.velocity), state)
                        self.assertIsNot(child._tap_spring, spring)
                children[0]._tap_spring.advance(11.80)
                self.assertNotEqual(children[0]._tap_spring.compression, state[0])
                self.assertEqual(children[1]._tap_spring.compression, state[0])
                self.assertEqual(spring.compression, state[0])
                for child in children:
                    child._tap_spring.advance(12.401)
                    self.assertFalse(child._tap_spring.active)
                    child._clear_tap()

    def test_batch_three_and_five_grow_once_after_pause(self):
        for count in (1, 2, 3, 4, 5):
            group, pet = self.group()
            pet._animate_growth = Mock()
            for n in range(count):
                pet._complete_split_click(11 + n * 0.04)
                self.assertEqual(pet.global_scale, 0.4)
            pet._settle_growth_clicks(11 + (count - 1) * 0.04 + 0.299)
            pet._animate_growth.assert_not_called()
            pet._settle_growth_clicks(11 + (count - 1) * 0.04 + 0.301)
            pet._animate_growth.assert_called_once()
            self.assertAlmostEqual(pet._animate_growth.call_args.args[0], 0.4 + count / 30)
            self.assertEqual(pet._growth_step, count)
            self.assertEqual(group.pending_bursts, [])

    def test_two_three_click_batches_cross_major_tier_without_splitting(self):
        group, pet = self.group()
        pet._animate_growth = Mock()
        for start in (11, 20):
            for n in range(3):
                pet._complete_split_click(start + n * 0.04)
            pet._settle_growth_clicks(start + 0.4)
            pet._to_idle()
        self.assertEqual((pet._growth_base, pet._growth_step), (0.6, 0))
        self.assertAlmostEqual(pet._animate_growth.call_args.args[0], 0.6)
        self.assertEqual(group.pending_bursts, [])

    def test_six_or_more_split_without_any_growth(self):
        group, pet = self.group()
        pet._growth_base, pet._growth_step, pet.global_scale = 0.6, 3, 0.7
        pet._animate_growth = Mock()
        for n in range(9):
            pet._complete_split_click(11 + n * 0.03)
        self.assertEqual(pet.global_scale, 0.7)
        pet._animate_growth.assert_not_called()
        self.assertEqual(len(group.pending_bursts), 1)
        self.assertEqual(group.pending_bursts[0][2], 0.6)

    def test_growth_bounce_overshoots_then_settles_exactly(self):
        group, pet = self.group()
        pet.foot_position = Mock(return_value=(200, 300))
        sizes = []
        def resize(scale, **kwargs):
            sizes.append(scale)
            pet.global_scale = scale
            self.assertEqual(kwargs["anchor"], (200, 300))
        pet.set_scale = resize
        target = 0.45
        pet._animate_growth(target, 10)
        for frame in range(26):
            pet._advance_growth(10 + frame / 60)
        self.assertGreater(max(sizes), target)
        self.assertLessEqual(max(sizes), target * 1.2 + 1e-9)
        self.assertEqual(pet.global_scale, target)
        self.assertIsNone(pet._growth_anim)

    def test_growth_puff_accelerates_overshoots_and_settles_once(self):
        group, pet = self.group()
        pet.foot_position = Mock(return_value=(200, 300))
        sizes = []
        def resize(scale, **kwargs):
            pet.global_scale = scale
            sizes.append(scale)
        pet.set_scale = resize
        pet._animate_growth(0.45, 10)
        for index in range(21):
            pet._advance_growth(10 + index * 0.02)
        self.assertLess(sizes[1] - sizes[0], sizes[2] - sizes[1])
        self.assertAlmostEqual(max(sizes), 0.45 * 1.2, places=8)
        peak_index = sizes.index(max(sizes))
        self.assertAlmostEqual(peak_index * 0.02, 0.16, delta=0.02)
        self.assertTrue(all(a >= b for a, b in zip(sizes[peak_index:], sizes[peak_index + 1:])))
        self.assertTrue(all(s >= 0.45 for s in sizes[peak_index:]))
        self.assertEqual(sizes[-1], 0.45)

    def test_rigid_bar_preserves_lengths_and_body_shape_throughout_shake(self):
        import math
        from pet_app import _split_bar_transform
        anchors = ((45, 120), (75, 120))
        previous = None
        for frame in range(1001):
            matrix, bar, angle = _split_bar_transform(anchors, frame / 1000)
            self.assertAlmostEqual(math.dist(*bar), 30)
            self.assertAlmostEqual((bar[0][1] + bar[1][1]) / 2, 120)
            self.assertLessEqual(abs((bar[0][0] + bar[1][0]) / 2 - 60), 1.5)
            a, b, _, d, e, _ = matrix
            # Any two perpendicular body segments retain length and angle.
            self.assertAlmostEqual(math.hypot(20 * a, 20 * d), 20)
            self.assertAlmostEqual(math.hypot(30 * b, 30 * e), 30)
            self.assertAlmostEqual(a * b + d * e, 0)
            self.assertLessEqual(abs(angle), 20 * (frame / 1000) ** 2)
            if previous is not None:
                self.assertLess(max(math.dist(p, q) for p, q in zip(previous, bar)), 2)
            previous = bar
        _, original, angle = _split_bar_transform(anchors, 0)
        self.assertEqual(original, anchors)
        self.assertEqual(angle, 0)

    def test_shake_frequency_rises_smoothly_from_ten_to_eighteen(self):
        import math
        from pet_app import _split_shake_phase
        from pet_group import SPLIT_DURATION_SEC
        epsilon = 1e-5
        def frequency(u):
            return (_split_shake_phase(u + epsilon) - _split_shake_phase(u)) / (
                2 * math.pi * epsilon * SPLIT_DURATION_SEC)
        self.assertAlmostEqual(frequency(0), 10, places=3)
        self.assertAlmostEqual(frequency(1 - epsilon), 18, places=2)
        frequencies = [frequency(u / 10) for u in range(10)]
        self.assertEqual(frequencies, sorted(frequencies))
        self.assertAlmostEqual(frequency(0.5), 12, places=2)
        self.assertGreater(frequency(0.95) - frequency(0.8), frequency(0.35) - frequency(0.2))

    def test_maximum_only_splits_on_six_even_beyond_forty(self):
        group, pet = self.group(1.0)
        for n in range(5):
            pet._complete_split_click(11 + n * 0.03)
        pet._settle_growth_clicks(11.5)
        self.assertEqual(pet.global_scale, 1.0)
        self.assertEqual(group.pending_bursts, [])
        group.pets += [object() for _ in range(45)]
        for n in range(6):
            pet._complete_split_click(12 + n * 0.03)
        self.assertTrue(pet._splitting)
        self.assertEqual(group.pending_bursts[0][2], 0.8)

    def test_maximum_still_puffs_for_one_to_five_clicks_then_returns_to_cap(self):
        for count in range(1, 6):
            group, pet = self.group(1.0)
            pet.foot_position = Mock(return_value=(200, 300))
            sizes = []
            def resize(scale, **kwargs):
                sizes.append(scale)
                pet.global_scale = scale
            pet.set_scale = resize
            for n in range(count):
                pet._complete_split_click(11 + n * 0.03)
            pet._settle_growth_clicks(12)
            for frame in range(26):
                pet._advance_growth(12 + frame / 60)
            self.assertGreater(max(sizes), 1.0)
            pet._animate_growth(1.0, 13)
            pet._advance_growth(13.16)
            self.assertAlmostEqual(pet.global_scale, 1.2)
            pet._advance_growth(13.41)
            self.assertEqual(sizes[-1], 1.0)
            self.assertEqual((pet._growth_base, pet._growth_step), (1.0, 0))
            self.assertEqual(group.pending_bursts, [])

    def test_long_batch_splits_beyond_old_capacity_without_growth(self):
        group, pet = self.group()
        group.pets += [object() for _ in range(45)]
        pet._animate_growth = Mock()
        for n in range(9):
            pet._complete_split_click(11 + n * 0.03)
        pet._settle_growth_clicks(12)
        pet._animate_growth.assert_not_called()
        self.assertEqual(pet._growth_step, 0)
        self.assertEqual(len(group.pending_bursts), 1)

    def test_split_uses_first_smaller_major_at_exact_and_small_tiers(self):
        for base, step, expected in ((1.0, 0, 0.8), (0.8, 0, 0.6), (0.6, 0, 0.4), (0.4, 0, 0.4),
                                      (0.6, 3, 0.6), (0.4, 5, 0.4)):
            with self.subTest(base=base, step=step):
                group, pet = self.group(base)
                pet._growth_step = step
                pet.global_scale = base + step / 30
                self.assertEqual(group.request_split(pet, 11), 2)
                self.assertEqual(group.pending_bursts[0][2], expected)
                self.assertEqual(group.pending_bursts[0][0], 11.75)

    def test_charge_survives_laugh_end_replaces_parent_and_primary(self):
        group, pet = self.group()
        self.assertEqual(group.request_split(pet, 11), 2)
        pet._to_idle()
        self.assertEqual(len(group.pending_bursts), 1)
        children = [Mock(), Mock()]
        group.spawn = Mock(side_effect=children)
        group.destroy_pet = Mock(side_effect=lambda p: group.cancel_emission(p))
        group.set_primary = Mock()
        group._emit_due(11.749)
        group.spawn.assert_not_called()
        group._emit_due(11.75)
        self.assertEqual([c.kwargs["direction"] for c in group.spawn.call_args_list], [-1, 1])
        group.destroy_pet.assert_called_once_with(pet)
        group.set_primary.assert_called_once_with(children[0])
        self.assertEqual(group.pending_bursts, [])

    def test_child_inherits_phase_range_sound_and_finishes_once(self):
        for full in (False, True):
            group, source = self.group()
            with patch("pet_app.time.monotonic", return_value=10):
                source.play("laugh", with_audio=True, full=full)
            child = app_probe(0.6)
            child.audio.output_latency_ms = 170
            with patch("pet_app.time.monotonic", return_value=12):
                child.inherit_animation(source)
                source.tick()
            self.assertEqual(child.cur_index, source.cur_index)
            self.assertEqual(child._action_start, source._action_start)
            self.assertEqual(child._range_end, source._range_end)
            self.assertEqual(child.audio.play.call_args.kwargs.get("loops", 0), 0)
            self.assertAlmostEqual(child.audio.play.call_args.kwargs["start_ms"],
                                   source._range_first * source.frame_ms + 2170)
            end = source._action_start + (source._range_end - source._range_first) * source.frame_ms / 1000
            with patch("pet_app.time.monotonic", return_value=end + 0.001):
                child.tick()
            self.assertEqual(child.mode, "idle")
            late = app_probe()
            with patch("pet_app.time.monotonic", return_value=end + 1):
                late.inherit_animation(source)
            self.assertEqual(late.mode, "idle")

    def test_short_laugh_last_frame_and_inclusive_328_frames(self):
        group, pet = self.group()
        self.assertEqual(pet._range_end - pet._range_first, 328)
        end = pet._action_start + 328 * pet.frame_ms / 1000
        with patch("pet_app.time.monotonic", return_value=end - 0.001):
            pet.tick()
        self.assertEqual(pet.cur_index, 380)
        with patch("pet_app.time.monotonic", return_value=end + 0.001):
            pet.tick()
        self.assertEqual(pet.mode, "idle")

    def test_failed_child_rolls_back_parent_and_progress(self):
        group, pet = self.group()
        child = Mock()
        group.request_split(pet, 11)
        group.spawn = Mock(side_effect=[child, RuntimeError("failure")])
        group.destroy_pet = Mock()
        group._emit_due(13)
        group.destroy_pet.assert_called_once_with(child)
        self.assertFalse(pet._splitting)
        self.assertIn(pet, group.pets)


class ThrowTests(unittest.TestCase):
    def test_adjusted_throw_survives_physics_speed_limit_and_resize(self):
        group = bare_group()
        group.physics = PhysicsWorld()
        group._sync_physics_timer = Mock()
        pet = app_probe()
        pet.visible_box = Mock(return_value=(0, 0, 60, 100))
        pet.foot_position = Mock(return_value=(130, 300))
        with patch("pet_group.work_area", return_value=(0, 0, 10000, 1080)):
            group.throw(pet, (1700, 0))
            particle = group.physics.particles[pet]
            self.assertEqual(tuple(particle.body.velocity), (3060, 0))
            group.physics.advance(STEP * 2)
            self.assertGreater(particle.body.velocity.x, 3000)
            group.resize_physics(pet)
            self.assertEqual(group.physics.particles[pet].speed_multiplier, 1.8)

    def test_velocity_directions_speed_and_pause(self):
        from collections import deque
        from types import SimpleNamespace
        app = app_probe()
        for x, y in ((120, 0), (-120, 0), (0, -120), (0, 120)):
            app._drag_samples = deque([(10, 0, 0), (10.12, x, y)])
            vx, vy = app._release_velocity(SimpleNamespace(x_root=x, y_root=y), 10.12)
            self.assertAlmostEqual(vx, x / 0.12)
            self.assertAlmostEqual(vy, y / 0.12)
            # Pausing before release removes the earlier swipe.
            self.assertEqual(app._release_velocity(SimpleNamespace(x_root=x, y_root=y), 10.4), (0, 0))

    def test_fast_release_prioritizes_throw_slow_release_can_merge(self):
        from collections import deque
        from types import SimpleNamespace
        for distance, throws in ((120, True), (1, False)):
            app = app_probe()
            app.group = SimpleNamespace(reunifying=False, throw=Mock(), try_merge=Mock(return_value=False),
                                        set_hover=Mock())
            app.visible_box = Mock(return_value=(0, 0, 100, 200))
            app.dragging = app.drag_moved = True
            app._press_valid = False
            app._press_x = app._press_y = 0
            app._drag_samples = deque([(10, 0, 0), (10.12, distance, 0)])
            with patch("pet_app.time.monotonic", return_value=10.12):
                app._on_release(SimpleNamespace(x_root=distance, y_root=0))
            self.assertEqual((app.mode, app.cur_index), ("thrown", 237) if throws else ("idle", 0))
            self.assertEqual(app.group.throw.called, throws)
            self.assertEqual(app.group.try_merge.called, not throws)


class PhysicsTests(unittest.TestCase):
    def run_world(self, world, seconds):
        for _ in range(round(seconds / STEP)):
            world.advance(STEP)

    def test_outside_release_and_extreme_speeds_stay_inside_each_monitor(self):
        for area in ((0, 0, 1920, 1080), (-3840, -2160, 0, 0)):
            for direction in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
                with self.subTest(area=area, direction=direction):
                    world = PhysicsWorld()
                    x = (area[0] + area[2]) / 2 + direction[0] * 10000
                    y = (area[1] + area[3]) / 2 + direction[1] * 10000
                    entry = world.add("pet", (0, 0, 24, 60), (x, y), area, dynamic=True,
                                      velocity=tuple(v * 100000 for v in direction), speed_multiplier=1.8)
                    for _ in range(240):
                        world.advance(STEP)
                        self.assertGreaterEqual(entry.shape.bb.left, area[0] - 1e-6)
                        self.assertGreaterEqual(entry.shape.bb.bottom, area[1] - 1e-6)
                        self.assertLessEqual(entry.shape.bb.right, area[2] + 1e-6)
                        self.assertLessEqual(entry.shape.bb.top, area[3] + 1e-6)

    def test_already_escaped_particle_is_recovered_without_reversing_inward_motion(self):
        world = PhysicsWorld()
        entry = world.add("pet", (0, 0, 64, 140), (400, 200), (0, 0, 1000, 1080), dynamic=True)
        entry.body.position = (-3500, 32767)
        entry.body.velocity = (200, -100)
        world.advance(STEP * 2)
        self.assertGreaterEqual(entry.shape.bb.left, 0)
        self.assertLessEqual(entry.shape.bb.top, 1080)
        self.assertGreater(entry.body.velocity.x, 0)
        self.assertLess(entry.body.velocity.y, 0)

    def test_stronger_gravity_accelerates_free_fall_on_each_screen_size(self):
        from pet_physics import MOTION_RATE
        for height in (540, 1080):
            world = PhysicsWorld()
            particle = world.add("fall", (0, 0, 30, 60), (200, 100),
                                 (0, 0, 1000, height), dynamic=True)
            world.advance(STEP / MOTION_RATE)
            self.assertAlmostEqual(particle.body.velocity.y, 5400 * height / 1080 * STEP)

    def test_boundary_rebound_is_adjusted_and_pet_rebound_is_preserved(self):
        from types import SimpleNamespace
        import pymunk
        world = PhysicsWorld()
        for kind, expected in ((2, 0.40625), (3, 0.34375), (1, 0.30)):
            with self.subTest(kind=kind):
                moving = SimpleNamespace(collision_type=1, body=SimpleNamespace(velocity=pymunk.Vec2d(1000, 0)))
                obstacle = SimpleNamespace(collision_type=kind, body=SimpleNamespace(velocity=pymunk.Vec2d(0, 0)))
                contact = SimpleNamespace(shapes=(moving, obstacle))
                world._contact(contact, None, None)
                self.assertEqual(contact.restitution, expected)

    def test_actual_wall_rebound_retains_about_forty_percent_speed(self):
        world = PhysicsWorld()
        entry = world.add("pet", (0, 0, 64, 140), (850, 200), (0, 0, 1000, 1080),
                          dynamic=True, velocity=(1000, 0))
        for _ in range(120):
            before = entry.body.velocity.x
            world.advance(STEP)
            after = entry.body.velocity.x
            if before > 0 and after < 0:
                self.assertAlmostEqual(abs(after / before), 0.40625, delta=0.01)
                break
        else:
            self.fail("No wall collision occurred")

    def test_ballistic_wall_floor_bounces_and_sleep(self):
        world = PhysicsWorld()
        area = (-1000, -700, 0, 0)
        entry = world.add("pet", (0, 0, 64, 140), (-140, -450), area,
                          dynamic=True, velocity=(600, -100))
        wall_bounce = False
        floor_bounces = 0
        previous = entry.body.velocity
        for _ in range(2400):
            world.advance(STEP)
            current = entry.body.velocity
            wall_bounce |= previous.x > 10 and current.x < -10
            floor_bounces += previous.y > 100 and current.y < -100
            previous = current
        self.assertTrue(wall_bounce)
        self.assertGreaterEqual(floor_bounces, 1)  # Softer boundaries settle after fewer visible bounces.
        self.assertTrue(entry.body.is_sleeping)
        self.assertGreaterEqual(entry.shape.bb.left, area[0] - 1)
        self.assertLessEqual(entry.shape.bb.top, area[3] + 1)

    def test_settled_detects_supported_rest_before_sleep_but_not_airborne_apex(self):
        world = PhysicsWorld()
        entry = world.add("pet", (0, 0, 64, 140), (400, 200), (0, 0, 1000, 1080),
                          dynamic=True, velocity=(0, 0))
        self.assertFalse(world.is_settled("pet"))
        bouncing = False
        for _ in range(2400):
            world.advance(STEP)
            if entry.body.velocity.y < -100:
                bouncing = True
                self.assertFalse(world.is_settled("pet"))
            if world.is_settled("pet"):
                self.assertTrue(bouncing)
                self.assertFalse(entry.body.is_sleeping)
                break
        else:
            self.fail("Supported rest was not detected before sleep")

    def test_forty_capsules_pile_stably_and_removing_support_wakes_them(self):
        world = PhysicsWorld()
        rng = random.Random(905)
        area = (0, 0, 1000, 1040)
        for index in range(40):
            world.add(index, (0, 0, 64, 140), (100 + index % 10 * 76, 20 + index // 10 * 165),
                      area, dynamic=True, velocity=(rng.uniform(-60, 60), 0))
        self.run_world(world, 25)
        self.assertTrue(all(e.body.is_sleeping for e in world.particles.values()))
        entries = list(world.particles.values())
        for a in entries:
            self.assertLessEqual(a.shape.bb.top, 1041)
            for b in entries:
                if a is b:
                    continue
                delta = a.body.position - b.body.position
                gap = max(0, abs(delta.y) - abs(a.shape.b.y) - abs(b.shape.b.y))
                self.assertGreaterEqual((delta.x ** 2 + gap ** 2) ** 0.5,
                                        a.shape.radius + b.shape.radius - 1.5)
        # A fixed support can be removed without leaving the sleeper suspended.
        world = PhysicsWorld()
        support = world.add("support", (0, 0, 120, 140), (300, 700), area)
        # An intentionally flat ledge isolates removal from rolling off a curved head.
        import pymunk
        space = world.spaces[area]
        space.remove(support.shape)
        support.shape = pymunk.Segment(support.body, (-200, -70), (200, -70), 10)
        support.shape.collision_type = 1
        support.shape.pet_owner = "support"
        space.add(support.shape)
        child = world.add("child", (0, 0, 64, 140), (328, 500), area, dynamic=True)
        self.run_world(world, 10)
        y = child.body.position.y
        world.remove("support")
        self.run_world(world, 3)
        self.assertGreater(child.body.position.y, y + 100)

    def test_source_grace_clears_after_separation_and_drag_can_pin(self):
        world = PhysicsWorld()
        area = (0, 0, 1920, 1040)
        world.add("source", (0, 0, 80, 170), (500, 400), area)
        child = world.add("child", (0, 0, 64, 140), (508, 415), area,
                          dynamic=True, velocity=(700, -900), source="source")
        self.run_world(world, 0.4)
        self.assertIsNone(child.source)
        self.assertLess(child.body.position.y, 485)
        world.add("child", (0, 0, 64, 140), (200, 200), area)
        self.run_world(world, 1)
        self.assertFalse(world.is_dynamic("child"))
        self.assertEqual(tuple(world.particles["child"].body.position), (232, 270))
        world.clear()
        self.assertEqual((world.particles, world.spaces), ({}, {}))

    def test_adjacent_monitors_and_top_wall_remain_independent(self):
        world = PhysicsWorld()
        left = world.add("left", (0, 0, 64, 140), (-90, -300), (-1000, -700, 0, 0),
                         dynamic=True, velocity=(1000, -900))
        right = world.add("right", (0, 0, 64, 140), (20, 300), (0, 0, 1000, 1040),
                          dynamic=True, velocity=(-1000, -900))
        self.run_world(world, 8)
        self.assertEqual(len(world.spaces), 2)
        self.assertLessEqual(left.shape.bb.right, 1)
        self.assertGreaterEqual(left.shape.bb.bottom, -701)
        self.assertGreaterEqual(right.shape.bb.left, -1)
        self.assertGreaterEqual(right.shape.bb.bottom, -1)

    def test_dense_fountain_does_not_form_a_floating_wall_arch(self):
        for seed in (0, 1, 10, 180, 548):
            with self.subTest(seed=seed):
                world = PhysicsWorld()
                area = (0, 0, 1707, 1019)
                world.add("source", (0, 0, 102, 224), (680, 289), area)
                rng = random.Random(seed)
                for index in range(39):
                    world.add(index, (0, 0, 64, 140), (699, 331), area,
                              dynamic=True, source="source",
                              velocity=world.launch_velocity(area, (-1) ** index, rng))
                self.run_world(world, 8)
                landed = sum(entry.body.position.y > 600 for pet, entry in world.particles.items() if pet != "source")
                self.assertGreaterEqual(landed, 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
