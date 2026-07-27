"""
Trade and position management utilities.
"""
import json
from datetime import datetime
from database import fetch_all, fetch_one, execute


# ─── TRADES ──────────────────────────────────────────────────────────────────

def get_trades(status=None, symbol=None, limit=500):
    where = []
    params = []
    if status:
        where.append("status=?")
        params.append(status)
    if symbol:
        where.append("symbol=?")
        params.append(symbol)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return fetch_all(f"SELECT * FROM trades {clause} ORDER BY entry_time DESC LIMIT ?", params + [limit])

def get_trade(trade_id):
    return fetch_one("SELECT * FROM trades WHERE id=?", (trade_id,))

def insert_trade(trade: dict) -> int:
    return execute("""
        INSERT INTO trades (account_id, broker_trade_id, broker, symbol, direction, entry_price, exit_price,
            entry_time, exit_time, quantity, pnl, commission, swap, raw_data, status)
        VALUES (:account_id, :broker_trade_id, :broker, :symbol, :direction, :entry_price, :exit_price,
            :entry_time, :exit_time, :quantity, :pnl, :commission, :swap, :raw_data, :status)
    """, trade)

def upsert_trade_from_broker(trade: dict) -> tuple[int, bool]:
    """Insert or update trade by broker_trade_id. Returns (id, is_new)."""
    if trade.get("broker_trade_id"):
        existing = fetch_one(
            "SELECT id FROM trades WHERE broker_trade_id=? AND broker=?",
            (trade["broker_trade_id"], trade.get("broker", ""))
        )
        if existing:
            execute("""
                UPDATE trades SET exit_price=?, exit_time=?, pnl=?, commission=?, swap=?, status=?, updated_at=datetime('now')
                WHERE id=?
            """, (trade.get("exit_price"), trade.get("exit_time"), trade.get("pnl"),
                  trade.get("commission", 0), trade.get("swap", 0), trade.get("status", "closed"),
                  existing["id"]))
            return existing["id"], False
    trade.setdefault("account_id", None)
    trade.setdefault("broker_trade_id", None)
    trade.setdefault("commission", 0)
    trade.setdefault("swap", 0)
    trade.setdefault("raw_data", None)
    new_id = insert_trade(trade)
    return new_id, True

def update_trade_playbook(trade_id, playbook_id, rules_met: dict, risk_score: float):
    execute("""
        UPDATE trades SET playbook_id=?, playbook_rules_met=?, risk_score=?, updated_at=datetime('now')
        WHERE id=?
    """, (playbook_id, json.dumps({str(k): v for k, v in rules_met.items()}), risk_score, trade_id))

def close_trade(trade_id, exit_price: float, exit_time: str = None,
                pnl: float = None, extra_commission: float = 0.0):
    """Fully close an open trade. P&L auto-computed from prices if not given."""
    t = get_trade(trade_id)
    if not t:
        raise ValueError(f"Trade #{trade_id} not found")
    qty  = float(t.get("quantity") or 0)
    ep   = float(t.get("entry_price") or 0)
    sign = 1 if t["direction"] == "LONG" else -1
    if pnl is None:
        pnl = (exit_price - ep) * qty * sign
    new_comm = float(t.get("commission") or 0) + abs(extra_commission)
    execute("""UPDATE trades SET exit_price=?, exit_time=?, pnl=?, commission=?,
               status='closed', updated_at=datetime('now') WHERE id=?""",
            (exit_price, exit_time or datetime.now().isoformat(timespec="seconds"),
             round(pnl, 4), round(new_comm, 4), trade_id))
    if t.get("position_id"):
        recompute_position(t["position_id"])


def partial_close_trade(trade_id, close_qty: float, exit_price: float,
                        exit_time: str = None, extra_commission: float = 0.0) -> int:
    """
    Take partial profits: split off `close_qty` shares into a new closed trade,
    reduce the open trade's quantity. Entry commission is split pro-rata.
    Returns the new closed trade's id.
    """
    t = get_trade(trade_id)
    if not t:
        raise ValueError(f"Trade #{trade_id} not found")
    if t["status"] != "open":
        raise ValueError(f"Trade #{trade_id} is not open")
    qty = float(t.get("quantity") or 0)
    if close_qty <= 0 or close_qty >= qty:
        raise ValueError(f"Close quantity must be between 0 and {qty:g} (use full close for all)")

    ep   = float(t.get("entry_price") or 0)
    sign = 1 if t["direction"] == "LONG" else -1
    pnl  = (exit_price - ep) * close_qty * sign

    orig_comm   = float(t.get("commission") or 0)
    closed_comm = orig_comm * (close_qty / qty) + abs(extra_commission)
    remain_comm = orig_comm * (1 - close_qty / qty)
    exit_time   = exit_time or datetime.now().isoformat(timespec="seconds")

    closed_part = {
        "account_id":      t.get("account_id"),
        "broker":          t.get("broker"),
        "broker_trade_id": (f"{t['broker_trade_id']}_p{trade_id}"
                            if t.get("broker_trade_id") else None),
        "symbol":          t["symbol"],
        "direction":       t["direction"],
        "entry_price":     ep,
        "exit_price":      exit_price,
        "entry_time":      t.get("entry_time"),
        "exit_time":       exit_time,
        "quantity":        close_qty,
        "pnl":             round(pnl, 4),
        "commission":      round(closed_comm, 4),
        "swap":            0.0,
        "raw_data":        t.get("raw_data"),
        "status":          "closed",
    }
    new_id = insert_trade(closed_part)

    # Keep the closed slice in the same merged position (if any) so it journals as one unit
    if t.get("position_id"):
        execute("UPDATE trades SET position_id=?, playbook_id=? WHERE id=?",
                (t["position_id"], t.get("playbook_id"), new_id))
    elif t.get("playbook_id"):
        execute("UPDATE trades SET playbook_id=? WHERE id=?", (t["playbook_id"], new_id))

    execute("""UPDATE trades SET quantity=?, commission=?, updated_at=datetime('now')
               WHERE id=?""",
            (round(qty - close_qty, 6), round(remain_comm, 4), trade_id))
    if t.get("position_id"):
        recompute_position(t["position_id"])
    return new_id


def reopen_trade(trade_id):
    """Mark a trade open again, clearing exit data (fix accidental closes)."""
    t = get_trade(trade_id)
    execute("""UPDATE trades SET status='open', exit_price=NULL, exit_time=NULL,
               pnl=0, updated_at=datetime('now') WHERE id=?""", (trade_id,))
    if t and t.get("position_id"):
        recompute_position(t["position_id"])


def update_trade_notes(trade_id, notes):
    execute("UPDATE trades SET raw_data=?, updated_at=datetime('now') WHERE id=?", (notes, trade_id))

def delete_trade(trade_id):
    t = get_trade(trade_id)
    execute("DELETE FROM trades WHERE id=?", (trade_id,))
    if t and t.get("position_id"):
        recompute_position(t["position_id"])


def recompute_position(position_id):
    """Recalculate a position's totals from its current constituent trades.
    Deletes the position if it has no trades left."""
    trades = fetch_all("SELECT * FROM trades WHERE position_id=?", (position_id,))
    if not trades:
        execute("DELETE FROM positions WHERE id=?", (position_id,))
        return

    total_qty = sum(float(t.get("quantity") or 0) for t in trades)
    total_pnl = sum(float(t.get("pnl") or 0) for t in trades)
    avg_entry = (sum((float(t.get("entry_price") or 0)) * (float(t.get("quantity") or 0)) for t in trades)
                 / total_qty) if total_qty else 0
    exit_trades = [t for t in trades if t.get("exit_price")]
    exit_qty = sum(float(t.get("quantity") or 0) for t in exit_trades)
    avg_exit = (sum(float(t["exit_price"]) * float(t.get("quantity") or 0) for t in exit_trades)
                / exit_qty) if exit_qty else None

    open_times  = [t["entry_time"] for t in trades if t.get("entry_time")]
    close_times = [t["exit_time"]  for t in trades if t.get("exit_time")]

    execute("""UPDATE positions SET open_time=?, close_time=?, avg_entry_price=?,
               avg_exit_price=?, total_quantity=?, total_pnl=? WHERE id=?""",
            (min(open_times) if open_times else None,
             max(close_times) if close_times else None,
             round(avg_entry, 5), round(avg_exit, 5) if avg_exit else None,
             round(total_qty, 2), round(total_pnl, 2), position_id))


# ─── POSITIONS ───────────────────────────────────────────────────────────────

def get_positions():
    return fetch_all("SELECT * FROM positions ORDER BY open_time DESC")

def get_position(position_id):
    pos = fetch_one("SELECT * FROM positions WHERE id=?", (position_id,))
    if pos:
        pos["trades"] = fetch_all("SELECT * FROM trades WHERE position_id=?", (position_id,))
    return pos

def merge_trades_into_position(trade_ids: list[int], notes: str = "",
                               playbook_id: int | None = None) -> int:
    """Merge multiple trades into a single position, computing weighted averages."""
    if not trade_ids:
        raise ValueError("No trade IDs provided")

    trades = [get_trade(tid) for tid in trade_ids]
    trades = [t for t in trades if t]

    if not trades:
        raise ValueError("No valid trades found")

    symbols = set(t["symbol"] for t in trades)
    if len(symbols) > 1:
        raise ValueError(f"Cannot merge trades with different symbols: {symbols}")

    directions = set(t["direction"] for t in trades)
    if len(directions) > 1:
        raise ValueError(f"Trades have mixed directions: {directions}")

    total_qty = sum(t.get("quantity") or 0 for t in trades)
    total_pnl = sum(t.get("pnl") or 0 for t in trades)

    # Weighted average entry price
    avg_entry = sum((t.get("entry_price") or 0) * (t.get("quantity") or 0) for t in trades) / total_qty if total_qty else 0
    avg_exit_trades = [t for t in trades if t.get("exit_price")]
    avg_exit = (sum((t["exit_price"]) * (t.get("quantity") or 0) for t in avg_exit_trades)
                / sum(t.get("quantity") or 0 for t in avg_exit_trades)) if avg_exit_trades else None

    open_times = [t["entry_time"] for t in trades if t.get("entry_time")]
    close_times = [t["exit_time"] for t in trades if t.get("exit_time")]

    position_id = execute("""
        INSERT INTO positions (symbol, direction, open_time, close_time, avg_entry_price, avg_exit_price,
            total_quantity, total_pnl, notes, playbook_id)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        trades[0]["symbol"], trades[0]["direction"],
        min(open_times) if open_times else None,
        max(close_times) if close_times else None,
        round(avg_entry, 5), round(avg_exit, 5) if avg_exit else None,
        round(total_qty, 2), round(total_pnl, 2), notes, playbook_id
    ))

    for tid in trade_ids:
        execute("UPDATE trades SET position_id=?, playbook_id=? WHERE id=?",
                (position_id, playbook_id, tid))

    return position_id

def unmerge_position(position_id):
    execute("UPDATE trades SET position_id=NULL, playbook_id=NULL WHERE position_id=?", (position_id,))
    execute("DELETE FROM positions WHERE id=?", (position_id,))

def update_position(position_id, notes=None, playbook_id=None):
    if notes is not None:
        execute("UPDATE positions SET notes=? WHERE id=?", (notes, position_id))
    if playbook_id is not None:
        execute("UPDATE positions SET playbook_id=? WHERE id=?", (playbook_id, position_id))

def set_position_playbook(position_id: int, playbook_id: int | None):
    """Assign (or clear) a playbook on a position and propagate to all its trades."""
    execute("UPDATE positions SET playbook_id=? WHERE id=?", (playbook_id, position_id))
    execute("UPDATE trades SET playbook_id=? WHERE position_id=?", (playbook_id, position_id))


# ─── JOURNAL ─────────────────────────────────────────────────────────────────

def get_journal_entries(entry_type=None, start_date=None, end_date=None):
    where = []
    params = []
    if entry_type:
        where.append("entry_type=?")
        params.append(entry_type)
    if start_date:
        where.append("entry_date>=?")
        params.append(start_date)
    if end_date:
        where.append("entry_date<=?")
        params.append(end_date)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return fetch_all(f"SELECT * FROM journal_entries {clause} ORDER BY entry_date DESC", params)

def get_journal_entry(entry_id):
    return fetch_one("SELECT * FROM journal_entries WHERE id=?", (entry_id,))

def get_journal_entry_for_date(entry_type, entry_date):
    return fetch_one(
        "SELECT * FROM journal_entries WHERE entry_type=? AND entry_date=?",
        (entry_type, entry_date)
    )

def save_journal_entry(entry_id, entry_type, entry_date, analysis, execution, psychology,
                       lessons, grade, mood, trade_id=None, position_id=None,
                       custom_fields=None, stage="post", playbook_id=None,
                       trade_category=None):
    if entry_id:
        execute("""
            UPDATE journal_entries SET analysis=?, execution=?, psychology=?, lessons=?, grade=?, mood=?,
            custom_fields=?, stage=?, playbook_id=?, trade_category=?, updated_at=datetime('now') WHERE id=?
        """, (analysis, execution, psychology, lessons, grade, mood,
              json.dumps(custom_fields) if custom_fields else None,
              stage, playbook_id, trade_category, entry_id))
        return entry_id
    else:
        return execute("""
            INSERT INTO journal_entries (entry_type, entry_date, trade_id, position_id, analysis,
                execution, psychology, lessons, grade, mood, custom_fields, stage, playbook_id, trade_category)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (entry_type, entry_date, trade_id, position_id, analysis, execution, psychology,
              lessons, grade, mood, json.dumps(custom_fields) if custom_fields else None,
              stage, playbook_id, trade_category))

def delete_journal_entry(entry_id):
    execute("DELETE FROM journal_entries WHERE id=?", (entry_id,))

def get_templates(template_type=None):
    if template_type:
        return fetch_all("SELECT * FROM journal_templates WHERE template_type=? ORDER BY is_default DESC, name", (template_type,))
    return fetch_all("SELECT * FROM journal_templates ORDER BY template_type, is_default DESC, name")

def save_template(template_id, name, template_type, analysis_template, execution_template,
                  psychology_template, is_default=False):
    if template_id:
        execute("""
            UPDATE journal_templates SET name=?, template_type=?, analysis_template=?, execution_template=?,
            psychology_template=?, is_default=? WHERE id=?
        """, (name, template_type, analysis_template, execution_template, psychology_template,
              int(is_default), template_id))
    else:
        execute("""
            INSERT INTO journal_templates (name, template_type, analysis_template, execution_template,
                psychology_template, is_default) VALUES (?,?,?,?,?,?)
        """, (name, template_type, analysis_template, execution_template, psychology_template, int(is_default)))
