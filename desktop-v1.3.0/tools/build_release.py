"""Build one self-contained Windows EXE with an explicit resource allowlist."""
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

ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.3.0"
APP_NAME = "奶小蛙"


def resource_files():
    manifest = json.loads((ROOT / "assets/manifest.json").read_text(encoding="utf-8"))
    if [c["id"] for c in manifest["clips"]] != ["laugh"]:
        raise ValueError("The current release only uses the laugh clip")
    paths = [Path("assets/manifest.json"), Path("assets/app.ico"), Path("media_map.json"), Path("pet_settings.json")]
    paths.append(Path("assets/effects/split_burst.png"))
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
    for package in ("Pillow", "pygame", "psutil", "pymunk", "cffi", "pycparser", "pyinstaller"):
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
    parser.add_argument("--icon", type=Path, default=ROOT / "assets/app.ico", help="Windows .ico file (default: assets/app.ico)")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build this Windows executable on Windows")
    resources = resource_files()
    staging = ROOT / "build/bundle-resources"
    copy_licenses(staging / "licenses")
    bundled = [(ROOT / p, p) for p in resources]
    bundled += [(ROOT / name, Path(name)) for name in ("QUICKSTART.txt", "THIRD_PARTY_NOTICES.md")]
    bundled += [(p, Path("licenses") / p.name) for p in sorted((staging / "licenses").glob("*.txt"))]
    inventory = {relative.as_posix(): hashlib.sha256(source.read_bytes()).hexdigest()
                 for source, relative in bundled}
    inventory_file = staging / "RESOURCE_FILES.sha256.json"
    inventory_file.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bundled.append((inventory_file, Path(inventory_file.name)))
    excludes = ["numpy", "imageio_ffmpeg", "matplotlib", "scipy", "IPython", "pytest", "pygame.tests", "PIL.ImageQt"]
    icon = None
    if args.icon:
        icon = args.icon.resolve(strict=True)
        if icon.suffix.lower() != ".ico":
            parser.error("Use a Windows .ico file")
        icon = str(icon)
    # Put the allowlist in a spec file: hundreds of --add-data switches exceed
    # Windows' command-line length limit.
    spec = ROOT / "build/specs/naiwa.spec"
    spec.parent.mkdir(parents=True, exist_ok=True)
    version_file = spec.parent / "version_info.txt"
    version = tuple(map(int, VERSION.split("."))) + (0,)
    fields = {"FileDescription": f"{APP_NAME}桌宠", "ProductName": APP_NAME,
              "FileVersion": VERSION, "ProductVersion": VERSION,
              "InternalName": APP_NAME, "OriginalFilename": f"{APP_NAME}.exe"}
    strings = ", ".join(f"StringStruct({key!r}, {value!r})" for key, value in fields.items())
    version_file.write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}), "
        f"kids=[StringFileInfo([StringTable('080404b0', [{strings}])]), "
        "VarFileInfo([VarStruct('Translation', [2052, 1200])])])", encoding="utf-8")
    datas = [(str(source), relative.parent.as_posix()) for source, relative in bundled]
    spec.write_text(
        f"a = Analysis([{str(ROOT / 'pet.pyw')!r}], pathex=[{str(ROOT)!r}], "
        f"binaries=[], datas={datas!r}, hiddenimports=[], hookspath=[], "
        f"hooksconfig={{}}, runtime_hooks=[], excludes={excludes!r}, noarchive=False)\n"
        "pyz = PYZ(a.pure)\n"
        f"exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name={APP_NAME!r}, "
        "debug=False, bootloader_ignore_signals=False, strip=False, upx=False, "
        f"console=False, icon={icon!r}, version={str(version_file)!r})\n", encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
               "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/pyinstaller"), str(spec)]
    subprocess.run(command, cwd=ROOT, check=True)
    releases = ROOT / "releases"
    releases.mkdir(exist_ok=True)
    executable = releases / f"{APP_NAME}.exe"
    shutil.copy2(ROOT / f"dist/{APP_NAME}.exe", executable)
    checksum = releases / "SHA256SUMS.txt"
    checksum.write_text(f"{hashlib.sha256(executable.read_bytes()).hexdigest()}  {executable.name}\n", encoding="utf-8")
    print(f"Built v{VERSION}: {executable} ({executable.stat().st_size / 1024**2:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
