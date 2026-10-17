"""Foreground supervisor for Task Scheduler; quiet logs and one MCP instance."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.mcp_config import CONFIG_PATH, load_config


def health(config: dict) -> dict | None:
    host = config["bind_address"]
    host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
    host = f"[{host}]" if ":" in host else host
    try:
        with build_opener(ProxyHandler({})).open(f"http://{host}:{config['port']}/health", timeout=2) as response:
            result = json.load(response)
            return result if result.get("service") == "trading-journal-mcp" else None
    except (OSError, URLError, ValueError):
        return None


def service_belongs_here(status: dict, config: dict, pid_file: Path) -> bool:
    if status.get("app_root") and status.get("database"):
        return (Path(status["app_root"]).resolve() == ROOT and
                Path(status["database"]).resolve() == Path(config["database"]).resolve())
    # Compatibility with the original launcher, whose health response lacks paths.
    try:
        return int(pid_file.read_text().strip()) == int(status["pid"])
    except (OSError, ValueError, KeyError):
        return False


def wait_for_pid(pid: int):
    if os.name != "nt":
        raise RuntimeError("Scheduled login startup is supported on Windows")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE; no polling loop.
    if not handle:
        raise OSError(ctypes.get_last_error(), "Cannot monitor the existing MCP process")
    try:
        kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
    finally:
        kernel.CloseHandle(handle)


def run(config_path: Path) -> int:
    config = load_config(config_path)
    log_dir = Path(config["database"]).parent / "mcp"
    log_dir.mkdir(parents=True, exist_ok=True)
    pid_file = log_dir / "server.pid"
    with (log_dir / "scheduled-task.log").open("a", encoding="utf-8") as log:
        try:
            status = health(config)
            if status:
                if not service_belongs_here(status, config, pid_file):
                    raise RuntimeError("Another journal instance occupies this port. Check the port/database in Settings > MCP.")
                log.write(f"Monitoring existing MCP process {status['pid']}.\n")
                log.flush()
                wait_for_pid(int(status["pid"]))
                return 1  # An unexpected stop activates Task Scheduler's restart policy.
            environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            with (log_dir / "server.stdout.log").open("a", encoding="utf-8") as output, (log_dir / "server.stderr.log").open("a", encoding="utf-8") as errors:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "mcp_server.py"), "--config", str(config_path)],
                    cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                    close_fds=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
                )
                for _ in range(100):
                    if process.poll() is not None:
                        return process.returncode or 1
                    status = health(config)
                    if status and service_belongs_here(status, config, pid_file):
                        pid_file.write_text(str(status["pid"]), encoding="utf-8")
                        log.write(f"Started MCP process {status['pid']}.\n")
                        log.flush()
                        break
                    time.sleep(0.2)
                else:
                    process.terminate()
                    process.wait(timeout=10)
                    raise RuntimeError("MCP did not become ready; see server.stderr.log")
                return process.wait() or 1
        except Exception as exc:
            log.write(f"Startup/monitor error: {exc}\n")
            log.flush()
            return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    raise SystemExit(run(args.config.resolve()))
