// lwchart — TradingView lightweight-charts (v5) Streamlit component.
//
// Replay has two engines:
//  - TICK engine (preferred): args.ticks = {base, dt[], p[]} — real tick
//    timestamps drive playback on a wall-clock scheduler, so 1x reproduces
//    the true rhythm of the tape (bursts and lulls included) and fills
//    execute on actual tick prices.
//  - BAR engine (fallback): 1-minute bars stepped O→extreme→extreme→C.
// Python re-validates every fill before money moves.
(function () {
  "use strict";

  const LWC = window.LightweightCharts;
  const LineStyle = { solid: 0, dotted: 1, dashed: 2, "large-dashed": 3, "sparse-dotted": 4 };

  const S = {
    dataKey: null,
    mode: "static",
    bars1m: [],          // static/bar-mode: full series; tick-mode: prior-day bars only
    tf: 5,
    chart: null,
    candles: null,
    markersApi: null,
    overlaySeries: [],
    paneSeries: [],
    priceLines: [],
    colors: {},
    indicators: [],
    levels: [],
    sessionStart: null,
    height: 650,
    seq: 0,
    queue: [],
    awaitingAck: false,
    // replay (shared)
    replay: null,
    playing: false,
    speed: 1,
    timerId: null,
    // bar engine
    cursor: 0,
    subStep: 0,
    // tick engine
    tickMode: false,
    tTimes: null,        // Float64Array of absolute shifted-epoch ms
    tBids: null,
    tAsks: null,
    tickIdx: 0,
    dayBars: [],         // 1m bars built live from tick mids
    curBar: null,
    lastPx: null,        // mid
    lastBid: null,
    lastAsk: null,
    simMs: 0,
    lastFrameTs: 0,
    fastForwarding: false,
    // trading
    orders: [],
    orderLines: [],
    locallyFilled: new Set(),
    locallyExited: new Set(),
    doneReqs: new Set(),
    drag: null,
    // volume-profile overlay + user view anchoring
    vprofile: null,
    vpCanvas: null,
    userRange: null,     // last user-visible time range (left-anchor on re-adjust)
    restoringRange: false,
  };

  // ── Event queue ─────────────────────────────────────────────────────────
  function replayCursor() { return S.tickMode ? S.tickIdx : S.cursor; }

  function queueEvent(type, payload) {
    payload = payload || {};
    payload.px = currentPrice();
    payload.bid = S.lastBid != null ? S.lastBid : payload.px;
    payload.ask = S.lastAsk != null ? S.lastAsk : payload.px;
    payload.cursor = replayCursor();
    payload.sub_step = S.tickMode ? 0 : S.subStep;
    payload.speed = S.speed;
    if (payload.bar_time === undefined) payload.bar_time = currentBarTime();
    S.queue.push({ type, payload });
    pumpQueue();
  }

  function pumpQueue() {
    if (S.awaitingAck || !S.queue.length) return;
    const ev = S.queue.shift();
    S.seq += 1;
    S.awaitingAck = true;
    S.lastSent = { seq: S.seq, type: ev.type, payload: ev.payload, data_key: S.dataKey };
    S.lastSentAt = performance.now();
    Streamlit.setComponentValue(S.lastSent);
  }

  // Self-healing: if an ack render never arrives (lost rerun/render message),
  // re-send the in-flight event — Python dedupes by seq, so this is safe.
  setInterval(() => {
    if (S.awaitingAck && S.lastSent && performance.now() - (S.lastSentAt || 0) > 3000) {
      S.lastSentAt = performance.now();
      // bump a nonce so Streamlit sees a changed value and reruns
      S.lastSent.resend = (S.lastSent.resend || 0) + 1;
      Streamlit.setComponentValue(S.lastSent);
    }
  }, 1500);

  // ── Aggregation & indicators ────────────────────────────────────────────
  function bucketTime(t, tfMin) {
    const sec = tfMin * 60;
    return Math.floor(t / sec) * sec;
  }

  function aggregate(bars, tfMin) {
    if (tfMin <= 1) return bars.slice();
    const out = [];
    let cur = null;
    for (const b of bars) {
      const bt = bucketTime(b.time, tfMin);
      if (!cur || cur.time !== bt) {
        if (cur) out.push(cur);
        cur = { time: bt, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume || 0 };
      } else {
        cur.high = Math.max(cur.high, b.high);
        cur.low = Math.min(cur.low, b.low);
        cur.close = b.close;
        cur.volume += b.volume || 0;
      }
    }
    if (cur) out.push(cur);
    return out;
  }

  function ema(bars, period) {
    const k = 2 / (period + 1);
    let prev = null;
    return bars.map((b, i) => {
      prev = prev === null ? b.close : b.close * k + prev * (1 - k);
      return { time: b.time, value: prev, _warm: i >= period - 1 };
    }).filter((p) => p._warm).map((p) => ({ time: p.time, value: p.value }));
  }

  function sma(bars, period) {
    const out = [];
    let sum = 0;
    for (let i = 0; i < bars.length; i++) {
      sum += bars[i].close;
      if (i >= period) sum -= bars[i - period].close;
      if (i >= period - 1) out.push({ time: bars[i].time, value: sum / period });
    }
    return out;
  }

  function vwap(bars) {
    let pv = 0, vol = 0;
    return bars.map((b) => {
      const tp = (b.high + b.low + b.close) / 3;
      const v = b.volume || 1;
      pv += tp * v;
      vol += v;
      return { time: b.time, value: pv / vol };
    });
  }

  function rsi(bars, period) {
    const out = [];
    let gain = 0, loss = 0;
    for (let i = 1; i < bars.length; i++) {
      const ch = bars[i].close - bars[i - 1].close;
      const g = Math.max(ch, 0), l = Math.max(-ch, 0);
      if (i <= period) {
        gain += g; loss += l;
        if (i === period) {
          gain /= period; loss /= period;
          out.push({ time: bars[i].time, value: 100 - 100 / (1 + (loss === 0 ? 1e9 : gain / loss)) });
        }
      } else {
        gain = (gain * (period - 1) + g) / period;
        loss = (loss * (period - 1) + l) / period;
        out.push({ time: bars[i].time, value: 100 - 100 / (1 + (loss === 0 ? 1e9 : gain / loss)) });
      }
    }
    return out;
  }

  // ── Chart construction ──────────────────────────────────────────────────
  function destroyChart() {
    stopTimer();
    if (S.vpTimer) { clearInterval(S.vpTimer); S.vpTimer = null; }
    if (S.chart) { S.chart.remove(); S.chart = null; }
    // remove ALL overlay canvases — a stale one left behind would show a
    // ghost volume profile from the previously loaded day
    document.querySelectorAll("#chart > canvas").forEach((c) => c.remove());
    S.vpCanvas = null;
    S.candles = null;
    S.markersApi = null;
    S.overlaySeries = [];
    S.paneSeries = [];
    S.priceLines = [];
    S.orderLines = [];
  }

  function stopTimer() {
    if (S.timerId) { clearInterval(S.timerId); S.timerId = null; }
  }

  function candleOptions() {
    const c = S.colors;
    return {
      upColor: c.up || "#00c896",
      downColor: c.down || "#ff4b6e",
      wickUpColor: c.wick_up || c.up || "#00c896",
      wickDownColor: c.wick_down || c.down || "#ff4b6e",
      borderUpColor: c.border_up || c.up || "#00c896",
      borderDownColor: c.border_down || c.down || "#ff4b6e",
      borderVisible: c.border_visible !== false,
      wickVisible: true,
    };
  }

  function buildChart() {
    destroyChart();
    const el = document.getElementById("chart");
    const toolbarH = document.getElementById("toolbar").offsetHeight;
    const c = S.colors;
    S.chart = LWC.createChart(el, {
      width: el.clientWidth || document.body.clientWidth,
      height: Math.max(200, S.height - toolbarH),
      layout: {
        background: { type: "solid", color: c.bg || "transparent" },
        textColor: c.text || "#9aa0ac",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: c.grid || "rgba(128,128,128,0.12)" },
        horzLines: { color: c.grid || "rgba(128,128,128,0.12)" },
      },
      // replay gets an MT-style "chart shift" (kept in sync with zoom by
      // updateChartShift: newest bar sits ~25% in from the right edge)
      timeScale: { timeVisible: true, secondsVisible: false,
                   rightOffset: S.mode === "replay" ? 16 : 6,
                   borderColor: c.grid || "rgba(128,128,128,0.25)" },
      rightPriceScale: { borderColor: c.grid || "rgba(128,128,128,0.25)" },
      crosshair: { mode: 0 },
    });
    S.candles = S.chart.addSeries(LWC.CandlestickSeries, candleOptions());
    S.markersApi = LWC.createSeriesMarkers(S.candles, []);
    S.chart.subscribeCrosshairMove(updateLegend);

    // translucent volume-profile overlay canvas
    S.vpCanvas = document.createElement("canvas");
    S.vpCanvas.style.cssText =
      "position:absolute;top:0;left:0;z-index:5;pointer-events:none;";
    el.appendChild(S.vpCanvas);

    // Track the range the user actually set (scroll/zoom) so re-adjustments
    // (TF switch, resize, re-render) keep the price action anchored to the
    // same LEFT edge instead of jumping to the right.
    S.chart.timeScale().subscribeVisibleTimeRangeChange((r) => {
      if (r && !S.restoringRange) S.userRange = { from: r.from, to: r.to };
      updateChartShift();
      drawVProfile();
    });
    // price autoscale settles without a time-range event — keep overlay fresh
    if (S.vpTimer) clearInterval(S.vpTimer);
    S.vpTimer = setInterval(drawVProfile, 250);

    bindDrag(el);
    window.addEventListener("resize", resize);
  }

  // MT-style chart shift, scaled with zoom: the playback anchor (newest bar)
  // sits ~25% in from the right edge regardless of bar spacing.
  function updateChartShift() {
    if (S.mode !== "replay" || !S.chart) return;
    const lr = S.chart.timeScale().getVisibleLogicalRange();
    if (!lr) return;
    const span = lr.to - lr.from;
    if (!(span > 0)) return;
    const want = Math.max(8, Math.round(span * 0.25));
    if (Math.abs(want - (S.curShift || 0)) / want > 0.15) {
      S.curShift = want;
      S.chart.applyOptions({ timeScale: { rightOffset: want } });
    }
  }

  function restoreUserRange() {
    if (!S.userRange || S.playing) return;
    S.restoringRange = true;
    try { S.chart.timeScale().setVisibleRange(S.userRange); } catch (e) { /* range outside data */ }
    S.restoringRange = false;
  }

  function drawVProfile() {
    const cv = S.vpCanvas;
    if (!cv || !S.chart || !S.candles) return;
    const el = document.getElementById("chart");
    if (cv.width !== el.clientWidth || cv.height !== el.clientHeight) {
      cv.width = el.clientWidth;
      cv.height = el.clientHeight;
    }
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);
    const vp = S.vprofile;
    if (!vp || !vp.bins || !vp.bins.length) return;

    const ts = S.chart.timeScale();
    let x0 = ts.timeToCoordinate(vp.anchor_time);
    if (x0 == null) {
      const vr = ts.getVisibleRange();
      if (!vr) return;
      x0 = vp.anchor_time < vr.from ? -1 : cv.width + 1;
    }
    if (x0 > cv.width) return;    // anchor right of viewport — nothing to draw
    x0 = Math.max(x0, 0);         // anchor scrolled off left → pin to left edge
    const maxV = Math.max.apply(null, vp.bins.map((b) => b.v));
    if (!(maxV > 0)) return;
    const maxW = cv.width * 0.16;
    for (const b of vp.bins) {
      const yTop = S.candles.priceToCoordinate(b.p1);
      const yBot = S.candles.priceToCoordinate(b.p0);
      if (yTop == null || yBot == null) continue;
      const h = Math.max(1, yBot - yTop - 1);
      const w = (b.v / maxV) * maxW;
      ctx.fillStyle = b.poc ? "rgba(255,179,64,0.45)"
        : b.va ? "rgba(74,158,255,0.22)"
        : "rgba(74,158,255,0.12)";
      ctx.fillRect(x0, yTop, w, h);
    }
  }

  function resize() {
    if (!S.chart) return;
    const toolbarH = document.getElementById("toolbar").offsetHeight;
    S.chart.applyOptions({ width: document.body.clientWidth, height: Math.max(200, S.height - toolbarH) });
    restoreUserRange();
    drawVProfile();
  }

  function updateLegend(param) {
    const el = document.getElementById("legend");
    let bar = null;
    if (param && param.seriesData && S.candles) bar = param.seriesData.get(S.candles);
    if (!bar) {
      const data = S.candles ? S.candles.data() : [];
      bar = data.length ? data[data.length - 1] : null;
    }
    if (!bar || bar.open === undefined) { el.textContent = ""; return; }
    const f = (v) => Number(v).toFixed(1);
    const up = bar.close >= bar.open;
    el.textContent = `O ${f(bar.open)}  H ${f(bar.high)}  L ${f(bar.low)}  C ${f(bar.close)}`;
    el.style.color = up ? (S.colors.up || "#00c896") : (S.colors.down || "#ff4b6e");
  }

  // ── Visible data ────────────────────────────────────────────────────────
  function visibleBars1m() {
    if (S.mode !== "replay") return S.bars1m;
    if (S.tickMode) {
      const out = S.bars1m.concat(S.dayBars);
      if (S.curBar) out.push(S.curBar);
      return out;
    }
    const out = S.bars1m.slice(0, S.cursor);
    const nb = S.bars1m[S.cursor];
    if (nb && S.subStep > 0) out.push(partialBar(nb, S.subStep));
    return out;
  }

  function partialBar(b, step) {
    const up = b.close >= b.open;
    const first = up ? b.low : b.high;
    const second = up ? b.high : b.low;
    let cl;
    if (step === 1) { cl = first; }
    else if (step === 2) { cl = second; }
    else { cl = b.close; }
    const seen = [b.open, first].concat(step >= 2 ? [second] : []).concat(step >= 3 ? [b.close] : []);
    return {
      time: b.time, open: b.open,
      high: Math.max.apply(null, seen), low: Math.min.apply(null, seen),
      close: cl, volume: b.volume,
    };
  }

  function currentPrice() {
    if (S.tickMode && S.lastPx != null) return S.lastPx;
    const v = visibleBars1m();
    return v.length ? v[v.length - 1].close : null;
  }

  function currentBarTime() {
    if (S.tickMode && S.curBar) return S.curBar.time;
    const v = visibleBars1m();
    return v.length ? v[v.length - 1].time : null;
  }

  function renderData(keepView) {
    const bars = aggregate(visibleBars1m(), S.tf);
    S.candles.setData(bars);
    renderMarkers(bars);
    renderIndicators(bars);
    if (!keepView) S.chart.timeScale().fitContent();
    else restoreUserRange();
    updateLegend(null);
    if (document.getElementById("poslegend")) updatePosLegend();
    drawVProfile();
  }

  // Number odd candles (1, 3, 5, …) counting from the focus-session start.
  function renderMarkers(bars) {
    const markers = [];
    if (S.colors.number_candles !== false) {
      let startIdx = 0;
      if (S.sessionStart) {
        startIdx = bars.findIndex((b) => b.time >= S.sessionStart);
        if (startIdx < 0) startIdx = bars.length;
      }
      for (let i = startIdx; i < bars.length; i += 2) {
        markers.push({
          time: bars[i].time,
          position: "belowBar",
          color: S.colors.number_color || "rgba(150,155,165,0.55)",
          shape: "arrowUp",
          size: 0,
          text: String(i - startIdx + 1),
        });
      }
    }
    S.markersApi.setMarkers(markers);
  }

  function renderIndicators(bars) {
    for (const s of S.overlaySeries) S.chart.removeSeries(s);
    for (const s of S.paneSeries) S.chart.removeSeries(s);
    S.overlaySeries = [];
    S.paneSeries = [];
    for (const ind of S.indicators) {
      const type = (ind.type || "").toLowerCase();
      const period = ind.period || 20;
      const color = ind.color || "#f5a623";
      const width = ind.width || 1;
      if (type === "ema" || type === "sma" || type === "vwap") {
        const data = type === "ema" ? ema(bars, period) : type === "sma" ? sma(bars, period) : vwap(bars);
        const s = S.chart.addSeries(LWC.LineSeries, {
          color: color, lineWidth: width, priceLineVisible: false, lastValueVisible: false,
          crosshairMarkerVisible: false,
          title: "", // no on-line label for overlays (EMA/SMA/VWAP)
        });
        s.setData(data);
        S.overlaySeries.push(s);
      } else if (type === "rsi") {
        const s = S.chart.addSeries(LWC.LineSeries, {
          color: color, lineWidth: width, priceLineVisible: false, lastValueVisible: true,
          title: `RSI ${period}`,
        }, 1);
        s.setData(rsi(bars, period));
        S.paneSeries.push(s);
        const panes = S.chart.panes();
        if (panes.length > 1) panes[1].setHeight(Math.round(S.height * 0.2));
      }
    }
  }

  function renderLevels() {
    for (const pl of S.priceLines) S.candles.removePriceLine(pl);
    S.priceLines = [];
    for (const lv of S.levels) {
      if (lv.visible === false || lv.price == null) continue;
      S.priceLines.push(S.candles.createPriceLine({
        price: lv.price,
        color: lv.color || "#4a9eff",
        lineWidth: lv.width || 1,
        lineStyle: LineStyle[lv.style || "dashed"] ?? 2,
        axisLabelVisible: true,
        title: lv.title || "",
      }));
    }
  }

  // ── Orders ──────────────────────────────────────────────────────────────
  const ORDER_STYLE = {
    entry: { color: "#4a9eff", style: 0, width: 2 },
    sl: { color: "#ff4b6e", style: 2, width: 2 },
    tp: { color: "#00c896", style: 2, width: 2 },
  };

  function renderOrders() {
    for (const ol of S.orderLines) S.candles.removePriceLine(ol.line);
    S.orderLines = [];
    for (const o of S.orders) {
      if (S.locallyFilled.has(o.id) && o.kind !== "position") continue;
      if (S.locallyExited.has(o.id)) continue;
      const isPos = o.kind === "position";
      const legs = [
        { field: "price", price: o.price, base: ORDER_STYLE.entry,
          title: (isPos ? "POS " : (o.kind + " ").toUpperCase()) + (o.side === "buy" ? "▲" : "▼") + " " + (o.qty ?? ""),
          draggable: !isPos },
        { field: "sl", price: o.sl, base: ORDER_STYLE.sl, title: "SL", draggable: true },
        { field: "tp", price: o.tp, base: ORDER_STYLE.tp, title: "TP", draggable: true },
      ];
      for (const leg of legs) {
        if (leg.price == null) continue;
        const line = S.candles.createPriceLine({
          price: leg.price,
          color: leg.base.color,
          lineWidth: leg.base.width,
          lineStyle: isPos && leg.field === "price" ? 1 : leg.base.style,
          axisLabelVisible: true,
          title: leg.title,
        });
        S.orderLines.push({ order: o, field: leg.field, line: line, draggable: leg.draggable });
      }
    }
  }

  function updatePosLegend() {
    const el = document.getElementById("poslegend");
    const px = currentPrice();
    const positions = S.orders.filter((o) => o.kind === "position" && !S.locallyExited.has(o.id));
    if (!positions.length || px == null || S.mode !== "replay") { el.textContent = ""; return; }
    const ccy = (S.replay && S.replay.ccy) || "$";
    let pnl = 0, risk = 0, parts = [];
    for (const p of positions) {
      const long = p.side === "buy";
      // mark-to-market on the closing side: longs vs bid, shorts vs ask
      const mark = long ? (S.lastBid != null ? S.lastBid : px)
                        : (S.lastAsk != null ? S.lastAsk : px);
      const pts = (mark - p.price) * (long ? 1 : -1);
      pnl += pts * (p.qty || 0);
      risk += p.risk || 0;
      parts.push((pts >= 0 ? "+" : "") + pts.toFixed(1) + "pts");
    }
    const r = risk > 0 ? ` | ${pnl >= 0 ? "+" : ""}${(pnl / risk).toFixed(2)}R` : "";
    el.textContent = `OPEN ${parts.join(" ")} | ${pnl >= 0 ? "+" : ""}${ccy}${pnl.toFixed(2)}${r}`;
    el.style.color = pnl >= 0 ? (S.colors.up || "#00c896") : (S.colors.down || "#ff4b6e");
  }

  // ── Dragging ────────────────────────────────────────────────────────────
  function bindDrag(container) {
    container.addEventListener("mousedown", (e) => {
      if (S.mode !== "replay" || !S.candles) return;
      const y = e.offsetY;
      for (const ol of S.orderLines) {
        if (!ol.draggable) continue;
        const cy = S.candles.priceToCoordinate(ol.line.options().price);
        if (cy != null && Math.abs(cy - y) <= 6) {
          S.drag = ol;
          S.chart.applyOptions({ handleScroll: false, handleScale: false });
          e.preventDefault();
          e.stopPropagation();
          return;
        }
      }
    }, true);

    container.addEventListener("mousemove", (e) => {
      if (!S.drag) {
        let near = false;
        const y = e.offsetY;
        for (const ol of S.orderLines) {
          if (!ol.draggable) continue;
          const cy = S.candles ? S.candles.priceToCoordinate(ol.line.options().price) : null;
          if (cy != null && Math.abs(cy - y) <= 6) { near = true; break; }
        }
        container.style.cursor = near ? "ns-resize" : "";
        return;
      }
      const p = S.candles.coordinateToPrice(e.offsetY);
      if (p != null) S.drag.line.applyOptions({ price: Math.round(p * 10) / 10 });
      e.preventDefault();
      e.stopPropagation();
    }, true);

    const endDrag = (e) => {
      if (!S.drag) return;
      const price = S.drag.line.options().price;
      const o = S.drag.order;
      o[S.drag.field] = price;
      queueEvent("order_moved", { order_id: o.id, kind: o.kind, field: S.drag.field, price: price });
      S.drag = null;
      S.chart.applyOptions({ handleScroll: true, handleScale: true });
      if (e) { e.preventDefault(); e.stopPropagation(); }
    };
    container.addEventListener("mouseup", endDrag, true);
    container.addEventListener("mouseleave", endDrag, true);
  }

  // ── Fill detection (spread-aware: buys on ask, sells on bid) ────────────
  // Bar-engine fallback has no spread data → bid = ask = bar price.
  function checkFills() {
    const px = currentPrice();
    if (px == null) return;
    evalFills(S.lastBid != null ? S.lastBid : px,
              S.lastAsk != null ? S.lastAsk : px, currentBarTime());
  }

  function evalFills(bid, ask, bt) {
    for (const o of S.orders) {
      if (o.kind === "position") {
        if (S.locallyExited.has(o.id)) continue;
        const long = o.side === "buy";
        // long exits sell → bid; short exits buy → ask
        const xp = long ? bid : ask;
        if (o.sl != null && (long ? xp <= o.sl : xp >= o.sl)) {
          S.locallyExited.add(o.id);
          queueEvent("exit", { trade_id: o.id, reason: "sl", price: xp, bar_time: bt });
        } else if (o.tp != null && (long ? xp >= o.tp : xp <= o.tp)) {
          S.locallyExited.add(o.id);
          queueEvent("exit", { trade_id: o.id, reason: "tp", price: xp, bar_time: bt });
        }
      } else {
        if (S.locallyFilled.has(o.id)) continue;
        const buy = o.side === "buy";
        const ep = buy ? ask : bid; // execution side
        let fillAt = null;
        if (o.kind === "market") fillAt = ep;
        else if (o.kind === "stop" && (buy ? ep >= o.price : ep <= o.price)) fillAt = ep;
        else if (o.kind === "limit" && (buy ? ep <= o.price : ep >= o.price)) fillAt = ep;
        if (fillAt != null) {
          S.locallyFilled.add(o.id);
          queueEvent("fill", { order_id: o.id, kind: o.kind, side: o.side, qty: o.qty,
                               sl: o.sl, tp: o.tp, price: fillAt, bar_time: bt });
          renderOrders();
        }
      }
    }
  }

  function processRequests(requests) {
    for (const r of requests || []) {
      if (S.doneReqs.has(r.req_id)) continue;
      S.doneReqs.add(r.req_id);
      if (r.action === "snapshot") {
        queueEvent("snapshot", { req_id: r.req_id, name: r.name });
      } else {
        queueEvent("close_ack", {
          req_id: r.req_id, trade_id: r.trade_id, qty: r.qty, action: r.action,
          price: currentPrice(), bar_time: currentBarTime(),
        });
      }
    }
  }

  // ── TICK engine ─────────────────────────────────────────────────────────
  function initTicks(ticks) {
    const n = ticks.dt.length + 1;
    S.tTimes = new Float64Array(n);
    S.tBids = new Float64Array(n);
    S.tAsks = new Float64Array(n);
    S.tTimes[0] = ticks.base;
    for (let i = 1; i < n; i++) S.tTimes[i] = S.tTimes[i - 1] + ticks.dt[i - 1];
    for (let i = 0; i < n; i++) { S.tBids[i] = ticks.b[i]; S.tAsks[i] = ticks.a[i]; }
    S.tickIdx = 0;
    S.dayBars = [];
    S.curBar = null;
    S.lastPx = null;
    S.lastBid = null;
    S.lastAsk = null;
  }

  function processTick(i, checkOrders) {
    const tms = S.tTimes[i];
    const bid = S.tBids[i], ask = S.tAsks[i];
    const p = Math.round((bid + ask) * 50) / 100; // mid, 2dp — candles stay mid-based
    const bucket = Math.floor(tms / 60000) * 60;  // lwc time (shifted epoch s)
    if (!S.curBar || S.curBar.time !== bucket) {
      if (S.curBar) S.dayBars.push(S.curBar);
      S.curBar = { time: bucket, open: p, high: p, low: p, close: p, volume: 1 };
    } else {
      if (p > S.curBar.high) S.curBar.high = p;
      if (p < S.curBar.low) S.curBar.low = p;
      S.curBar.close = p;
      S.curBar.volume += 1;
    }
    S.lastPx = p;
    S.lastBid = bid;
    S.lastAsk = ask;
    if (checkOrders) evalFills(bid, ask, bucket);
  }

  // Replay ticks 0..n without firing fills/events (used for session resume —
  // pending orders were armed before the save point and hadn't filled).
  function fastForward(n) {
    S.fastForwarding = true;
    const end = Math.min(n, S.tTimes.length);
    for (let i = 0; i < end; i++) processTick(i, false);
    S.tickIdx = end;
    S.fastForwarding = false;
  }

  function tickLoop() {
    const now = performance.now();
    const elapsed = now - S.lastFrameTs;
    S.lastFrameTs = now;
    S.simMs += elapsed * S.speed;
    let processed = 0;
    while (S.tickIdx < S.tTimes.length && S.tTimes[S.tickIdx] <= S.simMs) {
      processTick(S.tickIdx, true);
      S.tickIdx += 1;
      processed += 1;
    }
    if (processed > 0) {
      renderData(true);
      S.chart.timeScale().scrollToRealTime();
    } else {
      updatePosLegend();
    }
    if (S.tickIdx >= S.tTimes.length) {
      setPlaying(false, true);
      queueEvent("day_complete", {});
    }
  }

  function tickSkipUntil(predMs) {
    while (S.tickIdx < S.tTimes.length && (predMs == null || S.tTimes[S.tickIdx] < predMs)) {
      processTick(S.tickIdx, true);
      S.tickIdx += 1;
    }
    S.simMs = S.tickIdx < S.tTimes.length ? S.tTimes[S.tickIdx] : (S.tTimes.length ? S.tTimes[S.tTimes.length - 1] : 0);
    renderData(false);
  }

  function tickStepBar() {
    if (S.tickIdx >= S.tTimes.length) return;
    const curBucket = Math.floor(S.tTimes[S.tickIdx] / 60000);
    while (S.tickIdx < S.tTimes.length && Math.floor(S.tTimes[S.tickIdx] / 60000) === curBucket) {
      processTick(S.tickIdx, true);
      S.tickIdx += 1;
    }
    S.simMs = S.tickIdx < S.tTimes.length ? S.tTimes[S.tickIdx] : S.simMs;
    renderData(true);
    S.chart.timeScale().scrollToRealTime();
  }

  // ── BAR engine ──────────────────────────────────────────────────────────
  function barTick() {
    if (S.cursor >= S.bars1m.length) { setPlaying(false, true); queueEvent("day_complete", {}); return; }
    S.subStep += 1;
    if (S.subStep >= 4) { S.subStep = 0; S.cursor += 1; }
    renderData(true);
    checkFills();
    S.chart.timeScale().scrollToRealTime();
  }

  function barStep() {
    if (S.cursor >= S.bars1m.length) return;
    while (S.subStep < 3) { S.subStep += 1; checkFills(); }
    S.subStep = 0;
    S.cursor += 1;
    renderData(true);
    checkFills();
  }

  function barSkipTo(predicate) {
    while (S.cursor < S.bars1m.length && !predicate(S.bars1m[S.cursor])) {
      S.subStep = 3;
      checkFills();
      S.subStep = 0;
      S.cursor += 1;
    }
    renderData(false);
    checkFills();
  }

  // ── Playback control ────────────────────────────────────────────────────
  function setPlaying(playing, silent) {
    const wasPlaying = S.playing;
    S.playing = playing;
    document.getElementById("btn-play").textContent = playing ? "⏸" : "▶";
    stopTimer();
    if (playing) {
      if (S.tickMode) {
        // sync sim clock to the next tick so a pause doesn't create a gap
        S.simMs = S.tickIdx < S.tTimes.length ? S.tTimes[S.tickIdx] : S.simMs;
        S.lastFrameTs = performance.now();
        S.timerId = setInterval(tickLoop, 30);
      } else {
        // bar fallback: 1x = one 1-minute bar per 60s (4 sub-steps of 15s)
        S.timerId = setInterval(barTick, Math.max(15, 15000 / S.speed));
      }
    } else if (wasPlaying && !silent) {
      queueEvent("paused", {});
    }
  }

  function setSpeed(sp) {
    S.speed = Math.min(960, Math.max(1, sp));
    document.getElementById("speed-label").textContent = S.speed + "x";
    if (S.playing && !S.tickMode) setPlaying(true, true); // tick loop reads S.speed live
  }

  function bindToolbar() {
    document.querySelectorAll(".tf-btn").forEach((btn) => {
      btn.onclick = () => { setTf(parseInt(btn.dataset.tf, 10)); };
    });
    document.getElementById("btn-shot").onclick = () => {
      const shot = S.chart.takeScreenshot();
      // composite the volume-profile overlay into the export
      const out = document.createElement("canvas");
      out.width = shot.width;
      out.height = shot.height;
      const ctx = out.getContext("2d");
      ctx.drawImage(shot, 0, 0);
      if (S.vpCanvas && S.vprofile) ctx.drawImage(S.vpCanvas, 0, 0);
      queueEvent("screenshot", { png_base64: out.toDataURL("image/png").split(",")[1] });
    };
    document.getElementById("btn-play").onclick = () => setPlaying(!S.playing);
    document.getElementById("btn-step").onclick = () => { S.tickMode ? tickStepBar() : barStep(); };
    document.getElementById("btn-faster").onclick = () => setSpeed(S.speed * 2);
    document.getElementById("btn-slower").onclick = () => setSpeed(S.speed / 2);
    document.getElementById("btn-skip-session").onclick = () => {
      const end = S.replay && S.replay.session_end ? S.replay.session_end : null;
      if (S.tickMode) tickSkipUntil(end ? end * 1000 : null);
      else if (end) barSkipTo((b) => b.time >= end);
      else barSkipTo(() => false);
      queueEvent("skipped", { to: "session_end", cursor: replayCursor() });
    };
    document.getElementById("btn-skip-day").onclick = () => {
      if (S.tickMode) tickSkipUntil(null); else barSkipTo(() => false);
      queueEvent("day_complete", {});
    };
  }

  function setTf(tf) {
    S.tf = tf;
    document.querySelectorAll(".tf-btn").forEach((b) => {
      b.classList.toggle("active", parseInt(b.dataset.tf, 10) === tf);
    });
    renderData(S.mode === "replay");
  }

  // ── Streamlit glue ──────────────────────────────────────────────────────
  function applyToolbarTheme() {
    const c = S.colors;
    const root = document.body.style;
    if (c.toolbar_bg) root.setProperty("--btn-bg", c.toolbar_bg);
    if (c.text) root.setProperty("--btn-fg", c.text);
    if (c.grid) root.setProperty("--btn-border", c.grid);
    if (c.accent) root.setProperty("--btn-active-bg", c.accent);
  }

  Streamlit.onRender(function (args) {
    const newKey = args.data_key || "";
    const fullInit = newKey !== S.dataKey || !S.chart;
    S.awaitingAck = false;

    S.mode = args.mode || "static";
    S.colors = args.colors || {};
    S.indicators = args.indicators || [];
    S.levels = args.levels || [];
    S.height = args.height || 650;
    S.replay = args.replay || null;
    S.sessionStart = args.session_start || null;
    S.vprofile = args.vprofile || null;
    document.body.classList.toggle("replay", S.mode === "replay");
    // hard override too — a reused iframe must never show playback controls
    // on a static (reference) chart
    const rc = document.getElementById("replay-controls");
    if (rc) rc.style.display = S.mode === "replay" ? "flex" : "none";
    applyToolbarTheme();

    if (fullInit) {
      S.dataKey = newKey;
      S.bars1m = args.bars_1m || [];
      S.tf = args.default_tf || (S.mode === "replay" ? 1 : 5);
      S.cursor = 0;
      S.subStep = 0;
      S.playing = false;
      S.speed = (S.replay && S.replay.speed) || 1;
      S.orders = args.orders || [];
      S.locallyFilled = new Set();
      S.locallyExited = new Set();
      S.doneReqs = new Set();
      S.queue = [];
      S.userRange = null;
      S.tickMode = S.mode === "replay" && !!(args.ticks && args.ticks.b && args.ticks.b.length);
      buildChart();
      bindToolbar();
      if (S.tickMode) {
        initTicks(args.ticks);
        const start = (S.replay && S.replay.start_cursor) || 0;
        if (start > 0) fastForward(start);
      } else if (S.mode === "replay" && S.replay && S.replay.start_cursor) {
        S.cursor = Math.min(S.replay.start_cursor, S.bars1m.length);
        S.subStep = S.replay.start_substep || 0;
      }
      setTf(S.tf);
      setSpeed(S.speed);
      renderData(false);
    } else {
      S.orders = args.orders || [];
      const ids = new Set(S.orders.map((o) => o.id));
      for (const id of Array.from(S.locallyFilled)) if (!ids.has(id)) S.locallyFilled.delete(id);
      for (const id of Array.from(S.locallyExited)) if (!ids.has(id)) S.locallyExited.delete(id);
      S.candles.applyOptions(candleOptions());
      renderData(true);
    }
    renderLevels();
    renderOrders();
    processRequests(args.requests);
    if (S.mode === "replay") checkFills(); // market orders fill immediately, even paused
    pumpQueue();
    Streamlit.setFrameHeight(S.height);
  });

  Streamlit.setComponentReady();
  Streamlit.setFrameHeight(650);
  window.__lwchartState = S; // debugging hook (read-only use)
})();
