"""Extract and run the actual portable ZIP without using the build's Python."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pet_runtime import signal_instance


def wait_for(predicate, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for the packaged app")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="naiwa-打包验证-") as directory:
        target = Path(directory)
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(target)
        bundle = target / "naiwa"
        inventory = json.loads((bundle / "FILES.sha256.json").read_text(encoding="utf-8"))
        for name, digest in inventory.items():
            assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == digest, name
        frames = list((bundle / "assets/laugh").glob("*.webp"))
        assert len(frames) == 432
        assert not (bundle / "assets/dance1").exists()
        assert not (bundle / "assets/dance2").exists()
        assert not list(bundle.rglob("*ffmpeg*"))
        assert not list(bundle.rglob("*.mp4"))
        assert not list(bundle.rglob("*.m4a"))
        environment = dict(os.environ)
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        environment["SDL_AUDIODRIVER"] = "dummy"
        process = subprocess.Popen([str(bundle / "naiwa.exe")], cwd=target, env=environment)
        log = bundle / "_preview/pet.log"
        try:
            wait_for(lambda: log.exists() and "窗口已显示" in log.read_text(encoding="utf-8"))
            assert process.poll() is None
            metadata = json.loads((bundle / ".cache/pet-instance.json").read_text(encoding="utf-8"))
            assert metadata["pid"] == process.pid
            geometry = json.loads((bundle / "_preview/pet_geometry.json").read_text(encoding="utf-8"))
            assert geometry["w"] > 0 and geometry["h"] > 0
            duplicate = subprocess.run([str(bundle / "naiwa.exe")], cwd=target, env=environment, timeout=10)
            assert duplicate.returncode == 0
            assert log.read_text(encoding="utf-8").count("窗口已显示") == 1
            assert signal_instance("laugh", bundle)
            wait_for(lambda: "播放 捧腹大笑" in log.read_text(encoding="utf-8"))
            assert process.poll() is None
            text = log.read_text(encoding="utf-8")
            assert not any(word in text for word in ("Traceback", "Cannot preload", "Cannot play", "Audio device unavailable")), text
            assert signal_instance("quit", bundle)
            assert process.wait(timeout=8) == 0
            assert not (bundle / ".cache/pet-instance.json").exists()
            print(f"PASS: {len(inventory)} file hashes, 432 frames, frozen startup, Tk rendering, audio, single instance, graceful quit")
        finally:
            if process.poll() is None:
                signal_instance("quit", bundle)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
