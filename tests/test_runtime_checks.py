"""Startup permission checks must preserve databases and persisted settings."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from utils import runtime_checks

ROOT = Path(__file__).resolve().parents[1]


class StartupDataSafetyCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="startup safety ", dir=ROOT / "data")
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.database = self.data / "journal.db"
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO accounts VALUES (1, 'Keep this account')")
            conn.commit()
        (self.root / "app.py").touch()
        python = self.root / ".venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.touch()
        self.network = self.data / "network.json"
        self.network.write_text(json.dumps({"server_port": 8508, "server_address": "0.0.0.0"}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_repeated_full_preflight_preserves_database_and_network_settings(self):
        originals = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.database, self.network)}
        # The previous fixed-name probe must also be left alone if it exists.
        sentinel = self.data / "._startup_check"
        sentinel.write_text("Existing user file", encoding="utf-8")
        originals[sentinel] = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns)
        original_files = set(self.data.iterdir())
        with patch.object(runtime_checks, "__file__", str(self.root / "utils" / "runtime_checks.py")), \
             patch.object(runtime_checks, "DB_PATH", self.database):
            for _ in range(3):
                self.assertEqual(runtime_checks.collect_startup_issues(), [])
        for path, (content, modified) in originals.items():
            self.assertTrue(path.exists(), f"Startup deleted {path.name}")
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mtime_ns, modified)
        self.assertEqual(set(self.data.iterdir()), original_files)
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT name FROM accounts").fetchall(), [("Keep this account",)])

    def test_missing_file_probe_does_not_create_a_database_or_settings_file(self):
        missing = self.data / "missing.db"
        before = set(self.data.iterdir())
        self.assertTrue(runtime_checks._is_writable_file(missing))
        self.assertFalse(missing.exists())
        self.assertEqual(set(self.data.iterdir()), before)

    def test_inaccessible_file_is_reported_without_modification(self):
        original = self.database.read_bytes()
        with patch.object(Path, "open", side_effect=PermissionError("Access denied")):
            self.assertFalse(runtime_checks._is_writable_file(self.database))
        self.assertEqual(self.database.read_bytes(), original)

    def test_real_app_startup_keeps_existing_accounts_on_rerun(self):
        from streamlit.testing.v1 import AppTest
        import database

        target = self.data / "app-startup.db"
        original_network = self.network.read_bytes()
        with patch.object(database, "DB_PATH", target), \
             patch.object(runtime_checks, "DB_PATH", target), \
             patch.object(runtime_checks, "__file__", str(self.root / "utils" / "runtime_checks.py")), \
             patch("utils.ftp_sync.start_sync_thread"):
            database.init_db()
            account_id = database.execute("INSERT INTO accounts(name,broker) VALUES (?,?)", ("Startup survivor", "Test"))
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
            app.run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(database.fetch_one("SELECT name FROM accounts WHERE id=?", (account_id,))["name"], "Startup survivor")
        self.assertEqual(self.network.read_bytes(), original_network)


if __name__ == "__main__":
    unittest.main()
