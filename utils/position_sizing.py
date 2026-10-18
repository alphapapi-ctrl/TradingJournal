"""Cash-risk sizing from price distance and contract units; no tick data needed."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR


IC_MARKETS_CONTRACTS = {
    "ic_gold": {"label": "IC Markets Gold (XAUUSD.a)", "symbol": "XAUUSD.a",
        "contract_size": 100, "min_quantity": 0.01, "quantity_step": 0.01,
        "max_quantity": 100, "currency": "USD",
        "source": "https://cdn.icmarkets.com.au/uploads/AU/Commodity-Specification-Sheet.pdf"},
    "ic_us500": {"label": "IC Markets US500 (US500.a)", "symbol": "US500.a",
        "contract_size": 1, "min_quantity": 0.1, "quantity_step": 0.1,
        "max_quantity": 250, "currency": "USD",
        "source": "https://cdn.icmarkets.com.au/uploads/AU/Indices-Product-Specification-Sheet-ASIC.pdf"},
}


def decimal_number(value, name, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result == 0):
            raise ValueError()
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}") from None


def size_cash_risk(entry_price, stop_price, risk_budget, point_value=1,
                   quantity_step=1, min_quantity=1, max_quantity=None,
                   costs_per_unit=0, direction="LONG"):
    """point_value is cash per full 1.00 price move per lot/share, already scaled."""
    entry = decimal_number(entry_price, "Entry price", True)
    stop = decimal_number(stop_price, "Stop price", True)
    budget = decimal_number(risk_budget, "Risk budget")
    point = decimal_number(point_value, "Point value", True)
    step = decimal_number(quantity_step, "Lot/share step", True)
    minimum = decimal_number(min_quantity, "Minimum quantity", True)
    costs = decimal_number(costs_per_unit, "Cost allowance")
    if direction not in ("LONG", "SHORT"):
        raise ValueError("Direction must be LONG or SHORT")
    if (direction == "LONG" and stop >= entry) or (direction == "SHORT" and stop <= entry):
        raise ValueError("Stop must be below a long entry or above a short entry")
    if minimum % step:
        raise ValueError("Minimum quantity must be a multiple of the quantity step")
    maximum = None if max_quantity is None else decimal_number(max_quantity, "Maximum quantity", True)
    if maximum is not None and (maximum < minimum or maximum % step):
        raise ValueError("Maximum quantity must meet the minimum and quantity step")
    loss = abs(entry - stop) * point + costs
    raw = (budget / loss / step).to_integral_value(rounding=ROUND_FLOOR) * step
    capped = maximum is not None and raw > maximum
    quantity = min(raw, maximum) if maximum is not None else raw
    below = quantity < minimum
    if below:
        quantity = Decimal(0)
    return {"quantity": float(quantity), "actual_risk": float(quantity * loss),
            "risk_per_unit": float(loss), "stop_distance": float(abs(entry - stop)),
            "position_value": float(quantity * entry * point),
            "below_minimum": below, "capped_at_maximum": capped}
