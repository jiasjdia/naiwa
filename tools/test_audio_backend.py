"""Audio decoding, replay, offsets, volume, and failure isolation."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pet_audio import AudioPlayer, RATE, cached_wav


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "track.wav"
        with wave.open(str(self.source), "wb") as wav:
            wav.setparams((2, 2, RATE, 0, "NONE", ""))
            wav.writeframes(b"\0" * (RATE * 4 // 10) + b"\x10\0\x10\0" * RATE)
        (self.root / "media_map.json").write_text(json.dumps({"volume": 0.7, "outputLatencyMs": 170, "clips": {
            "laugh": {"media": "track.wav", "startOffsetMs": 100},
            "dance1": {"media": "track.wav"}}}), encoding="utf-8")
        self.mixer = Mock()
        self.player = AudioPlayer(self.root, self.mixer)
        self.addCleanup(self.player.quit)

    def test_decode_preserves_leading_silence_and_reuses_cache(self):
        target = cached_wav(self.source, self.root / ".cache/audio")
        mtime = target.stat().st_mtime_ns
        self.assertEqual(target, cached_wav(self.source, self.root / ".cache/audio"))
        self.assertEqual(mtime, target.stat().st_mtime_ns)
        with wave.open(str(target), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (RATE, 2, 2))
            self.assertEqual(wav.getnframes(), RATE + RATE // 10)
            self.assertFalse(any(wav.readframes(RATE // 10)))
            self.assertTrue(any(wav.readframes(100)))
        self.source.write_bytes(b"different")
        with self.assertRaises(Exception):
            cached_wav(self.source, self.root / ".cache/audio")

    def test_preload_replay_switch_offsets_and_volume(self):
        self.assertEqual(self.player.prewarm(), 2)
        channel = self.mixer.Channel.return_value
        channel.set_volume.assert_called_with(0.7)
        self.assertEqual(self.player.output_latency_ms, 170)
        self.assertEqual(len(self.player.pcm["laugh"]), round(RATE * 1.1) * 4)
        channel.play.reset_mock()
        channel.stop.reset_mock()
        for _ in range(3):
            self.assertTrue(self.player.play("laugh"))
        self.assertEqual(channel.play.call_count, 3)
        self.assertEqual(channel.stop.call_count, 3)
        self.assertTrue(self.player.play("dance1", start_ms=200))
        self.assertIn(("dance1", 200), self.player.sounds)
        self.assertEqual(self.player.current, "dance1")
        self.player.stop()
        self.assertIsNone(self.player.current)
        self.assertFalse(self.player.play("unknown"))
        self.assertFalse(self.player.play("laugh", start_ms=99999))

    def test_device_failure_does_not_escape(self):
        self.mixer.init.side_effect = RuntimeError("No device")
        with patch("pet_audio.audio_log"):
            self.assertEqual(self.player.prewarm(), 0)
            self.assertFalse(self.player.play("laugh"))
        self.assertFalse(self.player.enabled)

    def test_independent_voices_share_samples_and_do_not_stop_each_other(self):
        channels = {index: Mock() for index in range(21)}
        self.mixer.Channel.side_effect = channels.__getitem__
        self.assertTrue(self.player.play("dance1"))
        channels[0].stop.reset_mock()
        self.assertTrue(self.player.play_voice("a", "laugh"))
        self.assertTrue(self.player.play_voice("b", "laugh"))
        channels[0].stop.assert_not_called()
        channels[1].play.assert_called_once()
        channels[2].play.assert_called_once_with(channels[1].play.call_args.args[0])
        self.player.stop_voice("a")
        channels[1].stop.assert_called_once()
        channels[2].stop.assert_not_called()
        self.player.release_voice("a")
        self.assertTrue(self.player.play_voice("c", "laugh"))
        self.assertEqual(self.player.voice_channels["c"][0], 1)
        self.assertEqual(self.player.voice_channels["b"][0], 2)

    def test_forty_voices_and_bounded_continuation_cache(self):
        channels = {index: Mock() for index in range(41)}
        self.mixer.Channel.side_effect = channels.__getitem__
        for index in range(40):
            self.assertTrue(self.player.play_voice(index, "laugh"))
        self.mixer.set_num_channels.assert_called_with(41)
        self.assertEqual(len(self.player.voice_channels), 40)
        self.player.stop_voice(20)
        channels[21].stop.assert_called_once()
        channels[22].stop.assert_not_called()
        for offset in range(100):
            self.assertTrue(self.player.play_voice(0, "laugh", start_ms=offset))
        self.assertLessEqual(len(self.player.sounds), 64)

    def test_loop_segment_uses_exact_fractional_sample_boundaries(self):
        start, end = 1000 * 8 / 30, 1000 * 27 / 30
        self.assertTrue(self.player.play_voice("loop", "laugh", start_ms=start, end_ms=end, loops=-1))
        samples = self.mixer.Sound.call_args.kwargs["buffer"]
        first, last = round(start * RATE / 1000) * 4, round(end * RATE / 1000) * 4
        self.assertEqual(samples, self.player.pcm["laugh"][first:last])
        self.mixer.Channel.return_value.play.assert_called_once_with(self.mixer.Sound.return_value, loops=-1)
        self.assertTrue(self.player.play_voice("pad", "laugh", start_ms=1000, end_ms=1200, loops=-1))
        self.assertEqual(len(self.mixer.Sound.call_args.kwargs["buffer"]), round(0.2 * RATE) * 4)
        with patch("pet_audio.audio_log"):
            self.assertFalse(self.player.play_voice("invalid", "laugh", start_ms=800, end_ms=700, loops=-1))

    def test_real_dummy_mixer_can_overlap_forty_and_stop_one(self):
        script = """
import sys
from pet_audio import AudioPlayer
player = AudioPlayer(sys.argv[1])
try:
    assert player.prewarm('laugh') == 1
    for voice in range(40):
        assert player.play_voice(voice, 'laugh')
    assert len(player.voice_channels) == 40
    assert all(channel.get_busy() for _, channel in player.voice_channels.values())
    player.stop_voice(0)
    assert not player.voice_channels[0][1].get_busy()
    assert all(player.voice_channels[v][1].get_busy() for v in range(1, 40))
finally:
    player.quit()
assert not player.voice_channels
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.root)], cwd=ROOT,
                                env={**os.environ, "SDL_AUDIODRIVER": "dummy", "PYGAME_HIDE_SUPPORT_PROMPT": "1"},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_dummy_loop_continues_past_multiple_cycles_until_stop(self):
        script = """
import sys, time
from pet_audio import AudioPlayer
player = AudioPlayer(sys.argv[1])
try:
    assert player.play_voice('loop', 'laugh', start_ms=200, end_ms=300, loops=-1)
    channel = player.voice_channels['loop'][1]
    assert abs(channel.get_sound().get_length() - 0.1) < 1 / 48000
    time.sleep(0.36)
    assert channel.get_busy()
    player.stop_voice('loop')
    assert not channel.get_busy()
finally:
    player.quit()
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.root)], cwd=ROOT,
                                env={**os.environ, "SDL_AUDIODRIVER": "dummy", "PYGAME_HIDE_SUPPORT_PROMPT": "1"},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
