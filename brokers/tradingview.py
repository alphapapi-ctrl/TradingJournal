"""
TradingView Paper Trading CSV parser.
Handles the transaction/history export from TradingView paper portfolios.

TradingView export formats vary slightly; this parser is tolerant of:
  - "History" export:  Symbol, Side, Type, Qty, Limit Price, Stop Price,
                       Fill Price, Status, Commission, Placing Time, Closing Time, Order ID
  - "Trading history": Time, Symbol, Side, Type, Quantity, Price, Order ID
  - Position exports:  Symbol, Side, Qty, Avg Fill Price, ...

Fills are FIFO-paired per symbol into round-trip trades.
Buy → open lot; Sell → consumes open lots (LONG trades).
Sells with no prior buys are treated as opening SHORT lots consumed by later buys.
"""
import csv
import io
import json
import re
from collections import defaultdict, deque


def _safe_float(val, default=0.0):
    try:
        v = str(val).replace(",", "").replace("$", "").strip()
        return float(v) if v not in ("", "-", "N/A") else default
    except Exception:
        return default


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _find_col(headers_norm: list, *candidates) -> int | None:
    """Return index of first header matching any candidate (normalised)."""
    for cand in candidates:
        c = _norm(cand)
        for i, h in enumerate(headers_norm):
            if h == c:
                return i
    # fallback: contains match
    for cand in candidates:
        c = _norm(cand)
        for i, h in enumerate(headers_norm):
            if c in h:
                return i
    return None


def _clean_symbol(sym: str) -> str:
    """'ASX:BHP' → 'BHP.AX' style normalisation kept simple: strip exchange prefix."""
    sym = str(sym).strip().upper()
    if ":" in sym:
        exch, tick = sym.split(":", 1)
        # Keep exchange info in a suffix style consistent with the journal
        if exch in ("ASX",):
            return tick  # store bare; yfinance lookup adds .AX later using exchange hint
        return tick
    return sym


def _exchange_of(sym_raw: str) -> str:
    sym = str(sym_raw).strip().upper()
    if ":" in sym:
        return sym.split(":", 1)[0]
    return ""


def _parse_time(raw: str) -> str | None:
    if not raw or str(raw).strip() in ("", "-"):
        return None
    try:
        import pandas as pd
        return pd.to_datetime(str(raw).strip()).isoformat()
    except Exception:
        return str(raw).strip() or None


def parse_tradingview_csv(file_content: bytes) -> list[dict]:
    """
    Parse a TradingView paper-trading transactions CSV.
    Returns trade dicts compatible with upsert_trade_from_broker.
    """
    text = file_content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("Empty TradingView CSV")

    headers = rows[0]
    hn = [_norm(h) for h in headers]

    i_symbol = _find_col(hn, "symbol")
    i_side   = _find_col(hn, "side")
    i_qty    = _find_col(hn, "qty", "quantity", "filled qty")
    i_price  = _find_col(hn, "fill price", "avg fill price", "price")
    i_status = _find_col(hn, "status")
    i_comm   = _find_col(hn, "commission", "fees")
    i_time   = _find_col(hn, "closing time", "fill time", "time", "placing time", "date")
    i_oid    = _find_col(hn, "order id", "id")

    if i_symbol is None or i_side is None:
        raise ValueError(
            f"Unrecognised TradingView CSV — need at least Symbol and Side columns, got: {headers}"
        )

    # ── Collect fills ─────────────────────────────────────────────────────────
    fills = []
    for row in rows[1:]:
        if len(row) <= max(i_symbol, i_side):
            continue
        status = row[i_status].strip().lower() if (i_status is not None and i_status < len(row)) else "filled"
        # Only count executed orders
        if status and status not in ("filled", "executed", "done", ""):
            continue

        sym_raw = row[i_symbol]
        side    = row[i_side].strip().lower()
        if side not in ("buy", "sell"):
            continue
        qty   = abs(_safe_float(row[i_qty]))   if (i_qty   is not None and i_qty   < len(row)) else 0
        price = _safe_float(row[i_price])      if (i_price is not None and i_price < len(row)) else 0
        if qty <= 0 or price <= 0:
            continue

        fills.append({
            "symbol":   _clean_symbol(sym_raw),
            "exchange": _exchange_of(sym_raw),
            "side":     side,
            "qty":      qty,
            "price":    price,
            "comm":     abs(_safe_float(row[i_comm])) if (i_comm is not None and i_comm < len(row)) else 0.0,
            "time":     _parse_time(row[i_time]) if (i_time is not None and i_time < len(row)) else None,
            "order_id": row[i_oid].strip() if (i_oid is not None and i_oid < len(row)) else "",
            "raw":      json.dumps(row, default=str),
        })

    if not fills:
        raise ValueError("No filled buy/sell orders found in TradingView CSV")

    # TradingView exports newest-first. Detect ordering so that fills sharing the
    # same timestamp (e.g. a buy and sell in the same minute) keep true sequence
    # after the stable sort below.
    timed = [f["time"] for f in fills if f["time"]]
    if len(timed) >= 2 and timed[0] > timed[-1]:
        fills.reverse()

    # Sort chronologically. On equal timestamps process buys before sells —
    # stock/ETF portfolios are long-biased, so a same-minute buy+sell is a
    # round-trip long, and sells always close open share lots FIFO.
    fills.sort(key=lambda f: (f["time"] is None, f["time"] or "", 0 if f["side"] == "buy" else 1))

    # ── FIFO pair per symbol ──────────────────────────────────────────────────
    by_symbol = defaultdict(list)
    for f in fills:
        by_symbol[f["symbol"]].append(f)

    trades = []
    for symbol, sym_fills in by_symbol.items():
        exchange = next((f["exchange"] for f in sym_fills if f["exchange"]), "")
        long_lots: deque = deque()   # open buys awaiting sells
        short_lots: deque = deque()  # open sells awaiting buys (short trades)

        def close_against(lots, closing_fill, direction):
            """Consume open lots FIFO with the closing fill; emit closed trades."""
            remaining = closing_fill["qty"]
            matched, open_comm = [], 0.0
            while lots and remaining > 0:
                lot = lots[0]
                take = min(lot["qty"], remaining)
                frac = take / lot["qty"]
                matched.append({**lot, "qty": take, "comm": lot["comm"] * frac})
                open_comm += lot["comm"] * frac
                lot["qty"]  -= take
                lot["comm"] -= lot["comm"] * frac
                remaining   -= take
                if lot["qty"] <= 1e-9:
                    lots.popleft()
            if not matched:
                return remaining
            tq = sum(m["qty"] for m in matched)
            avg_entry = sum(m["price"] * m["qty"] for m in matched) / tq
            exit_price = closing_fill["price"]
            if direction == "LONG":
                pnl = (exit_price - avg_entry) * tq
            else:
                pnl = (avg_entry - exit_price) * tq
            close_comm = closing_fill["comm"] * (tq / closing_fill["qty"] if closing_fill["qty"] else 1)
            trades.append({
                "broker":          "TradingView",
                "broker_trade_id": f"TV_{symbol}_{matched[0]['time']}_{closing_fill['time']}",
                "symbol":          symbol,
                "direction":       direction,
                "entry_price":     round(avg_entry, 6),
                "exit_price":      exit_price,
                "entry_time":      matched[0]["time"],
                "exit_time":       closing_fill["time"],
                "quantity":        tq,
                "pnl":             round(pnl - open_comm - close_comm, 4),
                "commission":      round(open_comm + close_comm, 4),
                "swap":            0.0,
                "status":          "closed",
                "raw_data":        json.dumps({"exchange": exchange, "close": closing_fill["order_id"]}),
            })
            return remaining

        for f in sym_fills:
            if f["side"] == "buy":
                # First close any open shorts, remainder opens a long lot
                rem = close_against(short_lots, f, "SHORT") if short_lots else f["qty"]
                if rem > 1e-9:
                    frac = rem / f["qty"] if f["qty"] else 1
                    long_lots.append({**f, "qty": rem, "comm": f["comm"] * frac})
            else:  # sell
                rem = close_against(long_lots, f, "LONG") if long_lots else f["qty"]
                if rem > 1e-9:
                    frac = rem / f["qty"] if f["qty"] else 1
                    short_lots.append({**f, "qty": rem, "comm": f["comm"] * frac})

        # Remaining open lots → open positions
        for lot in long_lots:
            trades.append(_open_trade(symbol, exchange, lot, "LONG"))
        for lot in short_lots:
            trades.append(_open_trade(symbol, exchange, lot, "SHORT"))

    return trades


def _open_trade(symbol, exchange, lot, direction):
    return {
        "broker":          "TradingView",
        "broker_trade_id": f"TV_{symbol}_{lot['time']}_open",
        "symbol":          symbol,
        "direction":       direction,
        "entry_price":     lot["price"],
        "exit_price":      None,
        "entry_time":      lot["time"],
        "exit_time":       None,
        "quantity":        round(lot["qty"], 6),
        "pnl":             0.0,
        "commission":      round(lot["comm"], 4),
        "swap":            0.0,
        "status":          "open",
        "raw_data":        json.dumps({"exchange": exchange}),
    }
