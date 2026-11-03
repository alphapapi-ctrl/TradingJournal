"""Bounded, on-demand reports without pandas, charts, or model inference."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from statistics import mean
from datetime import date

import database as db
from journal_mcp.records import get_record, where_clause
from utils.playbook_logic import evaluate_playbook_rules, get_playbook
from utils.cashflows import is_dividend, cash_summary, account_net, legacy_fx, unrealised

MAX_REPORT_TRADES = 20000


def net(trade: dict) -> float:
    return float(trade.get("pnl") or 0) - abs(float(trade.get("commission") or 0)) - abs(float(trade.get("swap") or 0))


def performance(rows: list[dict]) -> dict:
    """Trade outcomes only; native cash income never participates in win ratios."""
    rows = sorted((row for row in rows if not is_dividend(row)), key=lambda r: (r.get("exit_time") or "", r["id"]))
    if not rows:
        return {"total_trades": 0, "net_pnl": 0, "equity_curve": []}
    values = [net(row) for row in rows]
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    avg_win, avg_loss = mean(wins) if wins else 0, mean(losses) if losses else 0
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    cumulative, peak, drawdown = 0.0, 0.0, 0.0
    win_streak = loss_streak = longest_win = longest_loss = 0
    curve, monthly = [], defaultdict(float)
    for row, value in zip(rows, values):
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
        monthly[(row.get("exit_time") or "Unknown")[:7]] += value
        win_streak = win_streak + 1 if value > 0 else 0
        loss_streak = loss_streak + 1 if value < 0 else 0
        longest_win, longest_loss = max(longest_win, win_streak), max(longest_loss, loss_streak)
        curve.append({"trade_id": row["id"], "entry_time": row.get("entry_time"), "exit_time": row.get("exit_time"),
                      "date": row.get("exit_time"), "cumulative_pnl": round(cumulative, 2)})
    def grouped(field):
        groups = defaultdict(list)
        for row in rows:
            groups[row.get(field)].append(net(row))
        return [{field: key, "trades": len(vals), "net_pnl": round(sum(vals), 2),
                 "avg_pnl": round(mean(vals), 2), "win_rate": round(sum(v > 0 for v in vals) / len(vals) * 100, 1)}
                for key, vals in groups.items()]
    stride = max(1, (len(curve) + 498) // 499)
    sampled = curve[::stride]
    if sampled[-1] != curve[-1]:
        sampled.append(curve[-1])
    return {
        "total_trades": len(rows), "wins": len(wins), "losses": len(losses),
        "breakeven": len(rows) - len(wins) - len(losses),
        "win_rate": round(len(wins) / len(rows) * 100, 1),
        "net_pnl": round(sum(values), 2), "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2), "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "rr_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss else None,
        "expectancy": round(mean(values), 2), "max_drawdown": round(drawdown, 2),
        "best_trade": round(max(values), 2), "worst_trade": round(min(values), 2),
        "max_win_streak": longest_win, "max_loss_streak": longest_loss,
        "by_symbol": grouped("symbol"), "by_direction": grouped("direction"),
        "monthly_pnl": [{"month": key, "pnl": round(val, 2)} for key, val in sorted(monthly.items())],
        "equity_curve": sampled, "equity_curve_sampled": stride > 1,
    }


def generate_report(kind: str = "all", account_id: int | None = None, start_date: str | None = None,
                    end_date: str | None = None, symbol: str | None = None, playbook_id: int | None = None,
                    include_test: bool = False, date_basis: str = "cashflow", valuations: list[dict] | None = None) -> dict:
    if kind not in ("all", "performance", "categories", "discipline", "playbooks", "risk", "open_positions"):
        raise ValueError("kind must be all, performance, categories, discipline, playbooks, risk, or open_positions")
    filters = {k: v for k, v in {"account_id": account_id, "symbol": symbol, "playbook_id": playbook_id}.items() if v is not None}
    if account_id is not None:
        get_record("accounts", account_id)
    if playbook_id is not None:
        get_record("playbooks", playbook_id)
    if date_basis not in ("cashflow", "entry"):
        raise ValueError("date_basis must be cashflow or entry")
    if start_date:
        date.fromisoformat(start_date)
    if end_date:
        date.fromisoformat(end_date)
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must precede end_date")
    if valuations is not None and (not isinstance(valuations, list) or len(valuations) > 500):
        raise ValueError("Supply up to 500 explicit open-position valuations")
    marks = {}
    for mark in valuations or []:
        if not isinstance(mark, dict) or not isinstance(mark.get("trade_id"), int) or mark["trade_id"] in marks:
            raise ValueError("Valuations require unique trade_id values")
        if not mark.get("as_of"):
            raise ValueError("Valuation as_of date/time is required")
        date.fromisoformat(mark["as_of"][:10])
        marks[mark["trade_id"]] = mark
    with db.transaction(write=False):
        where, params = where_clause("trades", filters)
        clauses = [where[7:]] if where else []
        date_column = "entry_time" if date_basis == "entry" else "CASE WHEN status='closed' THEN exit_time ELSE entry_time END"
        for boundary, operator in ((start_date, ">="), (end_date, "<=")):
            if boundary:
                clauses.append(f"substr({date_column},1,10) {operator} ?")
                params.append(boundary)
        base_where = " WHERE " + " AND ".join(clauses) if clauses else ""
        test_sql = "(COALESCE(is_test,0)=1 OR UPPER(COALESCE(broker,''))='DUMMY' OR substr(UPPER(COALESCE(broker_trade_id,'')),1,6)='DUMMY_')"
        # Respect the explicit column and the older raw-data test marker.
        test_sql = f"({test_sql} OR COALESCE(json_extract(CASE WHEN json_valid(raw_data) THEN raw_data ELSE '{{}}' END,'$.is_test'),0)=1)"
        excluded = db.fetch_one(f"SELECT COUNT(*) n FROM trades{base_where}{' AND ' if clauses else ' WHERE '}{test_sql}", params)["n"] if not include_test else 0
        if not include_test:
            clauses.append("NOT " + test_sql)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = db.fetch_all(f"SELECT * FROM trades{where} ORDER BY exit_time,id LIMIT ?", params + [MAX_REPORT_TRADES + 1])
        if len(rows) > MAX_REPORT_TRADES:
            raise ValueError(f"Report exceeds {MAX_REPORT_TRADES} trades; narrow the date/account/symbol filters")
        accounts = {a["id"]: a for a in db.fetch_all("SELECT id,name,currency FROM accounts")}
        open_ids = {r["id"] for r in rows if r["status"] == "open" and not is_dividend(r)}
        if set(marks) - open_ids:
            raise ValueError("Valuation trade_id must identify an open trade in the selected report")
        currency = lambda row: (accounts.get(row.get("account_id")) or {}).get("currency") or "UNASSIGNED"
        groups = defaultdict(list)
        for row in rows:
            groups[currency(row)].append(row)
        result = {
            "filters": {"account_id": account_id, "start_date": start_date, "end_date": end_date, "symbol": symbol, "playbook_id": playbook_id,
                        "include_test": include_test, "date_basis": date_basis},
            "methodology": {
                "date_basis": f"{date_basis}: closed trade exit dates / dividend payment dates; open entries use entry dates; inclusive calendar dates",
                "net_pnl": "pnl - abs(commission) - abs(swap), matching general performance reports",
                "drawdown": "Cumulative closed net P&L, including initial zero equity peak",
                "currency": "Stored P&L and costs in account currency; explicit dated FX metadata required for cross-currency valuation. Unknown amounts excluded with record IDs.",
                "income": "Dividends excluded from trade counts/ratios/discipline/playbooks; included once in realised cashflow and cash equity on payment dates. Unrealised price/FX gains remain separate.",
                "test_records": "Excluded by default: is_test, raw_data.is_test, DUMMY broker or DUMMY_ broker IDs. Demo account type alone is not a test flag.",
                "undefined_ratios": "null when no denominator exists",
                "review_selection": "Most recent trade review, otherwise most recent position review; each trade counted once",
            }, "currencies": {}, "trade_count": sum(not is_dividend(r) for r in rows),
            "cash_event_count": sum(is_dividend(r) for r in rows), "record_count": len(rows), "excluded_test_records": excluded,
        }
        # Select only review metadata: never load journal prose into a report.
        reviews = db.fetch_all(
            "SELECT id,trade_id,position_id,stage,grade,mood,trade_category FROM journal_entries "
            "WHERE entry_type='trade' AND NOT EXISTS(SELECT 1 FROM pattern_entries p WHERE p.legacy_journal_entry_id=journal_entries.id) ORDER BY updated_at,id") if kind != "performance" and kind != "open_positions" else []
        lookup = {}
        for review in reviews:
            if review.get("trade_id"):
                lookup[("trade", review["trade_id"], review["stage"])] = review
            if review.get("position_id"):
                lookup[("position", review["position_id"], review["stage"])] = review
        for ccy, group in groups.items():
            cash_closed = [r for r in group if r["status"] == "closed"]
            all_closed_trades = [r for r in cash_closed if not is_dividend(r)]
            closed = [r for r in all_closed_trades if account_net(r, ccy) is not None]
            section = {"closed_trade_count": len(all_closed_trades),
                       "unconverted_trade_ids": [r["id"] for r in all_closed_trades if account_net(r, ccy) is None],
                       "provisional_trade_ids": [r["id"] for r in all_closed_trades if legacy_fx(r).get("provisional")]}
            if kind in ("all", "performance"):
                section["performance"] = performance(closed)
                section["cashflow"] = cash_summary(cash_closed, ccy)
                section["by_account"] = [
                    {"account_id": aid, "name": (accounts.get(aid) or {}).get("name", "Unassigned"),
                     "cashflow": cash_summary([r for r in cash_closed if r.get("account_id") == aid], ccy),
                     **performance([r for r in closed if r.get("account_id") == aid])}
                    for aid in dict.fromkeys(r.get("account_id") for r in group)
                ]
            if kind in ("all", "performance", "open_positions"):
                section["open_positions"] = [
                    {**{k: r.get(k) for k in ("id", "account_id", "position_id", "symbol", "direction", "quantity", "entry_price", "entry_time", "stop_price", "take_profit", "quote_currency", "fx_metadata")},
                     "valuation": unrealised(r, ccy, marks[r["id"]]["price"], marks[r["id"]].get("fx"), marks[r["id"]].get("entry_fx"),
                                             marks[r["id"]]["as_of"], marks[r["id"]].get("quote_currency")) if r["id"] in marks else None}
                    for r in group if r["status"] == "open" and not is_dividend(r)][:500]
                section["open_trade_count"] = sum(r["status"] == "open" for r in group)
                section["open_positions_truncated"] = section["open_trade_count"] > 500
                valued = [r["valuation"]["unrealised_pnl"] for r in section["open_positions"] if r["valuation"] and r["valuation"]["unrealised_pnl"] is not None]
                section["unrealised"] = {"priced_trades": len(valued), "open_trades": section["open_trade_count"],
                                         "pnl": round(sum(valued), 2) if valued or not section["open_trade_count"] else None, "dividends_included": False}
                if "cashflow" in section:
                    section["total_return"] = {"realised_pnl": section["cashflow"]["realised_cash_pnl"],
                                               "unrealised_pnl": section["unrealised"]["pnl"],
                                               "pnl": round(section["cashflow"]["realised_cash_pnl"] + sum(valued), 2)
                                                      if len(valued) == section["open_trade_count"] and not section["cashflow"]["unconverted_record_ids"] else None,
                                               "complete": len(valued) == section["open_trade_count"] and not section["cashflow"]["unconverted_record_ids"]}
            grade_counts, mood, categories, discipline_months = Counter(), [], defaultdict(list), defaultdict(lambda: Counter())
            planned_count = reviewed_count = both_count = 0
            for row in closed if kind in ("all", "categories", "discipline") else []:
                review = lookup.get(("trade", row["id"], "post")) or lookup.get(("position", row.get("position_id"), "post"))
                planned = bool(lookup.get(("trade", row["id"], "pre")) or lookup.get(("position", row.get("position_id"), "pre")))
                planned_count += planned
                reviewed_count += bool(review)
                both_count += bool(review) and planned
                month = (row.get("exit_time") or "Unknown")[:7]
                discipline_months[month].update({"trades": 1, "planned": int(planned), "reviewed": int(bool(review))})
                if review:
                    if review.get("grade"):
                        grade_counts[review["grade"]] += 1
                    if review.get("mood") is not None:
                        mood.append(review["mood"])
                    if review.get("trade_category"):
                        categories[review["trade_category"]].append(row)
            if kind in ("all", "discipline"):
                section["discipline"] = {"closed_trades": len(closed), "planned": planned_count, "reviewed": reviewed_count,
                                         "planned_and_reviewed": both_count, "unplanned": len(closed) - planned_count,
                                         "unreviewed": len(closed) - reviewed_count, "grades": dict(grade_counts),
                                         "average_mood": round(mean(mood), 2) if mood else None,
                                         "monthly": [{"month": key, **counts} for key, counts in sorted(discipline_months.items())]}
            if kind in ("all", "categories"):
                section["categories"] = [{"category": key, **performance(value)} for key, value in sorted(categories.items())]
                section["uncategorised_trades"] = len(closed) - sum(len(g) for g in categories.values())
            if kind in ("all", "playbooks", "risk"):
                section["playbooks"] = []
                for pid in dict.fromkeys(r.get("playbook_id") for r in closed if r.get("playbook_id")):
                    pb = get_playbook(pid)
                    if not pb:
                        continue
                    pb_rows = [r for r in closed if r.get("playbook_id") == pid]
                    rule_counts, risk_groups = {}, defaultdict(list)
                    rules = pb["rules"]
                    for row in pb_rows:
                        try:
                            checks = json.loads(row.get("playbook_rules_met") or "{}")
                            if not isinstance(checks, dict):
                                checks = {}
                        except (ValueError, TypeError):
                            checks = {}
                        if row.get("playbook_rules_met") is not None:
                            assessment = evaluate_playbook_rules(pb, checks)
                            risk_groups[assessment.get("risk_level", "normal")].append(row)
                        for rule in rules:
                            s = rule_counts.setdefault(rule["id"], {**rule, "met": 0, "evaluated": 0, "pnl_when_met": 0.0, "pnl_when_not_met": 0.0})
                            if row.get("playbook_rules_met") is not None:
                                met = bool(checks.get(str(rule["id"]), False))
                                s["evaluated"] += 1
                                s["met"] += met
                                s["pnl_when_met" if met else "pnl_when_not_met"] += net(row)
                    for s in rule_counts.values():
                        s["compliance_pct"] = round(s["met"] / s["evaluated"] * 100, 1) if s["evaluated"] else None
                    section["playbooks"].append({"id": pb["id"], "name": pb["name"], "performance": performance(pb_rows), "rules": list(rule_counts.values()),
                                                  "unassessed_trades": sum(row.get("playbook_rules_met") is None for row in pb_rows),
                                                  "risk_outcomes": [{"risk_level": key, **performance(value)} for key, value in risk_groups.items()]})
            result["currencies"][ccy] = section
        return result


def markdown_report(report: dict) -> str:
    lines = ["# Trading journal report", "", "Filters: " + json.dumps(report["filters"]), ""]
    for ccy, sections in report["currencies"].items():
        lines += [f"## {ccy}", ""]
        for name, section in sections.items():
            lines += [f"### {name.replace('_', ' ').title()}", "", "```json", json.dumps(section, indent=2, ensure_ascii=False, allow_nan=False), "```", ""]
    lines += ["## Calculation notes", ""] + [f"- {key}: {value}" for key, value in report["methodology"].items()]
    return "\n".join(lines)
