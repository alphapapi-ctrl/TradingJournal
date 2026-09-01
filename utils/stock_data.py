"""
Stock data layer — yfinance wrappers for the Forward Test / Stock Analysis page.

Technical checks mirror the FX Evolution / Wyckoff strategy:
  EMA 21/50, SMA 200 (daily + weekly), RSI 14, OBV, volume-spike (capitulation) detection.

Fundamental checks are a crude Buffett/Burry value prequalification:
  traffic-light pass/warn/fail on valuation, profitability, balance-sheet strength.
"""
import pandas as pd
import numpy as np
from pathlib import Path


# ─── SYMBOL RESOLUTION ────────────────────────────────────────────────────────

def resolve_yf_symbol(symbol: str, exchange_hint: str = "") -> str:
    """
    Map journal symbols to yfinance tickers.
    'BHP' + ASX hint (or known AU ticker style) → 'BHP.AX'; US tickers pass through.
    """
    sym = symbol.strip().upper()
    if "." in sym:          # already suffixed (BHP.AX, 3DP.AX)
        return sym
    if exchange_hint.upper() in ("ASX", "AU"):
        return f"{sym}.AX"
    return sym


def fetch_history(yf_symbol: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(yf_symbol).history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f"No price data for {yf_symbol}")
    return df


def fetch_info(yf_symbol: str) -> dict:
    import yfinance as yf
    return yf.Ticker(yf_symbol).info or {}


# ─── ASX SUBSTANTIAL HOLDERS (Dashboard-app bridge) ───────────────────────────
# Prefer local storage under this repo: data/substantial_holders.
# Optionally, if dashboard_app_root is set to an existing folder, keep using that
# historical path. This prevents permission errors on different user profiles.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DASHBOARD_ROOT = PROJECT_ROOT / "data"
LEGACY_SUBHOLDERS_PATH = Path("stocks") / "results" / "substantial_holders" / "substantial_holders_history.csv"
LOCAL_SUBHOLDERS_PATH = Path("data") / "substantial_holders" / "substantial_holders_history.csv"


def _dashboard_root() -> str:
    try:
        from utils.market_data import get_setting
        cfg = get_setting("dashboard_app_root", "")
        if cfg:
            p = Path(cfg)
            looks_like_bridge = (p / "stocks" / "results").exists()
            if p.exists() and looks_like_bridge:
                return str(p)
    except Exception:
        pass
    return str(DEFAULT_DASHBOARD_ROOT)


def substantial_holders_path() -> str:
    root = Path(_dashboard_root())
    # Legacy project layout (older remote installs)
    legacy = root / LEGACY_SUBHOLDERS_PATH
    if legacy.exists() or (root / "stocks" / "results").exists():
        return str(legacy)
    return str(PROJECT_ROOT / LOCAL_SUBHOLDERS_PATH)


def substantial_holders_last_run():
    """Datetime the scraper last wrote the history file, or None if never run."""
    import os, datetime as _dt
    path = substantial_holders_path()
    if not os.path.exists(path):
        return None
    return _dt.datetime.fromtimestamp(os.path.getmtime(path))


def load_substantial_holders() -> pd.DataFrame | None:
    """All accumulated notices, newest first. None if the bridge file is missing."""
    import os
    path = substantial_holders_path()
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, dtype={"ann_id": str})
    return df.sort_values(["date", "time"], ascending=False)


_ASX_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'}

_SH_FORM_MAP = [
    ('becoming a substantial holder', '603', 'BECOMING'),
    ('ceasing to be a substantial holder', '605', 'CEASING'),
    ('change in substantial holding', '604', 'CHANGE'),
]

# Per-company announcement rows: date/time cell ... pdf link + title.
_SH_COMPANY_ROW_RE = None  # compiled lazily


def backfill_substantial_holders(base_ticker: str, period: str = "M6") -> tuple[bool, str]:
    """
    Fetch up to 6 months of one company's announcements from the legacy ASX
    search (asx/v2/statistics/announcements.do) and merge any substantial-holder
    notices (603/604/605) into the bridge history CSV. Same schema + ann_id
    dedupe as the dashboard app's scraper, so both apps share one record.
    """
    import os, re
    import requests
    from datetime import datetime

    global _SH_COMPANY_ROW_RE
    if _SH_COMPANY_ROW_RE is None:
        _SH_COMPANY_ROW_RE = re.compile(
            r'<td>\s*(\d{2}/\d{2}/\d{4})<br>\s*'
            r'<span class="dates-time">([^<]*)</span>\s*</td>'
            r'((?:(?!<tr).)*?)'
            r'href="(/asx/v2/statistics/displayAnnouncement\.do\?display=pdf&amp;idsId=(\d+))">\s*'
            r'([^<]+?)<br>',
            re.DOTALL)

    try:
        r = requests.get(
            'https://www.asx.com.au/asx/v2/statistics/announcements.do',
            params={'by': 'asxCode', 'asxCode': base_ticker.upper(),
                    'timeframe': 'D', 'period': period},
            headers=_ASX_UA, timeout=30)
        r.raise_for_status()
    except Exception as e:
        return False, f"ASX fetch failed: {e}"

    rows = []
    for m in _SH_COMPANY_ROW_RE.finditer(r.text):
        date_s, time_s, _mid, href, ids_id, title = m.groups()
        title = title.strip()
        form = action = None
        for needle, f, a in _SH_FORM_MAP:
            if needle in title.lower():
                form, action = f, a
                break
        if not form:
            continue
        try:
            dt = datetime.strptime(date_s, '%d/%m/%Y').strftime('%Y-%m-%d')
        except ValueError:
            dt = date_s
        rows.append({
            'ann_id': ids_id,
            'date': dt,
            'time': time_s.strip(),
            'ticker': base_ticker.upper(),
            'form': form,
            'action': action,
            'title': title,
            'pdf_url': 'https://www.asx.com.au' + href.replace('&amp;', '&'),
        })

    if not rows:
        return True, "No substantial holder notices found in the ASX search window."

    df_new = pd.DataFrame(rows)
    path = substantial_holders_path()
    path_dir = Path(path).parent
    path_dir.mkdir(parents=True, exist_ok=True)
    old_times = None
    if os.path.exists(path):
        st_ = os.stat(path)
        old_times = (st_.st_atime, st_.st_mtime)
        hist = pd.read_csv(path, dtype={'ann_id': str})
        n_before = len(hist)
        combined = pd.concat([hist, df_new], ignore_index=True)
    else:
        n_before = 0
        combined = df_new
    combined['ann_id'] = combined['ann_id'].astype(str)
    combined = (combined.drop_duplicates(subset='ann_id', keep='first')
                        .sort_values(['date', 'time'], ascending=False))
    combined.to_csv(path, index=False)
    if old_times:
        # keep mtime = last full-market scraper run (drives the "run today?" banner)
        try:
            os.utime(path, old_times)
        except OSError:
            pass
    return True, f"{len(combined) - n_before} new notice(s) merged ({len(rows)} found in window)."


def refresh_substantial_holders(timeout: int = 120) -> tuple[bool, str]:
    """Run the dashboard-app scraper when available, otherwise backfill from
    local ASX tickers in the journal data so the feature still works without
    external project wiring.
    """
    import os, sys, subprocess

    candidates = []
    cfg_root = _dashboard_root()
    candidates.append((cfg_root, os.path.join(cfg_root, "stocks", "asx_substantial_holders.py")))

    # Optional legacy location (if settings still point to a sibling project folder)
    legacy_root = os.path.join(PROJECT_ROOT.parent, "stocks")
    if os.path.exists(legacy_root):
        candidates.append((legacy_root, os.path.join(legacy_root, "asx_substantial_holders.py")))

    # Absolute fallback paths that often work on this repo layout
    fallback_root = os.path.join(PROJECT_ROOT, "stocks")
    candidates.append((fallback_root, os.path.join(fallback_root, "asx_substantial_holders.py")))

    script = ""
    root = ""
    for cand_root, cand_script in candidates:
        if cand_script and os.path.exists(cand_script):
            root = cand_root
            script = cand_script
            break

    if not script:
        # Fallback: backfill based on symbols already in this repo's database.
        try:
            from database import fetch_all

            rows = fetch_all(
                "SELECT DISTINCT symbol FROM trades WHERE symbol IS NOT NULL AND symbol != ''"
            )
            symbols = sorted({
                str(r["symbol"]).strip().upper() for r in rows if str(r["symbol"]).strip()
            })
            if not symbols:
                return (
                    False,
                    "No tickers found in journal data for fallback backfill. "
                    "Save settings `dashboard_app_root` if you still want the external scraper."
                )

            attempted = 0
            added = 0
            failures = []
            for sym in symbols:
                base = sym.split(".")[0].strip()
                if not base:
                    continue
                attempted += 1
                ok, msg = backfill_substantial_holders(base)
                if ok:
                    try:
                        # Success format: \"<n> new notice(s)...\"
                        if "new notice" in msg:
                            added += int(msg.split(" new notice", 1)[0].split()[-1])
                    except Exception:
                        pass
                else:
                    failures.append(f"{base}: {msg}")

            summary = f"Fallback backfill ran for {attempted} ticker(s), added ~{added} new notices."
            if failures:
                summary += " Some failed: " + "; ".join(failures[:6])
                return False, summary
            return True, summary
        except Exception as e:
            return (
                False,
                "Scraper not found. Set app setting `dashboard_app_root` to the legacy Dashboard project "
                "that contains `stocks\\asx_substantial_holders.py`, or rely on per-ticker backfill by opening ASX tickers."
            ) if str(e) else "Scraper not found."
    py = os.path.join(root, ".venv", "Scripts", "python.exe")
    if not os.path.exists(py):
        py = sys.executable
    try:
        r = subprocess.run([py, script], cwd=root, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)


# ─── TECHNICALS ───────────────────────────────────────────────────────────────

def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/length, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/length, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def compute_technicals(daily: pd.DataFrame, weekly: pd.DataFrame | None = None) -> dict:
    """
    Compute the strategy's technical read from daily (and optional weekly) OHLCV.
    Returns dict of indicator values + rule-based signals.
    """
    df = daily.copy()
    close, vol = df["Close"], df["Volume"]

    df["EMA21"]  = close.ewm(span=21,  adjust=False).mean()
    df["EMA50"]  = close.ewm(span=50,  adjust=False).mean()
    df["SMA200"] = close.rolling(200).mean()
    df["RSI"]    = _rsi(close)
    df["OBV"]    = _obv(close, vol)
    df["VolAvg20"] = vol.rolling(20).mean()

    last = df.iloc[-1]
    price = float(last["Close"])

    sig = {}
    # ── Strategy management rules ────────────────────────────────────────────
    ema21, ema50, sma200 = float(last["EMA21"]), float(last["EMA50"]), float(last["SMA200"]) if not np.isnan(last["SMA200"]) else None

    sig["price"]  = price
    sig["ema21"]  = ema21
    sig["ema50"]  = ema50
    sig["sma200"] = sma200
    sig["rsi"]    = float(last["RSI"]) if not np.isnan(last["RSI"]) else None

    # Trend state per the strategy: above 21 = continuation; 21-50 = larger pullback;
    # daily close below 50-63 zone = close swing trade
    if price > ema21:
        sig["trend_state"] = ("continuation", "Price above 21 EMA — trend continuation")
    elif price > ema50:
        sig["trend_state"] = ("pullback", "Between 21 and 50 EMA — larger pullback / possible consolidation")
    else:
        sig["trend_state"] = ("warning", "Daily close below 50 EMA — grounds to close swing trade, expect larger consolidation")

    sig["above_sma200"] = bool(sma200 and price > sma200)
    sig["ema_cross_bull"] = bool(ema21 > ema50)

    # Volume spike / capitulation scan (last 30 bars): vol > 2.5x 20-bar avg
    recent = df.tail(30)
    spikes = recent[recent["Volume"] > 2.5 * recent["VolAvg20"]]
    sig["recent_volume_spikes"] = [
        {"date": str(idx.date()), "volume": int(r["Volume"]),
         "ratio": round(float(r["Volume"] / r["VolAvg20"]), 1),
         "close": round(float(r["Close"]), 4),
         "bearish": bool(r["Close"] < r["Open"])}
        for idx, r in spikes.iterrows() if not np.isnan(r["VolAvg20"]) and r["VolAvg20"] > 0
    ]

    # OBV divergence hint: OBV trend over last 20 bars vs price trend
    if len(df) >= 20:
        obv_slope   = np.polyfit(range(20), df["OBV"].tail(20).values, 1)[0]
        price_slope = np.polyfit(range(20), close.tail(20).values, 1)[0]
        if price_slope < 0 and obv_slope > 0:
            sig["obv_note"] = ("bullish", "OBV rising while price falls — possible accumulation")
        elif price_slope > 0 and obv_slope < 0:
            sig["obv_note"] = ("bearish", "OBV falling while price rises — possible distribution")
        else:
            sig["obv_note"] = ("neutral", "OBV confirming price")

    # Distance from 52w high/low
    yr = df.tail(252)
    sig["pct_off_52w_high"] = round((price / float(yr["Close"].max()) - 1) * 100, 1)
    sig["pct_off_52w_low"]  = round((price / float(yr["Close"].min()) - 1) * 100, 1)

    # ── Weekly context ───────────────────────────────────────────────────────
    if weekly is not None and len(weekly) > 50:
        w = weekly.copy()
        w["EMA21"] = w["Close"].ewm(span=21, adjust=False).mean()
        w["EMA50"] = w["Close"].ewm(span=50, adjust=False).mean()
        w["RSI"]   = _rsi(w["Close"])
        wl = w.iloc[-1]
        sig["weekly"] = {
            "above_ema21": bool(wl["Close"] > wl["EMA21"]),
            "above_ema50": bool(wl["Close"] > wl["EMA50"]),
            "rsi": float(wl["RSI"]) if not np.isnan(wl["RSI"]) else None,
        }
        # Last bearish weekly candle in an uptrend → demand zone (close-to-wick low)
        wtail = w.tail(26)
        bear = wtail[wtail["Close"] < wtail["Open"]]
        if not bear.empty:
            lb = bear.iloc[-1]
            sig["weekly_demand_zone"] = {
                "date": str(bear.index[-1].date()),
                "zone_top": round(float(min(lb["Open"], lb["Close"])), 4),
                "zone_bottom": round(float(lb["Low"]), 4),
            }

    sig["df"] = df  # for charting
    return sig


# ─── FUNDAMENTALS (General fundamental quality framework) ────────────────────

def fundamental_checks(info: dict) -> dict:
    """
    General fundamental quality checks. Each check returns
    (status, value_display, explanation) where status ∈ pass/warn/fail/na.
    """
    def g(key, default=None):
        return info.get(key, default)

    checks = {}

    def add(name, status, value, note, bucket="General"):
        checks[name] = {"status": status, "value": value, "note": note}
        if status in ("pass", "warn", "fail"):
            bucket_totals[bucket]["total"] += 1
            if status == "pass":
                bucket_totals[bucket]["pass"] += 1
            elif status == "fail":
                bucket_totals[bucket]["fail"] += 1

    bucket_totals = {
        "Valuation": {"pass": 0, "fail": 0, "total": 0},
        "Quality": {"pass": 0, "fail": 0, "total": 0},
        "Growth": {"pass": 0, "fail": 0, "total": 0},
        "Financial Strength": {"pass": 0, "fail": 0, "total": 0},
        "Analyst": {"pass": 0, "fail": 0, "total": 0},
    }

    # Valuation
    pe = g("trailingPE")
    if pe is None or pe <= 0:
        add("P/E Ratio", "na" if pe is None else "warn", "n/a" if pe is None else f"{pe:.1f}",
            "No meaningful earnings yet" if pe is not None else "No data", bucket="Valuation")
    elif pe < 14:
        add("P/E Ratio", "pass", f"{pe:.1f}", "Attractive on earnings multiple", bucket="Valuation")
    elif pe < 26:
        add("P/E Ratio", "warn", f"{pe:.1f}", "Fair valuation; depends on durability of growth", bucket="Valuation")
    else:
        add("P/E Ratio", "fail", f"{pe:.1f}", "High multiple versus current earnings", bucket="Valuation")

    pe_fwd = g("forwardPE")
    if pe_fwd is not None:
        if pe_fwd < 14:
            status = "pass"
            note = "Forward earnings imply additional upside if growth holds"
        elif pe_fwd < 27:
            status = "warn"
            note = "Forward multiple sits near fair value"
        else:
            status = "fail"
            note = "Forward multiple still elevated"
        add("Forward P/E", status, f"{pe_fwd:.1f}", note, bucket="Valuation")

    pb = g("priceToBook")
    if pb is None:
            add("Price/Book", "na", "n/a", "No data", bucket="Valuation")
    elif pb < 1.2:
        add("Price/Book", "pass", f"{pb:.2f}", "Trades below/book value on this metric", bucket="Valuation")
    elif pb < 3:
        add("Price/Book", "warn", f"{pb:.2f}", "Mid-band; balance-sheet support limited", bucket="Valuation")
    else:
        add("Price/Book", "fail", f"{pb:.2f}", "Premium to book; valuation is less conservative", bucket="Valuation")

    # Profitability and operating quality
    roe = g("returnOnEquity")
    if roe is None:
            add("Return on Equity", "na", "n/a", "No data", bucket="Quality")
    elif roe > 0.18:
        add("Return on Equity", "pass", f"{roe*100:.1f}%", "Strong ROE indicates good capital use", bucket="Quality")
    elif roe > 0.08:
        add("Return on Equity", "warn", f"{roe*100:.1f}%", "Moderate ROE, mixed quality", bucket="Quality")
    else:
        add("Return on Equity", "fail", f"{roe*100:.1f}%", "Weak ROE; capital efficiency may be low", bucket="Quality")

    margins = g("profitMargins")
    if margins is None:
            add("Net Margin", "na", "n/a", "No data", bucket="Quality")
    elif margins > 0.12:
        add("Net Margin", "pass", f"{margins*100:.1f}%", "Strong bottom-line conversion", bucket="Quality")
    elif margins > 0.03:
        add("Net Margin", "warn", f"{margins*100:.1f}%", "Positive but moderate margins", bucket="Quality")
    else:
        add("Net Margin", "fail", f"{margins*100:.1f}%", "Thin or negative net margins", bucket="Quality")

    fcf = g("freeCashflow")
    if fcf is None:
            add("Free Cash Flow", "na", "n/a", "No data", bucket="Quality")
    elif fcf > 0:
        mcap = g("marketCap")
        yield_txt = f" (FCF yield {fcf/mcap*100:.1f}%)" if mcap else ""
        add("Free Cash Flow", "pass", f"{fcf/1e6:,.0f}M{yield_txt}", "Positive, funding core operations", bucket="Quality")
    else:
        add("Free Cash Flow", "fail", f"{fcf/1e6:,.0f}M", "Negative cash generation", bucket="Quality")

    # Balance sheet and liquidity
    de = g("debtToEquity")
    if de is None:
            add("Debt / Equity", "na", "n/a", "No data", bucket="Financial Strength")
    else:
        de_ratio = de / 100 if de > 10 else de  # yfinance can emit percentage style
        if de_ratio < 0.4:
            add("Debt / Equity", "pass", f"{de_ratio:.2f}", "Conservative leverage", bucket="Financial Strength")
        elif de_ratio < 1.0:
            add("Debt / Equity", "warn", f"{de_ratio:.2f}", "Moderate leverage", bucket="Financial Strength")
        else:
            add("Debt / Equity", "fail", f"{de_ratio:.2f}", "High leverage exposure", bucket="Financial Strength")

    cr = g("currentRatio")
    if cr is None:
            add("Current Ratio", "na", "n/a", "No data", bucket="Financial Strength")
    elif cr > 1.6:
        add("Current Ratio", "pass", f"{cr:.2f}", "Healthy short-term liquidity", bucket="Financial Strength")
    elif cr > 1.0:
        add("Current Ratio", "warn", f"{cr:.2f}", "Adequate liquidity", bucket="Financial Strength")
    else:
        add("Current Ratio", "fail", f"{cr:.2f}", "Tighter short-term coverage", bucket="Financial Strength")

    # Growth
    rev_g = g("revenueGrowth")
    if rev_g is not None:
        if rev_g > 0.08:
            add("Revenue Growth", "pass", f"{rev_g*100:+.1f}%", "Meaningful top-line growth", bucket="Growth")
        elif rev_g >= 0:
            add("Revenue Growth", "warn", f"{rev_g*100:+.1f}%", "Low to mid single-digit growth", bucket="Growth")
        else:
            add("Revenue Growth", "fail", f"{rev_g*100:+.1f}%", "Declining revenue trend", bucket="Growth")

    e_g = g("earningsGrowth")
    if e_g is not None:
        if e_g > 0.10:
            add("Earnings Growth", "pass", f"{e_g*100:+.1f}%", "Healthy earnings acceleration", bucket="Growth")
        elif e_g >= 0:
            add("Earnings Growth", "warn", f"{e_g*100:+.1f}%", "Mixed earnings quality", bucket="Growth")
        else:
            add("Earnings Growth", "fail", f"{e_g*100:+.1f}%", "Shrinking earnings growth", bucket="Growth")

    # Analyst view
    t_low = g("targetLowPrice")
    t_mid = g("targetMeanPrice")
    t_high = g("targetHighPrice")
    price = g("regularMarketPrice") or g("previousClose")
    n_analysts = g("numberOfAnalystOpinions")
    analyst_points = sum(1 for v in (t_low, t_mid, t_high) if v is not None)
    if analyst_points >= 2 and price and price > 0:
        target = t_mid if t_mid is not None else ((t_low + t_high) / 2 if t_low is not None and t_high is not None else None)
        if target is None:
            add("Analyst Target", "na", "n/a", "Insufficient target bands to estimate")
        else:
            upside = (target / price - 1) * 100
            if upside >= 15:
                status = "pass"
                note = "Analyst target materially above market"
            elif upside >= 0:
                status = "warn"
                note = "Target is roughly in line or only modestly above market"
            else:
                status = "fail"
                note = "Consensus target below market"
            if t_low is not None and t_high is not None:
                target_display = f"{t_low:.2f} / {t_mid:.2f} / {t_high:.2f}" if t_mid is not None else f"{t_low:.2f} / {t_high:.2f}"
            elif t_mid is not None:
                target_display = f"{t_mid:.2f}"
            else:
                target_display = f"{t_low:.2f}" if t_low is not None else f"{t_high:.2f}"
            add("Analyst Target", status, target_display, f"{note} ({upside:+.1f}%)" + (f"; {int(n_analysts)} contributors" if n_analysts else ""), bucket="Analyst")
    else:
        if analyst_points >= 2 and not (price and price > 0):
            # Price unavailable means we cannot compute a sensible relative signal
            pass
        elif analyst_points >= 1:
            # If only one target value is available, skip to avoid misleading display
            pass

    # Dividend signal
    div_y = g("dividendYield")
    if div_y:
        dy = div_y if div_y < 1 else div_y / 100
            add("Dividend Yield", "pass" if dy > 0.025 else "warn", f"{dy*100:.2f}%",
            "Meaningful yield" if dy > 0.025 else "Modest yield", bucket="Financial Strength")

    # Verdict
    scored = [c for c in checks.values() if c["status"] in ("pass", "warn", "fail")]
    n_pass = sum(1 for c in scored if c["status"] == "pass")
    n_fail = sum(1 for c in scored if c["status"] == "fail")
    n = len(scored)

    if n == 0:
        verdict = ("na", "Insufficient data for a fundamental view")
    elif n_fail >= n * 0.45:
        verdict = ("fail", "Multiple weak checks — fundamentals need careful review")
    elif n_pass >= n * 0.65:
        verdict = ("pass", "Fundamental profile is generally constructive")
    else:
        verdict = ("warn", "Mixed signals across fundamentals")

    return {
        "checks": checks,
        "verdict": verdict,
        "summary": {
            "pass": n_pass,
            "fail": n_fail,
            "total": n,
            "groups": bucket_totals,
        },
    }
