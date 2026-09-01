"""Sidebar aggregate queries used across pages."""

from database import fetch_all


def get_sidebar_stats() -> dict:
    rows = fetch_all(
        """
        SELECT
            (SELECT COUNT(*) FROM trades WHERE status='closed') AS total_closed,
            (SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl>0) AS wins_closed,
            (SELECT COUNT(*) FROM trades WHERE status='open') AS open_n,
            COALESCE(
                (
                    SELECT ROUND(SUM(pnl - ABS(COALESCE(commission,0)), 2)
                    FROM trades WHERE status='closed'
                ),
                0
            ) AS net_pnl,
            (SELECT COUNT(*) FROM accounts) AS acc_n,
            (SELECT value FROM app_settings WHERE key='trader_name') AS trader_name
        """
    )
    return rows[0] if rows else {}
