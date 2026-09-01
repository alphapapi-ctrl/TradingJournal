"""
Market data bridge — reads the School Run App parquet caches directly.

The sibling repo (default C:/Users/pc/School Run App) maintains Dukascopy
tick + 1-minute parquet caches. We only READ its files here; we never import
its code (both repos have a top-level ``data`` name that would collide).
Data updates shell its fetcher as a subprocess (see run_data_update).
"""
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz
import streamlit as st
from utils.app_settings import get_setting as _get_setting, set_setting as _set_setting

BERLIN = pytz.timezone("Europe/Berlin")  # kept for backwards compat
DEFAULT_SR_ROOT = r"C:\Users\pc\School Run App"
DEFAULT_INSTRUMENT = "DEUIDXEUR"


def _session(instrument: str):
    """(tz, (open_h, open_m), (close_h, close_m)) from the instrument registry."""
    from utils.instruments import session_times
    tz_name, start, end = session_times(instrument)
    return pytz.timezone(tz_name), start, end


def current_instrument() -> str:
    return get_setting("reference_instrument", DEFAULT_INSTRUMENT)


def set_current_instrument(code: str):
    save_setting("reference_instrument", code)

_STEM_RE = re.compile(
    r"^(?P<inst>[A-Z0-9]+)_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})(?P<flags>(?:_(?:ext|fd))?)_(?P<kind>1min|ticks)$"
)


# ── Settings ──────────────────────────────────────────────────────────────
def get_setting(key: str, default=None):
    return _get_setting(key, default)


def save_setting(key: str, value):
    _set_setting(key, value)


def get_sr_root() -> Path:
    return Path(get_setting("school_run_root", DEFAULT_SR_ROOT))


def get_data_root() -> Path:
    return get_sr_root() / "data"


# ── Cache discovery ───────────────────────────────────────────────────────
def list_caches(kind: str, instrument: str = DEFAULT_INSTRUMENT) -> list[dict]:
    """All parquet caches of a kind ('1min'|'ticks') for an instrument."""
    folder = get_data_root() / kind
    out = []
    if not folder.is_dir():
        return out
    for p in folder.glob("*.parquet"):
        m = _STEM_RE.match(p.stem)
        if not m or m.group("inst") != instrument:
            continue
        out.append({
            "path": p,
            "start": m.group("start"),
            "end": m.group("end"),
            "ext": "_ext" in m.group("flags"),
            "fd": "_fd" in m.group("flags"),
            "days": (pd.Timestamp(m.group("end")) - pd.Timestamp(m.group("start"))).days,
        })
    return out


def find_cache(kind: str, instrument: str = DEFAULT_INSTRUMENT, prefer_ext: bool = True) -> Path | None:
    """Widest-range cache; extended-hours (_ext/_fd) breaks coverage ties
    (so DAX picks its ext twin, but a small ext slice never beats a
    decade-long session cache)."""
    caches = list_caches(kind, instrument)
    if not caches:
        return None
    caches.sort(key=lambda c: (c["days"], c["end"],
                               (c["ext"] or c["fd"]) if prefer_ext else not (c["ext"] or c["fd"])),
                reverse=True)
    return caches[0]["path"]


# ── Bar loading ───────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="Loading 1-minute bar cache…")
def _load_1min_cached(path_str: str, mtime: float) -> pd.DataFrame:
    df = pd.read_parquet(path_str)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.sort_index()


def load_1min(instrument: str = DEFAULT_INSTRUMENT) -> pd.DataFrame | None:
    """Full 1-min history, UTC index. Cached per (path, mtime)."""
    p = find_cache("1min", instrument, prefer_ext=True)
    if p is None:
        return None
    return _load_1min_cached(str(p), p.stat().st_mtime)


def list_instruments() -> list[str]:
    """Instrument codes that have at least one 1-min cache."""
    folder = get_data_root() / "1min"
    if not folder.is_dir():
        return []
    codes = set()
    for p in folder.glob("*.parquet"):
        m = _STEM_RE.match(p.stem)
        if m:
            codes.add(m.group("inst"))
    return sorted(codes)


def list_available_days(instrument: str = DEFAULT_INSTRUMENT) -> list[str]:
    """Trading days (exchange-local dates) present in the 1-min cache."""
    df = load_1min(instrument)
    if df is None or df.empty:
        return []
    tz, _, _ = _session(instrument)
    local = df.index.tz_convert(tz)
    return sorted({d.strftime("%Y-%m-%d") for d in local.normalize().unique()})


def get_day_bars(date_str: str, session_only: bool = True,
                 instrument: str = DEFAULT_INSTRUMENT,
                 session: dict | None = None) -> pd.DataFrame:
    """1-min bars for one exchange-local calendar day (UTC index).

    session_only=True clips to the native cash session, or to `session`
    ({tz, open, close}) when a focus-session override is given.
    """
    df = load_1min(instrument)
    if df is None:
        return pd.DataFrame()
    start, end = _day_window(instrument, date_str, session_only, session)
    return df.loc[start.astimezone(pytz.UTC):end.astimezone(pytz.UTC)]


def _session_override(session: dict | None):
    """Parse a focus-session dict {tz, open, close} into (tz, (h,m), (h,m))."""
    if not session:
        return None
    tz = pytz.timezone(session["tz"])
    oh, om = (int(x) for x in session["open"].split(":"))
    ch, cm = (int(x) for x in session["close"].split(":"))
    return tz, (oh, om), (ch, cm)


def _day_window(instrument: str, date_str: str, session_only: bool,
                session: dict | None = None):
    """(start, end) tz-aware bounds for a local day.

    session: optional focus-session override {tz, open, close} — the window is
    that session on the same calendar date, localized in the session's tz.
    Sessions that wrap midnight (24h FX/metals) fall back to the whole local day.
    """
    tz, sess_start, sess_end = _session_override(session) or _session(instrument)
    day = pd.Timestamp(date_str)
    wraps = sess_end <= sess_start
    if session_only and not wraps:
        start = tz.localize(day.replace(hour=sess_start[0], minute=sess_start[1]))
        end = tz.localize(day.replace(hour=sess_end[0], minute=sess_end[1]))
    else:
        native_tz, _, _ = _session(instrument)
        start = native_tz.localize(day)
        end = start + timedelta(days=1)
    return start, end


def session_bounds(date_str: str, instrument: str = DEFAULT_INSTRUMENT,
                   session: dict | None = None):
    """Public (start, end) tz-aware bounds of the (focus) session on a date."""
    return _day_window(instrument, date_str, session_only=True, session=session)


def get_day_ticks(date_str: str, instrument: str = DEFAULT_INSTRUMENT,
                  session_only: bool = True, session: dict | None = None) -> pd.DataFrame:
    """Ticks for one day via predicate-filtered parquet read (files are GB-scale)."""
    import pyarrow.parquet as pq
    import pyarrow.compute as pc

    p = find_cache("ticks", instrument, prefer_ext=True)
    if p is None:
        return pd.DataFrame()
    start, end = _day_window(instrument, date_str, session_only, session)
    # index column name varies between cache generations
    schema = pq.ParquetFile(p).schema_arrow
    ts_col = next((n for n in schema.names if "timestamp" in n or n.startswith("__index")),
                  schema.names[-1])
    tbl = pq.read_table(
        p,
        filters=[(ts_col, ">=", start.astimezone(pytz.UTC)),
                 (ts_col, "<", end.astimezone(pytz.UTC))],
    )
    df = tbl.to_pandas()
    if ts_col in df.columns:
        df = df.set_index(ts_col)
    df.index.name = "timestamp"
    return df


# ── Chart serialization ───────────────────────────────────────────────────
def bars_to_lwc(df: pd.DataFrame, instrument: str = DEFAULT_INSTRUMENT) -> list[dict]:
    """DataFrame → lightweight-charts candle dicts.

    Epochs are shifted by the instrument's local UTC offset so the
    (UTC-rendering) chart displays exchange-local times.
    """
    if df is None or df.empty:
        return []
    tz, _, _ = _session(instrument)
    # Convert to local wall-clock, then read it as if it were UTC: that is
    # exactly the shifted epoch the chart needs (handles DST per-row).
    # (cast via datetime64[s] — the parquet index is ms-unit, so asi8 would be ms)
    epochs = df.index.tz_convert(tz).tz_localize(None).values.astype("datetime64[s]").astype("int64")
    out = []
    for ts, (o, h, l, c, v) in zip(epochs, df[["open", "high", "low", "close", "volume"]].itertuples(index=False)):
        out.append({"time": int(ts), "open": round(float(o), 2), "high": round(float(h), 2),
                    "low": round(float(l), 2), "close": round(float(c), 2), "volume": int(v)})
    return out


def _shifted_epoch(ts, instrument: str) -> int:
    """UTC timestamp → the chart's instrument-local-shifted epoch."""
    tz, _, _ = _session(instrument)
    local = ts.astimezone(tz)
    return int(local.timestamp()) + int(local.utcoffset().total_seconds())


def session_end_epoch(date_str: str, instrument: str = DEFAULT_INSTRUMENT,
                      session: dict | None = None) -> int:
    """Session-end as a local-shifted epoch (same convention as bars_to_lwc)."""
    _, end = _day_window(instrument, date_str, session_only=True, session=session)
    return _shifted_epoch(end, instrument)


def session_start_epoch(date_str: str, instrument: str = DEFAULT_INSTRUMENT,
                        session: dict | None = None) -> int:
    """Focus-session start as a local-shifted epoch — anchors candle numbering."""
    start, _ = _day_window(instrument, date_str, session_only=True, session=session)
    return _shifted_epoch(start, instrument)


def ticks_to_lwc(ticks: pd.DataFrame, instrument: str = DEFAULT_INSTRUMENT) -> dict | None:
    """Tick DataFrame (bid/ask, tz-aware index) → compact replay payload:
    {base: first shifted-epoch ms, dt: [ms deltas], b: [bids], a: [asks]}.

    Bid AND ask are shipped so the replay engine can fill spread-aware
    (buys on ask, sells on bid); candles are built from mids client-side.
    Timestamps use the same instrument-local-shifted convention as
    bars_to_lwc, in milliseconds, so true inter-tick timing is preserved.
    """
    if ticks is None or ticks.empty:
        return None
    tz, _, _ = _session(instrument)
    ms = ticks.index.tz_convert(tz).tz_localize(None).values.astype("datetime64[ms]").astype("int64")
    base = int(ms[0])
    dt = (ms[1:] - ms[:-1]).astype("int64")
    return {
        "base": base,
        "dt": [int(x) for x in dt],
        "b": [float(x) for x in ticks["bid"].values.round(2)],
        "a": [float(x) for x in ticks["ask"].values.round(2)],
    }


def volume_profile(bars: pd.DataFrame, bins: int = 50,
                   value_area_pct: float = 0.70) -> dict | None:
    """Volume profile of a bar set → {poc, vah, val}.

    Each bar's volume is spread evenly across the price bins its H–L range
    covers; the value area expands from the POC until value_area_pct of
    total volume is enclosed (classic market-profile method).
    """
    if bars is None or bars.empty or "volume" not in bars:
        return None
    lo, hi = float(bars["low"].min()), float(bars["high"].max())
    if hi <= lo:
        return None
    import numpy as np
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    for h, l, v in zip(bars["high"].values, bars["low"].values, bars["volume"].values):
        i0 = int(np.searchsorted(edges, l, "right")) - 1
        i1 = int(np.searchsorted(edges, h, "left"))
        i0, i1 = max(0, i0), min(bins, max(i1, i0 + 1))
        vol[i0:i1] += float(v) / (i1 - i0)
    poc_i = int(vol.argmax())
    total = vol.sum()
    if total <= 0:
        return None
    # expand value area around POC, adding the larger neighbour each step
    inc = vol[poc_i]
    lo_i = hi_i = poc_i
    while inc < value_area_pct * total and (lo_i > 0 or hi_i < bins - 1):
        below = vol[lo_i - 1] if lo_i > 0 else -1.0
        above = vol[hi_i + 1] if hi_i < bins - 1 else -1.0
        if above >= below:
            hi_i += 1
            inc += vol[hi_i]
        else:
            lo_i -= 1
            inc += vol[lo_i]
    mid = lambda i: float((edges[i] + edges[i + 1]) / 2)
    return {
        "poc": round(mid(poc_i), 1),
        "vah": round(float(edges[hi_i + 1]), 1),
        "val": round(float(edges[lo_i]), 1),
        # per-bin rows for on-chart histogram rendering
        "bins": [{"p0": round(float(edges[i]), 2), "p1": round(float(edges[i + 1]), 2),
                  "v": round(float(vol[i]), 1),
                  "va": lo_i <= i <= hi_i, "poc": i == poc_i}
                 for i in range(bins)],
    }


# ── Data update (subprocess into School Run App) ──────────────────────────
def sr_python_exe() -> Path:
    root = get_sr_root()
    cand = root / ".venv" / "Scripts" / "python.exe"
    return cand if cand.exists() else Path(get_setting("school_run_python", str(cand)))


UPDATE_LOG = Path(__file__).resolve().parent.parent / "data" / "data_update.log"


def run_data_update(instrument: str = DEFAULT_INSTRUMENT,
                    start: str | None = None,
                    end: str | None = None) -> subprocess.Popen:
    """Download/extend the School Run 1-min + tick caches, in a subprocess.

    Without start/end this extends the existing cache to today; with them it
    performs an initial download of that range (fresh instruments). Runs with
    cwd at the School Run root so its own package imports resolve; output is
    teed to UPDATE_LOG for the UI to tail.
    """
    root = get_sr_root()
    end = end or datetime.now().strftime("%Y-%m-%d")
    all_caches = list_caches("1min", instrument)
    ext_caches = [c for c in all_caches if c["ext"] or c["fd"]]
    # extend the same flavour of cache the instrument already has;
    # fresh instruments (no caches) default to extended hours
    extended = bool(ext_caches) or not all_caches
    if start is None:
        pool = ext_caches if ext_caches else all_caches
        start = min((c["start"] for c in pool), default="2016-01-01")
    code = (
        "from data.fetcher import fetch_1min_stitched\n"
        f"fetch_1min_stitched('{start}', '{end}', instrument='{instrument}', "
        f"extended_hours={extended})\n"
    )
    UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = open(UPDATE_LOG, "w", encoding="utf-8", errors="replace")
    # Force UTF-8 stdout in the child: the fetcher prints ✓ etc., which crashes
    # with UnicodeEncodeError under the Windows-default cp1252 when redirected.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.Popen(
        [str(sr_python_exe()), "-c", code],
        cwd=str(root), stdout=logf, stderr=subprocess.STDOUT, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def read_update_log(max_lines: int = 40) -> str:
    if not UPDATE_LOG.exists():
        return ""
    try:
        lines = UPDATE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return ""
