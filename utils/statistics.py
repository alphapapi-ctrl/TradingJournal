"""
Trading statistics calculations.
"""
import pandas as pd
import numpy as np
import json
from database import fetch_all
from utils.playbook_logic import get_playbook, get_playbook_compliance_stats


def get_trade_stats(symbol=None, start_date=None, end_date=None, playbook_id=None, account_id=None):
    """Compute general trading statistics from closed trades."""
    where = ["status='closed'"]
    params = []
    if symbol:
        where.append("symbol=?")
        params.append(symbol)
    if start_date:
        where.append("entry_time>=?")
        params.append(start_date)
    if end_date:
        where.append("entry_time<=?")
        params.append(end_date)
    if playbook_id:
        where.append("playbook_id=?")
        params.append(playbook_id)
    if account_id:
        where.append("account_id=?")
        params.append(account_id)

    trades = fetch_all(
        f"SELECT * FROM trades WHERE {' AND '.join(where)} ORDER BY entry_time",
        params
    )

    if not trades:
        return {}

    df = pd.DataFrame(trades)
    df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0)
    df["commission"] = pd.to_numeric(df["commission"], errors="coerce").fillna(0)
    df["swap"] = pd.to_numeric(df["swap"], errors="coerce").fillna(0)
    df["net_pnl"] = df["pnl"] - df["commission"].abs() - df["swap"].abs()

    winners = df[df["net_pnl"] > 0]
    losers  = df[df["net_pnl"] < 0]

    total = len(df)
    wins  = len(winners)
    losses = len(losers)

    win_rate = wins / total * 100 if total > 0 else 0

    avg_win  = winners["net_pnl"].mean() if len(winners) > 0 else 0
    avg_loss = losers["net_pnl"].mean()  if len(losers)  > 0 else 0
    rr_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    gross_profit = winners["net_pnl"].sum()
    gross_loss   = abs(losers["net_pnl"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve
    df["cumulative_pnl"] = df["net_pnl"].cumsum()

    # Max drawdown
    peak = df["cumulative_pnl"].cummax()
    dd = df["cumulative_pnl"] - peak
    max_drawdown = dd.min()

    # Streaks
    df["win_flag"] = (df["net_pnl"] > 0).astype(int)
    streaks = []
    curr = 1
    for i in range(1, len(df)):
        if df["win_flag"].iloc[i] == df["win_flag"].iloc[i-1]:
            curr += 1
        else:
            streaks.append((df["win_flag"].iloc[i-1], curr))
            curr = 1
    streaks.append((df["win_flag"].iloc[-1] if len(df) > 0 else 0, curr))

    win_streaks  = [s[1] for s in streaks if s[0] == 1]
    loss_streaks = [s[1] for s in streaks if s[0] == 0]

    # By symbol
    by_symbol = df.groupby("symbol").agg(
        trades=("net_pnl", "count"),
        total_pnl=("net_pnl", "sum"),
        win_rate=("win_flag", lambda x: x.mean() * 100),
        avg_pnl=("net_pnl", "mean"),
    ).reset_index().to_dict("records")

    # By direction
    by_direction = df.groupby("direction").agg(
        trades=("net_pnl", "count"),
        total_pnl=("net_pnl", "sum"),
        win_rate=("win_flag", lambda x: x.mean() * 100),
    ).reset_index().to_dict("records")

    # Expectancy
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    # Monthly P&L
    if "entry_time" in df.columns:
        df["month"] = pd.to_datetime(df["entry_time"], errors="coerce").dt.to_period("M").astype(str)
        monthly = df.groupby("month")["net_pnl"].sum().reset_index()
        monthly.columns = ["month", "pnl"]
        monthly_data = monthly.to_dict("records")
    else:
        monthly_data = []

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "breakeven": total - wins - losses,
        "win_rate": round(win_rate, 1),
        "avg_win": round(float(avg_win), 2),
        "avg_loss": round(float(avg_loss), 2),
        "rr_ratio": round(float(rr_ratio), 2),
        "profit_factor": round(float(profit_factor), 2) if profit_factor != float("inf") else 999,
        "gross_profit": round(float(gross_profit), 2),
        "gross_loss": round(float(gross_loss), 2),
        "net_pnl": round(float(df["net_pnl"].sum()), 2),
        "max_drawdown": round(float(max_drawdown), 2),
        "expectancy": round(float(expectancy), 2),
        "best_trade": round(float(df["net_pnl"].max()), 2),
        "worst_trade": round(float(df["net_pnl"].min()), 2),
        "max_win_streak": max(win_streaks, default=0),
        "max_loss_streak": max(loss_streaks, default=0),
        "by_symbol": by_symbol,
        "by_direction": by_direction,
        "monthly": monthly_data,
        "equity_curve": df[["entry_time", "cumulative_pnl"]].to_dict("records"),
    }


def get_playbook_stats(playbook_id: int) -> dict:
    """Per-rule compliance stats with PnL correlation."""
    base = get_playbook_compliance_stats(playbook_id)
    if not base:
        return {}

    pb = get_playbook(playbook_id)

    # Risk score distribution
    trades = fetch_all(
        "SELECT risk_score, pnl FROM trades WHERE playbook_id=? AND status='closed'",
        (playbook_id,)
    )
    if trades:
        df = pd.DataFrame(trades)
        df["risk_score"] = pd.to_numeric(df["risk_score"], errors="coerce")
        df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0)
        score_corr = df[["risk_score", "pnl"]].dropna().corr().iloc[0, 1]
        base["score_pnl_correlation"] = round(float(score_corr), 3) if not np.isnan(score_corr) else 0
    else:
        base["score_pnl_correlation"] = 0

    base["playbook_name"] = pb["name"] if pb else ""
    return base
