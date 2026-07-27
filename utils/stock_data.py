"""
Stock data layer — yfinance wrappers for the Forward Test / Stock Analysis page.

Technical checks mirror the FX Evolution / Wyckoff strategy:
  EMA 21/50, SMA 200 (daily + weekly), RSI 14, OBV, volume-spike (capitulation) detection.

Fundamental checks are a crude Buffett/Burry value prequalification:
  traffic-light pass/warn/fail on valuation, profitability, balance-sheet strength.
"""
import pandas as pd
import numpy as np


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


# ─── FUNDAMENTALS (Buffett / Burry crude prequalification) ────────────────────

def fundamental_checks(info: dict) -> dict:
    """
    Crude value-investing prequalification. Each check returns
    (status, value_display, explanation) where status ∈ pass/warn/fail/na.
    """
    def g(key, default=None):
        v = info.get(key, default)
        return v if v is not None else default

    checks = {}

    def add(name, status, value, note):
        checks[name] = {"status": status, "value": value, "note": note}

    # ── Valuation (Burry: cheap relative to earnings/book) ───────────────────
    pe = g("trailingPE")
    if pe is None or pe <= 0:
        add("P/E Ratio", "na" if pe is None else "warn",
            "n/a" if pe is None else f"{pe:.1f}",
            "No earnings (or negative) — speculative for a value approach" if pe is not None else "No data")
    elif pe < 15:
        add("P/E Ratio", "pass", f"{pe:.1f}", "Cheap earnings multiple (<15) — classic value zone")
    elif pe < 25:
        add("P/E Ratio", "warn", f"{pe:.1f}", "Fair (15–25) — pay attention to growth justification")
    else:
        add("P/E Ratio", "fail", f"{pe:.1f}", "Expensive (>25) — priced for growth, little margin of safety")

    pb = g("priceToBook")
    if pb is None:
        add("P/B Ratio", "na", "n/a", "No data")
    elif pb < 1.5:
        add("P/B Ratio", "pass", f"{pb:.2f}", "Below 1.5 — Burry-style asset value support")
    elif pb < 3:
        add("P/B Ratio", "warn", f"{pb:.2f}", "Moderate — book value gives limited downside cover")
    else:
        add("P/B Ratio", "fail", f"{pb:.2f}", "High multiple to book — asset backing thin")

    peg = g("pegRatio") or g("trailingPegRatio")
    if peg is not None:
        if 0 < peg < 1:
            add("PEG Ratio", "pass", f"{peg:.2f}", "Growth cheaper than the multiple implies")
        elif 0 < peg < 2:
            add("PEG Ratio", "warn", f"{peg:.2f}", "Fairly priced growth")
        else:
            add("PEG Ratio", "fail", f"{peg:.2f}", "Paying up for growth")

    # ── Profitability / moat (Buffett: consistent high returns) ──────────────
    roe = g("returnOnEquity")
    if roe is None:
        add("Return on Equity", "na", "n/a", "No data")
    elif roe > 0.15:
        add("Return on Equity", "pass", f"{roe*100:.1f}%", "Above 15% — Buffett quality threshold")
    elif roe > 0.08:
        add("Return on Equity", "warn", f"{roe*100:.1f}%", "Modest returns on equity")
    else:
        add("Return on Equity", "fail", f"{roe*100:.1f}%", "Weak returns — capital not compounding well")

    margins = g("profitMargins")
    if margins is None:
        add("Net Margin", "na", "n/a", "No data")
    elif margins > 0.15:
        add("Net Margin", "pass", f"{margins*100:.1f}%", "Fat margins — pricing power / moat signal")
    elif margins > 0.05:
        add("Net Margin", "warn", f"{margins*100:.1f}%", "Average margins")
    else:
        add("Net Margin", "fail", f"{margins*100:.1f}%", "Thin or negative margins")

    fcf = g("freeCashflow")
    if fcf is None:
        add("Free Cash Flow", "na", "n/a", "No data")
    elif fcf > 0:
        mcap = g("marketCap")
        yield_txt = f" (FCF yield {fcf/mcap*100:.1f}%)" if mcap else ""
        add("Free Cash Flow", "pass", f"{fcf/1e6:,.0f}M{yield_txt}", "Positive FCF — the business funds itself")
    else:
        add("Free Cash Flow", "fail", f"{fcf/1e6:,.0f}M", "Burning cash")

    # ── Balance sheet (Burry: survivability) ─────────────────────────────────
    de = g("debtToEquity")
    if de is None:
        add("Debt / Equity", "na", "n/a", "No data")
    else:
        de_ratio = de / 100 if de > 10 else de   # yfinance often returns percentage
        if de_ratio < 0.5:
            add("Debt / Equity", "pass", f"{de_ratio:.2f}", "Low leverage — sleeps well at night")
        elif de_ratio < 1.0:
            add("Debt / Equity", "warn", f"{de_ratio:.2f}", "Moderate leverage")
        else:
            add("Debt / Equity", "fail", f"{de_ratio:.2f}", "High leverage — fragile in downturns")

    cr = g("currentRatio")
    if cr is None:
        add("Current Ratio", "na", "n/a", "No data")
    elif cr > 1.5:
        add("Current Ratio", "pass", f"{cr:.2f}", "Comfortable short-term liquidity")
    elif cr > 1.0:
        add("Current Ratio", "warn", f"{cr:.2f}", "Adequate but tight liquidity")
    else:
        add("Current Ratio", "fail", f"{cr:.2f}", "Current liabilities exceed current assets")

    # ── Growth sanity ────────────────────────────────────────────────────────
    rev_g = g("revenueGrowth")
    if rev_g is not None:
        if rev_g > 0.10:
            add("Revenue Growth", "pass", f"{rev_g*100:+.1f}%", "Healthy top-line growth")
        elif rev_g > 0:
            add("Revenue Growth", "warn", f"{rev_g*100:+.1f}%", "Slow growth")
        else:
            add("Revenue Growth", "fail", f"{rev_g*100:+.1f}%", "Shrinking revenue")

    div_y = g("dividendYield")
    if div_y:
        dy = div_y if div_y < 1 else div_y / 100
        add("Dividend Yield", "pass" if dy > 0.03 else "warn", f"{dy*100:.2f}%",
            "Meaningful income" if dy > 0.03 else "Modest income")

    # ── Verdict ──────────────────────────────────────────────────────────────
    scored = [c for c in checks.values() if c["status"] in ("pass", "warn", "fail")]
    n_pass = sum(1 for c in scored if c["status"] == "pass")
    n_fail = sum(1 for c in scored if c["status"] == "fail")
    n = len(scored)

    if n == 0:
        verdict = ("na", "Insufficient data")
    elif n_fail == 0 and n_pass >= n * 0.6:
        verdict = ("pass", "Good fundamentals — value prequalification passed")
    elif n_fail >= n * 0.4:
        verdict = ("fail", "Weak fundamentals — fails crude value screen")
    else:
        verdict = ("warn", "Mixed fundamentals — needs a judgement call")

    return {"checks": checks, "verdict": verdict,
            "summary": {"pass": n_pass, "fail": n_fail, "total": n}}
