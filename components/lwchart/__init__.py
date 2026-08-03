"""lwchart — custom Streamlit component wrapping TradingView lightweight-charts.

The component receives a full day of 1-minute bars once (keyed by data_key)
and handles timeframe aggregation, replay playback and drag interactions
client-side. It reports coarse events back as its return value:
    {"seq": int, "type": str, "payload": dict, "data_key": str}
Events must be deduplicated by callers using `seq` (the same value is
returned on every rerun until a new event fires).
"""
import os

import streamlit.components.v1 as components

_frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
_component = components.declare_component("lwchart", path=_frontend_dir)


def lwchart(
    *,
    bars_1m: list,
    data_key: str,
    mode: str = "static",
    levels: list | None = None,
    colors: dict | None = None,
    indicators: list | None = None,
    replay: dict | None = None,
    orders: list | None = None,
    requests: list | None = None,
    ticks: dict | None = None,
    vprofile: dict | None = None,
    ack: int = 0,
    session_start: int | None = None,
    default_tf: int | None = None,
    height: int = 650,
    key: str | None = None,
):
    """Render the chart. Returns the last event dict (or None).

    bars_1m    : [{time, open, high, low, close, volume}] — `time` is epoch
                 seconds pre-shifted so the chart displays exchange-local time.
    data_key   : identity of the loaded day; changing it re-initialises the
                 chart (and resets replay state). Keep it stable across reruns.
    mode       : "static" (reference) or "replay".
    levels     : [{price, title, color, style: solid|dotted|dashed, width, visible}]
    colors     : {up, down, wick_up, wick_down, border_up, border_down,
                  border_visible, bg, text, grid, accent, toolbar_bg,
                  number_candles: bool, number_color}
    indicators : [{type: ema|sma|vwap|rsi, period, color, width, label}]
    replay     : {session_end: epoch, start_cursor: int, speed: float, ccy: str}
                 start_cursor is a TICK index when `ticks` is given, else a
                 bar index into bars_1m.
    ticks      : {base: shifted-epoch ms, dt: [ms deltas], p: [mid prices]} —
                 enables the true-tick replay engine (replay mode only).
    orders     : [{id, kind, side, price, sl, tp, qty, draggable}] (replay mode)
    """
    return _component(
        bars_1m=bars_1m,
        data_key=data_key,
        mode=mode,
        levels=levels or [],
        colors=colors or {},
        indicators=indicators or [],
        replay=replay,
        orders=orders or [],
        requests=requests or [],
        ticks=ticks,
        vprofile=vprofile,
        # last event seq the caller processed — changing it forces Streamlit to
        # re-render the component (its ack), un-blocking the JS event queue
        ack=ack,
        session_start=session_start,
        default_tf=default_tf,
        height=height,
        key=key,
        default=None,
    )
