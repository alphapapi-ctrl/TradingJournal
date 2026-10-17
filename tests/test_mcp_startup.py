"""Startup tests use isolated config/databases and remove their temporary task."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from utils import mcp_config as config, mcp_control as control

ROOT = Path(__file__).resolve().parents[1]


class StartupConfigCase(unittest.TestCase):
    def test_roundtrip_and_aliases(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as folder:
            target = Path(folder) / "settings.json"
            original = config.normalise({"connection_host": "MT5Tools", "allowed_hosts": ["mt5tools", "192.168.2.234"]})
            config.save_config(original, target)
            self.assertEqual(config.load_config(target), original)
            self.assertEqual(original["allowed_hosts"], ["mt5tools", "192.168.2.234"])
            self.assertEqual(config.endpoint(original), "http://mt5tools:8765/mcp")
            with self.assertRaises(ValueError):
                config.save_config({**original, "port": 0}, target)
            self.assertEqual(config.load_config(target), original)
            target.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                config.load_config(target)

    def test_reject_invalid_settings(self):
        for value in ({"port": True}, {"port": 65536}, {"port": "8765"}, {"bind_address": "all"},
                      {"connection_host": "http://mt5tools"}, {"allowed_hosts": ["*"]}, {"allowed_hosts": "mt5tools"},
                      {"task_name": "bad/task"}, {"database": ["bad"]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.normalise(value)
        self.assertEqual(config.endpoint(config.normalise({"connection_host": "::1"})), "http://[::1]:8765/mcp")

    def test_task_failures_are_reported_and_arguments_are_separate(self):
        settings = config.normalise({"connection_host": "mt5tools", "database": "data/a folder/test.db"})
        args = control.task_command("Install", settings, start_now=True)
        self.assertEqual(args[args.index("-Database") + 1], settings["database"])
        self.assertIn("-StartNow", args)
        with patch.object(control, "manage_task", side_effect=RuntimeError("Access denied")):
            status = control.task_status(settings)
            self.assertIsNone(status["installed"])
            self.assertIn("Access denied", status["error"])
        with self.assertRaises(ValueError):
            control.task_command("Delete", settings)

    def test_settings_ui_shows_mismatch_and_does_not_install_on_view(self):
        from streamlit.testing.v1 import AppTest
        settings = config.normalise({"connection_host": "mt5tools"})
        status = ({"running": True, "alias_accepted": False, "server": {"hostname": "test-host", "pid": 123,
                   "database": str(ROOT / "data" / "different-test.db")}}, {"installed": False, "state": "Not installed"})
        with patch("utils.mcp_settings_ui.load_config", return_value=settings), patch("utils.mcp_settings_ui._status", return_value=status), \
             patch("utils.mcp_settings_ui._requirements", return_value={"windows": True, "python": True, "script": True, "dependencies": True}), \
             patch.object(control, "manage_task") as mutate:
            app = AppTest.from_string("from utils.mcp_settings_ui import show\nshow()").run(timeout=15)
            self.assertFalse(app.exception, str(app.exception))
            self.assertTrue(any("different database" in error.value for error in app.error))
            self.assertTrue(any("rejects the connection hostname" in warn.value for warn in app.warning))
            self.assertTrue(any("http://mt5tools:8765/mcp" in code.value for code in app.code))
            mutate.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows Task Scheduler integration")
class WindowsTaskCase(unittest.TestCase):
    def command(self, action, settings, target, *extra):
        return subprocess.run(control.task_command(action, settings, target) + list(extra), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=40, cwd=ROOT)

    def test_installer_preview_has_no_side_effects(self):
        with tempfile.TemporaryDirectory(prefix="mcp preview ", dir=ROOT / "data") as folder:
            target = Path(folder) / "config.json"
            settings = config.normalise({"connection_host": "mt5tools", "database": str(Path(folder) / "test.db")})
            result = self.command("Install", settings, target, "-Preview")
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["logon_type"], "Interactive")
            self.assertEqual(plan["run_level"], "Limited")
            self.assertEqual(plan["execution_time_limit"], "PT0S")
            self.assertEqual(plan["multiple_instances"], "IgnoreNew")
            self.assertIn(str(target), plan["arguments"])
            self.assertEqual(plan["config"]["connection_host"], "mt5tools")
            self.assertFalse(target.exists())
            self.assertFalse(Path(settings["database"]).exists())

    def test_task_install_start_duplicate_stop_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="mcp task ", dir=ROOT / "data") as folder:
            target = Path(folder) / "config.json"
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            name = "Journal MCP test " + uuid4().hex
            settings = config.normalise({"connection_host": "mt5tools", "database": str(Path(folder) / "journal.db"),
                                         "task_name": name, "port": port})
            try:
                result = self.command("Install", settings, target)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)["installed"])
                self.assertFalse(Path(settings["database"]).exists(), "Installing must not create journal records")
                self.assertEqual(config.load_config(target)["allowed_hosts"], ["mt5tools"])
                start = self.command("Start", settings, target)
                self.assertEqual(start.returncode, 0, start.stderr)
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    health = control.probe_health(settings)
                    if health["running"]:
                        break
                    time.sleep(0.25)
                self.assertTrue(health["running"], health)
                self.assertTrue(health["alias_accepted"], health)
                self.assertEqual(Path(health["server"]["database"]), Path(settings["database"]))
                duplicate = self.command("Start", settings, target)
                self.assertEqual(duplicate.returncode, 0, duplicate.stderr)
                time.sleep(0.5)
                self.assertEqual(control.probe_health(settings)["server"]["pid"], health["server"]["pid"])
                refused_update = self.command("Install", settings, target)
                self.assertNotEqual(refused_update.returncode, 0)
                self.assertIn("Stop the login task", refused_update.stderr)
                stopped = self.command("Stop", settings, target)
                self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
                self.assertNotEqual(json.loads(stopped.stdout)["state"], "Running")
                deadline = time.monotonic() + 5
                while control.probe_health(settings)["running"] and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertFalse(control.probe_health(settings)["running"])
            finally:
                # Always clean up this unique test task, including after assertions fail.
                self.command("Stop", settings, target)
                cleanup = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Unregister-ScheduledTask -TaskName '" + name + "' -Confirm:$false -ErrorAction SilentlyContinue"],
                    capture_output=True, text=True, timeout=15)
                if cleanup.returncode and target.exists():
                    self.fail(cleanup.stderr)


if __name__ == "__main__":
    unittest.main()
