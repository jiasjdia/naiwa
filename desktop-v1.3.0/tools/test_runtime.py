"""Instance ownership and control events, isolated from the user's running pet."""
from pathlib import Path
import ctypes
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pet_runtime import GlobalHotkeys, InstanceGuard, project_processes, signal_instance


class RuntimeTests(unittest.TestCase):
    def test_global_hotkeys_dispatch_and_release(self):
        # F21-F24 keep this check independent of an already-running pet.
        hotkeys = GlobalHotkeys(keys=(0x84, 0x85, 0x86, 0x87))
        try:
            self.assertEqual(hotkeys.registered, [1, 2, 3, 4])
            self.assertTrue(ctypes.windll.user32.PostThreadMessageW(hotkeys.thread_id, 0x0312, 1, 0))
            deadline = time.monotonic() + 1
            while hotkeys.messages.empty() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(hotkeys.messages.get_nowait(), "laugh")
        finally:
            hotkeys.close()
        self.assertFalse(hotkeys.thread.is_alive())

    def test_only_explicit_project_entrypoints_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            own = Mock(info={"name": "pythonw.exe", "cmdline": ["pythonw.exe", str(root / "pet.pyw")]})
            other = Mock(info={"name": "python.exe", "cmdline": ["python.exe", str(root / "unrelated.py")]})
            foreign = Mock(info={"name": "pythonw.exe", "cmdline": ["pythonw.exe", str(ROOT / "pet.pyw")]})
            relative = Mock(info={"name": "pythonw.exe", "cmdline": ["pythonw.exe", "pet.pyw"]})
            relative.cwd.return_value = str(ROOT)
            for process in (own, other, foreign, relative):
                process.cmdline.return_value = process.info["cmdline"]
            with patch("pet_runtime.psutil.process_iter", return_value=[own, other, foreign, relative]):
                self.assertEqual(list(project_processes(root)), [own])

    def test_copy_and_paste_hotkeys_dispatch(self):
        hotkeys = GlobalHotkeys(keys=tuple(range(0x7C, 0x80)))  # Ctrl+Alt+F13..F16
        try:
            self.assertEqual(hotkeys.registered, [1, 2, 3, 4])
            for key_id, action in ((3, "copy"), (4, "paste")):
                self.assertTrue(ctypes.windll.user32.PostThreadMessageW(hotkeys.thread_id, 0x0312, key_id, 0))
                deadline = time.monotonic() + 1
                while hotkeys.messages.empty() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(hotkeys.messages.get_nowait(), action)
        finally:
            hotkeys.close()

    def test_single_instance_control_and_reacquire(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = InstanceGuard(root)
            second = InstanceGuard(root)
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                self.assertIn("show", first.pending())
                self.assertTrue(signal_instance("laugh", root))
                self.assertIn("laugh", first.pending())
                with self.assertRaises(ValueError):
                    signal_instance("dance1", root)
                with self.assertRaises(ValueError):
                    signal_instance("dance2", root)
                self.assertEqual(first.pending(), [])
                self.assertTrue(signal_instance("quit", root))
                self.assertIn("quit", first.pending())
                self.assertEqual(list(project_processes(root)), [])
                first.close()
                self.assertFalse(first.metadata.exists())
                self.assertTrue(second.acquire())
            finally:
                first.close()
                second.close()


if __name__ == "__main__":
    unittest.main()
