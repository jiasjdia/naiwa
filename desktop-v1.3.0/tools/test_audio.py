"""Audible playback and interruption smoke check for the Python mixer."""
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pet_audio import AudioPlayer


def main():
    player = AudioPlayer()
    try:
        loaded = player.prewarm("laugh")
        print("Preloaded tracks:", loaded)
        if loaded != 1:
            return 1
        for clip in ("laugh",):
            if not player.play(clip):
                return 1
            print("Playing:", clip)
            time.sleep(2.5)
            player.stop()
            if player.channel.get_busy():
                return 1
        for clip in ("laugh", "laugh", "laugh"):
            if not player.play(clip):
                return 1
            time.sleep(0.15)
        print("PASS: replay, replacement, and stop")
        return 0
    finally:
        player.quit()


if __name__ == "__main__":
    raise SystemExit(main())
