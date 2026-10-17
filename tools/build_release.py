"""Build a portable Windows release using an explicit resource allowlist."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import wave
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.0.0"


def resource_files():
    manifest = json.loads((ROOT / "assets/manifest.json").read_text(encoding="utf-8"))
    if [c["id"] for c in manifest["clips"]] != ["laugh"]:
        raise ValueError("The current release only uses the laugh clip")
    paths = [Path("assets/manifest.json"), Path("media_map.json"), Path("pet_settings.json")]
    for clip in manifest["clips"]:
        if len(clip["frameFiles"]) != clip["frames"]:
            raise ValueError("Animation frame count mismatch")
        paths += [Path("assets") / clip["dir"] / frame for frame in clip["frameFiles"]]
    paths += [Path("assets/eyes") / name for name in ("backing.png", "pupil.png", "geometry.json")]
    audio = json.loads((ROOT / "media_map.json").read_text(encoding="utf-8"))
    if set(audio["clips"]) != {"laugh"}:
        raise ValueError("Unexpected audio clip")
    for clip in audio["clips"].values():
        path = Path(clip["media"])
        with wave.open(str(ROOT / path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (48000, 2, 2, "NONE"):
                raise ValueError("Audio must be ready-to-play PCM WAV")
        paths.append(path)
    for path in paths:
        if path.is_absolute() or ".." in path.parts or not (ROOT / path).is_file():
            raise ValueError(f"Missing or unsafe resource: {path}")
    return paths


def copy_licenses(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "licenses").glob("*.txt"):
        shutil.copy2(source, destination / source.name)
    for package in ("Pillow", "pygame", "psutil", "pyinstaller"):
        distribution = importlib.metadata.distribution(package)
        for entry in distribution.files or ():
            if any(word in entry.name.lower() for word in ("license", "copying", "lgpl")):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    shutil.copy2(source, destination / f"{package}-{entry.name}")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copy2(python_license, destination / "Python-LICENSE.txt")
    for name in ("tcl8.6", "tk8.6"):
        license_file = Path(sys.base_prefix) / "tcl" / name / "license.terms"
        if license_file.exists():
            shutil.copy2(license_file, destination / f"{name}-license.txt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--icon", type=Path, help="optional Windows .ico file")
    parser.add_argument("--archive-only", action="store_true", help="repackage an existing executable build")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build this Windows executable on Windows")
    resources = resource_files()
    output = ROOT / "dist/naiwa"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
               "--name", "naiwa", "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
               "--specpath", str(ROOT / "build"), "--paths", str(ROOT), "--noupx"]
    for module in ("numpy", "imageio_ffmpeg", "matplotlib", "scipy", "IPython", "pytest", "pygame.tests", "PIL.ImageQt"):
        command += ["--exclude-module", module]
    if args.icon:
        icon = args.icon.resolve(strict=True)
        if icon.suffix.lower() != ".ico":
            parser.error("Use a Windows .ico file")
        command += ["--icon", str(icon)]
    command.append(str(ROOT / "pet.pyw"))
    if not args.archive_only:
        subprocess.run(command, cwd=ROOT, check=True)
    elif not (output / "naiwa.exe").is_file():
        parser.error("Build the executable before using --archive-only")
    for relative in resources:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    for name in ("QUICKSTART.txt", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, output / name)
    copy_licenses(output / "licenses")
    inventory = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(output.rglob("*")) if p.is_file() and p.name != "FILES.sha256.json"}
    (output / "FILES.sha256.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    releases = ROOT / "releases"
    releases.mkdir(exist_ok=True)
    archive = releases / f"naiwa-v{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                package.write(path, path.relative_to(output.parent))
    checksum = releases / "SHA256SUMS.txt"
    checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
    print(f"Built {archive} ({archive.stat().st_size / 1024**2:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
