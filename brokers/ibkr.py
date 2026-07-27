"""
Interactive Brokers (IBKR) Activity Statement CSV parser.
Handles the multi-section format exported via:
  Reports → Activity → Activity Statement → CSV
Supports: Stock (ASX + US), Options. Forex rows (currency conversions) are skipped.
"""
import csv
import io
import re
import json
from collections import defaultdict


def _safe_float(val, default=0.0) -> float:
    try:
        return float(str(val).replace(",", "").strip())
    except Exception:
        return default


def _parse_trade_time(raw: str) -> str | None:
    """
    IBKR embeds a newline + timezone inside quoted Trade Time fields:
      '2025-10-08\n11:37:33, Australia/Sydney'  →  '2025-10-08 11:37:33'
    """
    if not raw:
        return None
    # Replace embedded newline, strip trailing timezone label
    cleaned = raw.replace("\n", " ").replace("\r", "")
    # Remove ", Timezone/Name" suffix
    cleaned = re.sub(r",\s*[A-Za-z_/]+$", "", cleaned).strip()
    try:
        import pandas as pd
        return pd.to_datetime(cleaned).isoformat()
    except Exception:
        return cleaned or None


def _extract_ticker(symbol_raw: str) -> str:
    """
    'POINTERRA LTD (3DP.AU)'  →  '3DP.AU'
    'Global X Uranium ETF (URA)'  →  'URA'
    'PayPal (PYPL 20251219 CALL 70.0)'  →  'PYPL 20251219 CALL 70.0'
    Falls back to the full name if no parentheses found.
    """
    m = re.search(r"\(([^)]+)\)$", symbol_raw.strip())
    return m.group(1).strip() if m else symbol_raw.strip()


def _col(row: list, headers: list, name: str, default=""):
    """Return row value by column name from headers list (offset by 4 fixed cols)."""
    try:
        idx = headers.index(name) + 4
        return row[idx] if idx < len(row) else default
    except ValueError:
        return default


def parse_ibkr_csv(file_content: bytes) -> list[dict]:
    """
    Parse IBKR Activity Statement CSV.
    Returns list of trade dicts compatible with upsert_trade_from_broker.
    """
    text = file_content.decode("utf-8-sig", errors="replace")  # utf-8-sig strips BOM
    reader = csv.reader(io.StringIO(text))
    all_rows = list(reader)

    # ── Collect all Trades DATA rows, tracking the current header per asset class ──
    # Header rows:  row[0]=="Trades", row[1] in ("","Stock","Forex","Option"),
    #               row[3] == "" (not DATA/TOTAL), row[4] == "Symbol" or "Symbol(Base.Quote)"
    # DATA rows:    row[3] == "DATA"

    current_headers: list[str] = []
    stock_rows: list[tuple[list, list]] = []   # (data_row, headers_snapshot)
    option_rows: list[tuple[list, list]] = []

    for row in all_rows:
        if not row or row[0] != "Trades":
            continue
        row_type = row[3] if len(row) > 3 else ""
        asset    = row[1] if len(row) > 1 else ""

        # Detect header rows (col[3] is empty, col[4] is "Symbol" or "Symbol(Base.Quote)")
        if row_type not in ("DATA", "TOTAL", "HEADER_DATA") and len(row) > 4 and "Symbol" in row[4]:
            current_headers = [c.strip() for c in row[4:]]
            continue

        if row_type != "DATA":
            continue

        if asset == "Stock":
            stock_rows.append((row, list(current_headers)))
        elif asset == "Option":
            option_rows.append((row, list(current_headers)))
        # Forex rows (currency conversions) are skipped

    trades = _parse_stock_trades(stock_rows)
    trades += _parse_option_trades(option_rows)
    return trades


def _parse_stock_trades(rows: list[tuple]) -> list[dict]:
    """
    Group Open/Close DATA rows by ticker and FIFO-pair them into complete trades.
    Multiple Opens before a Close → weighted-average entry, summed commissions.
    """
    from collections import deque

    # Group rows by (ticker, currency) so AU and US stocks don't collide
    groups: dict[tuple, list] = defaultdict(list)
    for row, headers in rows:
        sym_raw  = _col(row, headers, "Symbol", row[4] if len(row) > 4 else "")
        currency = row[-1].strip() if row else "AUD"
        ticker   = _extract_ticker(sym_raw)
        groups[(ticker, currency)].append((row, headers, sym_raw))

    result = []
    for (ticker, currency), group_rows in groups.items():
        # Build chronological list of Open/Close events
        events = []
        for row, headers, sym_raw in group_rows:
            activity = _col(row, headers, "Activity Type", "").strip()
            qty_raw  = _col(row, headers, "Quantity", "0")
            qty      = _safe_float(qty_raw)
            price    = _safe_float(_col(row, headers, "Trade Price", "0"))
            amount   = _safe_float(_col(row, headers, "Amount", "0"))
            pnl      = _safe_float(_col(row, headers, "Realized P/L", "0"))
            trade_time = _parse_trade_time(_col(row, headers, "Trade Time", ""))
            settle_date = _col(row, headers, "Settle Date", "")

            # Commission: sum Brokerage fee, Commission, SEC fee, GST — all negative
            comm = 0.0
            for fee_col in ("Brokerage fee", "Commission", "SEC Fee", "GST",
                            "Settlement Fee", "Clearing Fee"):
                comm += abs(_safe_float(_col(row, headers, fee_col, "0")))

            market = _col(row, headers, "Market", "")
            exchange = _col(row, headers, "Exchange", "")

            events.append({
                "activity": activity,
                "qty": qty,
                "price": price,
                "amount": amount,
                "pnl": pnl,
                "comm": comm,
                "time": trade_time,
                "settle": settle_date,
                "currency": currency,
                "sym_raw": sym_raw,
                "market": market,
                "exchange": exchange,
                "row_json": json.dumps(row, default=str),
            })

        # Sort events chronologically
        events.sort(key=lambda e: e["time"] or "")

        # FIFO queue of open lots
        open_queue: deque = deque()

        for ev in events:
            activity = ev["activity"].lower()
            qty      = abs(ev["qty"])

            if "open" in activity or (ev["qty"] > 0 and "close" not in activity):
                open_queue.append({**ev, "qty": qty})

            elif "close" in activity or ev["qty"] < 0:
                # Consume opens (FIFO) to build a closed trade
                remaining_qty = qty
                matched_opens = []
                total_open_comm = 0.0

                while open_queue and remaining_qty > 0:
                    oldest = open_queue[0]
                    if oldest["qty"] <= remaining_qty:
                        matched_opens.append(oldest)
                        remaining_qty -= oldest["qty"]
                        total_open_comm += oldest["comm"]
                        open_queue.popleft()
                    else:
                        # Partial consume
                        frac = remaining_qty / oldest["qty"]
                        partial = dict(oldest)
                        partial["qty"] = remaining_qty
                        partial["comm"] = oldest["comm"] * frac
                        matched_opens.append(partial)
                        oldest["qty"] -= remaining_qty
                        oldest["comm"] -= partial["comm"]
                        total_open_comm += partial["comm"]
                        remaining_qty = 0

                if matched_opens:
                    # Weighted avg entry price
                    total_open_qty = sum(o["qty"] for o in matched_opens)
                    avg_entry = (
                        sum(o["price"] * o["qty"] for o in matched_opens) / total_open_qty
                        if total_open_qty > 0 else matched_opens[0]["price"]
                    )
                    entry_time  = matched_opens[0]["time"]
                    total_comm  = total_open_comm + ev["comm"]
                    symbol_disp = ticker.replace(".AU", "") if ".AU" in ticker else ticker

                    result.append({
                        "broker":         "IBKR",
                        "broker_trade_id": f"IBKR_{ticker}_{entry_time}",
                        "symbol":         symbol_disp,
                        "direction":      "LONG",   # stock buys are always long
                        "entry_price":    round(avg_entry, 6),
                        "exit_price":     ev["price"],
                        "entry_time":     entry_time,
                        "exit_time":      ev["time"],
                        "quantity":       total_open_qty,
                        "pnl":            ev["pnl"] if ev["pnl"] != 0 else (
                            abs(ev["amount"]) - sum(o["qty"] * o["price"] for o in matched_opens)
                        ),
                        "commission":     round(total_comm, 4),
                        "swap":           0.0,
                        "status":         "closed",
                        "raw_data":       ev["row_json"],
                    })
                else:
                    # Close with no matching open — treat as standalone
                    symbol_disp = ticker.replace(".AU", "") if ".AU" in ticker else ticker
                    result.append({
                        "broker":         "IBKR",
                        "broker_trade_id": f"IBKR_{ticker}_{ev['time']}",
                        "symbol":         symbol_disp,
                        "direction":      "SHORT",
                        "entry_price":    ev["price"],
                        "exit_price":     None,
                        "entry_time":     ev["time"],
                        "exit_time":      None,
                        "quantity":       qty,
                        "pnl":            ev["pnl"],
                        "commission":     ev["comm"],
                        "swap":           0.0,
                        "status":         "closed",
                        "raw_data":       ev["row_json"],
                    })

        # Remaining in the open queue → open positions
        for lot in open_queue:
            symbol_disp = ticker.replace(".AU", "") if ".AU" in ticker else ticker
            result.append({
                "broker":         "IBKR",
                "broker_trade_id": f"IBKR_{ticker}_{lot['time']}",
                "symbol":         symbol_disp,
                "direction":      "LONG",
                "entry_price":    lot["price"],
                "exit_price":     None,
                "entry_time":     lot["time"],
                "exit_time":      None,
                "quantity":       lot["qty"],
                "pnl":            0.0,
                "commission":     lot["comm"],
                "swap":           0.0,
                "status":         "open",
                "raw_data":       lot["row_json"],
            })

    return result


def _parse_option_trades(rows: list[tuple]) -> list[dict]:
    """Parse option Open/Close rows — same structure as stocks."""
    result = []
    for row, headers, in [(r, h) for r, h in rows]:
        sym_raw  = _col(row, headers, "Symbol", row[4] if len(row) > 4 else "")
        activity = _col(row, headers, "Activity Type", "").strip().lower()
        qty      = abs(_safe_float(_col(row, headers, "Quantity", "0")))
        price    = _safe_float(_col(row, headers, "Trade Price", "0"))
        pnl      = _safe_float(_col(row, headers, "Realized P/L", "0"))
        trade_time = _parse_trade_time(_col(row, headers, "Trade Time", ""))
        comm     = abs(_safe_float(_col(row, headers, "Commission", "0")))
        currency = row[-1].strip() if row else "USD"

        ticker = _extract_ticker(sym_raw)
        status = "closed" if "close" in activity else "open"

        result.append({
            "broker":         "IBKR",
            "broker_trade_id": f"IBKR_OPT_{ticker}_{trade_time}",
            "symbol":         ticker,
            "direction":      "LONG" if "open" in activity else "SHORT",
            "entry_price":    price if "open" in activity else None,
            "exit_price":     price if "close" in activity else None,
            "entry_time":     trade_time if "open" in activity else None,
            "exit_time":      trade_time if "close" in activity else None,
            "quantity":       qty,
            "pnl":            pnl,
            "commission":     comm,
            "swap":           0.0,
            "status":         status,
            "raw_data":       json.dumps(row, default=str),
        })
    return result
