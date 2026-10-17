"""On-demand MCP health and Windows startup controls used by Settings."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from utils.mcp_config import CONFIG_PATH, ROOT, endpoint, normalise

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
SETUP_SCRIPT = ROOT / "scripts" / "setup_mcp_task.ps1"


def probe_health(config: dict) -> dict:
    """Probe this machine directly; also validate the client's chosen Host header."""
    host = config["bind_address"]
    host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
    host = f"[{host}]" if ":" in host else host
    url = f"http://{host}:{config['port']}/health"
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url, timeout=2) as response:
            status = json.load(response)
        if not isinstance(status, dict) or status.get("service") != "trading-journal-mcp":
            raise ValueError("This port is serving another application")
    except (OSError, ValueError) as exc:
        return {"running": False, "error": str(exc)}
    result = {"running": True, "server": status}
    try:
        client_host = endpoint(config).split("//", 1)[1].split("/", 1)[0]
        with opener.open(Request(url, headers={"Host": client_host}), timeout=2) as response:
            result["alias_accepted"] = json.load(response).get("service") == "trading-journal-mcp"
    except (OSError, ValueError) as exc:
        result.update(alias_accepted=False, alias_error=str(exc))
    return result


def _execute(arguments: list[str], timeout: int = 20) -> str:
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Command failed").strip()[-2500:])
    return result.stdout


def prerequisites() -> dict:
    result = {"windows": os.name == "nt", "python": PYTHON.is_file() and PYTHONW.is_file(),
              "script": SETUP_SCRIPT.is_file(), "dependencies": False}
    if result["python"]:
        try:
            _execute([str(PYTHON), "-c", "from mcp.server import MCPServer"], timeout=10)
            result["dependencies"] = True
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            result["error"] = str(exc)
    return result


def task_command(action: str, config: dict, config_path: Path = CONFIG_PATH, start_now: bool = False) -> list[str]:
    if action not in {"Install", "Status", "Start", "Stop"}:
        raise ValueError("Unknown startup action")
    config = normalise(config)
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SETUP_SCRIPT),
            "-Action", action, "-ConfigPath", str(config_path), "-TaskName", config["task_name"], "-Json"]
    if action == "Install":
        args += ["-ConnectionHost", config["connection_host"], "-AllowedHost", ",".join(config["allowed_hosts"]),
                 "-BindAddress", config["bind_address"], "-Port", str(config["port"]), "-Database", config["database"]]
        if start_now:
            args.append("-StartNow")
    return args


def manage_task(action: str, config: dict, start_now: bool = False) -> dict:
    if os.name != "nt":
        raise RuntimeError("Login tasks require Windows on the journal machine")
    return json.loads(_execute(task_command(action, config, start_now=start_now), timeout=30))


def task_status(config: dict) -> dict:
    try:
        return manage_task("Status", config)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"installed": None, "state": "Unable to check", "error": str(exc)}


def setup_command(config: dict) -> str:
    args = task_command("Install", config, start_now=True)
    # PowerShell single-quoted arguments protect spaces, $, and backticks.
    return "powershell " + " ".join(
        arg if arg.startswith("-") else "'" + arg.replace("'", "''") + "'" for arg in args[1:])
