"""
Broker import parsers for Trading Journal
Supports: MT5 HTML/CSV export, IC Markets CSV
"""
import pandas as pd
import numpy as np
from datetime import datetime
import io
import re
import json

# ─── IC MARKETS ──────────────────────────────────────────────────────────────

def parse_icmarkets_csv(file_content: bytes) -> list[dict]:
    """
    Parse IC Markets trade history CSV export.
    IC Markets exports via MT4/MT5 with their specific column format.
    Expected columns: Open Time, Type, Size, Symbol, Open Price, S/L, T/P,
                      Close Time, Close Price, Commission, Taxes, Swap, Profit
    """
    try:
        text = file_content.decode("utf-8", errors="replace")
        # IC Markets sometimes has a header block before the CSV data
        lines = text.splitlines()
        
        # Find the header row
        header_idx = 0
        for i, line in enumerate(lines):
            if any(kw in line.lower() for kw in ["open time", "symbol", "profit", "close time"]):
                header_idx = i
                break

        csv_text = "\n".join(lines[header_idx:])
        df = pd.read_csv(io.StringIO(csv_text))
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        trades = []
        for _, row in df.iterrows():
            # Skip summary/total rows
            if pd.isna(row.get("symbol", None)) or str(row.get("symbol", "")).strip() == "":
                continue
            
            trade_type = str(row.get("type", "")).strip().lower()
            if trade_type not in ("buy", "sell", "buy limit", "sell limit", "buy stop", "sell stop"):
                continue

            direction = "LONG" if "buy" in trade_type else "SHORT"
            
            try:
                entry_time = pd.to_datetime(row.get("open_time", "")).isoformat()
            except:
                entry_time = None
            try:
                exit_time = pd.to_datetime(row.get("close_time", "")).isoformat()
            except:
                exit_time = None

            def safe_float(val, default=0.0):
                try:
                    return float(str(val).replace(",", "").strip())
                except:
                    return default

            trades.append({
                "broker": "IC Markets",
                "broker_trade_id": str(row.get("ticket", row.get("order", ""))).strip(),
                "symbol": str(row.get("symbol", "")).strip().upper(),
                "direction": direction,
                "entry_price": safe_float(row.get("open_price", 0)),
                "exit_price": safe_float(row.get("close_price", 0)),
                "entry_time": entry_time,
                "exit_time": exit_time,
                "quantity": safe_float(row.get("size", row.get("volume", 0))),
                "pnl": safe_float(row.get("profit", 0)),
                "commission": safe_float(row.get("commission", 0)),
                "swap": safe_float(row.get("swap", 0)),
                "status": "closed" if exit_time else "open",
                "raw_data": json.dumps(row.to_dict(), default=str),
            })
        return trades
    except Exception as e:
        raise ValueError(f"IC Markets CSV parse error: {e}")


# ─── MT5 HTML REPORT ─────────────────────────────────────────────────────────

def parse_mt5_html(file_content: bytes) -> list[dict]:
    """
    Parse MT5 HTML account statement / trade history.
    MT5 exports as an HTML table report.
    """
    try:
        text = file_content.decode("utf-8", errors="replace")
        tables = pd.read_html(io.StringIO(text))
        
        if not tables:
            raise ValueError("No tables found in MT5 HTML file")
        
        # MT5 HTML usually has Deals table
        deals_df = None
        for df in tables:
            cols_lower = [str(c).lower() for c in df.columns]
            if any("symbol" in c for c in cols_lower) and any("profit" in c for c in cols_lower):
                deals_df = df
                break
        
        if deals_df is None:
            deals_df = tables[-1]  # fallback to last table

        deals_df.columns = [str(c).strip().lower().replace(" ", "_").replace("/", "_") for c in deals_df.columns]
        
        trades = []
        # MT5 deals need to be paired: in -> out
        # We'll create one record per closed deal pair
        open_deals = {}

        for _, row in deals_df.iterrows():
            direction_raw = str(row.get("direction", row.get("type", ""))).strip().lower()
            symbol = str(row.get("symbol", "")).strip().upper()
            
            if not symbol or symbol in ("", "NAN", "SYMBOL"):
                continue

            def safe_float(val, default=0.0):
                try:
                    v = str(val).replace(" ", "").replace(",", "")
                    return float(v) if v not in ("", "-", "nan") else default
                except:
                    return default

            try:
                deal_time = pd.to_datetime(row.get("time", row.get("open_time", ""))).isoformat()
            except:
                deal_time = None

            price = safe_float(row.get("price", 0))
            volume = safe_float(row.get("volume", row.get("size", 0)))
            profit = safe_float(row.get("profit", 0))
            commission = safe_float(row.get("commission", 0))
            swap = safe_float(row.get("swap", 0))

            if "in" in direction_raw or direction_raw in ("buy", "sell"):
                direction = "LONG" if "buy" in direction_raw else "SHORT"
                open_deals[symbol] = {
                    "broker": "MT5",
                    "symbol": symbol,
                    "direction": direction,
                    "entry_price": price,
                    "entry_time": deal_time,
                    "quantity": volume,
                    "commission": commission,
                    "swap": swap,
                    "status": "open",
                    "raw_data": json.dumps(row.to_dict(), default=str),
                }
            elif "out" in direction_raw and symbol in open_deals:
                trade = open_deals.pop(symbol)
                trade.update({
                    "exit_price": price,
                    "exit_time": deal_time,
                    "pnl": profit,
                    "commission": trade["commission"] + commission,
                    "swap": trade["swap"] + swap,
                    "status": "closed",
                })
                trades.append(trade)

        # Add any still-open deals
        for t in open_deals.values():
            t["pnl"] = 0
            t["exit_price"] = None
            t["exit_time"] = None
            trades.append(t)

        return trades
    except Exception as e:
        raise ValueError(f"MT5 HTML parse error: {e}")


# ─── MT5 CSV ─────────────────────────────────────────────────────────────────

def parse_mt5_csv(file_content: bytes) -> list[dict]:
    """Parse MT5 CSV export (deals report)."""
    try:
        text = file_content.decode("utf-8", errors="replace")
        df = pd.read_csv(io.StringIO(text), sep="\t" if "\t" in text[:500] else ",")
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

        trades = []
        for _, row in df.iterrows():
            def safe_float(val, default=0.0):
                try:
                    return float(str(val).replace(",", "").strip())
                except:
                    return default

            direction_raw = str(row.get("direction", row.get("type", ""))).lower()
            if "buy" in direction_raw:
                direction = "LONG"
            elif "sell" in direction_raw:
                direction = "SHORT"
            else:
                continue

            symbol = str(row.get("symbol", "")).strip().upper()
            if not symbol:
                continue

            try:
                entry_time = pd.to_datetime(row.get("time", row.get("open_time", ""))).isoformat()
            except:
                entry_time = None

            trades.append({
                "broker": "MT5",
                "symbol": symbol,
                "direction": direction,
                "entry_price": safe_float(row.get("price", row.get("open_price", 0))),
                "exit_price": safe_float(row.get("close_price", 0)) or None,
                "entry_time": entry_time,
                "exit_time": None,
                "quantity": safe_float(row.get("volume", row.get("size", 0))),
                "pnl": safe_float(row.get("profit", 0)),
                "commission": safe_float(row.get("commission", 0)),
                "swap": safe_float(row.get("swap", 0)),
                "status": "closed" if safe_float(row.get("profit", 0)) != 0 else "open",
                "raw_data": json.dumps(row.to_dict(), default=str),
            })
        return trades
    except Exception as e:
        raise ValueError(f"MT5 CSV parse error: {e}")


# ─── AUTO DETECT ─────────────────────────────────────────────────────────────

def auto_detect_and_parse(filename: str, file_content: bytes) -> tuple[list[dict], str]:
    """
    Auto-detect broker format and parse.
    Returns (trades_list, broker_name)
    """
    fname = filename.lower()
    content_preview = file_content[:2000].decode("utf-8", errors="replace").lower()

    if fname.endswith(".html") or fname.endswith(".htm"):
        if "metatrader" in content_preview or "mt5" in content_preview or "terminal" in content_preview:
            return parse_mt5_html(file_content), "MT5"
        raise ValueError("Unrecognized HTML format")

    if fname.endswith(".csv"):
        # IC Markets signature: has "commission" + "swap" + "taxes" or "IC Markets" in header
        if "ic markets" in content_preview or ("taxes" in content_preview and "swap" in content_preview):
            return parse_icmarkets_csv(file_content), "IC Markets"
        # MT5 CSV
        if "metatrader" in content_preview or ("volume" in content_preview and "profit" in content_preview):
            return parse_mt5_csv(file_content), "MT5"
        # Try IC Markets as default CSV
        return parse_icmarkets_csv(file_content), "IC Markets"

    raise ValueError(f"Unsupported file format: {filename}")


# ─── CMC WRAPPER ─────────────────────────────────────────────────────────────
# Replaced auto_detect_and_parse with CMC-aware version below

def auto_detect_and_parse(filename: str, file_content: bytes) -> tuple:
    """
    Auto-detect broker format and parse.  Returns (trades_list, broker_name).
    Detection order: IBKR → CMC Markets → MT5 HTML → MT5 CSV → IC Markets
    """
    import re as _re
    from brokers.cmc_markets import parse_cmc_csv
    from brokers.ibkr import parse_ibkr_csv
    from brokers.tradingview import parse_tradingview_csv

    fname   = filename.lower()
    preview = file_content[:3000].decode("utf-8-sig", errors="replace")
    lower   = preview.lower()

    if fname.endswith((".html", ".htm")):
        if "metatrader" in lower or "mt5" in lower or "terminal" in lower:
            return parse_mt5_html(file_content), "MT5"
        raise ValueError("Unrecognized HTML format")

    if fname.endswith(".csv"):
        # IBKR Activity Statement: "Activity Statement" header + "Account Information" rows
        is_ibkr = (
            "activity statement" in lower and
            "account information" in lower and
            "cash report" in lower
        )
        # Also match by filename pattern Statement_XXXXXXXX_YYYYMMDD_YYYYMMDD.csv
        if not is_ibkr and _re.search(r'statement_\d{6,}_\d{8}_\d{8}', fname):
            is_ibkr = True
        if is_ibkr:
            return parse_ibkr_csv(file_content), "IBKR"

        # TradingView paper-trading export: has Symbol + Side (+ Fill Price / Placing Time)
        first_line = lower.splitlines()[0] if lower else ""
        is_tv = (
            "symbol" in first_line and "side" in first_line and
            ("fill price" in first_line or "placing time" in first_line or
             "order id" in first_line or "tradingview" in fname)
        )
        if is_tv:
            return parse_tradingview_csv(file_content), "TradingView"

        # CMC Markets: distinctive Date/Reference/Type/Description/Debit/Credit columns
        is_cmc = (
            ('Date' in preview and 'Reference' in preview and 'Type' in preview) and
            any(t in preview for t in ['"CB"', '"CS"', ',CB,', ',CS,', '\nCB,', '\nCS,'])
        )
        if not is_cmc and _re.search(r'statement[-_]\d{6,}', fname):
            is_cmc = True
        if is_cmc:
            return parse_cmc_csv(file_content), "CMC Markets"

        if "ic markets" in lower or ("taxes" in lower and "swap" in lower):
            return parse_icmarkets_csv(file_content), "IC Markets"
        if "metatrader" in lower or ("volume" in lower and "profit" in lower):
            return parse_mt5_csv(file_content), "MT5"
        # Fallback: try CMC then IC Markets
        try:
            result = parse_cmc_csv(file_content)
            if result:
                return result, "CMC Markets"
        except Exception:
            pass
        return parse_icmarkets_csv(file_content), "IC Markets"

    raise ValueError(f"Unsupported file format: {filename}")
