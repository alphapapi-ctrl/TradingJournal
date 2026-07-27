"""
Account management — CRUD for trading accounts.
"""
from database import fetch_all, fetch_one, execute


def get_accounts() -> list[dict]:
    return fetch_all("SELECT * FROM accounts ORDER BY is_default DESC, name")


def get_account(account_id: int) -> dict | None:
    return fetch_one("SELECT * FROM accounts WHERE id=?", (account_id,))


def get_default_account() -> dict | None:
    a = fetch_one("SELECT * FROM accounts WHERE is_default=1 LIMIT 1")
    if not a:
        a = fetch_one("SELECT * FROM accounts LIMIT 1")
    return a


def create_account(name: str, broker: str, currency: str = "AUD",
                   initial_balance: float = 0, account_number: str = "",
                   notes: str = "", set_default: bool = False,
                   account_type: str = "Personal",
                   prop_profit_target_pct: float = None,
                   prop_max_loss_pct: float = None,
                   prop_daily_loss_pct: float = None,
                   prop_personal_daily_loss_pct: float = None) -> int:
    if set_default:
        execute("UPDATE accounts SET is_default=0")
    return execute(
        """INSERT INTO accounts (name, broker, account_number, currency,
           initial_balance, is_default, notes, account_type,
           prop_profit_target_pct, prop_max_loss_pct,
           prop_daily_loss_pct, prop_personal_daily_loss_pct, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (name, broker, account_number, currency, initial_balance,
         int(set_default), notes, account_type,
         prop_profit_target_pct, prop_max_loss_pct,
         prop_daily_loss_pct, prop_personal_daily_loss_pct)
    )


def update_account(account_id: int, name: str, broker: str, currency: str,
                   initial_balance: float, account_number: str, notes: str,
                   is_default: bool, account_type: str = "Personal",
                   prop_profit_target_pct: float = None,
                   prop_max_loss_pct: float = None,
                   prop_daily_loss_pct: float = None,
                   prop_personal_daily_loss_pct: float = None):
    if is_default:
        execute("UPDATE accounts SET is_default=0")
    execute(
        """UPDATE accounts SET name=?, broker=?, account_number=?, currency=?,
           initial_balance=?, is_default=?, notes=?, account_type=?,
           prop_profit_target_pct=?, prop_max_loss_pct=?,
           prop_daily_loss_pct=?, prop_personal_daily_loss_pct=?
           WHERE id=?""",
        (name, broker, account_number, currency, initial_balance,
         int(is_default), notes, account_type,
         prop_profit_target_pct, prop_max_loss_pct,
         prop_daily_loss_pct, prop_personal_daily_loss_pct, account_id)
    )


def delete_account(account_id: int):
    # Un-link trades (don't delete them)
    execute("UPDATE trades SET account_id=NULL WHERE account_id=?", (account_id,))
    execute("DELETE FROM accounts WHERE id=?", (account_id,))


def get_account_stats(account_id: int) -> dict:
    """Quick stats for sidebar/header display."""
    rows = fetch_all(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed,
                  SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_pos,
                  ROUND(SUM(CASE WHEN status='closed' THEN pnl ELSE 0 END),2) as net_pnl,
                  SUM(CASE WHEN status='closed' AND pnl>0 THEN 1 ELSE 0 END) as wins
           FROM trades WHERE account_id=?""",
        (account_id,)
    )
    r = rows[0] if rows else {}
    closed = r.get("closed") or 0
    wins   = r.get("wins")   or 0
    return {
        "total":    r.get("total")   or 0,
        "closed":   closed,
        "open":     r.get("open_pos") or 0,
        "net_pnl":  r.get("net_pnl") or 0.0,
        "wins":     wins,
        "win_rate": round(wins / closed * 100, 1) if closed else 0.0,
    }


def ensure_default_accounts():
    """Called at startup — create accounts for brokers that have trades but no account."""
    brokers_without_account = fetch_all(
        """SELECT DISTINCT t.broker FROM trades t
           WHERE t.account_id IS NULL AND t.broker IS NOT NULL"""
    )
    for row in brokers_without_account:
        broker = row["broker"]
        currency = "AUD" if broker in ("CMC Markets",) else "USD"
        acc_id = create_account(
            name=f"{broker} Account",
            broker=broker,
            currency=currency,
            set_default=False,
        )
        execute("UPDATE trades SET account_id=? WHERE broker=? AND account_id IS NULL",
                (acc_id, broker))
