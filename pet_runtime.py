"""Windows instance ownership, control events, and global hotkeys."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import queue
import threading

import psutil

from pet_paths import DATA_ROOT as ROOT
ACTIONS = ("show", "quit", "laugh")


def _name(root, suffix):
    identity = hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:24]
    return "Local\\DesktopPet_" + identity + "_" + suffix


def _kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateEventW.restype = wintypes.HANDLE
    api.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    api.OpenEventW.restype = wintypes.HANDLE
    api.SetEvent.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    return api


def project_processes(root=ROOT):
    root = Path(root).resolve()
    entries = {str(root / "pet_app.py").casefold(), str(root / "pet.pyw").casefold()}
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if not (process.info["name"] or "").lower().startswith("python"):
                continue
            for arg in process.cmdline()[1:]:
                if not arg.lower().endswith((".py", ".pyw")):
                    continue
                entry = Path(arg)
                if not entry.is_absolute():
                    entry = Path(process.cwd()) / entry
                if str(entry.resolve()).casefold() in entries:
                    yield process
                    break
        except (OSError, ValueError, psutil.Error):
            continue


def signal_instance(action, root=ROOT):
    if action not in ACTIONS:
        raise ValueError(action)
    api = _kernel()
    handle = api.OpenEventW(0x0002, False, _name(root, action))
    if not handle:
        return False
    try:
        return bool(api.SetEvent(handle))
    finally:
        api.CloseHandle(handle)


def stop_project(root=ROOT, timeout=4.0):
    # Capture and validate process identity before signalling or terminating it.
    processes = list(project_processes(root))
    signal_instance("quit", root)
    _, alive = psutil.wait_procs(processes, timeout=timeout)
    valid = {p.pid: p.create_time() for p in project_processes(root)}
    for process in alive:
        try:
            if valid.get(process.pid) != process.create_time():
                continue
            children = process.children(recursive=True)
            process.terminate()
            for child in children:
                try:
                    child.terminate()
                except psutil.Error:
                    pass
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(alive, timeout=2)
    return not alive


class GlobalHotkeys:
    def __init__(self, keys=(ord("1"), ord("Q"), ord("C"), ord("V"))):
        self.keys = keys
        self.messages = queue.SimpleQueue()
        self.thread_id = None
        self.ready = threading.Event()
        self.registered = []
        self.thread = threading.Thread(target=self._run, daemon=True, name="pet-hotkeys")
        self.thread.start()
        self.ready.wait(2)

    def _run(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = _kernel()
        self.thread_id = kernel.GetCurrentThreadId()
        message = wintypes.MSG()
        user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        actions = {1: "laugh", 2: "quit", 3: "copy", 4: "paste"}
        try:
            for key_id, key in enumerate(self.keys, 1):
                if user.RegisterHotKey(None, key_id, 0x0001 | 0x0002 | 0x4000, key):
                    self.registered.append(key_id)
                else:
                    from pet_app import log
                    label = chr(key) if 32 <= key < 127 else f"VK{key:02X}"
                    log(f"Global shortcut Ctrl+Alt+{label} is already in use")
            self.ready.set()
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312 and message.wParam in actions:
                    self.messages.put(actions[message.wParam])
        finally:
            for key_id in self.registered:
                user.UnregisterHotKey(None, key_id)

    def close(self):
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=1)


class InstanceGuard:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.api = _kernel()
        self.handle = None
        self.events = {}
        self.hotkeys = None
        self.metadata = self.root / ".cache" / "pet-instance.json"

    def acquire(self):
        ctypes.set_last_error(0)
        self.handle = self.api.CreateMutexW(None, False, _name(self.root, "instance"))
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            self.api.CloseHandle(self.handle)
            self.handle = None
            signal_instance("show", self.root)
            return False
        try:
            for action in ACTIONS:
                event = self.api.CreateEventW(None, False, False, _name(self.root, action))
                if not event:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.events[action] = event
            self.metadata.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.metadata.with_suffix(".tmp")
            temporary.write_text(json.dumps({"pid": os.getpid(), "created": psutil.Process().create_time(),
                                             "root": str(self.root.resolve())}), encoding="utf-8")
            temporary.replace(self.metadata)
            return True
        except Exception:
            self.close()
            raise

    def start_hotkeys(self):
        self.hotkeys = GlobalHotkeys()

    def pending(self):
        actions = [action for action, event in self.events.items()
                   if self.api.WaitForSingleObject(event, 0) == 0]
        if self.hotkeys:
            while not self.hotkeys.messages.empty():
                actions.append(self.hotkeys.messages.get())
        return actions

    def close(self):
        if self.hotkeys:
            self.hotkeys.close()
            self.hotkeys = None
        for event in self.events.values():
            self.api.CloseHandle(event)
        self.events.clear()
        if self.handle:
            try:
                data = json.loads(self.metadata.read_text(encoding="utf-8"))
                if data.get("pid") == os.getpid():
                    self.metadata.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            self.api.CloseHandle(self.handle)
            self.handle = None
