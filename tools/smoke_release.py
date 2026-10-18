"""Run the standalone EXE alone, with isolated user state and no Python on PATH."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pet_runtime import signal_instance


def wait_for(predicate, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for the packaged app")


def embedded_files(executable):
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    names = {name.replace("\\", "/"): name for name in archive.toc}
    inventory = json.loads(archive.extract(names["RESOURCE_FILES.sha256.json"]))
    for name, digest in inventory.items():
        assert hashlib.sha256(archive.extract(names[name])).hexdigest() == digest, name
    assert len([name for name in names if name.startswith("assets/laugh/") and name.endswith(".webp")]) == 432
    assert not any("dance1/" in name or "dance2/" in name or "ffmpeg" in name.lower()
                   or name.lower().endswith((".mp4", ".m4a")) for name in names)
    assert any("_chipmunk" in name and name.endswith(".pyd") for name in names)
    assert any("_cffi_backend" in name and name.endswith(".pyd") for name in names)
    assert "pet_physics" in archive.open_embedded_archive("PYZ.pyz").toc
    # Verify that Explorer's compiled icon really matches the supplied ICO.
    import pefile
    import struct
    data = (ROOT / "assets/app.ico").read_bytes()
    count = struct.unpack_from("<H", data, 4)[0]
    expected = set()
    for index in range(count):
        size, offset = struct.unpack_from("<II", data, 6 + index * 16 + 8)
        expected.add(hashlib.sha256(data[offset:offset + size]).digest())
    with pefile.PE(str(executable)) as pe:
        properties = {key: value for tables in pe.FileInfo for table in tables
                      if hasattr(table, "StringTable") for strings in table.StringTable
                      for key, value in strings.entries.items()}
        assert properties[b"ProductName"].decode("utf-8") == "奶小蛙"
        assert properties[b"OriginalFilename"].decode("utf-8") == "奶小蛙.exe"
        actual = set()
        for resource in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if resource.id == pefile.RESOURCE_TYPE["RT_ICON"]:
                for icon in resource.directory.entries:
                    for language in icon.directory.entries:
                        entry = language.data.struct
                        actual.add(hashlib.sha256(pe.get_data(entry.OffsetToData, entry.Size)).digest())
        assert expected <= actual, "Compiled EXE icon does not match assets/app.ico"
    return len(inventory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    count = embedded_files(args.executable)
    with tempfile.TemporaryDirectory(prefix="naiwa-单文件验证-") as directory:
        target = Path(directory)
        download = target / "下载"
        moved = target / "移动后"
        temporary = target / "临时解包"
        for folder in (download, moved, temporary):
            folder.mkdir()
        executable = download / "奶小蛙.exe"
        renamed = moved / "移动后的奶小蛙.exe"
        shutil.copy2(args.executable, executable)
        shutil.copy2(args.executable, renamed)
        state = target / "用户数据/naiwa"
        environment = dict(os.environ)
        for variable in ("PYTHONHOME", "PYTHONPATH", "NAIWA_DATA_DIR"):
            environment.pop(variable, None)
        environment.update(PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                           SDL_AUDIODRIVER="dummy", LOCALAPPDATA=str(state.parent),
                           TEMP=str(temporary), TMP=str(temporary))
        log = state / "_preview/pet.log"
        geometry = state / "_preview/pet_geometry.json"
        metadata = state / ".cache/pet-instance.json"
        settings = state / "pet_settings.json"
        process = subprocess.Popen([str(executable)], cwd=target, env=environment)
        try:
            wait_for(lambda: log.exists() and "窗口已显示" in log.read_text(encoding="utf-8"))
            assert process.poll() is None
            identity = json.loads(metadata.read_text(encoding="utf-8"))
            assert Path(identity["root"]) == state
            worker = psutil.Process(identity["pid"])
            assert Path(worker.exe()).resolve() == executable.resolve()
            assert worker.pid == process.pid or worker.ppid() == process.pid
            displayed = json.loads(geometry.read_text(encoding="utf-8"))
            assert displayed["w"] > 0 and displayed["h"] > 0
            for entry in (executable, renamed):
                duplicate = subprocess.run([str(entry)], cwd=target, env=environment, timeout=25)
                assert duplicate.returncode == 0
                assert log.read_text(encoding="utf-8").count("窗口已显示") == 1
            assert signal_instance("laugh", state)
            wait_for(lambda: "播放 捧腹大笑" in log.read_text(encoding="utf-8"))
            assert process.poll() is None
            text = log.read_text(encoding="utf-8")
            assert not any(word in text for word in ("Traceback", "Cannot preload", "Cannot play", "Audio device unavailable")), text
            assert signal_instance("quit", state)
            assert process.wait(timeout=15) == 0
            assert not metadata.exists()
            preferences = json.loads(settings.read_text(encoding="utf-8"))
            preferences["displayScale"] = 0.8
            settings.write_text(json.dumps(preferences), encoding="utf-8")
            process = subprocess.Popen([str(renamed)], cwd=target, env=environment)
            wait_for(lambda: log.read_text(encoding="utf-8").count("窗口已显示") == 2)
            assert json.loads(geometry.read_text(encoding="utf-8"))["scale"] == 0.8
            assert signal_instance("quit", state)
            assert process.wait(timeout=15) == 0
            assert sorted(p.name for p in download.iterdir()) == ["奶小蛙.exe"]
            assert sorted(p.name for p in moved.iterdir()) == ["移动后的奶小蛙.exe"]
            wait_for(lambda: not list(temporary.glob("_MEI*")))
            print(f"PASS: {count} embedded hashes, custom Windows icon, 432 frames, EXE alone, "
                  "Tk rendering, native physics engine, audio, "
                  "single instance across extraction/rename, persistent size after moving, clean shutdown")
        except Exception:
            if log.exists():
                print(log.read_text(encoding="utf-8"), flush=True)
            raise
        finally:
            if process.poll() is None:
                signal_instance("quit", state)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    for child in psutil.Process(process.pid).children(recursive=True):
                        child.terminate()
                    process.terminate()
                    process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
