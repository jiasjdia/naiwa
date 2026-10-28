"""Preloaded audio for the desktop pet; no player subprocesses."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import wave
from collections import OrderedDict
from pathlib import Path

from pet_paths import ROOT, DATA_ROOT
RATE = 48000
CHANNELS = 2
SAMPLE_BYTES = 2


def audio_log(message):
    from pet_app import log
    log(message)


def cached_wav(source: Path, cache: Path) -> Path:
    """Decode the complete track once, including its original leading silence."""
    if source.suffix.lower() == ".wav":
        try:
            with wave.open(str(source), "rb") as wav:
                if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) == (RATE, CHANNELS, SAMPLE_BYTES, "NONE"):
                    return source
        except (OSError, EOFError, wave.Error):
            pass
    fingerprint = hashlib.sha256(source.read_bytes() + b"pcm-s16le-48000-stereo-v1").hexdigest()
    target = cache / (fingerprint + ".wav")
    if target.exists():
        try:
            with wave.open(str(target), "rb") as wav:
                if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (RATE, CHANNELS, SAMPLE_BYTES):
                    return target
        except (OSError, EOFError, wave.Error):
            pass
    import imageio_ffmpeg
    cache.mkdir(parents=True, exist_ok=True)
    temporary = cache / (fingerprint + f".{os.getpid()}.tmp.wav")
    try:
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(source), "-vn", "-ar", str(RATE), "-ac", str(CHANNELS),
             "-c:a", "pcm_s16le", str(temporary)],
            check=True, capture_output=True, timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


class AudioPlayer:
    def __init__(self, root=ROOT, mixer=None):
        self.root = Path(root)
        self.media = {}
        self.offset_ms = {}
        self.volume = 0.7
        self.enabled = True
        self.current = None
        self.mixer = mixer
        self.channel = None
        self.voice_channels = {}
        self.sounds = OrderedDict()
        self.pcm = {}
        self.last_play_latency_ms = None
        self.output_latency_ms = 0.0
        try:
            data = json.loads((self.root / "media_map.json").read_text(encoding="utf-8"))
            self.volume = max(0.0, min(1.0, float(data.get("volume", 0.7))))
            self.output_latency_ms = max(0.0, min(1000.0, float(data.get("outputLatencyMs", 0))))
            for cid, item in data.get("clips", {}).items():
                media = item.get("media") if isinstance(item, dict) else item
                if not media:
                    continue
                path = Path(str(media).replace("\\", "/"))
                self.media[cid] = path if path.is_absolute() else self.root / path
                self.offset_ms[cid] = max(0, int(item.get("startOffsetMs", 0))) if isinstance(item, dict) else 0
        except Exception as error:
            self.enabled = False
            audio_log(f"Audio configuration unavailable: {error}")

    def available(self, clip_id):
        return self.enabled and clip_id in self.media and self.media[clip_id].is_file()

    def _initialize(self):
        if self.channel is not None:
            return True
        try:
            if self.mixer is None:
                os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
                from pygame import mixer
                self.mixer = mixer
            self.mixer.init(frequency=RATE, size=-16, channels=CHANNELS, buffer=512, allowedchanges=0)
            # Keep legacy channel 0; every pet (including broadcast) owns a voice.
            self._channel_capacity = 41
            self.mixer.set_num_channels(self._channel_capacity)
            self.channel = self.mixer.Channel(0)
            self.channel.set_volume(self.volume)
            return True
        except Exception as error:
            self.enabled = False
            audio_log(f"Audio device unavailable; continuing silently: {error}")
            return False

    def _sound(self, clip_id, offset, end_ms=None):
        key = (clip_id, offset) if end_ms is None else (clip_id, offset, end_ms)
        if key not in self.sounds:
            if clip_id not in self.pcm:
                path = cached_wav(self.media[clip_id], DATA_ROOT / ".cache" / "audio")
                with wave.open(str(path), "rb") as wav:
                    self.pcm[clip_id] = wav.readframes(wav.getnframes())
            start = round(offset * RATE / 1000) * CHANNELS * SAMPLE_BYTES
            pcm = self.pcm[clip_id]
            if start >= len(pcm):
                raise ValueError("startOffsetMs is past the end of the track")
            end = len(pcm) if end_ms is None else round(end_ms * RATE / 1000) * CHANNELS * SAMPLE_BYTES
            if end <= start:
                raise ValueError("audio segment end must follow its start")
            samples = pcm[start:end]
            if end > len(pcm):
                # Match the exact animation cycle even when the track is shorter.
                samples += b"\0" * (end - len(pcm))
            if not samples:
                raise ValueError("startOffsetMs is past the end of the track")
            self.sounds[key] = self.mixer.Sound(buffer=samples)
            while len(self.sounds) > 64:
                self.sounds.popitem(last=False)
        self.sounds.move_to_end(key)
        return self.sounds[key]

    def prewarm(self, clip_id=None):
        if not self.enabled or not self._initialize():
            return 0
        count = 0
        for cid in ([clip_id] if clip_id else self.media):
            if not self.available(cid):
                continue
            try:
                self._sound(cid, self.offset_ms.get(cid, 0))
                count += 1
            except Exception as error:
                audio_log(f"Cannot preload {cid}: {error}")
        return count

    def play(self, clip_id, start_ms=None):
        self.stop()
        if not self.available(clip_id) or not self._initialize():
            return False
        try:
            offset = max(0, int(self.offset_ms.get(clip_id, 0) if start_ms is None else start_ms))
            sound = self._sound(clip_id, offset)
            started = time.perf_counter()
            self.channel.play(sound)
            self.last_play_latency_ms = (time.perf_counter() - started) * 1000
            self.current = clip_id
            return True
        except Exception as error:
            audio_log(f"Cannot play {clip_id}: {error}")
            return False

    def stop(self):
        if self.channel is not None:
            try:
                self.channel.stop()
            except Exception:
                pass
        self.current = None

    def play_voice(self, voice, clip_id, start_ms=None, *, end_ms=None, loops=0):
        self.stop_voice(voice)
        if not self.available(clip_id) or not self._initialize():
            return False
        try:
            entry = self.voice_channels.get(voice)
            if entry is None:
                used = {index for index, _ in self.voice_channels.values()}
                index = 1
                while index in used:
                    index += 1
                if index >= self._channel_capacity:
                    self._channel_capacity = max(index + 1, self._channel_capacity * 2)
                    self.mixer.set_num_channels(self._channel_capacity)
                channel = self.mixer.Channel(index)
                channel.set_volume(self.volume)
                self.voice_channels[voice] = (index, channel)
            else:
                _, channel = entry
            offset = max(0, float(self.offset_ms.get(clip_id, 0) if start_ms is None else start_ms))
            sound = self._sound(clip_id, offset, end_ms)
            if loops:
                channel.play(sound, loops=loops)
            else:
                channel.play(sound)
            return True
        except Exception as error:
            audio_log(f"Cannot play independent {clip_id}: {error}")
            return False

    def stop_voice(self, voice):
        entry = self.voice_channels.get(voice)
        if entry is not None:
            try:
                entry[1].stop()
            except Exception:
                pass

    def release_voice(self, voice):
        self.stop_voice(voice)
        self.voice_channels.pop(voice, None)

    def quit(self):
        self.stop()
        for voice in tuple(self.voice_channels):
            self.release_voice(voice)
        self.sounds.clear()
        self.pcm.clear()
        if self.channel is not None:
            self.mixer.quit()
            self.channel = None
