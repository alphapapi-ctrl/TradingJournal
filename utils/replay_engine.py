"""
Replay trading engine — authoritative Python side of the replay trainer.

The chart component simulates fills client-side for responsiveness, but every
fill/exit it reports is re-validated here against the 1-min bars before a
trade is written (via utils.trade_ops) into the Replay Demo account. R and
position sizing live here too.
"""
import json
from datetime import datetime, timezone

from database import fetch_all, fetch_one, execute
from utils import market_data as md
from utils.accounts import create_account
from utils.trade_ops import insert_trade, close_trade, partial_close_trade, get_trade

SYMBOL = "DAX"
BROKER = "Replay"
DEFAULT_BALANCE = 10000.0
DEFAULT_CURRENCY = "USD"
POINT_VALUE = 1.0  # account-currency per point per unit of quantity
_CCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "AUD": "A$", "CHF": "Fr ", "JPY": "¥"}


# ── Demo account ──────────────────────────────────────────────────────────
def ensure_demo_account() -> int:
    """Find-or-create the Replay Demo account. Returns its id."""
    row = fetch_one("SELECT id FROM accounts WHERE broker=? AND account_type='Demo' LIMIT 1",
                    (BROKER,))
    if row:
        return row["id"]
    initial = float(md.get_setting("replay_demo_initial_balance", DEFAULT_BALANCE))
    return create_account(
        name="Replay Demo", broker=BROKER,
        currency=md.get_setting("replay_demo_currency", DEFAULT_CURRENCY),
        initial_balance=initial, account_number="REPLAY-001",
        notes="Simulated account for the tick-replay trainer.",
        account_type="Demo",
    )


def get_account_number(account_id: int) -> str:
    acc = fetch_one("SELECT account_number FROM accounts WHERE id=?", (account_id,))
    return (acc or {}).get("account_number") or "SIM"


def currency_symbol(account_id: int) -> str:
    acc = fetch_one("SELECT currency FROM accounts WHERE id=?", (account_id,))
    ccy = (acc or {}).get("currency") or DEFAULT_CURRENCY
    return _CCY_SYMBOLS.get(ccy, ccy + " ")


def demo_balance(account_id: int) -> float:
    acc = fetch_one("SELECT initial_balance FROM accounts WHERE id=?", (account_id,))
    pnl = fetch_one(
        "SELECT COALESCE(SUM(pnl - COALESCE(commission,0)),0) s FROM trades "
        "WHERE account_id=? AND status='closed'", (account_id,))
    return float(acc["initial_balance"] or 0) + float(pnl["s"] or 0)


# ── Position sizing ───────────────────────────────────────────────────────
def position_size(risk_amount: float, entry: float, stop: float) -> float:
    """Quantity so that entry→stop loses risk_amount. 0 if degenerate."""
    dist = abs(entry - stop)
    if dist <= 0 or risk_amount <= 0:
        return 0.0
    return round(risk_amount / (dist * POINT_VALUE), 2)


def risk_amount(account_id: int, mode: str, fixed_amount: float, pct: float) -> float:
    """mode: 'fixed' (€ amount) or 'pct' (percent of current balance, compounding)."""
    if mode == "pct":
        return round(demo_balance(account_id) * pct / 100.0, 2)
    return fixed_amount


# ── Fill validation ───────────────────────────────────────────────────────
def validate_fill(day: str, kind: str, side: str, price: float, bar_time: int,
                  instrument: str | None = None) -> bool:
    """Check a reported fill against the 1-min bar it claims to have happened in.

    bar_time is the component's shifted epoch (exchange wall-clock as UTC).
    The fill price must lie within that bar's (or a neighbour's) range.
    """
    instrument = instrument or md.DEFAULT_INSTRUMENT
    bars = md.get_day_bars(day, session_only=True, instrument=instrument)
    if bars.empty:
        return False
    lwc = md.bars_to_lwc(bars, instrument)
    by_time = {b["time"]: b for b in lwc}
    # tolerance covers half-spread (fills are on bid/ask, bars are mid-based)
    tol = 3.0
    for t in (bar_time, bar_time - 60, bar_time + 60):
        b = by_time.get(t)
        if b and b["low"] - tol <= price <= b["high"] + tol:
            return True
    return False


def _replay_clock(day: str, bar_time: int | None) -> str:
    """Historical timestamp for the trade: the replayed day + bar wall-clock."""
    if bar_time:
        return datetime.fromtimestamp(bar_time, tz=timezone.utc).strftime(f"{day}T%H:%M:%S")
    return f"{day}T09:00:00"


# ── Trade lifecycle ───────────────────────────────────────────────────────
def open_replay_trade(account_id: int, day: str, side: str, price: float,
                      qty: float, sl: float | None, tp: float | None,
                      bar_time: int | None = None, symbol: str | None = None) -> int:
    direction = "LONG" if side == "buy" else "SHORT"
    planned_risk = round(abs(price - sl) * qty * POINT_VALUE, 2) if sl else None
    trade_id = insert_trade({
        "account_id": account_id,
        "broker_trade_id": None,
        "broker": BROKER,
        "symbol": symbol or SYMBOL,
        "direction": direction,
        "entry_price": price,
        "exit_price": None,
        "entry_time": _replay_clock(day, bar_time),
        "exit_time": None,
        "quantity": qty,
        "pnl": 0,
        "commission": 0,
        "swap": 0,
        "raw_data": None,
        "status": "open",
    })
    execute("""UPDATE trades SET stop_price=?, take_profit=?, planned_risk=?,
               is_replay=1, replay_day=?, trade_type='Replay' WHERE id=?""",
            (sl, tp, planned_risk, day, trade_id))
    return trade_id


def close_replay_trade(trade_id: int, exit_price: float, day: str,
                       bar_time: int | None = None):
    close_trade(trade_id, exit_price, exit_time=_replay_clock(day, bar_time))
    _set_r_multiple(trade_id)


def partial_close_replay_trade(trade_id: int, close_qty: float, exit_price: float,
                               day: str, bar_time: int | None = None) -> int:
    new_id = partial_close_trade(trade_id, close_qty, exit_price,
                                 exit_time=_replay_clock(day, bar_time))
    # carry replay metadata onto the closed slice
    t = get_trade(trade_id)
    execute("""UPDATE trades SET stop_price=?, take_profit=?, is_replay=1, replay_day=?,
               trade_type='Replay',
               planned_risk=(SELECT planned_risk FROM trades WHERE id=?) * ? / (quantity + ?)
               WHERE id=?""",
            (t.get("stop_price"), t.get("take_profit"), day, trade_id,
             close_qty, close_qty, new_id))
    _set_r_multiple(new_id)
    return new_id


def update_trade_levels(trade_id: int, sl: float | None = None, tp: float | None = None):
    t = get_trade(trade_id)
    if not t:
        return
    if sl is not None:
        execute("UPDATE trades SET stop_price=? WHERE id=?", (sl, trade_id))
    if tp is not None:
        execute("UPDATE trades SET take_profit=? WHERE id=?", (tp, trade_id))


def _set_r_multiple(trade_id: int):
    t = get_trade(trade_id)
    if not t:
        return
    risk = t.get("planned_risk")
    if risk and float(risk) > 0 and t.get("pnl") is not None:
        execute("UPDATE trades SET r_multiple=? WHERE id=?",
                (round(float(t["pnl"]) / float(risk), 2), trade_id))


# ── Unrealized P&L ────────────────────────────────────────────────────────
def unrealized(trade: dict, price: float) -> dict:
    """{pnl, pts, r} for an open trade at the given price."""
    sign = 1 if trade["direction"] == "LONG" else -1
    pts = (price - float(trade["entry_price"])) * sign
    pnl = pts * float(trade["quantity"]) * POINT_VALUE
    risk = float(trade.get("planned_risk") or 0)
    return {"pnl": round(pnl, 2), "pts": round(pts, 1),
            "r": round(pnl / risk, 2) if risk > 0 else None}


# ── Stored replay sessions (each with its own demo account) ──────────────
def create_replay_session(name: str, instrument: str, day: str,
                          balance: float, currency: str = DEFAULT_CURRENCY) -> int:
    """New stored session + its own demo account (generated account number),
    so replay trades never mix with real journal accounts."""
    import random, string
    acct_no = "SIM-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    account_id = create_account(
        name=f"Replay: {name}", broker=BROKER, currency=currency,
        initial_balance=balance, account_number=acct_no,
        notes=f"Auto-generated for replay session '{name}'.",
        account_type="Demo",
    )
    return execute("""INSERT INTO replay_sessions
                      (name, instrument, day, cursor, sub_step, speed,
                       pending_orders, account_id)
                      VALUES (?,?,?,0,0,10,'[]',?)""",
                   (name, instrument, day, account_id))


def save_session(session_id: int, day: str, cursor: int, sub_step: int,
                 speed: float, pending_orders: list):
    execute("""UPDATE replay_sessions SET day=?, cursor=?, sub_step=?, speed=?,
               pending_orders=?, updated_at=datetime('now') WHERE id=?""",
            (day, cursor, sub_step, speed, json.dumps(pending_orders), session_id))


def list_sessions(instrument: str | None = None) -> list[dict]:
    q = "SELECT * FROM replay_sessions WHERE status='active'"
    p: tuple = ()
    if instrument:
        q += " AND instrument=?"
        p = (instrument,)
    return fetch_all(q + " ORDER BY updated_at DESC", p)


def get_session(session_id: int) -> dict | None:
    return fetch_one("SELECT * FROM replay_sessions WHERE id=?", (session_id,))


def delete_session(session_id: int, wipe_account: bool = False):
    """Delete a stored session. wipe_account also removes its demo account,
    all its trades and its journal entries (full clean slate)."""
    s = get_session(session_id)
    execute("DELETE FROM replay_sessions WHERE id=?", (session_id,))
    if wipe_account and s and s.get("account_id"):
        execute("DELETE FROM trades WHERE account_id=?", (s["account_id"],))
        execute("DELETE FROM journal_entries WHERE account_id=?", (s["account_id"],))
        execute("DELETE FROM accounts WHERE id=?", (s["account_id"],))


# ── Queries for the UI ────────────────────────────────────────────────────
def open_replay_trades(account_id: int, day: str | None = None) -> list[dict]:
    q = "SELECT * FROM trades WHERE account_id=? AND status='open' AND is_replay=1"
    p = [account_id]
    if day:
        q += " AND replay_day=?"
        p.append(day)
    return fetch_all(q + " ORDER BY id", tuple(p))


def closed_replay_trades(account_id: int, day: str) -> list[dict]:
    return fetch_all(
        "SELECT * FROM trades WHERE account_id=? AND status='closed' AND is_replay=1 "
        "AND replay_day=? ORDER BY id", (account_id, day))


def day_stats(account_id: int, day: str) -> dict:
    rows = closed_replay_trades(account_id, day)
    pnl = sum(float(t.get("pnl") or 0) for t in rows)
    r = sum(float(t.get("r_multiple") or 0) for t in rows)
    return {"trades": len(rows), "pnl": round(pnl, 2), "r": round(r, 2)}
