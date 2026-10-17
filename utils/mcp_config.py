"""Shared, file-backed MCP startup preferences (no database or UI imports)."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "mcp" / "settings.json"
DEFAULT_TASK_NAME = "Trading Journal MCP"


def hostname(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Supply a hostname or IP address")
    value = value.strip().lower()
    address = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    try:
        ipaddress.ip_address(address)
        return address
    except ValueError:
        if not re.fullmatch(r"[a-z0-9_](?:[a-z0-9_.-]{0,251}[a-z0-9_])?", value):
            raise ValueError("Use a hostname/IP only, without http://, a port, spaces or wildcards")
        return value


def normalise(value: dict | None = None) -> dict:
    value = value or {}
    bind = str(value.get("bind_address", "0.0.0.0")).strip()
    try:
        ipaddress.ip_address(bind)
    except ValueError as exc:
        raise ValueError("Listen address must be an IP address, e.g. 0.0.0.0 or 127.0.0.1") from exc
    port = value.get("port", 8765)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("MCP port must be an integer between 1 and 65535")
    hosts = value.get("allowed_hosts", [])
    if not isinstance(hosts, list) or len(hosts) > 30:
        raise ValueError("Supply up to 30 hostname aliases")
    task = value.get("task_name", DEFAULT_TASK_NAME)
    if not isinstance(task, str) or not task.strip() or len(task) > 150 or any(c in task for c in '\\/:*?"<>|\r\n'):
        raise ValueError("Invalid scheduled task name")
    connection_host = hostname(value.get("connection_host") or socket.gethostname())
    aliases = list(dict.fromkeys(hostname(h) for h in [connection_host, *hosts]))
    database = value.get("database") or str(ROOT / "data" / "journal.db")
    if not isinstance(database, str):
        raise ValueError("database must be a file path")
    database_path = Path(database).expanduser()
    if not database_path.is_absolute():
        database_path = ROOT / database_path
    return {"bind_address": bind, "port": port, "allowed_hosts": aliases,
            "connection_host": connection_host, "task_name": task.strip(),
            "database": str(database_path.resolve())}


def load_config(path: str | Path | None = None) -> dict:
    source = Path(path) if path else CONFIG_PATH
    if not source.exists():
        return normalise()
    try:
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read MCP startup settings at {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("MCP startup settings must contain a JSON object")
    return normalise(raw)


def save_config(value: dict, path: str | Path | None = None) -> dict:
    result = normalise(value)
    target = Path(path) if path else CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def endpoint(config: dict, health: bool = False) -> str:
    host = hostname(config["connection_host"])
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{config['port']}/{'health' if health else 'mcp'}"
