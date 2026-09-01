import json
from pathlib import Path
from typing import Any, Dict


ROOT_DIR = Path(__file__).resolve().parent.parent
NETWORK_CONFIG_PATH = ROOT_DIR / "data" / "network.json"
STREAMLIT_CONFIG_PATH = ROOT_DIR / ".streamlit" / "config.toml"


def _default_config() -> Dict[str, Any]:
    return {
        "server_address": "127.0.0.1",
        "server_port": 8503,
        "server_headless": True,
        "open_browser": True,
    }


def load_network_config() -> Dict[str, Any]:
    if not NETWORK_CONFIG_PATH.exists():
        config = _default_config()
        save_network_config(config)
        return config
    try:
        with NETWORK_CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return _default_config()

    defaults = _default_config()
    merged = dict(defaults)
    merged.update(raw or {})
    merged["server_port"] = _coerce_port(merged.get("server_port"))
    merged["server_headless"] = bool(str(merged.get("server_headless", True)).lower() in ("1", "true", "yes", "on"))
    merged["open_browser"] = bool(str(merged.get("open_browser", True)).lower() in ("1", "true", "yes", "on"))
    return merged


def save_network_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(_default_config())
    normalized.update(config)
    normalized["server_port"] = _coerce_port(normalized.get("server_port"))
    normalized["server_headless"] = bool(
        str(normalized.get("server_headless", True)).lower() in ("1", "true", "yes", "on")
    )
    normalized["open_browser"] = bool(
        str(normalized.get("open_browser", True)).lower() in ("1", "true", "yes", "on")
    )
    NETWORK_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NETWORK_CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2)
    _sync_to_streamlit_config(normalized)
    return normalized


def streamlit_launch_command(config: Dict[str, Any] | None = None) -> str:
    cfg = config or load_network_config()
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
    in_server = False
    in_other_section = False
    replaced_server = False
    for line in parts:
        if line.strip() == "[server]":
            if not replaced_server:
                out.extend([
                    "[server]",
                    f'address = "{host}"',
                    f"port = {port}",
                    f'headless = {headless}',
                ])
            replaced_server = True
            in_server = True
            continue
        if in_server:
            if line.startswith("[") and line.endswith("]"):
                in_server = False
                in_other_section = True
                out.append(line)
            else:
                continue
        else:
            out.append(line)

    if not replaced_server:
        out.append("")
        out.extend([
            "[server]",
            'headless = true',
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
