"""
Seed realistic sample trades, playbooks, and journal entries for demo/testing.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import random
from datetime import datetime, timedelta
from database import execute, fetch_all, init_db


def seed_all(clear_first=False):
    init_db()
    if clear_first:
        for table in ["trades", "positions", "playbooks", "playbook_rules",
                      "playbook_risk_rules", "journal_entries", "accounts"]:
            execute(f"DELETE FROM {table}")

    already = fetch_all("SELECT COUNT(*) as n FROM trades")[0]["n"]
    if already > 0 and not clear_first:
        return  # Already seeded

    # Create a demo account for seed data
    demo_acc_id = execute(
        """INSERT INTO accounts (name, broker, account_number, currency, initial_balance,
           is_default, notes, created_at)
           VALUES ('IC Markets Demo', 'IC Markets', 'DEMO-001', 'USD', 10000, 1,
           'Demo account with synthetic trade data', datetime('now'))"""
    )

    pb1 = _seed_breakout_playbook()
    pb2 = _seed_pullback_playbook()
    trade_ids = _seed_trades(pb1, pb2, demo_acc_id)
    _seed_journals(trade_ids)
    print(f"Seeded {len(trade_ids)} trades (account #{demo_acc_id}), 2 playbooks, journals.")


def _seed_breakout_playbook() -> int:
    pb_id = execute(
        "INSERT INTO playbooks (name, description, symbol_filter, direction) VALUES (?,?,?,?)",
        ("Breakout Setup", "High-volume breakouts above key resistance with momentum confirmation",
         "EURUSD,GBPUSD,USDJPY,XAUUSD", "BOTH")
    )
    rules = [
        ("Price closes above key resistance", "Daily or 4H close above level", "required", 1.5, 0),
        ("Volume confirmation", "Volume spike 1.5x above 20-period average", "required", 1.0, 1),
        ("Higher timeframe aligned", "Daily trend direction matches trade", "required", 1.2, 2),
        ("Wait for retest", "Price retests broken level before entry", "optional", 1.0, 3),
        ("RSI above 50 (long) / below 50 (short)", "Momentum confirmation", "optional", 0.8, 4),
        ("ATR expansion", "ATR is expanding — volatility increasing", "optional", 0.7, 5),
        ("News catalyst present", "Fundamental driver supports breakout", "bonus", 0.5, 6),
        ("Clean structure — no chop above", "Room to run above resistance", "bonus", 0.6, 7),
    ]
    for name, desc, rtype, weight, order in rules:
        execute(
            "INSERT INTO playbook_rules (playbook_id, name, description, rule_type, weight, sort_order) VALUES (?,?,?,?,?,?)",
            (pb_id, name, desc, rtype, weight, order)
        )
    # Risk thresholds
    execute(
        "INSERT INTO playbook_risk_rules (playbook_id, min_required_pct, min_optional_pct, risk_level, risk_multiplier, warning_message) VALUES (?,?,?,?,?,?)",
        (pb_id, 67.0, 50.0, "reduced", 0.5, "⚠️ Only 2/3 required rules met — take half size")
    )
    execute(
        "INSERT INTO playbook_risk_rules (playbook_id, min_required_pct, min_optional_pct, risk_level, risk_multiplier, warning_message) VALUES (?,?,?,?,?,?)",
        (pb_id, 34.0, 0.0, "no_trade", 0.0, "🚫 Less than 2 required rules met — DO NOT trade")
    )
    return pb_id


def _seed_pullback_playbook() -> int:
    pb_id = execute(
        "INSERT INTO playbooks (name, description, symbol_filter, direction) VALUES (?,?,?,?)",
        ("Trend Pullback", "Enter on pullbacks in established trends at key structure levels",
         "*", "BOTH")
    )
    rules = [
        ("Trend identified on HTF", "Clear trend on D1 or H4 — higher highs/lows (long), lower highs/lows (short)", "required", 1.5, 0),
        ("Price at key structure level", "Pullback to previous resistance-turned-support, or Fibonacci 50-61.8%", "required", 1.3, 1),
        ("Rejection candle present", "Pin bar, engulfing, or doji at the level", "required", 1.0, 2),
        ("EMA confluence", "Price is at or near key EMA (20/50/200)", "optional", 1.0, 3),
        ("RSI oversold/overbought reset", "RSI retraced from extreme on entry TF", "optional", 0.8, 4),
        ("Low spread", "Spread below 1.5x average for the session", "optional", 0.5, 5),
        ("London/NY session overlap", "Entering during high-liquidity session", "bonus", 0.6, 6),
        ("Previous swing high/low as target", "Clear take profit level visible", "bonus", 0.7, 7),
    ]
    for name, desc, rtype, weight, order in rules:
        execute(
            "INSERT INTO playbook_rules (playbook_id, name, description, rule_type, weight, sort_order) VALUES (?,?,?,?,?,?)",
            (pb_id, name, desc, rtype, weight, order)
        )
    execute(
        "INSERT INTO playbook_risk_rules (playbook_id, min_required_pct, min_optional_pct, risk_level, risk_multiplier, warning_message) VALUES (?,?,?,?,?,?)",
        (pb_id, 100.0, 33.0, "reduced", 0.75, "⚠️ Fewer than 2 optional rules met — reduce to 75% size")
    )
    execute(
        "INSERT INTO playbook_risk_rules (playbook_id, min_required_pct, min_optional_pct, risk_level, risk_multiplier, warning_message) VALUES (?,?,?,?,?,?)",
        (pb_id, 67.0, 0.0, "no_trade", 0.0, "🚫 Required rules incomplete — skip this trade")
    )
    return pb_id


def _seed_trades(pb1_id: int, pb2_id: int, account_id: int = None) -> list:
    random.seed(42)
    symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "USDCAD", "AUDUSD"]
    symbol_prices = {
        "EURUSD": 1.0850, "GBPUSD": 1.2650, "USDJPY": 148.50,
        "XAUUSD": 2020.0, "USDCAD": 1.3550, "AUDUSD": 0.6520
    }
    # Fetch rules for each playbook
    pb1_rules = fetch_all("SELECT id, rule_type FROM playbook_rules WHERE playbook_id=?", (pb1_id,))
    pb2_rules = fetch_all("SELECT id, rule_type FROM playbook_rules WHERE playbook_id=?", (pb2_id,))

    def make_rules_met(rules, quality: float) -> dict:
        """quality 0-1: how well the rules were followed"""
        result = {}
        for r in rules:
            if r["rule_type"] == "required":
                result[str(r["id"])] = random.random() < (quality + 0.2)
            elif r["rule_type"] == "optional":
                result[str(r["id"])] = random.random() < quality
            else:  # bonus
                result[str(r["id"])] = random.random() < (quality - 0.1)
        return result

    trade_ids = []
    base_date = datetime.now() - timedelta(days=90)

    for i in range(60):
        trade_date = base_date + timedelta(days=random.randint(0, 85), hours=random.randint(1, 22))
        symbol = random.choice(symbols)
        direction = random.choice(["LONG", "SHORT"])
        base_price = symbol_prices[symbol]
        pip_size = 0.0001 if "JPY" not in symbol and "XAU" not in symbol else (0.01 if "JPY" in symbol else 0.1)

        entry_price = base_price * (1 + random.uniform(-0.002, 0.002))
        use_pb1 = random.random() > 0.4
        pb_id = pb1_id if use_pb1 else pb2_id
        rules = pb1_rules if use_pb1 else pb2_rules

        # Determine trade quality (affects both rules met and outcome)
        quality = random.betavariate(2, 1.5)  # slight skew toward higher quality
        rules_met = make_rules_met(rules, quality)
        req_met = sum(1 for r in rules if r["rule_type"] == "required" and rules_met.get(str(r["id"]), False))
        req_total = max(1, sum(1 for r in rules if r["rule_type"] == "required"))

        # Outcome correlated with quality + some randomness
        base_outcome = quality - 0.45 + random.gauss(0, 0.3)
        pip_range = random.uniform(10, 80)
        if base_outcome > 0:
            pnl_pips = pip_range * random.uniform(0.8, 2.5)
        else:
            pnl_pips = -pip_range * random.uniform(0.5, 1.8)

        # Convert pips to price movement
        exit_price = entry_price + (pnl_pips * pip_size * (1 if direction == "LONG" else -1))
        quantity = round(random.choice([0.1, 0.1, 0.2, 0.2, 0.5, 1.0]), 1)

        # For FX pairs, rough PnL = pips * pip_value * lots
        # For simplicity use a flat conversion
        lot_value = 1000 if "XAU" not in symbol else 100
        pnl = round(pnl_pips * quantity * lot_value * (0.1 if "JPY" not in symbol else 0.01), 2)
        commission = round(quantity * 3.5, 2)

        # Risk score
        optional_met = sum(1 for r in rules if r["rule_type"] == "optional" and rules_met.get(str(r["id"]), False))
        optional_total = max(1, sum(1 for r in rules if r["rule_type"] == "optional"))
        bonus_met = sum(1 for r in rules if r["rule_type"] == "bonus" and rules_met.get(str(r["id"]), False))
        bonus_total = max(1, sum(1 for r in rules if r["rule_type"] == "bonus"))
        risk_score = round((req_met/req_total)*60 + (optional_met/optional_total)*30 + (bonus_met/bonus_total)*10, 1)

        hold_hours = random.randint(1, 72)
        exit_time  = trade_date + timedelta(hours=hold_hours)

        tid = execute("""
            INSERT INTO trades (account_id, broker, symbol, direction, entry_price, exit_price,
                entry_time, exit_time, quantity, pnl, commission, swap,
                playbook_id, playbook_rules_met, risk_score, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            account_id, "IC Markets", symbol, direction,
            round(entry_price, 5), round(exit_price, 5),
            trade_date.strftime("%Y-%m-%d %H:%M:%S"),
            exit_time.strftime("%Y-%m-%d %H:%M:%S"),
            quantity, pnl, commission, round(random.uniform(-0.5, 0.5), 2),
            pb_id, json.dumps(rules_met), risk_score, "closed"
        ))
        trade_ids.append(tid)

    return trade_ids


def _seed_journals(trade_ids: list):
    random.seed(99)
    base_date = datetime.now() - timedelta(days=90)

    analyses = [
        "Market was ranging in the Asian session, waited for London open to see direction. Clear break of the 4H resistance at {price} with a strong engulfing candle. Higher timeframe structure strongly bullish. Identified key level from last week's high as confluence.",
        "HTF trend clearly bearish with lower highs and lower lows on the daily. Waited for a pullback to the 50 EMA which lined up perfectly with prior support-turned-resistance. Entry on the rejection with a tight stop above the wick.",
        "Wide chop in early session. Should have stayed out. Entered on what I thought was a breakout but volume was weak and spread was elevated. No clear confluence — just impatient.",
        "Pre-market analysis flagged EURUSD showing compression near weekly high. News risk was present so sized down to 50%. Breakout came on better-than-expected data. Execution was clean.",
    ]
    executions = [
        "Entered on the close of the candle at market. Stop placed below the wick — 1.3x ATR. Target at 2:1 based on next structure level. Trailed stop at breakeven after price moved 1R.",
        "Used limit order at the identified level. Triggered perfectly. Moved stop to breakeven too early and got stopped out before the move materialised.",
        "Chased the entry after missing the initial setup. Entered 15 pips late. This inflated my risk significantly. Should have waited for the next setup.",
        "Good entry. Followed the plan completely. Let it run to target without interfering.",
    ]
    psychologies = [
        "Felt calm and focused today. Stuck to the plan. Didn't feel the urge to overtrade even when sitting at the screen.",
        "Frustrated after two stop-outs in the morning. Should have taken a break. Took a revenge trade in the afternoon which violated my rules. Need to implement a loss limit.",
        "Good mental state. Missed one trade but accepted it without chasing. The discipline of NOT trading feels like progress.",
        "Anxiety around the news event affected my decision. Entered too early to 'get ahead' of the move — rookie mistake.",
    ]
    lessons_list = [
        "Wait for volume confirmation before entering breakouts. Low-volume breakouts fail 70% of the time in my data.",
        "Moving stop to breakeven too early is costing me good trades. Set a rule: no breakeven until 1.5R.",
        "Loss limit needed. After 2 losses in a session, step away for at least 2 hours.",
        "Pre-market prep is the difference between reactive and proactive trading.",
    ]
    grades = ["A", "A", "B", "B+", "B", "C+", "C", "D", "A+", "B"]

    # Daily journals
    for day_offset in range(0, 90, 3):
        entry_date = (base_date + timedelta(days=day_offset)).strftime("%Y-%m-%d")
        execute("""
            INSERT INTO journal_entries (entry_type, entry_date, analysis, execution, psychology, lessons, grade, mood)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            "daily", entry_date,
            random.choice(analyses).format(price=round(random.uniform(1.08, 1.10), 4)),
            random.choice(executions),
            random.choice(psychologies),
            random.choice(lessons_list),
            random.choice(grades),
            random.randint(4, 9)
        ))

    # Weekly journals
    for week_offset in range(0, 12):
        week_date = (base_date + timedelta(weeks=week_offset)).strftime("%Y-%m-%d")
        execute("""
            INSERT INTO journal_entries (entry_type, entry_date, analysis, execution, psychology, lessons, grade, mood)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            "weekly", week_date,
            f"Week {week_offset+1}: Market theme was {random.choice(['risk-on', 'risk-off', 'dollar strength', 'dollar weakness'])}. "
            f"Key economic events: {random.choice(['NFP miss', 'CPI beat', 'Fed hawkish', 'ECB dovish', 'GDP inline'])}. "
            "Stayed aligned with the macro bias for the most part.",
            f"Took {random.randint(3,8)} trades this week. Best was a clean {random.choice(['breakout','pullback','reversal'])} setup. "
            f"Worst was an impulsive entry I shouldn't have taken. Execution score: {random.randint(6,9)}/10.",
            f"Mindset: {random.choice(['solid week, felt in control', 'struggled with patience on Thursday', 'strong focus, good energy', 'a bit distracted mid-week'])}. "
            f"Discipline: {random.randint(6,10)}/10.",
            random.choice(lessons_list),
            random.choice(grades),
            random.randint(5, 9)
        ))

    # Trade journals for first 15 trades
    for tid in trade_ids[:15]:
        execute("""
            INSERT INTO journal_entries (entry_type, entry_date, trade_id, analysis, execution, psychology, lessons, grade, mood)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            "trade", datetime.now().strftime("%Y-%m-%d"), tid,
            random.choice(analyses).format(price=round(random.uniform(1.08, 1.10), 4)),
            random.choice(executions),
            random.choice(psychologies),
            random.choice(lessons_list),
            random.choice(grades),
            random.randint(4, 9)
        ))


if __name__ == "__main__":
    seed_all(clear_first="--reset" in sys.argv)
    print("Done.")
