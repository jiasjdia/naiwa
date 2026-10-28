"""Run the Python desktop pet's behavior and integration checks."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", action="store_true", help="also run real Tk rendering and scale checks")
    args = parser.parse_args()
    checks = [["tools/run_pet.py", "check"], ["tools/test_behavior.py"],
              ["tools/test_jelly.py"] + (["--gui"] if args.gui else []),
              ["tools/test_eyes.py"] + (["--gui"] if args.gui else []), ["tools/test_audio_backend.py"],
              ["tools/test_audio_gating.py"], ["tools/test_runtime.py"]]
    checks.append(["tools/test_group.py"] + (["--gui"] if args.gui else []))
    checks.append(["tools/test_split.py"])
    checks.append(["tools/test_paths.py"])
    checks.append(["tools/test_laugh_spread.py"])
    checks.append(["tools/test_effects.py"] + (["--gui"] if args.gui else []))
    checks.append(["tools/test_drawing.py"] + (["--gui"] if args.gui else []))
    if args.gui:
        checks.append(["tools/test_scale.py"])
    failures = []
    for check in checks:
        print("\nRunning", " ".join(check), flush=True)
        if subprocess.call([sys.executable] + check, cwd=ROOT):
            failures.append(check[0])
    print("\nPASS" if not failures else "\nFAIL: " + ", ".join(failures))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
