"""Cash events and explicit quote-to-account valuation, with no network calls."""
from __future__ import annotations

import json
import math
from datetime import date


def object_value(value):
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(value or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def is_dividend(row):
    return row.get("trade_type") == "dividend"


def is_test(row):
    raw = object_value(row.get("raw_data"))
    return bool(row.get("is_test") or raw.get("is_test") or str(row.get("broker", "")).upper() == "DUMMY"
                or str(row.get("broker_trade_id") or "").upper().startswith("DUMMY_"))


def real_trade_sql(alias="", include_test=False):
    """Internal SQL predicate shared by browser statistics (no user SQL)."""
    if alias not in ("", "t"):
        raise ValueError("Unknown trade table alias")
    prefix = alias + "." if alias else ""
    trade = f"COALESCE({prefix}trade_type,'')<>'dividend'"
    if include_test:
        return trade
    return trade + " AND " + non_test_sql(alias)


def non_test_sql(alias=""):
    if alias not in ("", "t"):
        raise ValueError("Unknown trade table alias")
    prefix = alias + "." if alias else ""
    return (f"COALESCE({prefix}is_test,0)=0 AND UPPER(COALESCE({prefix}broker,''))<>'DUMMY' "
            f"AND substr(UPPER(COALESCE({prefix}broker_trade_id,'')),1,6)<>'DUMMY_' "
            f"AND COALESCE(json_extract(CASE WHEN json_valid({prefix}raw_data) THEN {prefix}raw_data ELSE '{{}}' END,'$.is_test'),0)<>1")


def quote_currency(row, account_currency=None):
    raw = object_value(row.get("raw_data"))
    explicit = row.get("quote_currency") or raw.get("quote_currency")
    if explicit:
        return explicit.upper()
    exchange = str(raw.get("exchange") or "").upper()
    if exchange in ("ASX", "AU") or str(row.get("symbol", "")).endswith(".AX"):
        return "AUD"
    if exchange in ("AMEX", "NASDAQ", "NYSE", "BOATS", "BATS", "CBOE", "ARCA", "NYSEARCA"):
        return "USD"
    return account_currency  # Legacy/manual P&L uses the account's stated units.


def currency_code(value):
    if not isinstance(value, str) or len(value) != 3 or not value.isalpha():
        raise ValueError("Currency must be a three-letter code")
    return value.upper()


def fx_observation(value, requested_date=None):
    if not isinstance(value, dict):
        raise ValueError("FX observation must be an object")
    result = dict(value)
    rate = result.get("account_per_quote")
    if rate is None and result.get("usd_per_aud") is not None:
        rate = 1 / positive(result["usd_per_aud"], "usd_per_aud")
    result["account_per_quote"] = positive(rate, "account_per_quote")
    if not isinstance(result.get("source"), str) or not result["source"].strip():
        raise ValueError("FX requires a source")
    for key in ("rate_date",):
        date.fromisoformat(result.get(key, ""))
    if result.get("field") not in ("open", "close", "spot", "broker"):
        raise ValueError("FX field must be open, close, spot or broker")
    if requested_date:
        requested = date.fromisoformat(str(requested_date)[:10])
        age = (requested - date.fromisoformat(result["rate_date"])).days
        if age < 0 or age > 7:
            raise ValueError("FX must be dated on or within seven prior days of the cashflow")
        result["requested_date"] = requested.isoformat()
    result["approximate"] = bool(result.get("approximate", True))
    return result


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


def rate_for(day, rates):
    requested = date.fromisoformat(str(day)[:10]).isoformat()
    if not isinstance(rates, dict):
        raise ValueError("Cross-currency imports require fx_rates keyed by YYYY-MM-DD")
    # Explicit event-date keys can point to a prior market date (holiday/weekend).
    keys = []
    for key in rates:
        date.fromisoformat(key)
        if key <= requested:
            keys.append(key)
    if not keys:
        raise ValueError(f"Missing dated FX for {requested}")
    return fx_observation(rates[max(keys)], requested)


def legacy_fx(row):
    raw = object_value(row.get("raw_data"))
    current = object_value(row.get("fx_metadata"))
    if current:
        reconciliation = object_value(raw.get("reconciliation_20261103"))
        return {**current, "provisional": bool(current.get("provisional") or reconciliation.get("entry_discrepancy")),
                "reconciled": bool(current.get("reconciled") or raw.get("dividend_import_20261103") or reconciliation.get("account_currency_pnl"))}
    # Retain compatibility with the independently reconciled November 3 records.
    cash = object_value(raw.get("dividend_import_20261103"))
    if cash:
        return {"payment": cash.get("fx"), "pnl_currency": raw.get("account_currency"), "reconciled": True}
    reconciliation = object_value(raw.get("reconciliation_20261103"))
    pnl = object_value(reconciliation.get("account_currency_pnl"))
    return {"entry": pnl.get("entry_fx"), "exit": pnl.get("exit_fx"), "pnl_currency": pnl.get("currency"),
            "provisional": bool(reconciliation.get("entry_discrepancy")), "reconciled": bool(pnl)}


def entry_fx_field(row):
    """Retain the explicit opening-price basis of authorised demo rebalances."""
    raw = object_value(row.get("raw_data"))
    source = object_value(object_value(raw.get("reconciliation_20261103")).get("source_buy"))
    if source.get("kind") == "user_authorised_demo_rebalance" and str(source.get("file", "")).lower().endswith("_open.csv"):
        return "open"
    return raw.get("entry_fx_field") or "close"


def prepare_import(row, account_currency, explicit_quote=None, fx_rates=None):
    """Convert new TradingView source amounts; original source/provenance survives."""
    row = dict(row)
    raw = object_value(row.get("raw_data"))
    account_currency = currency_code(account_currency)
    quote = currency_code(explicit_quote or quote_currency(row))
    row["quote_currency"] = quote
    raw.update(quote_currency=quote, account_currency=account_currency)
    raw["currency_basis"] = "Explicit quote override" if explicit_quote else "Exchange inference; TradingView export has no currency column"
    if isinstance(raw.get("dividend_source"), dict):
        raw["dividend_source"].update(currency_original=quote, currency_basis=raw["currency_basis"])
    metadata = {"quote_currency": quote, "pnl_currency": account_currency, "cost_currency": account_currency,
                "original_currency": quote, "original_pnl": row.get("pnl"), "original_commission": row.get("commission", 0),
                "original_swap": row.get("swap", 0)}
    if quote != account_currency:
        if is_dividend(row):
            payment = rate_for(row["exit_time"], fx_rates)
            metadata["payment"] = payment
            row["pnl"] = round(float(row["pnl"]) * payment["account_per_quote"], 2)
            row["commission"] = round(float(row.get("commission") or 0) * payment["account_per_quote"], 4)
        else:
            entry = rate_for(row["entry_time"], fx_rates)
            metadata["entry"] = entry
            if row["status"] == "closed":
                exit_fx = rate_for(row["exit_time"], fx_rates)
                metadata["exit"] = exit_fx
                sign = 1 if row["direction"] == "LONG" else -1
                lots = raw.get("quote_entry_lots") or [{"quantity": row["quantity"], "price": row["entry_price"],
                                                       "entry_time": row["entry_time"], "commission": raw.get("quote_costs", {}).get("entry", 0)}]
                metadata["entry_lots"] = [{**lot, "fx": rate_for(lot["entry_time"], fx_rates)} for lot in lots]
                cost = sum(lot["quantity"] * lot["price"] * lot["fx"]["account_per_quote"] for lot in metadata["entry_lots"])
                metadata["cost_basis_account"] = cost
                row["pnl"] = round((row["exit_price"] * exit_fx["account_per_quote"] * row["quantity"] - cost) * sign, 2)
                # Commissions originate at each fill, not entirely at the exit rate.
                costs = raw.get("quote_costs", {})
                row["commission"] = round(sum(float(lot.get("commission", 0)) * lot["fx"]["account_per_quote"] for lot in metadata["entry_lots"]) +
                                          float(costs.get("exit", 0)) * exit_fx["account_per_quote"], 4)
            else:
                row["commission"] = round(float(row.get("commission") or 0) * entry["account_per_quote"], 4)
            row["swap"] = 0  # TradingView CSV has no swap field.
    row["fx_metadata"] = metadata
    row["raw_data"] = raw
    return row


def validate_money(row, account_currency):
    if row.get("is_test") not in (None, 0, 1):
        raise ValueError("is_test must be 0 or 1")
    if row.get("quote_currency"):
        currency_code(row["quote_currency"])
    metadata = object_value(row.get("fx_metadata"))
    if row.get("quote_currency") and row["quote_currency"].upper() != account_currency and not metadata and not legacy_fx(row).get("reconciled"):
        raise ValueError("Explicit cross-currency records require dated FX metadata")
    if metadata:
        if metadata.get("quote_currency") not in (None, quote_currency(row, account_currency)):
            raise ValueError("FX metadata quote currency does not match the instrument")
        if metadata.get("pnl_currency") != account_currency or metadata.get("cost_currency", account_currency) != account_currency:
            raise ValueError("P&L and costs must use the linked account currency")
        for key, when in (("entry", row.get("entry_time")), ("exit", row.get("exit_time")), ("payment", row.get("exit_time"))):
            if metadata.get(key):
                fx_observation(metadata[key], when)
        if metadata.get("entry_lots"):
            for lot in metadata["entry_lots"]:
                fx_observation(lot["fx"], lot["entry_time"])
                positive(lot["quantity"], "lot quantity")
                positive(lot["price"], "lot price")
            if abs(sum(lot["quantity"] for lot in metadata["entry_lots"]) - row["quantity"]) > 1e-6:
                raise ValueError("FX entry-lot quantities must match the trade quantity")
        if quote_currency(row, account_currency) != account_currency:
            required = ("payment",) if is_dividend(row) else ("entry", "exit") if row.get("status") == "closed" else ("entry",)
            if any(not metadata.get(key) for key in required):
                raise ValueError("Cross-currency record requires dated FX metadata")
    if is_dividend(row):
        if isinstance(row.get("pnl"), bool) or not isinstance(row.get("pnl"), (int, float)) or not math.isfinite(row["pnl"]):
            raise ValueError("Dividend P&L must be explicit finite account-currency cash")
        if quote_currency(row, account_currency) != account_currency and not metadata and not legacy_fx(row).get("reconciled"):
            raise ValueError("Cross-currency dividend requires dated FX provenance")
        if not row.get("account_id") or row.get("status") != "closed" or row.get("quantity") != 0 or row.get("entry_price") != 0 or row.get("exit_price") != 0:
            raise ValueError("Dividends require an account, closed status, zero quantity and zero entry/exit prices")
        if not row.get("exit_time") or str(row["entry_time"])[:10] != str(row["exit_time"])[:10]:
            raise ValueError("Dividend entry and exit dates must both be the payment date")
        if row.get("position_id") or row.get("playbook_id") or row.get("instrument_spec_id"):
            raise ValueError("Cash dividends cannot be linked to trading positions, playbooks or contract sizing")


def account_net(row, account_currency):
    """Unknown cross-currency units stay unknown, rather than being relabelled."""
    quote = quote_currency(row, account_currency)
    metadata = legacy_fx(row)
    if metadata.get("pnl_currency") not in (None, account_currency):
        return None
    if quote != account_currency and metadata.get("pnl_currency") != account_currency:
        return None
    return float(row.get("pnl") or 0) - abs(float(row.get("commission") or 0)) - abs(float(row.get("swap") or 0))


def unrealised(row, account_currency, price, current_fx=None, entry_fx=None, as_of=None, supplied_quote=None):
    positive(price, "price")
    if row.get("instrument_spec_id"):
        raise ValueError("This stock/ETF cost-basis valuation does not support derivative contract marks")
    quote = currency_code(supplied_quote or quote_currency(row, account_currency))
    known_quote = row.get("quote_currency") or object_value(row.get("raw_data")).get("quote_currency")
    if known_quote and quote != known_quote.upper():
        raise ValueError("Valuation quote currency does not match the recorded instrument")
    entry = legacy_fx(row).get("entry") or entry_fx
    quantity = float(row["quantity"])
    sign = 1 if row["direction"] == "LONG" else -1
    if quote != account_currency:
        if not entry or not current_fx:
            return {"unrealised_pnl": None, "currency": account_currency, "quote_currency": quote,
                    "reason": "Entry-date and valuation-date FX are required"}
        entry = fx_observation(entry, row["entry_time"])
        if entry_fx_field(row) == "open" and entry["field"] == "close":
            raise ValueError("This opening-price rebalance requires opening-date FX, not daily close FX")
        current_fx = fx_observation(current_fx, as_of)
        cost = float(row["entry_price"]) * quantity * entry["account_per_quote"]
        value = price * quantity * current_fx["account_per_quote"]
    else:
        cost = float(row["entry_price"]) * quantity
        value = price * quantity
    fees = abs(float(row.get("commission") or 0)) + abs(float(row.get("swap") or 0))
    return {"unrealised_pnl": round((value - cost) * sign - fees, 2), "cost_basis": round(cost + fees * sign, 2),
            "gross_cost_basis": round(cost, 2), "entry_costs": round(fees, 2),
            "market_value": round(value, 2), "currency": account_currency, "quote_currency": quote,
            "as_of": as_of, "entry_fx": entry, "current_fx": current_fx,
            "dividends_included": False, "costs_included": True}


def cash_summary(rows, account_currency):
    events = []
    unknown = []
    for row in rows:
        amount = account_net(row, account_currency)
        if amount is None:
            unknown.append(row["id"])
        else:
            events.append({"id": row["id"], "account_id": row.get("account_id"), "date": row.get("exit_time"),
                           "kind": "dividend" if is_dividend(row) else "trade", "amount": amount})
    events.sort(key=lambda e: (e["date"] or "", e["id"]))
    cumulative = peak = drawdown = 0.0
    curve, monthly = [], {}
    for event in events:
        cumulative += event["amount"]
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
        month = (event["date"] or "Unknown")[:7]
        monthly[month] = monthly.get(month, 0) + event["amount"]
        curve.append({**event, "amount": round(event["amount"], 2), "cumulative_pnl": round(cumulative, 2)})
    stride = max(1, (len(curve) + 498) // 499)
    sampled = curve[::stride]
    if curve and sampled[-1] != curve[-1]:
        sampled.append(curve[-1])
    dividends = [e for e in events if e["kind"] == "dividend"]
    return {"currency": account_currency, "dividend_count": len(dividends),
            "dividend_income": round(sum(e["amount"] for e in dividends), 2),
            "trade_net_pnl": round(sum(e["amount"] for e in events if e["kind"] == "trade"), 2),
            "realised_cash_pnl": round(cumulative, 2), "unconverted_record_ids": unknown,
            "max_drawdown": round(drawdown, 2), "equity_curve": sampled, "equity_curve_sampled": stride > 1,
            "monthly_pnl": [{"month": k, "pnl": round(v, 2)} for k, v in sorted(monthly.items())]}
