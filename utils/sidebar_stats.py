"""Sidebar aggregate queries used across pages."""

import sqlite3
from database import fetch_all
from utils.cashflows import non_test_sql


def _account_net_sql():
    raw = "CASE WHEN json_valid(t.raw_data) THEN t.raw_data ELSE '{}' END"
    fx = "CASE WHEN json_valid(t.fx_metadata) THEN t.fx_metadata ELSE '{}' END"
    account = "COALESCE(a.currency,'UNASSIGNED')"
    quote = (f"COALESCE(t.quote_currency,json_extract({raw},'$.quote_currency'),CASE "
             f"WHEN json_extract({raw},'$.exchange') IN ('ASX','AU') OR t.symbol LIKE '%.AX' THEN 'AUD' "
             f"WHEN json_extract({raw},'$.exchange') IN ('AMEX','NASDAQ','NYSE','BOATS','BATS','CBOE','ARCA','NYSEARCA') THEN 'USD' ELSE {account} END)")
    pnl_currency = (f"COALESCE(json_extract({fx},'$.pnl_currency'),json_extract({raw},'$.reconciliation_20261103.account_currency_pnl.currency'),"
                    f"CASE WHEN json_type({raw},'$.dividend_import_20261103')='object' THEN json_extract({raw},'$.account_currency') END)")
    return (f"CASE WHEN ({pnl_currency} IS NULL OR {pnl_currency}={account}) AND ({quote}={account} OR {pnl_currency}={account}) "
            "THEN COALESCE(t.pnl,0)-ABS(COALESCE(t.commission,0))-ABS(COALESCE(t.swap,0)) END")


def get_sidebar_stats() -> dict:
    money = _account_net_sql()
    try:
        rows = fetch_all(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN t.status='closed' AND COALESCE(t.trade_type,'')<>'dividend' THEN 1 ELSE 0 END), 0) AS total_closed,
                COALESCE(SUM(CASE WHEN t.status='closed' AND COALESCE(t.trade_type,'')<>'dividend' AND {money} > 0 THEN 1 ELSE 0 END), 0) AS wins_closed,
                COALESCE(SUM(CASE WHEN t.status='closed' AND COALESCE(t.trade_type,'')<>'dividend' AND {money} IS NULL THEN 1 ELSE 0 END), 0) AS unvalued_closed,
                COALESCE(SUM(CASE WHEN t.status='open' THEN 1 ELSE 0 END), 0) AS open_n,
                COALESCE(ROUND(SUM(CASE WHEN t.status='closed' THEN {money} ELSE 0 END), 2), 0) AS net_pnl,
                (SELECT COUNT(*) FROM accounts) AS acc_n,
                (SELECT value FROM app_settings WHERE "key" = 'trader_name' LIMIT 1) AS trader_name
            FROM trades t LEFT JOIN accounts a ON a.id=t.account_id WHERE {non_test_sql('t')}
            """
        )
    except sqlite3.OperationalError as exc:
        if "no such table:" in str(exc).lower():
            return {}
        raise
    result = rows[0] if rows else {}
    # Account currencies must never be summed into one nominal balance.
    currencies = fetch_all(f"""SELECT COALESCE(a.currency,'UNASSIGNED') AS currency, ROUND(SUM({money}),2) AS pnl
                               FROM trades t LEFT JOIN accounts a ON a.id=t.account_id
                               WHERE t.status='closed' AND {non_test_sql('t')} GROUP BY a.currency""")
    result["net_pnl_by_currency"] = {r["currency"]: r["pnl"] for r in currencies}
    if len(currencies) > 1:
        result["net_pnl"] = 0  # Older consumers cannot mistake a mixed sum for money.
    return result
