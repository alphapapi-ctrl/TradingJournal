"""Shared lwchart config: candle colors + theme wiring (used by reference & replay)."""
import json

from utils.market_data import get_setting, save_setting
from utils.theme import get_theme, _PALETTES

DEFAULT_CANDLE_COLORS = {
    "up": "#00c896",
    "down": "#ff4b6e",
    "wick_up": "#00c896",
    "wick_down": "#ff4b6e",
    "border_up": "#00c896",
    "border_down": "#ff4b6e",
    "border_visible": True,
    "number_candles": True,
}


def get_candle_colors() -> dict:
    raw = get_setting("chart_candle_colors")
    colors = dict(DEFAULT_CANDLE_COLORS)
    if raw:
        try:
            colors.update(json.loads(raw))
        except (ValueError, TypeError):
            pass
    return colors


def save_candle_colors(colors: dict):
    save_setting("chart_candle_colors", json.dumps(colors))


def chart_colors() -> dict:
    """Candle colors + theme-derived chart chrome, ready for lwchart(colors=…)."""
    p = _PALETTES.get(get_theme(), _PALETTES["dark"])
    c = get_candle_colors()
    c.update({
        "bg": "transparent",
        "text": p["--text-muted"],
        "grid": p["--border"],
        "accent": p["--accent"],
        "toolbar_bg": p["--bg-card"],
        "number_color": p["--text-faint"],
    })
    return c
