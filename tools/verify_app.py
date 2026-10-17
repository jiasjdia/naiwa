"""Verify the published app using its trimmed resource set."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", action="store_true")
    args = parser.parse_args()
    checks = [["tools/run_pet.py", "check"], ["tools/test_behavior.py"],
              ["tools/test_audio_backend.py"], ["tools/test_audio_gating.py"], ["tools/test_runtime.py"]]
    for name in ("test_jelly.py", "test_eyes.py", "test_group.py"):
        checks.append(["tools/" + name] + (["--gui"] if args.gui else []))
    if args.gui:
        checks.append(["tools/test_scale.py"])
    failures = []
    for check in checks:
        print("Running", " ".join(check), flush=True)
        if subprocess.call([sys.executable] + check, cwd=ROOT):
            failures.append(check[0])
    print("FAIL: " + ", ".join(failures) if failures else "PASS")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
