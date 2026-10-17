"""Frozen resources, persistent settings, and source-mode path behavior."""
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pet_app


class PathTests(unittest.TestCase):
    def frozen_paths(self, resources, local, override=None):
        environment = {"LOCALAPPDATA": str(local)}
        if override is not None:
            environment["NAIWA_DATA_DIR"] = str(override)
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "_MEIPASS", str(resources), create=True), \
             patch.dict(os.environ, environment, clear=True):
            return runpy.run_path(str(ROOT / "pet_paths.py"))

    def test_resources_change_but_user_state_and_instance_identity_stay_stable(self):
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp) / "用户目录"
            first = self.frozen_paths(Path(temp) / "_MEI111", local)
            second = self.frozen_paths(Path(temp) / "_MEI222", local)
            self.assertNotEqual(first["ROOT"], second["ROOT"])
            self.assertEqual(first["DATA_ROOT"], second["DATA_ROOT"])
            self.assertEqual(first["DATA_ROOT"], local / "naiwa")
            self.assertFalse(first["DATA_ROOT"].exists())

    def test_isolated_data_directory_override(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            paths = self.frozen_paths(Path(temp) / "bundle", Path(temp) / "local", state)
            self.assertEqual(paths["DATA_ROOT"], state)

    def test_source_resources_and_settings_remain_in_project(self):
        with patch.object(sys, "frozen", False, create=True):
            paths = runpy.run_path(str(ROOT / "pet_paths.py"))
        self.assertEqual(paths["ROOT"], ROOT)
        self.assertEqual(paths["DATA_ROOT"], ROOT)

    def test_first_run_copies_packaged_defaults_then_keeps_user_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "bundle"
            bundle.mkdir()
            defaults = bundle / "pet_settings.json"
            defaults.write_text(json.dumps({"displayScale": 0.6}), encoding="utf-8")
            settings = Path(temp) / "user/naiwa/pet_settings.json"
            with patch.object(pet_app, "ROOT", bundle), patch.object(pet_app, "SETTINGS", settings):
                self.assertEqual(pet_app.load_settings()["displayScale"], 0.6)
                settings.write_text(json.dumps({"displayScale": 1.2}), encoding="utf-8")
                self.assertEqual(pet_app.load_settings()["displayScale"], 1.2)
            self.assertEqual(json.loads(defaults.read_text(encoding="utf-8"))["displayScale"], 0.6)


if __name__ == "__main__":
    unittest.main()
