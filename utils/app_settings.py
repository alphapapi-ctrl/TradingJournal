"""Centralized app_settings helpers and typed runtime settings containers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from database import execute, fetch_all


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _coerce_port(value: Any, default: int = 8503) -> int:
    try:
        port = int(value)
        if 1 <= port <= 65535:
            return port
    except (TypeError, ValueError):
        pass
    return default


def get_setting(key: str, default: Any = None) -> Any:
    rows = fetch_all("SELECT value FROM app_settings WHERE key=?", (key,))
    return rows[0]["value"] if rows else default


def set_setting(key: str, value: Any) -> None:
    existing = fetch_all("SELECT key FROM app_settings WHERE key=?", (key,))
    if existing:
        execute("UPDATE app_settings SET value=?, updated_at=datetime('now') WHERE key=?", (str(value), key))
    else:
        execute("INSERT INTO app_settings (key, value) VALUES (?,?)", (key, str(value)))


def set_many_settings(values: dict[str, Any]) -> None:
    for k, v in values.items():
        set_setting(k, v)


def get_settings_dict() -> dict[str, str]:
    rows = fetch_all("SELECT key, value FROM app_settings")
    return {r["key"]: r["value"] for r in rows} if rows else {}


@dataclass(frozen=True)
class NetworkSettings:
    server_address: str = "127.0.0.1"
    server_port: int = 8503
    server_headless: bool = True
    open_browser: bool = True

    @classmethod
    def from_raw(cls, value: dict[str, Any] | None = None) -> "NetworkSettings":
        src = value or {}
        return cls(
            server_address=str(src.get("server_address", "127.0.0.1")).strip() or "127.0.0.1",
            server_port=_coerce_port(src.get("server_port"), 8503),
            server_headless=_coerce_bool(src.get("server_headless"), True),
            open_browser=_coerce_bool(src.get("open_browser"), True),
        )

    @classmethod
    def load(cls) -> "NetworkSettings":
        return cls(
            server_address=str(get_setting("server_address", "127.0.0.1")).strip() or "127.0.0.1",
            server_port=_coerce_port(get_setting("server_port"), 8503),
            server_headless=_coerce_bool(get_setting("server_headless"), True),
            open_browser=_coerce_bool(get_setting("open_browser"), True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_address": self.server_address,
            "server_port": self.server_port,
            "server_headless": self.server_headless,
            "open_browser": self.open_browser,
        }


def load_network_settings() -> NetworkSettings:
    return NetworkSettings.load()


def save_network_settings(settings: NetworkSettings | dict[str, Any]) -> NetworkSettings:
    cfg = settings if isinstance(settings, NetworkSettings) else NetworkSettings.from_raw(settings)
    set_many_settings(cfg.to_dict())
    return cfg


def normalize_network_dict(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    return NetworkSettings.from_raw(settings).to_dict()


def settings_file_hint() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "network.json"
