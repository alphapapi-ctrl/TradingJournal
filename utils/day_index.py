"""
day_features index — per-day strategy features for the DAX Reference scanner.

Built from the School Run App extended-hours 1-min parquet cache.
Windows (Europe/Berlin local):
  session    09:00–17:30  (Xetra cash)
  overnight  00:00–09:00  (approximation: the _ext cache has no prior-evening
                           17:30–24:00 data, so the overnight range starts at
                           midnight local, matching School Run's ON window)
  b1 / b2    first and second 15-min bars of the session
"""
import sqlite3
from datetime import time as dtime

import pandas as pd

from database import DB_PATH, fetch_all
from utils import market_data as md
from utils.news_events import flags_for_date

ENTRY_BUFFER_PTS = 3.0   # school run entry buffer (matches live JB config)
DOJI_BODY_FRAC = 0.25    # body < 25% of range → doji


def _add_minutes(t: dtime, minutes: int) -> dtime:
    total = t.hour * 60 + t.minute + minutes
    return dtime((total // 60) % 24, total % 60)


# ── Feature computation ───────────────────────────────────────────────────
def _bar_type(o, h, l, c) -> str:
    rng = h - l
    if rng <= 0:
        return "doji"
    body = abs(c - o)
    if body < DOJI_BODY_FRAC * rng:
        return "doji"
    return "bull" if c > o else "bear"


def _b2_rel(b1_h, b1_l, b2_h, b2_l) -> str:
    broke_high, broke_low = b2_h > b1_h, b2_l < b1_l
    if broke_high and broke_low:
        return "outside"
    if broke_high:
        return "broke_high"
    if broke_low:
        return "broke_low"
    return "inside"


def _range_pos(price, high, low, above="above", below="below", inside="inside") -> str | None:
    if high is None or low is None:
        return None
    if price > high:
        return above
    if price < low:
        return below
    return inside


def compute_day_features(date_str: str, day_local: pd.DataFrame, prior: dict | None,
                         instrument: str) -> dict | None:
    """Features for one day. day_local: 1-min bars indexed by exchange-local time."""
    from utils.instruments import session_times
    _, (oh, om), (ch, cm) = session_times(instrument)
    s_open, s_close = dtime(oh, om), dtime(ch, cm)
    wraps = s_close <= s_open  # 24h / midnight-crossing session → whole local day
    if wraps:
        s_open, s_close = dtime(0, 0), dtime(23, 59, 59)
    b1_end = _add_minutes(s_open, 15)
    b2_end = _add_minutes(s_open, 30)

    t = day_local.index.time
    sess = day_local[(t >= s_open) & (t < s_close)]
    onr = day_local[t < s_open]
    b1 = sess[sess.index.time < b1_end]
    b2 = sess[(sess.index.time >= b1_end) & (sess.index.time < b2_end)]
    if sess.empty or b1.empty:
        return None

    f = {
        "date": date_str,
        "instrument": instrument,
        "session_open": float(sess.iloc[0]["open"]),
        "session_close": float(sess.iloc[-1]["close"]),
        "session_high": float(sess["high"].max()),
        "session_low": float(sess["low"].min()),
        "overnight_high": float(onr["high"].max()) if not onr.empty else None,
        "overnight_low": float(onr["low"].min()) if not onr.empty else None,
        "prior_high": prior.get("session_high") if prior else None,
        "prior_low": prior.get("session_low") if prior else None,
        "prior_close": prior.get("session_close") if prior else None,
        "news_flags": flags_for_date(date_str),
    }

    # Gap vs prior session close (hindsight gap-close flag)
    if f["prior_close"]:
        gap = f["session_open"] - f["prior_close"]
        f["gap_pts"] = round(gap, 1)
        f["gap_pct"] = round(100.0 * gap / f["prior_close"], 3)
        f["gap_dir"] = "up" if gap > 0 else ("down" if gap < 0 else "flat")
        touched = sess[(sess["low"] <= f["prior_close"]) & (sess["high"] >= f["prior_close"])]
        f["gap_closed"] = int(not touched.empty)
        f["gap_close_time"] = touched.index[0].strftime("%H:%M") if not touched.empty else None
        f["open_vs_prior"] = _range_pos(f["session_open"], f["prior_high"], f["prior_low"],
                                        above="above_high", below="below_low")
    else:
        f.update({"gap_pts": None, "gap_pct": None, "gap_dir": None,
                  "gap_closed": None, "gap_close_time": None, "open_vs_prior": None})

    f["open_vs_onr"] = _range_pos(f["session_open"], f["overnight_high"], f["overnight_low"])

    # Setup bars (first/second 15m)
    b1_o, b1_c = float(b1.iloc[0]["open"]), float(b1.iloc[-1]["close"])
    b1_h, b1_l = float(b1["high"].max()), float(b1["low"].min())
    f.update({"b1_o": b1_o, "b1_h": b1_h, "b1_l": b1_l, "b1_c": b1_c,
              "b1_type": _bar_type(b1_o, b1_h, b1_l, b1_c)})
    if not b2.empty:
        b2_o, b2_c = float(b2.iloc[0]["open"]), float(b2.iloc[-1]["close"])
        b2_h, b2_l = float(b2["high"].max()), float(b2["low"].min())
        f.update({"b2_o": b2_o, "b2_h": b2_h, "b2_l": b2_l, "b2_c": b2_c,
                  "b2_type": _bar_type(b2_o, b2_h, b2_l, b2_c),
                  "b2_rel_b1": _b2_rel(b1_h, b1_l, b2_h, b2_l)})
    else:
        f.update({"b2_o": None, "b2_h": None, "b2_l": None, "b2_c": None,
                  "b2_type": None, "b2_rel_b1": None})

    # School-run levels: stop entry beyond b1 ± buffer, SL at opposite side (ORB mode)
    f["sr_long_entry"] = round(b1_h + ENTRY_BUFFER_PTS, 1)
    f["sr_long_stop"] = round(b1_l, 1)
    f["sr_short_entry"] = round(b1_l - ENTRY_BUFFER_PTS, 1)
    f["sr_short_stop"] = round(b1_h, 1)
    return f


# ── Index build ───────────────────────────────────────────────────────────
_COLS = ["date", "instrument", "session_open", "session_close", "session_high", "session_low",
         "prior_high", "prior_low", "prior_close", "overnight_high", "overnight_low",
         "gap_pts", "gap_pct", "gap_dir", "gap_closed", "gap_close_time",
         "open_vs_prior", "open_vs_onr",
         "b1_o", "b1_h", "b1_l", "b1_c", "b1_type",
         "b2_o", "b2_h", "b2_l", "b2_c", "b2_type", "b2_rel_b1",
         "sr_long_entry", "sr_long_stop", "sr_short_entry", "sr_short_stop", "news_flags"]


def rebuild_index(instrument: str = md.DEFAULT_INSTRUMENT, force: bool = False,
                  progress=None) -> int:
    """(Re)build day_features from the 1-min cache. Incremental unless force.

    Returns the number of rows written. progress: optional callable(done, total).
    """
    df = md.load_1min(instrument)
    if df is None or df.empty:
        return 0
    from utils.instruments import session_times
    import pytz
    tz_name, (oh, om), (ch, cm) = session_times(instrument)
    if (ch, cm) <= (oh, om):  # midnight-wrapping session → whole local day
        (oh, om), (ch, cm) = (0, 0), (23, 59)
    tz = pytz.timezone(tz_name)
    local = df.copy()
    local.index = df.index.tz_convert(tz)
    by_day = dict(tuple(local.groupby(local.index.normalize())))
    all_days = sorted(k.strftime("%Y-%m-%d") for k in by_day)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if force:
            conn.execute("DELETE FROM day_features WHERE instrument=?", (instrument,))
            last = None
        else:
            row = conn.execute("SELECT MAX(date) m FROM day_features WHERE instrument=?",
                               (instrument,)).fetchone()
            last = row["m"]

        # prior-session values seed: last indexed row, else roll forward inside loop
        prior = None
        if last:
            r = conn.execute("SELECT session_high, session_low, session_close FROM day_features "
                             "WHERE instrument=? AND date=?", (instrument, last)).fetchone()
            prior = dict(r) if r else None

        todo = [d for d in all_days if (last is None or d > last)]
        # need prior context even for skipped days
        if last is None:
            todo = all_days
        written = 0
        rows = []
        for i, d in enumerate(all_days):
            day_bars = by_day[pd.Timestamp(d, tz=tz)]
            if d in todo:
                f = compute_day_features(d, day_bars, prior, instrument)
                if f:
                    rows.append(tuple(f.get(c) for c in _COLS))
                    written += 1
                    prior = {"session_high": f["session_high"], "session_low": f["session_low"],
                             "session_close": f["session_close"]}
                if progress and (written % 100 == 0):
                    progress(written, len(todo))
            else:
                # roll prior forward from already-indexed day
                t = day_bars.index.time
                sess = day_bars[(t >= dtime(oh, om)) & (t < dtime(ch, cm))]
                if not sess.empty:
                    prior = {"session_high": float(sess["high"].max()),
                             "session_low": float(sess["low"].min()),
                             "session_close": float(sess.iloc[-1]["close"])}
        if rows:
            ph = ",".join("?" * len(_COLS))
            conn.executemany(
                f"INSERT OR REPLACE INTO day_features ({','.join(_COLS)}) VALUES ({ph})", rows)
        conn.commit()
        return written
    finally:
        conn.close()


# ── Queries ───────────────────────────────────────────────────────────────
def get_day(date_str: str, instrument: str = md.DEFAULT_INSTRUMENT) -> dict | None:
    rows = fetch_all("SELECT * FROM day_features WHERE date=? AND instrument=?",
                     (date_str, instrument))
    return dict(rows[0]) if rows else None


def index_count(instrument: str = md.DEFAULT_INSTRUMENT) -> int:
    rows = fetch_all("SELECT COUNT(*) n FROM day_features WHERE instrument=?", (instrument,))
    return rows[0]["n"] if rows else 0


def query_days(filters: dict, instrument: str = md.DEFAULT_INSTRUMENT,
               limit: int = 300) -> list[dict]:
    """Filter day_features. Supported keys:
    gap_dir ('up'/'down'), gap_min/gap_max (abs pts), gap_closed (bool),
    open_vs_prior, open_vs_onr, b1_type, b2_type, b2_rel_b1,
    news (event code or 'none' or 'any'), date_from, date_to.
    """
    where, params = ["instrument=?"], [instrument]
    if filters.get("gap_dir"):
        where.append("gap_dir=?"); params.append(filters["gap_dir"])
    if filters.get("gap_min") is not None:
        where.append("ABS(gap_pts)>=?"); params.append(filters["gap_min"])
    if filters.get("gap_max") is not None:
        where.append("ABS(gap_pts)<=?"); params.append(filters["gap_max"])
    if filters.get("gap_closed") is not None:
        where.append("gap_closed=?"); params.append(int(filters["gap_closed"]))
    for key in ("open_vs_prior", "open_vs_onr", "b1_type", "b2_type", "b2_rel_b1"):
        if filters.get(key):
            where.append(f"{key}=?"); params.append(filters[key])
    news = filters.get("news")
    if news == "any":
        where.append("news_flags != ''")
    elif news == "none":
        where.append("(news_flags = '' OR news_flags IS NULL)")
    elif news:
        where.append("news_flags LIKE ?"); params.append(f"%{news}%")
    if filters.get("date_from"):
        where.append("date>=?"); params.append(str(filters["date_from"]))
    if filters.get("date_to"):
        where.append("date<=?"); params.append(str(filters["date_to"]))
    sql = (f"SELECT * FROM day_features WHERE {' AND '.join(where)} "
           f"ORDER BY date DESC LIMIT {int(limit)}")
    return [dict(r) for r in fetch_all(sql, tuple(params))]
