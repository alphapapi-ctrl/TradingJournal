import json
from pathlib import Path
from typing import Any, Dict

from utils.app_settings import NetworkSettings, load_network_settings, normalize_network_dict, save_network_settings

ROOT_DIR = Path(__file__).resolve().parent.parent
NETWORK_CONFIG_PATH = ROOT_DIR / "data" / "network.json"
STREAMLIT_CONFIG_PATH = ROOT_DIR / ".streamlit" / "config.toml"


def _default_config() -> Dict[str, Any]:
    return NetworkSettings().to_dict()


def load_network_config() -> Dict[str, Any]:
    if not NETWORK_CONFIG_PATH.exists():
        config = _default_config()
        save_network_config(config)
        return config
    try:
        with NETWORK_CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    merged = _default_config()
    merged.update(raw or {})
    return normalize_network_dict(merged)


def save_network_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_network_dict(config)
    NETWORK_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NETWORK_CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2)
    save_network_settings(normalized)
    _sync_to_streamlit_config(normalized)
    return normalized


def streamlit_launch_command(config: Dict[str, Any] | None = None) -> str:
    cfg = normalize_network_dict(load_network_settings().to_dict() if config is None else config)
    host = str(cfg.get("server_address", "127.0.0.1")).strip() or "127.0.0.1"
    port = _coerce_port(cfg.get("server_port"))
    headless = "true" if bool(cfg.get("server_headless", True)) else "false"
    return (
        f"streamlit run app.py "
        f"--server.address {host} "
        f"--server.port {port} "
        f"--server.headless {headless}"
    )


def _sync_to_streamlit_config(config: Dict[str, Any]) -> None:
    host = str(config.get("server_address", "127.0.0.1")).strip() or "127.0.0.1"
    port = _coerce_port(config.get("server_port"))
    headless = str(bool(config.get("server_headless", True))).lower()

    STREAMLIT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if STREAMLIT_CONFIG_PATH.exists():
        existing = STREAMLIT_CONFIG_PATH.read_text(encoding="utf-8")

    parts = existing.splitlines()
    out: list[str] = []
    wrote_server = False
    in_server = False

    for line in parts:
        if line.strip() == "[server]":
            out.append("[server]")
            out.append(f'address = "{host}"')
            out.append(f"port = {port}")
            out.append(f'headless = {headless}')
            wrote_server = True
            in_server = True
            continue
        if in_server:
            if line.startswith("[") and line.endswith("]"):
                out.append(line)
                in_server = False
            # Skip old server lines entirely.
            continue
        out.append(line)

    if not wrote_server:
        out.extend([
            "",
            "[server]",
            f'address = "{host}"',
            f"port = {port}",
            f'headless = {headless}',
        ])

    # If we replaced server then duplicate header could stay in `out`; clean duplicates.
    cleaned: list[str] = []
    seen_server = False
    for line in out:
        if line.strip() == "[server]":
            if seen_server:
                continue
            seen_server = True
        cleaned.append(line)

    with STREAMLIT_CONFIG_PATH.open("w", encoding="utf-8") as f:
        f.write("\n".join(cleaned).strip() + "\n")


def _coerce_port(value: Any) -> int:
    try:
        port = int(value)
        return port if 1 <= port <= 65535 else 8503
    except (TypeError, ValueError):
        return 8503
