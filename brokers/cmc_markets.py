"""
CMC Markets statement CSV parser.

CMC exports a cash ledger, not a trade list. We reconstruct closed trades
using FIFO lot matching:
  CB  (Cash Buy)   → open a LONG lot
  CS  (Cash Sell)  → close oldest LONG lot(s) FIFO → realised P&L per matched parcel
  JG ]CYP BUY      → open a crypto LONG lot
  JG ]CYP SELL     → close crypto lot(s)
  JG dividend      → income entry (stored as a separate "dividend" trade)
  JG SMS fee       → commission, attached to nearest preceding CB

Returned records match the standard broker dict used by upsert_trade_from_broker().
"""

import csv
import re
import io
import json
from datetime import datetime
from collections import defaultdict


# ── Regex patterns ────────────────────────────────────────────────────────────

RE_BUY  = re.compile(r'Bght\s+([\d,]+)\s+(\S+)\s+@\s+([\d.]+)\s+(\S+)', re.I)
RE_SELL = re.compile(r'Sold\s+([\d,]+)\s+(\S+)\s+@\s+([\d.]+)\s+(\S+)', re.I)
RE_CYP_BUY  = re.compile(r'\]CYP\s+BUY\s+(\S+)\s+(\d+)', re.I)
RE_CYP_SELL = re.compile(r'\]CYP\s+SELL\s+(\S+)\s+(\d+)', re.I)
RE_DIV  = re.compile(r'([\S]+)\s+Intl\s+Div', re.I)
RE_SMS  = re.compile(r'SMS Confirmation', re.I)


def _parse_date(s: str) -> str:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return s.strip()


def _safe_float(s: str) -> float:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def parse_cmc_csv(file_content: bytes) -> list[dict]:
    """
    Parse a CMC Markets statement CSV and return a list of trade dicts
    compatible with upsert_trade_from_broker().
    """
    text = file_content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    # Normalise column names (strip whitespace and BOM)
    clean_rows = []
    for r in rows:
        clean_rows.append({k.strip().lstrip("\ufeff"): v.strip() for k, v in r.items()})
    rows = clean_rows

    trades = []

    # FIFO lot queues per symbol: deque of dicts with entry info
    # Each lot: {date, ref, qty, price, currency, amount_aud, commission}
    lots: dict[str, list[dict]] = defaultdict(list)

    # Pending SMS fees (attach to the most recent CB before them)
    # We accumulate and drain when we see a CS for that symbol
    pending_fees: dict[str, float] = defaultdict(float)

    # Track last CB reference per account (for SMS fee attribution)
    last_cb_refs: list[str] = []

    for row in rows:
        tx_type = row.get("Type", "").strip()
        date_str = row.get("Date", "").strip()
        ref      = row.get("Reference", "").strip()
        desc     = row.get("Description", "").strip()
        debit    = _safe_float(row.get("Debit $", 0))
        credit   = _safe_float(row.get("Credit $", 0))

        parsed_date = _parse_date(date_str)

        # ── SMS fee (JG, not dividend, not crypto) ────────────────────────
        if tx_type == "JG" and RE_SMS.search(desc):
            # Will be attributed to buys globally (small ~$0.33 each)
            # We'll distribute at the end or ignore — too small to track per-trade
            # Store as global commission pool
            pending_fees["__global__"] += debit
            continue

        # ── Dividend (JG with "Intl Div") ────────────────────────────────
        if tx_type == "JG" and "Intl Div" in desc:
            m = RE_DIV.search(desc)
            sym = m.group(1).strip() if m else "DIVIDEND"
            trades.append({
                "broker":           "CMC Markets",
                "broker_trade_id":  ref,
                "symbol":           sym,
                "direction":        "LONG",
                "entry_price":      0.0,
                "exit_price":       0.0,
                "entry_time":       parsed_date,
                "exit_time":        parsed_date,
                "quantity":         0.0,
                "pnl":              credit,
                "commission":       0.0,
                "swap":             0.0,
                "status":           "closed",
                "trade_type":       "dividend",
                "raw_data":         json.dumps(row),
            })
            continue

        # ── NPP / bank deposit (JG with DEP) ─────────────────────────────
        if tx_type == "JG" and ("]NPP" in desc or "]CYP" not in desc):
            # Fund deposits / other journal entries — skip unless crypto
            if "]CYP" not in desc:
                continue

        # ── Cash Buy (CB) → open LONG lot ────────────────────────────────
        if tx_type == "CB":
            m = RE_BUY.match(desc)
            if m:
                qty      = _safe_float(m.group(1))
                symbol   = m.group(2).strip().upper()
                price    = _safe_float(m.group(3))
                currency = m.group(4).strip()
                lots[symbol].append({
                    "date":       parsed_date,
                    "ref":        ref,
                    "qty":        qty,
                    "price":      price,
                    "currency":   currency,
                    "amount_aud": debit,
                    "commission": 0.0,
                    "raw":        row,
                })
            continue

        # ── Crypto Buy (JG ]CYP BUY) → open LONG lot ─────────────────────
        if tx_type == "JG" and "]CYP BUY" in desc.upper():
            m = RE_CYP_BUY.search(desc)
            if m:
                raw_sym  = m.group(1).strip().upper()  # e.g. UNI/USD
                symbol   = raw_sym.replace("/USD", "").replace("/AUD", "")
                cyp_ref  = m.group(2)
                price    = debit / 1 if debit else 0  # no qty in description — use amount
                lots[symbol].append({
                    "date":       parsed_date,
                    "ref":        ref,
                    "qty":        1.0,            # quantity unknown from CMC crypto entry
                    "price":      debit,          # store cost as price, qty=1
                    "currency":   "AUD",
                    "amount_aud": debit,
                    "commission": 0.0,
                    "raw":        row,
                    "is_crypto":  True,
                    "cyp_ref":    cyp_ref,
                })
            continue

        # ── Cash Sell (CS) → close FIFO lots ─────────────────────────────
        if tx_type == "CS":
            m = RE_SELL.match(desc)
            if not m:
                continue
            sell_qty    = _safe_float(m.group(1))
            symbol      = m.group(2).strip().upper()
            sell_price  = _safe_float(m.group(3))
            currency    = m.group(4).strip()
            proceeds    = credit

            remaining_sell_qty = sell_qty
            symbol_lots = lots.get(symbol, [])

            if not symbol_lots:
                # Sell with no matching buy — could be short or data gap
                trades.append({
                    "broker":           "CMC Markets",
                    "broker_trade_id":  ref,
                    "symbol":           symbol,
                    "direction":        "SHORT",
                    "entry_price":      sell_price,
                    "exit_price":       None,
                    "entry_time":       parsed_date,
                    "exit_time":        None,
                    "quantity":         sell_qty,
                    "pnl":              0.0,
                    "commission":       0.0,
                    "swap":             0.0,
                    "status":           "open",
                    "trade_type":       "equity",
                    "raw_data":         json.dumps(row),
                })
                continue

            while remaining_sell_qty > 0 and symbol_lots:
                lot = symbol_lots[0]
                lot_qty = lot["qty"]

                if lot_qty <= remaining_sell_qty:
                    # Fully consume this lot
                    matched_qty   = lot_qty
                    entry_price   = lot["price"]
                    entry_amount  = lot["amount_aud"]
                    entry_date    = lot["date"]
                    entry_ref     = lot["ref"]
                    lot_commission = lot.get("commission", 0.0)

                    # Proportional proceeds for this lot
                    lot_proceeds  = proceeds * (matched_qty / sell_qty) if sell_qty else proceeds
                    lot_pnl       = lot_proceeds - entry_amount * (matched_qty / lot_qty)

                    trades.append({
                        "broker":           "CMC Markets",
                        "broker_trade_id":  f"{entry_ref}-{ref}",
                        "symbol":           symbol,
                        "direction":        "LONG",
                        "entry_price":      entry_price,
                        "exit_price":       sell_price,
                        "entry_time":       entry_date,
                        "exit_time":        parsed_date,
                        "quantity":         matched_qty,
                        "pnl":              round(lot_pnl, 2),
                        "commission":       round(lot_commission, 2),
                        "swap":             0.0,
                        "status":           "closed",
                        "trade_type":       "equity",
                        "currency":         currency,
                        "raw_data":         json.dumps({"buy": lot["raw"], "sell": row}),
                    })

                    remaining_sell_qty -= matched_qty
                    symbol_lots.pop(0)

                else:
                    # Partially consume this lot
                    matched_qty   = remaining_sell_qty
                    entry_price   = lot["price"]
                    entry_amount  = lot["amount_aud"]
                    entry_date    = lot["date"]
                    entry_ref     = lot["ref"]

                    lot_proceeds  = proceeds * (matched_qty / sell_qty) if sell_qty else proceeds
                    partial_cost  = entry_amount * (matched_qty / lot_qty)
                    lot_pnl       = lot_proceeds - partial_cost

                    trades.append({
                        "broker":           "CMC Markets",
                        "broker_trade_id":  f"{entry_ref}-{ref}-p",
                        "symbol":           symbol,
                        "direction":        "LONG",
                        "entry_price":      entry_price,
                        "exit_price":       sell_price,
                        "entry_time":       entry_date,
                        "exit_time":        parsed_date,
                        "quantity":         matched_qty,
                        "pnl":              round(lot_pnl, 2),
                        "commission":       0.0,
                        "swap":             0.0,
                        "status":           "closed",
                        "trade_type":       "equity",
                        "currency":         currency,
                        "raw_data":         json.dumps({"buy": lot["raw"], "sell": row}),
                    })

                    # Reduce remaining lot quantity
                    lot["qty"]        -= matched_qty
                    lot["amount_aud"] -= partial_cost
                    remaining_sell_qty = 0

            continue

        # ── Crypto Sell (JG ]CYP SELL) ────────────────────────────────────
        if tx_type == "JG" and "]CYP SELL" in desc.upper():
            m = RE_CYP_SELL.search(desc)
            if m:
                raw_sym = m.group(1).strip().upper()
                symbol  = raw_sym.replace("/USD", "").replace("/AUD", "")
                symbol_lots = lots.get(symbol, [])

                if symbol_lots:
                    lot = symbol_lots.pop(0)
                    pnl = credit - lot["amount_aud"]
                    trades.append({
                        "broker":           "CMC Markets",
                        "broker_trade_id":  f"{lot['ref']}-{ref}",
                        "symbol":           symbol,
                        "direction":        "LONG",
                        "entry_price":      lot["amount_aud"],
                        "exit_price":       credit,
                        "entry_time":       lot["date"],
                        "exit_time":        parsed_date,
                        "quantity":         1.0,
                        "pnl":              round(pnl, 2),
                        "commission":       0.0,
                        "swap":             0.0,
                        "status":           "closed",
                        "trade_type":       "crypto",
                        "currency":         "AUD",
                        "raw_data":         json.dumps({"buy": lot["raw"], "sell": row}),
                    })
                else:
                    trades.append({
                        "broker":           "CMC Markets",
                        "broker_trade_id":  ref,
                        "symbol":           symbol,
                        "direction":        "LONG",
                        "entry_price":      0.0,
                        "exit_price":       credit,
                        "entry_time":       parsed_date,
                        "exit_time":        parsed_date,
                        "quantity":         1.0,
                        "pnl":              credit,
                        "commission":       0.0,
                        "swap":             0.0,
                        "status":           "closed",
                        "trade_type":       "crypto",
                        "currency":         "AUD",
                        "raw_data":         json.dumps(row),
                    })
            continue

    # ── Add any still-open lots as open trades ────────────────────────────
    for symbol, open_lots in lots.items():
        for lot in open_lots:
            trades.append({
                "broker":           "CMC Markets",
                "broker_trade_id":  lot["ref"],
                "symbol":           symbol,
                "direction":        "LONG",
                "entry_price":      lot["price"],
                "exit_price":       None,
                "entry_time":       lot["date"],
                "exit_time":        None,
                "quantity":         lot["qty"],
                "pnl":              0.0,
                "commission":       lot.get("commission", 0.0),
                "swap":             0.0,
                "status":           "open",
                "trade_type":       "equity",
                "currency":         lot.get("currency", "AUD"),
                "raw_data":         json.dumps(lot.get("raw", {})),
            })

    return trades


# ── Summary helper ────────────────────────────────────────────────────────────

def summarise_cmc_import(trades: list[dict]) -> dict:
    """Return a summary dict for display after import."""
    closed = [t for t in trades if t["status"] == "closed" and t.get("trade_type") != "dividend"]
    open_  = [t for t in trades if t["status"] == "open"]
    divs   = [t for t in trades if t.get("trade_type") == "dividend"]
    total_pnl = sum(t["pnl"] for t in closed)
    wins  = sum(1 for t in closed if t["pnl"] > 0)
    return {
        "closed_trades": len(closed),
        "open_positions": len(open_),
        "dividends": len(divs),
        "total_pnl": round(total_pnl, 2),
        "wins": wins,
        "losses": len(closed) - wins,
        "win_rate": round(wins / len(closed) * 100, 1) if closed else 0,
        "symbols": sorted(set(t["symbol"] for t in closed)),
    }
