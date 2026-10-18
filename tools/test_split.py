"""Recursive emission, phase inheritance, capacity reservations and real physics."""
from __future__ import annotations

from pathlib import Path
import random
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pet_physics import PhysicsWorld, STEP, TIERS, burst_scales, snap_scale, tier_of
from tools.test_group import bare_group
from tools.test_jelly import app_probe, click


class SplitTests(unittest.TestCase):
    def group(self, scale=0.8):
        group = bare_group()
        group.pending_bursts = []
        pet = app_probe(scale)
        group.pets = [pet]
        group.primary = pet
        pet.group = group
        pet.audio.output_latency_ms = 170
        with patch("pet_app.time.monotonic", return_value=10):
            pet.play("laugh", with_audio=True)
        return group, pet

    def test_all_tiers_and_random_combinations(self):
        rng = random.Random(46)
        for scale, tier in zip(TIERS, range(1, 5)):
            sizes = [burst_scales(scale, rng) for _ in range(100)]
            if tier == 4:
                self.assertTrue(all(s == [] for s in sizes))
            else:
                self.assertEqual({len(s) for s in sizes}, set(range(2, 6 - tier)))
                self.assertTrue(all(tier_of(x) > tier for s in sizes for x in s))
        self.assertEqual(snap_scale(0.65), 0.7)
        self.assertEqual(snap_scale(1.2), 0.8)
        self.assertEqual(snap_scale(0.4), 0.5)

    def test_click_window_boundary_reset_and_no_single_click_stop(self):
        group, pet = self.group()
        group.request_split = Mock()
        for now in [11] + [13] * 6:
            pet._complete_split_click(now)
        group.request_split.assert_not_called()
        pet._complete_split_click(13)
        group.request_split.assert_called_once_with(pet, 13)
        self.assertEqual(len(pet._split_clicks), 0)
        group.request_split.reset_mock()
        pet._complete_split_click(14)
        for _ in range(7):
            pet._complete_split_click(16.001)
        group.request_split.assert_not_called()
        pet._complete_split_click(16.001)
        group.request_split.assert_called_once()
        with patch("pet_app.time.monotonic", return_value=17):
            click(pet)
        self.assertEqual(pet.mode, "playing")
        pet.stop_laugh()
        self.assertEqual((pet.mode, len(pet._split_clicks)), ("idle", 0))

    def test_reservations_stagger_partial_capacity_and_no_debt(self):
        group, pet = self.group()
        group.pets += [object() for _ in range(38)]
        with patch("pet_group.burst_scales", return_value=[0.7, 0.6, 0.5]):
            self.assertEqual(group.request_split(pet, 11), 1)
            self.assertEqual(group.request_split(pet, 11), 0)
        self.assertEqual(len(group.pending_bursts), 1)
        group.cancel_emission(pet)
        group.pets = [pet]
        with patch("pet_group.burst_scales", return_value=[0.7, 0.6, 0.5]):
            self.assertEqual(group.request_split(pet, 11), 3)
        queue = group.pending_bursts
        self.assertEqual([round(e[0], 1) for e in queue], [11, 11.1, 11.2])
        self.assertEqual(queue[0][3], -queue[1][3])
        group.spawn = Mock()
        group._emit_due(11.05)
        self.assertEqual(group.spawn.call_count, 1)
        self.assertEqual(len(group.pending_bursts), 2)
        pet.stop_laugh()
        self.assertEqual(group.pending_bursts, [])
        group._emit_due(12)
        self.assertEqual(group.spawn.call_count, 1)

    def test_emitted_loop_starts_at_frame_54_with_matching_audio_and_independent_stop(self):
        group, source = self.group()
        child = app_probe(0.7)
        child.group = group
        child.audio.output_latency_ms = 170
        group.pets.append(child)
        with patch("pet_app.time.monotonic", return_value=11):
            child.inherit_animation(source)
            source.tick()
        self.assertEqual(child.cur_index, 53)
        self.assertEqual(child._loop_start, 53)
        self.assertAlmostEqual(child.audio.play.call_args.kwargs["start_ms"], 53 * child.frame_ms)
        self.assertAlmostEqual(child.audio.play.call_args.kwargs["end_ms"], child.clips["laugh"].frames * child.frame_ms)
        self.assertEqual(child.audio.play.call_args.kwargs["loops"], -1)
        self.assertEqual((len(child._clicks), len(child._split_clicks)), (0, 0))
        self.assertGreater(group.request_split(child, 11.1), 0)
        source.stop_laugh()
        self.assertEqual(child.mode, "playing")
        self.assertTrue(all(e[1] is child for e in group.pending_bursts))
        group.stop_all_laughs()
        self.assertEqual(child.mode, "idle")
        self.assertEqual(group.pending_bursts, [])

    def test_silent_inheritance_and_late_frame_catchup(self):
        group, source = self.group()
        with patch("pet_app.time.monotonic", return_value=10):
            source.play("laugh", with_audio=False)
        child = app_probe(0.6)
        child.group = group
        with patch("pet_app.time.monotonic", return_value=12):
            child.inherit_animation(source)
            child.tick()
            source.tick()
        self.assertEqual(child.cur_index, 53)
        self.assertEqual(source.cur_index, 60)
        child.audio.play.assert_not_called()
        with patch("pet_app.time.monotonic", return_value=25):
            child.tick()
            source.tick()
        self.assertEqual((child.mode, source.mode), ("playing", "idle"))
        self.assertGreaterEqual(child.cur_index, 53)

    def test_loop_boundary_last_frame_wrap_and_multiple_cycles(self):
        group, source = self.group()
        child = app_probe(0.7)
        child.group = group
        group.pets.append(child)
        child.audio.output_latency_ms = 170
        with patch("pet_app.time.monotonic", return_value=11):
            child.inherit_animation(source)
        began = child._action_start
        frames = child.clips["laugh"].frames
        period = (frames - 53) * child.frame_ms / 1000
        for elapsed, expected in ((0, 53), (period - child.frame_ms / 2000, frames - 1),
                                  (period, 53), (period * 20 + 1, 83)):
            with self.subTest(elapsed=elapsed), patch("pet_app.time.monotonic", return_value=began + elapsed):
                child.tick()
                self.assertEqual(child.mode, "playing")
                self.assertEqual(child.cur_index, expected)
                self.assertIsNotNone(child.animation_snapshot())
        child.audio.play.assert_called_once()
        source.stop_laugh()
        self.assertGreater(group.request_split(child, began + period * 20 + 1), 0)
        child.stop_laugh()
        self.assertEqual(child.mode, "idle")
        self.assertIsNone(child._loop_start)
        self.assertEqual(group.pending_bursts, [])


class PhysicsTests(unittest.TestCase):
    def run_world(self, world, seconds):
        for _ in range(round(seconds / STEP)):
            world.advance(STEP)

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
        self.assertGreaterEqual(floor_bounces, 2)
        self.assertTrue(entry.body.is_sleeping)
        self.assertGreaterEqual(entry.shape.bb.left, area[0] - 1)
        self.assertLessEqual(entry.shape.bb.top, area[3] + 1)

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
