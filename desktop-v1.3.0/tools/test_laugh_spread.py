"""Laughter exposure, distance, chaining, audio and cooldown behavior."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pet_group import LAUGH_SPREAD_RADIUS
from tools.test_group import bare_group
from tools.test_jelly import app_probe


class LaughSpreadTests(unittest.TestCase):
    def setUp(self):
        self.group = bare_group()
        self.quiet = patch("pet_app.log")
        self.quiet.start()
        self.addCleanup(self.quiet.stop)

    def pet(self, center=0, scale=0.4):
        pet = app_probe(scale)
        self.move(pet, center)
        self.group.pets.append(pet)
        return pet

    def move(self, pet, center):
        box = pet.visible_box()
        pet.root.x = center - (box[0] + box[2]) / 2
        pet.root.y = -(box[1] + box[3]) / 2

    def laugh(self, pet, now=10, audible=True):
        with patch("pet_app.time.monotonic", return_value=now):
            pet.play_short_laugh(with_audio=audible)

    def spread(self, now):
        with patch("pet_app.time.monotonic", return_value=now):
            self.group._spread_laughter(now)

    def test_delay_range_audio_and_no_restart_or_automatic_growth(self):
        source, target = self.pet(), self.pet(100)
        self.laugh(source)
        self.spread(10)
        self.spread(10.699)
        self.assertEqual(target.mode, "idle")
        self.spread(10.7)
        self.assertEqual((target.mode, target.cur_index), ("playing", 120))
        target.audio.play.assert_called_once_with("laugh", start_ms=120 * target.frame_ms,
                                                 end_ms=300 * target.frame_ms)
        self.assertEqual(target._range_end - target._range_first, 180)
        self.assertEqual(getattr(target, "_growth_click_count", 0), 0)
        self.spread(11.5)
        self.assertEqual(target._action_start, 10.7)
        self.assertEqual(target.audio.play.call_count, 1)

    def test_radius_uses_source_height_and_resets_when_leaving(self):
        source = self.pet()
        radius = (source.visible_box()[3] - source.visible_box()[1]) * LAUGH_SPREAD_RADIUS
        target = self.pet(radius - 1)
        self.laugh(source)
        self.spread(10)
        self.move(target, radius + 1)
        self.spread(10.6)
        self.move(target, radius - 1)
        self.spread(10.8)
        self.spread(11.499)
        self.assertEqual(target.mode, "idle")
        self.spread(11.5)
        self.assertEqual(target.mode, "playing")

    def test_source_size_changes_radius(self):
        source, target = self.pet(), self.pet(220)
        self.laugh(source)
        self.spread(10)
        self.spread(10.8)
        self.assertEqual(target.mode, "idle")
        larger = self.pet(scale=0.8)
        self.laugh(larger, now=11)
        self.spread(11)
        self.spread(11.7)
        self.assertEqual(target.mode, "playing")

    def test_silent_chain_spreads_in_separate_waves(self):
        a, b, c = self.pet(), self.pet(150), self.pet(300)
        self.laugh(a, audible=False)
        self.spread(10)
        self.spread(10.7)
        self.assertEqual(b.mode, "playing")
        self.assertEqual(c.mode, "idle")
        self.spread(10.71)
        self.spread(11.409)
        self.assertEqual(c.mode, "idle")
        self.spread(11.41)
        self.assertEqual(c.mode, "playing")
        for pet in (b, c):
            pet.audio.play.assert_not_called()
            self.assertFalse(pet._action_audible)

    def test_cooldown_after_finish_and_manual_start_still_allowed(self):
        source, target = self.pet(), self.pet(100)
        self.laugh(source)
        self.laugh(target)
        with patch("pet_app.time.monotonic", return_value=11):
            target.stop_laugh()
        self.assertEqual(target._laugh_cooldown_until, 14)
        self.spread(11)
        self.spread(13.99)
        self.assertEqual(target.mode, "idle")
        self.spread(14)
        self.spread(14.699)
        self.assertEqual(target.mode, "idle")
        self.spread(14.7)
        self.assertEqual(target.mode, "playing")
        with patch("pet_app.time.monotonic", return_value=14.8):
            target.stop_laugh()
        self.laugh(target, now=14.9)
        self.assertEqual(target.mode, "playing")

    def test_expired_source_and_interrupted_exposure_do_not_infect(self):
        source, target = self.pet(), self.pet(100)
        self.laugh(source)
        self.spread(15.5)
        self.spread(16.2)
        self.assertEqual(target.mode, "idle")
        self.laugh(source, now=20)
        self.spread(20)
        with patch("pet_app.time.monotonic", return_value=20.5):
            source.stop_laugh()
        self.spread(20.7)
        self.assertEqual(target.mode, "idle")

    def test_busy_pet_and_reunification_cancel_exposure(self):
        source, target = self.pet(), self.pet(100)
        self.laugh(source)
        for flag in ("dragging", "menu_open", "_press_valid", "_splitting"):
            setattr(target, flag, True)
            self.spread(10)
            self.spread(10.8)
            self.assertEqual(target.mode, "idle")
            setattr(target, flag, False)
        self.spread(11)
        self.group.reunifying = True
        self.spread(11.5)
        self.group.reunifying = False
        self.spread(12)
        self.spread(12.69)
        self.assertEqual(target.mode, "idle")
        self.spread(12.7)
        self.assertEqual(target.mode, "playing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
