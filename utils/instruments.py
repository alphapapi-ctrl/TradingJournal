"""
Instrument registry for the Reference/Replay modules.

Sessions and timezones are loaded from the School Run App's
strategy/instruments.py (a pure-data file — exec'd in an isolated namespace,
never imported, to avoid the repos' colliding package names). Instruments
missing from that registry fall back to a 24h UTC session.
"""
from functools import lru_cache
from pathlib import Path

FALLBACK = {
    "name": None,
    "timezone": "UTC",
    "session_open": "00:00",
    "session_close": "23:59",
    "currency": "",
}


def _sr_instruments_path() -> Path:
    from utils.market_data import get_sr_root
    return get_sr_root() / "strategy" / "instruments.py"


@lru_cache(maxsize=1)
def _load_registry() -> dict:
    path = _sr_instruments_path()
    try:
        ns: dict = {"__file__": str(path), "__name__": "sr_instruments"}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
        reg = ns.get("INSTRUMENTS", {})
        return reg if isinstance(reg, dict) else {}
    except Exception:
        return {}


def get_instrument(code: str) -> dict:
    """Session config for one instrument. Always returns a usable dict."""
    base = dict(FALLBACK)
    base["name"] = code
    reg = _load_registry()
    # exact match, else prefix match for suffixed caches like XAUUSD_ICM
    entry = reg.get(code)
    if entry is None:
        for k, v in reg.items():
            if code.startswith(k):
                entry = v
                break
    if entry:
        for key in ("name", "timezone", "session_open", "session_close", "currency"):
            if entry.get(key):
                base[key] = entry[key]
    return base


def session_times(code: str) -> tuple[str, tuple[int, int], tuple[int, int]]:
    """(timezone, (open_h, open_m), (close_h, close_m)) for an instrument."""
    ins = get_instrument(code)
    oh, om = (int(x) for x in ins["session_open"].split(":"))
    ch, cm = (int(x) for x in ins["session_close"].split(":"))
    return ins["timezone"], (oh, om), (ch, cm)


def session_opens(code: str) -> dict:
    """Selectable focus sessions for an instrument:
    {label: {tz, open, close}} — native session first, then the global ones
    (Midnight UTC / Tokyo / London / New York) from the School Run registry."""
    ins = get_instrument(code)
    native_label = "Native session"
    out = {native_label: {"tz": ins["timezone"], "open": ins["session_open"],
                          "close": ins["session_close"]}}
    reg = _load_registry()
    entry = reg.get(code)
    if entry is None:
        for k, v in reg.items():
            if code.startswith(k):
                entry = v
                break
    for lbl, sess in ((entry or {}).get("session_opens") or {}).items():
        if all(k in sess for k in ("tz", "open", "close")):
            if "(native)" in lbl:
                continue  # already covered by "Native session"
            out[lbl] = {"tz": sess["tz"], "open": sess["open"], "close": sess["close"]}
    return out


def label(code: str) -> str:
    ins = get_instrument(code)
    return f"{code} — {ins['name']}" if ins["name"] and ins["name"] != code else code


def reload_registry():
    _load_registry.cache_clear()
