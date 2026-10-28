"""Start, stop, or diagnose the one Python desktop-pet implementation."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
sys.path.insert(0, str(ROOT))


def diagnose():
    failures = []
    print("Python:", sys.version.split()[0], sys.executable)
    for module in ("tkinter", "PIL", "pygame.mixer", "psutil", "pymunk"):
        try:
            importlib.import_module(module)
            print("OK", module)
        except ImportError as error:
            failures.append(str(error))
            print("FAIL", module, error)
    for relative in ("pet_app.py", "pet_group.py", "pet_audio.py", "pet_eyes.py", "pet_runtime.py", "pet_physics.py", "pet_surface.py",
                     "assets/manifest.json", "media_map.json", "assets/eyes/backing.png",
                     "assets/eyes/pupil.png", "assets/eyes/geometry.json"):
        present = (ROOT / relative).is_file()
        print("OK" if present else "FAIL", relative)
        if not present:
            failures.append(relative)
    try:
        manifest = json.loads((ROOT / "assets/manifest.json").read_text(encoding="utf-8"))
        missing = [str(ROOT / "assets" / c["dir"] / frame)
                   for c in manifest["clips"] for frame in c["frameFiles"]
                   if not (ROOT / "assets" / c["dir"] / frame).is_file()]
        failures.extend(missing)
        print("Animation frames:", sum(c["frames"] for c in manifest["clips"]), "missing:", len(missing))
        media = json.loads((ROOT / "media_map.json").read_text(encoding="utf-8"))
        for cid, item in media["clips"].items():
            path = Path(item["media"].replace("\\", "/"))
            path = path if path.is_absolute() else ROOT / path
            if not path.is_file():
                failures.append(str(path))
            print("Audio", cid, "OK" if path.is_file() else "MISSING", path.name)
    except (OSError, ValueError, KeyError) as error:
        failures.append(str(error))
    print("Log:", ROOT / "_preview" / "pet.log")
    print("PASS" if not failures else f"FAIL: {len(failures)} issue(s)")
    return int(bool(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("start", "stop", "check"), default="start")
    parser.add_argument("--foreground", action="store_true")
    args = parser.parse_args()
    if args.command == "check":
        return diagnose()
    if args.command == "stop":
        from pet_runtime import stop_project
        return 0 if stop_project() else 1
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if args.foreground:
        return subprocess.call([sys.executable, str(ROOT / "pet_app.py")], cwd=ROOT)
    executable = pythonw if pythonw.is_file() else Path(sys.executable)
    subprocess.Popen([str(executable), str(ROOT / "pet.pyw")], cwd=ROOT,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
