"""Sidebar aggregate queries used across pages."""

import sqlite3
from database import fetch_all


def get_sidebar_stats() -> dict:
    try:
        rows = fetch_all(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END), 0) AS total_closed,
                COALESCE(SUM(CASE WHEN status='closed' AND pnl > 0 THEN 1 ELSE 0 END), 0) AS wins_closed,
                COALESCE(SUM(CASE WHEN status='open' THEN 1 ELSE 0 END), 0) AS open_n,
                COALESCE(ROUND(SUM(CASE WHEN status='closed' THEN pnl - ABS(COALESCE(commission,0)) ELSE 0 END), 2), 0) AS net_pnl,
                (SELECT COUNT(*) FROM accounts) AS acc_n,
                (SELECT value FROM app_settings WHERE "key" = 'trader_name' LIMIT 1) AS trader_name
            FROM trades
            """
        )
    except sqlite3.OperationalError as exc:
        if "no such table:" in str(exc).lower():
            return {}
        raise
    return rows[0] if rows else {}
