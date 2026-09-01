"""Startup preflight checks to catch cross-machine deployment issues early."""

from __future__ import annotations

from pathlib import Path

from database import DB_PATH


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / "._startup_check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _is_writable_file(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
        path.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def collect_startup_issues() -> list[str]:
    root = Path(__file__).resolve().parent.parent
    issues: list[str] = []

    app_py = root / "app.py"
    if not app_py.exists():
        issues.append("App entrypoint missing: app.py")

    data_dir = root / "data"
    if not data_dir.exists():
        issues.append("Data folder missing under project root.")
    elif not _is_writable_dir(data_dir):
        issues.append("Data folder is not writable (check permissions or Windows profile path).")

    config_dir = root / "data" / "network.json"
    if config_dir.parent.exists() and not _is_writable_file(config_dir):
        issues.append("Cannot write data/network.json. Network settings may not save.")

    python_exe = root / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        issues.append("Python virtual env not found at .venv\\Scripts\\python.exe.")

    if not DB_PATH.parent.exists():
        issues.append("Database folder is missing.")
    elif not _is_writable_file(DB_PATH):
        issues.append("Database file path is not writable.")

    return issues
