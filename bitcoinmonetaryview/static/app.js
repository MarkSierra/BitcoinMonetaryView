/* BitcoinMonetaryView — SPDX-License-Identifier: AGPL-3.0-or-later
 * Vanilla JS, no dependencies, no external requests. All DOM text is set via
 * textContent; HTML strings are never parsed. */
"use strict";

(() => {
  // ------------------------------------------------------------------ constants
  const CARRIERS = ["envelope", "op_return", "multisig", "scriptsig"];
  const LABEL = {
    monetary: "Monetary data",
    envelope: "Inscriptions (witness envelopes)",
    op_return: "OP_RETURN over 83 bytes",
    multisig: "Fake-key outputs (Stamps)",
    scriptsig: "Oversized scriptSig",
    data_key: "Fake-key outputs (Stamps)",
    p2tr_dust: "Inscription-era P2TR dust",
  };
  const SHORT = {
    monetary: "Monetary", envelope: "Inscriptions", op_return: "OP_RETURN", multisig: "Stamps",
    scriptsig: "scriptSig", data_key: "Stamps", p2tr_dust: "P2TR dust",
  };
  const MILESTONES = [
    { month: "2014-01", label: "Counterparty" },
    { month: "2017-08", label: "SegWit" },
    { month: "2021-11", label: "Taproot" },
    { month: "2022-12", label: "Ordinals" },
    { month: "2023-03", label: "Stamps" },
  ];
  const NF = new Intl.NumberFormat("en-US");
  const SVGNS = "http://www.w3.org/2000/svg";

  // ------------------------------------------------------------------ state
  const S = {
    status: null, summary: null, csrf: null, view: null, lastHeight: null, lastDataFetch: 0,
    range: "144", tableBlocks: [], settings: null, connTest: null,
  };

  // ------------------------------------------------------------------ dom helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v == null || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
        else if (k === "style") Object.assign(el.style, v);
        else el.setAttribute(k, v === true ? "" : v);
      }
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
    for (const kid of kids.flat()) if (kid) el.append(kid);
    return el;
  }
  const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

  // ------------------------------------------------------------------ formatting
  function fmtBytes(b, digits) {
    if (b == null || isNaN(b)) return "–";
    const neg = b < 0; b = Math.abs(b);
    const u = ["B", "kB", "MB", "GB", "TB"];
    let i = 0;
    while (b >= 1000 && i < u.length - 1) { b /= 1000; i++; }
    const d = digits != null ? digits : (b >= 100 || i === 0 ? 0 : b >= 10 ? 1 : 2);
    return (neg ? "−" : "") + b.toFixed(d) + " " + u[i];
  }
  const fmtNum = (n) => (n == null || isNaN(n) ? "–" : NF.format(Math.round(n)));
  function fmtCompact(n) {
    if (n == null) return "–";
    const a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1) + "B";
    if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
    if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k";
    return String(Math.round(n));
  }
  function fmtPct(p, d = 1) { return p == null || isNaN(p) ? "–" : p.toFixed(d) + " %"; }
  function fmtDur(sec) {
    if (sec == null || !isFinite(sec)) return "–";
    sec = Math.max(0, Math.round(sec));
    const d = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
    if (d) return `${d}d ${hh}h`;
    if (hh) return `${hh}h ${m}m`;
    if (m) return `${m}m ${sec % 60}s`;
    return `${sec}s`;
  }
  function timeAgo(t) {
    const d = Date.now() / 1000 - t;
    if (d < 60) return "just now";
    if (d < 3600) return `${Math.floor(d / 60)} min ago`;
    if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
    return new Date(t * 1000).toISOString().slice(0, 10);
  }
  const fmtDate = (t) => new Date(t * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
  const clock = (t) => new Date(t * 1000).toLocaleTimeString("en-GB", { hour12: false });

  // ------------------------------------------------------------------ api
  async function api(path) {
    const r = await fetch(path, { credentials: "same-origin", cache: "no-store" });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }
  async function post(path, body, retried) {
    if (!S.csrf) S.csrf = (await api("/api/session")).csrf;
    const r = await fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": S.csrf },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (r.status === 403 && /CSRF/.test(data.error || "") && !retried) {
      S.csrf = null;                 // server restarted -> fetch a fresh token once
      return post(path, body, true);
    }
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  // ------------------------------------------------------------------ theme
  function storageGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function storageSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
  function currentTheme() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t) return t;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function initTheme() {
    const saved = storageGet("bmv-theme");
    if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);
    $("#theme-toggle").addEventListener("click", () => {
      const next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      storageSet("bmv-theme", next);
    });
  }

  // ------------------------------------------------------------------ tooltip
  const tip = () => $("#tooltip");
  function showTip(evt, title, rows) {
    const t = clear(tip());
    t.append(h("div", { class: "tt-title", text: title }));
    for (const r of rows) {
      t.append(h("div", { class: "tt-row" },
        r.key ? h("span", { class: `swatch b-${r.key}` }) : h("span"),
        h("span", { text: r.label }), h("b", { text: r.value })));
    }
    t.hidden = false;
    const pad = 14, w = t.offsetWidth, hh = t.offsetHeight;
    let x = evt.clientX + pad, y = evt.clientY + pad;
    if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
    if (y + hh > innerHeight - 8) y = evt.clientY - hh - pad;
    t.style.left = Math.max(8, x) + "px";
    t.style.top = Math.max(8, y) + "px";
  }
  const hideTip = () => { tip().hidden = true; };

  // ------------------------------------------------------------------ charts
  function chartWidth(el) { return Math.max(300, Math.round(el.clientWidth || 900)); }
  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }
  function roundedTopPath(x, y, w, hgt, r) {
    r = Math.min(r, w / 2, hgt);
    if (hgt <= 0) return "";
    return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
  }

  /** Vertical stacked bars. rows: [{label, title, values:{key:n}}], keys bottom→top. */
  function stackedBars(container, rows, keys, opts = {}) {
    clear(container);
    if (!rows.length) { container.append(h("div", { class: "chart-empty", text: opts.empty || "No data yet" })); return; }
    const W = chartWidth(container), H = opts.height || 260, m = { l: 52, r: 8, t: 10, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const totals = rows.map((r) => keys.reduce((a, k) => a + (r.values[k] || 0), 0));
    const max = niceMax(Math.max(...totals));
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || "Stacked bar chart" });
    for (let i = 0; i <= 4; i++) {
      const y = m.t + ih - (ih * i) / 4;
      svg.append(s("line", { class: i ? "grid-line" : "baseline", x1: m.l, x2: W - m.r, y1: y, y2: y }));
      const t = s("text", { class: "axis", x: m.l - 8, y: y + 4, "text-anchor": "end" });
      t.textContent = opts.yFmt ? opts.yFmt((max * i) / 4) : fmtBytes((max * i) / 4, 0);
      svg.append(t);
    }
    const n = rows.length, slot = iw / n;
    const bw = Math.max(1, Math.min(28, slot * 0.72));
    const gap = n > 150 ? 0 : 2;
    const labelEvery = Math.ceil(n / Math.max(3, Math.floor(W / 110)));
    rows.forEach((r, i) => {
      const x = m.l + i * slot + (slot - bw) / 2;
      let y = m.t + ih;
      const g = s("g", {});
      const present = keys.filter((k) => (r.values[k] || 0) > 0);
      present.forEach((k, j) => {
        const hh = (r.values[k] / max) * ih;
        const top = j === present.length - 1;
        const segH = Math.max(0, hh - (top ? 0 : gap));
        y -= hh;
        if (segH <= 0) return;
        if (top) g.append(s("path", { class: `f-${k}`, d: roundedTopPath(x, y, bw, segH, bw > 8 ? 4 : 1) }));
        else g.append(s("rect", { class: `f-${k}`, x, y: y + gap, width: bw, height: segH }));
      });
      svg.append(g);
      const hit = s("rect", { class: "hit", x: m.l + i * slot, y: m.t, width: slot, height: ih });
      hit.addEventListener("mousemove", (e) => {
        g.classList.remove("mark-hover");
        showTip(e, r.title || r.label, keys.slice().reverse().filter((k) => r.values[k] > 0)
          .map((k) => ({ key: k, label: LABEL[k], value: opts.yFmt ? opts.yFmt(r.values[k]) : fmtBytes(r.values[k]) }))
          .concat(r.extra || []));
      });
      hit.addEventListener("mouseleave", hideTip);
      if (opts.onClick) { hit.style.cursor = "pointer"; hit.addEventListener("click", () => opts.onClick(r)); }
      svg.append(hit);
      if (i % labelEvery === 0) {
        const t = s("text", { class: "axis", x: m.l + i * slot + slot / 2, y: H - 8, "text-anchor": "middle" });
        t.textContent = r.label;
        svg.append(t);
      }
    });
    container.append(svg);
  }

  /** Stacked area over months with crosshair tooltip and milestone annotations. */
  function stackedArea(container, rows, keys, opts = {}) {
    clear(container);
    if (rows.length < 2) { container.append(h("div", { class: "chart-empty", text: opts.empty || "Not enough data yet" })); return; }
    const W = chartWidth(container), H = opts.height || 280, m = { l: 56, r: 12, t: 34, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const totals = rows.map((r) => keys.reduce((a, k) => a + (r.values[k] || 0), 0));
    const max = niceMax(Math.max(...totals));
    const X = (i) => m.l + (rows.length === 1 ? iw / 2 : (iw * i) / (rows.length - 1));
    const Y = (v) => m.t + ih - (v / max) * ih;
    const fmt = opts.yFmt || ((v) => fmtBytes(v, 0));
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || "Area chart" });
    for (let i = 0; i <= 4; i++) {
      const y = m.t + ih - (ih * i) / 4;
      svg.append(s("line", { class: i ? "grid-line" : "baseline", x1: m.l, x2: W - m.r, y1: y, y2: y }));
      const t = s("text", { class: "axis", x: m.l - 8, y: y + 4, "text-anchor": "end" });
      t.textContent = fmt((max * i) / 4);
      svg.append(t);
    }
    const cum = rows.map(() => 0);
    keys.forEach((k) => {
      const lower = cum.slice();
      rows.forEach((r, i) => { cum[i] += r.values[k] || 0; });
      if (!rows.some((r) => (r.values[k] || 0) > 0)) return;
      let d = `M${X(0)},${Y(cum[0])}`;
      for (let i = 1; i < rows.length; i++) d += `L${X(i)},${Y(cum[i])}`;
      for (let i = rows.length - 1; i >= 0; i--) d += `L${X(i)},${Y(lower[i])}`;
      svg.append(s("path", { class: `f-${k} area`, d: d + "Z" }));
      let top = `M${X(0)},${Y(cum[0])}`;
      for (let i = 1; i < rows.length; i++) top += `L${X(i)},${Y(cum[i])}`;
      svg.append(s("path", { class: `line s-surface`, d: top, "stroke-width": 1.5 }));
    });
    if (opts.milestones) {
      let lastX = -1e9, level = 0;
      opts.milestones.forEach((ms) => {
        const i = rows.findIndex((r) => r.key >= ms.month);
        if (i < 0) return;
        level = X(i) - lastX < 90 ? (level + 1) % 2 : 0;
        lastX = X(i);
        const g = s("g", { class: "milestone" });
        g.append(s("line", { x1: X(i), x2: X(i), y1: m.t - 18 + level * 14, y2: m.t + ih }));
        const t = s("text", { x: X(i) + 4, y: m.t - 22 + level * 14 });
        t.textContent = ms.label;
        g.append(t);
        svg.append(g);
      });
    }
    const labelEvery = Math.ceil(rows.length / Math.max(3, Math.floor(W / 110)));
    rows.forEach((r, i) => {
      if (i % labelEvery) return;
      const t = s("text", { class: "axis", x: X(i), y: H - 8, "text-anchor": "middle" });
      t.textContent = r.label;
      svg.append(t);
    });
    const cross = s("line", { class: "crosshair", y1: m.t, y2: m.t + ih, x1: 0, x2: 0, visibility: "hidden" });
    svg.append(cross);
    const hit = s("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih });
    hit.addEventListener("mousemove", (e) => {
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / rect.width) * W;
      const i = Math.max(0, Math.min(rows.length - 1, Math.round(((px - m.l) / iw) * (rows.length - 1))));
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
      const r = rows[i];
      showTip(e, r.title || r.label, keys.slice().reverse().filter((k) => r.values[k] > 0)
        .map((k) => ({ key: k, label: LABEL[k], value: fmt(r.values[k]) })).concat(r.extra || []));
    });
    hit.addEventListener("mouseleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
    svg.append(hit);
    container.append(svg);
  }

  /** Single-series line with crosshair. */
  function lineChart(container, rows, key, opts = {}) {
    clear(container);
    if (rows.length < 2) { container.append(h("div", { class: "chart-empty", text: opts.empty || "Not enough data yet" })); return; }
    const W = chartWidth(container), H = opts.height || 220, m = { l: 56, r: 12, t: 12, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const max = niceMax(Math.max(...rows.map((r) => r.value)));
    const X = (i) => m.l + (iw * i) / (rows.length - 1);
    const Y = (v) => m.t + ih - (v / max) * ih;
    const fmt = opts.yFmt || fmtNum;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || "Line chart" });
    for (let i = 0; i <= 4; i++) {
      const y = m.t + ih - (ih * i) / 4;
      svg.append(s("line", { class: i ? "grid-line" : "baseline", x1: m.l, x2: W - m.r, y1: y, y2: y }));
      const t = s("text", { class: "axis", x: m.l - 8, y: y + 4, "text-anchor": "end" });
      t.textContent = (opts.axisFmt || fmt)((max * i) / 4);
      svg.append(t);
    }
    let d = "";
    rows.forEach((r, i) => { d += `${i ? "L" : "M"}${X(i)},${Y(r.value)}`; });
    svg.append(s("path", { class: `line l-${key}`, d }));
    const labelEvery = Math.ceil(rows.length / Math.max(3, Math.floor(W / 110)));
    rows.forEach((r, i) => {
      if (i % labelEvery) return;
      const t = s("text", { class: "axis", x: X(i), y: H - 8, "text-anchor": "middle" });
      t.textContent = r.label;
      svg.append(t);
    });
    const cross = s("line", { class: "crosshair", y1: m.t, y2: m.t + ih, x1: 0, x2: 0, visibility: "hidden" });
    const dot = s("circle", { class: `f-${key} dot-mark`, r: 5, cx: 0, cy: 0, visibility: "hidden" });
    svg.append(cross, dot);
    const hit = s("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih });
    hit.addEventListener("mousemove", (e) => {
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / rect.width) * W;
      const i = Math.max(0, Math.min(rows.length - 1, Math.round(((px - m.l) / iw) * (rows.length - 1))));
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", X(i)); dot.setAttribute("cy", Y(rows[i].value)); dot.setAttribute("visibility", "visible");
      showTip(e, rows[i].title || rows[i].label, [{ key, label: opts.seriesLabel || LABEL[key] || key, value: fmt(rows[i].value) }]);
    });
    hit.addEventListener("mouseleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
    svg.append(hit);
    container.append(svg);
  }

  function donut(container, items, center) {
    clear(container);
    const total = items.reduce((a, b) => a + b.value, 0);
    const R = 90, r = 62, C = 100;
    const svg = s("svg", { viewBox: "0 0 200 200", role: "img", "aria-label": "Donut chart" });
    if (!total) {
      svg.append(s("circle", { cx: C, cy: C, r: (R + r) / 2, fill: "none", class: "grid-line", "stroke-width": R - r }));
    } else {
      let a0 = -Math.PI / 2;
      const gapA = items.filter((i) => i.value > 0).length > 1 ? 0.025 : 0;
      items.forEach((it) => {
        if (it.value <= 0) return;
        const frac = it.value / total;
        let a1 = a0 + frac * Math.PI * 2;
        const s0 = a0 + gapA / 2, s1 = Math.max(s0 + 0.002, a1 - gapA / 2);
        const large = s1 - s0 > Math.PI ? 1 : 0;
        const p = (rad, ang) => `${C + rad * Math.cos(ang)},${C + rad * Math.sin(ang)}`;
        const d = frac >= 0.9999
          ? `M${C},${C - R}A${R},${R} 0 1 1 ${C - 0.01},${C - R}L${C - 0.01},${C - r}A${r},${r} 0 1 0 ${C},${C - r}Z`
          : `M${p(R, s0)}A${R},${R} 0 ${large} 1 ${p(R, s1)}L${p(r, s1)}A${r},${r} 0 ${large} 0 ${p(r, s0)}Z`;
        const path = s("path", { class: `f-${it.key}`, d });
        path.addEventListener("mousemove", (e) => showTip(e, LABEL[it.key], [{ key: it.key, label: "Bytes", value: fmtBytes(it.value) }, { label: "Share", value: fmtPct(frac * 100) }]));
        path.addEventListener("mouseleave", hideTip);
        svg.append(path);
        a0 = a1;
      });
    }
    if (center) {
      const t1 = s("text", { x: C, y: C + 2, "text-anchor": "middle", class: "donut-center-v" }); t1.textContent = center[0];
      const t2 = s("text", { x: C, y: C + 20, "text-anchor": "middle", class: "donut-center-l" }); t2.textContent = center[1];
      svg.append(t1, t2);
    }
    container.append(svg);
  }

  function dataTable(headers, rows) {
    return h("div", { class: "table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, headers.map((x, i) => h("th", { class: i ? null : "l", text: x })))),
      h("tbody", null, rows.map((r) => h("tr", null, r.map((c, i) => h("td", { class: i ? null : "l", text: c })))))));
  }
  function withTableToggle(card, makeTable) {
    let shown = false, tableEl = null;
    const btn = h("button", { class: "toggle-table", type: "button", text: "Show as table" });
    btn.addEventListener("click", () => {
      shown = !shown;
      if (shown) { tableEl = makeTable(); card.append(tableEl); btn.textContent = "Hide table"; }
      else { tableEl && tableEl.remove(); btn.textContent = "Show as table"; }
    });
    card.append(btn);
  }
  function legend(keys, values, fmt = fmtBytes) {
    return h("div", { class: "legend" }, keys.map((k) => h("span", null,
      h("i", { class: `swatch b-${k}` }), SHORT[k] || LABEL[k],
      values ? h("span", { class: "val", text: fmt(values[k] || 0) }) : null)));
  }

  // ------------------------------------------------------------------ status bar
  const BUSY = new Set(["quick", "sample", "full", "loading_filter", "connecting", "utxo_info", "starting"]);
  function renderStatus() {
    const st = S.status;
    if (!st) return;
    const pill = $("#phase-pill");
    pill.dataset.tone = st.phase === "live" ? "live" : st.phase === "error" ? "error"
      : ["waiting_node", "paused", "window"].includes(st.phase) ? "warn" : BUSY.has(st.phase) ? "busy" : "";
    $("#phase-label").textContent = st.phase_label || st.phase;
    $("#status-detail").textContent = st.detail || "";
    $("#status-detail").title = st.detail || "";
    const prog = st.phase === "live" ? 100 : st.progress != null ? st.progress : 0;
    $("#progress-fill").style.transform = `scaleX(${Math.max(0, Math.min(100, prog)) / 100})`;
    $("#progress").setAttribute("aria-valuenow", String(Math.round(prog)));
    $("#m-height").textContent = st.height != null ? `${fmtNum(st.height)} / ${fmtNum(st.tip)}` : "–";
    $("#m-progress").textContent = ["quick", "sample"].includes(st.phase) ? `${fmtPct(prog)} (${st.phase})` : st.progress != null ? fmtPct(prog, 2) : "–";
    $("#m-speed").textContent = ["full", "quick", "sample"].includes(st.phase) && st.bytes_per_s
      ? `${fmtBytes(st.bytes_per_s)}/s · ${st.blocks_per_s.toFixed(1)} blk/s` : "–";
    $("#m-elapsed").textContent = fmtDur(st.now - (st.full_started_at || st.started_at));
    $("#m-eta").textContent = st.phase === "live" ? "done" : st.eta_seconds ? "~" + fmtDur(st.eta_seconds) : "–";
    const pb = $("#pause-btn");
    pb.hidden = !st.can_pause || st.phase === "live";
    pb.textContent = st.paused ? "Resume" : "Pause";
    $("#app-version").textContent = st.version || "";
    renderBanners();
  }
  function renderBanners() {
    const st = S.status, box = clear($("#banners"));
    const add = (kind, text) => box.append(h("div", { class: `banner ${kind}` },
      h("span", { class: "ico", text: kind === "info" ? "i" : "!" }), h("div", { text })));
    if (st.managed) add("info", `Settings are managed by ${st.managed_by === "startos" ? "StartOS" : st.managed_by} — change them in the service's settings/actions.`);
    if (st.node && st.node.plaintext_remote) add("warn", "Your RPC connection uses plain HTTP to another machine, so the RPC password travels unencrypted. Prefer an https:// URL, Tor or an SSH tunnel.");
    if (st.node && st.node.pruned) add("warn", `Your node is pruned: only blocks from ${fmtNum(st.node.prune_height)} on can be analysed, and the spam UTXO figure will be incomplete.`);
    for (const w of st.warnings || []) add("warn", w);
    if (st.phase === "error" && st.last_error) add("error", st.last_error);
  }

  // ------------------------------------------------------------------ views
  const main = () => $("#main");
  function viewShell(...kids) { const m = clear(main()); m.append(h("div", { class: "fade-in" }, kids)); }
  function partialNote(sum) {
    const st = S.status || {};
    if (sum.meta && sum.meta.full_done === "1") return null;
    const est = sum.estimate;
    if (est) {
      const m = est.spam_margin_pct != null && est.spam_margin_pct >= 0.1 ? ` (±${fmtPct(est.spam_margin_pct)})` : "";
      return h("span", { class: "partial" }, "◔ ",
        `Estimate${m} from ${fmtNum(est.samples)} sample blocks spread across the chain — exact figures replace it as the full history scan proceeds (${fmtPct(est.exact_share_pct)} of the data scanned exactly so far)`);
    }
    const tipH = st.tip != null ? st.tip + 1 : null;
    return h("span", { class: "partial" }, "◔ ",
      `Based on ${fmtNum(sum.blocks_scanned)}${tipH ? " of " + fmtNum(tipH) : ""} blocks scanned so far — the full history scan is still running`);
  }
  function emptyState(text) {
    return h("div", { class: "card", style: { textAlign: "center", padding: "48px 20px" } },
      h("h2", { text: "Scanning your node…" }),
      h("p", { class: "sub", text: text || "The first results appear after a few blocks. You can watch the progress in the bar above." }));
  }

  function viewOverview() {
    const sum = S.summary;
    if (!sum || sum.empty) return viewShell(emptyState());
    // While the full scan runs, the chain-wide figures come from the estimate (exact so far + sample);
    // the UTXO figures are always exact.
    const est = sum.estimate;
    const B = est || sum;
    const t = B.totals;
    const saved = B.saved_bytes;
    const ap = est ? "≈" : "";
    const items = CARRIERS.map((k) => ({ key: k, value: B.by_carrier[k] || 0 }));
    const donutBox = h("div", { class: "chart" });
    const donutCard = h("div", { class: "card" },
      h("h2", { text: "Spam by carrier" }),
      h("p", { class: "sub", text: est ? "Bytes a Monetary Node removes from block storage, by type — estimated for the whole chain" : "Bytes a Monetary Node removes from block storage, by type" }),
      h("div", { class: "donut-wrap" }, donutBox,
        h("div", { class: "donut-legend" }, items.map((it) => h("div", { class: "row" },
          h("i", { class: `swatch b-${it.key}` }), h("span", { text: LABEL[it.key] }),
          h("span", { class: "v num", text: fmtBytes(it.value) }),
          h("span", { class: "p num", text: fmtPct(B.spam_bytes ? (it.value / B.spam_bytes) * 100 : 0) }))))));
    donut(donutBox, items, [ap + fmtPct(B.spam_pct), "of block data"]);
    withTableToggle(donutCard, () => dataTable(["Carrier", "Bytes", "Share of spam"],
      items.map((it) => [LABEL[it.key], ap + fmtBytes(it.value), fmtPct(B.spam_bytes ? (it.value / B.spam_bytes) * 100 : 0)])));

    const maxB = Math.max(t.size, t.stored) || 1;
    const compare = h("div", { class: "card" },
      h("h2", { text: "Block storage: today vs. Monetary Node" }),
      h("p", { class: "sub", text: est ? "Same blocks, same proof-of-work — only the data carriers removed (estimated for the whole chain)" : "Same blocks, same proof-of-work — only the data carriers removed" }),
      h("div", { class: "compare" },
        compareRow("Your node (Core/Knots)", t.size, maxB, [["monetary", t.size - B.spam_bytes], ...CARRIERS.map((k) => [k, t[k]])]),
        compareRow("Monetary Node", t.stored, maxB, [["monetary", t.stored]])),
      h("p", { class: "sub", style: { marginTop: "14px", marginBottom: 0 },
        text: "The Monetary Node size includes its own bookkeeping (stored txids and filter entries for removed outputs), so the saving is slightly smaller than the spam total. Undo files and optional indexes are not included." }));

    const u = sum.utxo;
    const kpis = h("div", { class: "grid grid-4" },
      kpi("Spam in your blocks", ap + fmtBytes(B.spam_bytes), `${ap}${fmtPct(B.spam_pct)} of ${ap}${fmtBytes(t.size)} block data`, "envelope"),
      kpi("Storage a Monetary Node saves", saved >= 0 ? ap + fmtBytes(saved) : "—", saved >= 0 ? `${ap}${fmtPct(B.saved_pct)} less block storage` : "No saving in the blocks scanned so far", "monetary"),
      kpi("Spam entries in the UTXO set", fmtCompact(u.count), utxoFoot(u), "p2tr_dust"),
      kpi("Transactions touched", ap + fmtPct(B.modified_tx_pct), est ? "of all transactions modified or reduced to a txid" : `${fmtNum(t.modified + t.stripped)} of ${fmtNum(t.tx_count)} modified or reduced to a txid`));

    viewShell(
      h("section", { class: "hero" },
        h("h1", null, "Your node stores ", h("span", { class: "hl", text: (est ? "≈ " : "") + fmtBytes(B.spam_bytes) }), " of spam."),
        h("p", { text: est
          ? `Estimated for the whole chain (${fmtNum(est.blocks)} blocks): a Monetary Node would store about ${fmtBytes(t.stored)} instead of ${fmtBytes(t.size)} — ${fmtBytes(Math.max(0, saved))} (${fmtPct(Math.max(0, est.saved_pct))}) less.`
          : saved > 0
          ? `A Monetary Node validating the same ${fmtNum(sum.blocks_scanned)} blocks would store ${fmtBytes(t.stored)} instead of ${fmtBytes(t.size)} — ${fmtBytes(saved)} (${fmtPct(sum.saved_pct)}) less — and keep ${fmtNum(u.count)} spam entries out of its UTXO set.`
          : `In the ${fmtNum(sum.blocks_scanned)} blocks scanned so far, spam makes up ${fmtPct(sum.spam_pct)} of the data.` }),
        partialNote(sum),
        h("div", { class: "hero-actions" },
          h("button", { class: "btn btn-primary", type: "button", onclick: openShare, text: "Share my results" }),
          h("a", { class: "btn", href: "/api/export.csv", text: "Download CSV" }),
          h("a", { class: "btn", href: "/api/export.json", text: "Download JSON" }),
          h("a", { class: "btn btn-ghost", href: "#/about", text: "What is a Monetary Node?" }))),
      kpis,
      h("div", { class: "grid grid-2", style: { marginTop: "16px" } }, donutCard, compare),
      h("div", { class: "section-title", text: "Latest blocks" }),
      blockStrip());
  }
  function utxoFoot(u) {
    if (!u.full_done) return "Exact figure available after the full history scan";
    let s2 = `≈ ${fmtBytes(u.disk_estimate)} of chainstate`;
    if (u.share_of_entries_pct != null) s2 += ` · ${fmtPct(u.share_of_entries_pct)} of all UTXOs`;
    return s2;
  }
  function kpi(label, value, foot, key) {
    const parts = String(value).split(" ");
    return h("div", { class: "card kpi" },
      h("div", { class: "kpi-label" }, key ? h("i", { class: `swatch b-${key}` }) : null, label),
      h("div", { class: "kpi-value" }, parts[0], parts[1] ? h("small", { text: parts.slice(1).join(" ") }) : null),
      h("div", { class: "kpi-foot", text: foot }));
  }
  function compareRow(label, total, max, segs) {
    const track = h("div", { class: "bar-track" });
    track.style.width = Math.max(2, (total / max) * 100) + "%";
    const sum = segs.reduce((a, b) => a + Math.max(0, b[1] || 0), 0) || 1;
    for (const [k, v] of segs) {
      if (!v || v <= 0) continue;
      const d = h("div", { class: `b-${k}`, title: `${LABEL[k]}: ${fmtBytes(v)}` });
      d.style.width = (v / sum) * 100 + "%";
      track.append(d);
    }
    return h("div", { class: "compare-row" }, h("div", { class: "lbl" }, h("span", { text: label }), h("b", { text: fmtBytes(total) })), track);
  }

  function blockStrip() {
    const wrap = h("div", { class: "strip" });
    const blocks = (S.latestBlocks || []).slice(0, 8);
    if (!blocks.length) wrap.append(h("div", { class: "muted", text: "No blocks yet." }));
    for (const b of blocks) {
      const fill = h("div", { class: "fill" });
      const spamTotal = b.spam_bytes || 1;
      for (const k of CARRIERS) {
        if (!b[k]) continue;
        const d = h("div", { class: `b-${k}` });
        d.style.height = (b[k] / spamTotal) * 100 + "%";
        fill.append(d);
      }
      fill.style.height = Math.min(100, b.spam_pct) + "%";
      wrap.append(h("a", { class: "blockcard", href: `#/block/${b.height}`, title: `Block ${fmtNum(b.height)}` },
        fill,
        h("div", { class: "top" }, h("div", { class: "h", text: fmtNum(b.height) }), h("div", { class: "t", text: timeAgo(b.time) })),
        h("div", { class: "bottom" }, h("div", { class: "pct" }, fmtPct(b.spam_pct, 0), h("small", { text: `spam · ${fmtBytes(b.size, 2)}` })))));
    }
    return wrap;
  }

  async function viewBlocks() {
    const chartBox = h("div", { class: "chart" });
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Range" });
    const ranges = [["144", "1 day"], ["1008", "1 week"], ["4320", "1 month"], ["all", "All scanned"]];
    for (const [v, l] of ranges) {
      seg.append(h("button", { type: "button", "aria-pressed": String(S.range === v), text: l,
        onclick: () => { S.range = v; viewBlocks(); } }));
    }
    const chartCard = h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("div", null, h("h2", { text: "Spam per block" }), h("p", { class: "sub", text: "Block bytes by type. Click a bar to open the block." })), seg),
      legend(["monetary", ...CARRIERS]), chartBox);
    const tableBox = h("div");
    const search = h("input", { type: "text", inputmode: "numeric", placeholder: "Go to block height…", "aria-label": "Block height" });
    search.addEventListener("keydown", (e) => { if (e.key === "Enter" && /^\d+$/.test(search.value.trim())) location.hash = `#/block/${search.value.trim()}`; });
    const tableCard = h("div", { class: "card" },
      h("div", { class: "card-head" }, h("div", null, h("h2", { text: "Analysed blocks" }), h("p", { class: "sub", text: "Newest first" })), search),
      tableBox);
    viewShell(blockStrip(), h("div", { style: { height: "16px" } }), chartCard, h("div", { style: { height: "16px" } }), tableCard);

    const hi = S.summary && !S.summary.empty ? S.summary.highest : null;
    if (hi == null) { stackedBars(chartBox, [], []); }
    else {
      const lo = S.range === "all" ? S.summary.lowest : Math.max(S.summary.lowest, hi - parseInt(S.range, 10) + 1);
      try {
        const r = await api(`/api/range?from=${lo}&to=${hi}&points=180`);
        const rows = r.buckets.map((b) => {
          const spam = CARRIERS.reduce((a, k) => a + (b[k] || 0), 0);
          const per = b.blocks || 1;
          const vals = { monetary: (b.size - spam) / per };
          CARRIERS.forEach((k) => { vals[k] = (b[k] || 0) / per; });
          return {
            label: fmtNum(b.from), height: b.from,
            title: b.from === b.to ? `Block ${fmtNum(b.from)}` : `Blocks ${fmtNum(b.from)}–${fmtNum(b.to)} (average)`,
            values: vals, extra: [{ label: "Spam share", value: fmtPct(b.size ? (spam / b.size) * 100 : 0) }],
          };
        });
        stackedBars(chartBox, rows, ["monetary", ...CARRIERS], {
          aria: "Bytes per block by type", onClick: (row) => { location.hash = `#/block/${row.height}`; },
        });
        withTableToggle(chartCard, () => dataTable(["Blocks", "Monetary", ...CARRIERS.map((k) => SHORT[k])],
          rows.map((r) => [r.title, fmtBytes(r.values.monetary), ...CARRIERS.map((k) => fmtBytes(r.values[k]))])));
      } catch (e) { chartBox.append(h("div", { class: "chart-empty", text: String(e.message) })); }
    }
    S.tableBlocks = await api("/api/blocks?limit=50").catch(() => []);
    renderBlockTable(tableBox);
  }
  function renderBlockTable(box) {
    clear(box);
    const rows = S.tableBlocks;
    if (!rows.length) { box.append(h("div", { class: "chart-empty", text: "No blocks analysed yet" })); return; }
    const tbody = h("tbody");
    for (const b of rows) {
      const bar = h("span", { class: "pctbar" }, h("i"));
      bar.firstChild.style.width = Math.min(100, b.spam_pct) + "%";
      const tr = h("tr", { class: "clickable", tabindex: "0" },
        h("td", { class: "l", text: fmtNum(b.height) }), h("td", { text: fmtDate(b.time).slice(0, 16) }),
        h("td", { text: fmtBytes(b.size) }), h("td", null, fmtPct(b.spam_pct), bar),
        ...CARRIERS.map((k) => h("td", { text: b[k] ? fmtBytes(b[k]) : "–" })),
        h("td", { text: fmtBytes(b.stored) }), h("td", { text: fmtNum(b.modified + b.stripped) }));
      const go = () => { location.hash = `#/block/${b.height}`; };
      tr.addEventListener("click", go);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      tbody.append(tr);
    }
    box.append(h("div", { class: "table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, ["Height", "Time (UTC)", "Size", "Spam", ...CARRIERS.map((k) => SHORT[k]), "Monetary Node", "Txs touched"]
        .map((x, i) => h("th", { class: i ? null : "l", text: x })))), tbody)));
    const last = rows[rows.length - 1];
    if (last && last.height > (S.summary ? S.summary.lowest : 0)) {
      box.append(h("button", { class: "btn", type: "button", style: { marginTop: "12px" }, text: "Load more",
        onclick: async () => {
          const more = await api(`/api/blocks?limit=50&before=${last.height}`).catch(() => []);
          S.tableBlocks = S.tableBlocks.concat(more);
          renderBlockTable(box);
        } }));
    }
  }

  async function openBlock(height) {
    let b;
    try { b = await api(`/api/block/${height}`); } catch (e) { b = null; }
    const dlg = $("#dialog"), body = clear($("#dialog-body"));
    const close = h("button", { class: "icon-btn", type: "button", "aria-label": "Close", text: "✕", onclick: () => { dlg.close(); } });
    if (!b) {
      body.append(h("div", { class: "dlg" }, h("div", { class: "dlg-head" }, h("h2", { text: `Block ${fmtNum(height)}` }), close),
        h("p", { class: "muted", text: "This block has not been analysed yet. The history scan will get there." })));
    } else {
      const items = CARRIERS.map((k) => ({ key: k, value: b[k] || 0 }));
      const dn = h("div", { class: "chart" });
      donut(dn, [{ key: "monetary", value: b.size - b.spam_bytes }, ...items], [fmtPct(b.spam_pct), "spam"]);
      body.append(h("div", { class: "dlg" },
        h("div", { class: "dlg-head" }, h("h2", { text: `Block ${fmtNum(b.height)}` }), close),
        h("div", { class: "donut-wrap" }, dn, h("div", { class: "donut-legend" },
          [{ key: "monetary", value: b.size - b.spam_bytes }, ...items].map((it) => h("div", { class: "row" },
            h("i", { class: `swatch b-${it.key}` }), h("span", { text: LABEL[it.key] }), h("span", { class: "v", text: fmtBytes(it.value) }),
            h("span", { class: "p", text: fmtPct(b.size ? (it.value / b.size) * 100 : 0) }))))),
        h("dl", { class: "kv", style: { marginTop: "18px" } },
          h("dt", { text: "Hash" }), h("dd", { class: "num", text: b.hash }),
          h("dt", { text: "Time" }), h("dd", { text: fmtDate(b.time) }),
          h("dt", { text: "Size / weight" }), h("dd", { text: `${fmtBytes(b.size, 3)} · ${fmtNum(b.weight)} WU` }),
          h("dt", { text: "Monetary Node stores" }), h("dd", { text: `${fmtBytes(b.stored, 3)} (${b.size - b.stored >= 0 ? fmtBytes(b.size - b.stored) + " less" : fmtBytes(b.stored - b.size) + " more — format overhead"})` }),
          h("dt", { text: "Transactions" }), h("dd", { text: `${fmtNum(b.tx_count)} total · ${fmtNum(b.whole)} kept whole · ${fmtNum(b.modified)} modified · ${fmtNum(b.stripped)} reduced to a txid` }),
          h("dt", { text: "Outputs" }), h("dd", { text: `${fmtNum(b.filter_entries)} data outputs dropped (filter entry kept) · ${fmtNum(b.dust_outputs)} P2TR dust` }),
          b.utxo_done ? h("dt", { text: "Spam UTXO change" }) : null,
          b.utxo_done ? h("dd", { text: `+${fmtNum(b.utxo_added)} created · −${fmtNum(b.utxo_spent)} spent` }) : null,
          b.retained_protocol ? h("dt", { text: "Kept by carrier policy" }) : null,
          b.retained_protocol ? h("dd", { text: `${fmtNum(b.retained_protocol)} payment-protocol OP_RETURN(s)` }) : null)));
    }
    if (!dlg.open) dlg.showModal();
    dlg.addEventListener("close", () => { if (location.hash.startsWith("#/block/")) history.replaceState(null, "", "#/blocks"); }, { once: true });
  }

  async function viewHistory() {
    const area = h("div", { class: "chart" }), share = h("div", { class: "chart" }), utxo = h("div", { class: "chart" });
    const c1 = h("div", { class: "card" }, h("h2", { text: "Spam per month" }),
      h("p", { class: "sub", text: "Bytes removed by a Monetary Node, by carrier, for each month of blocks scanned" }), legend(CARRIERS), area);
    const c2 = h("div", { class: "card" }, h("h2", { text: "Spam share of block data" }),
      h("p", { class: "sub", text: "Percentage of each month's block bytes that are spam" }), share);
    const c3 = h("div", { class: "card" }, h("h2", { text: "Spam entries in the UTXO set over time" }),
      h("p", { class: "sub", text: "Unspent spam and dust outputs, built up block by block during the full history scan" }), utxo);
    viewShell(c1, h("div", { style: { height: "16px" } }), h("div", { class: "grid grid-2" }, c2, c3));
    const data = await api("/api/history").catch(() => ({ months: [] }));
    const months = data.months;
    const mainnet = S.status && S.status.network === "mainnet";
    const rows = months.map((mo) => {
      const vals = {}; CARRIERS.forEach((k) => { vals[k] = mo[k] || 0; });
      return { key: mo.month, label: mo.month, title: `${mo.month} · blocks ${fmtNum(mo.from)}–${fmtNum(mo.to)}`, values: vals,
        extra: [{ label: "Block data", value: fmtBytes(mo.size) }] };
    });
    stackedArea(area, rows, CARRIERS, { milestones: mainnet ? MILESTONES : null, aria: "Spam bytes per month by carrier" });
    withTableToggle(c1, () => dataTable(["Month", "Blocks", "Block data", ...CARRIERS.map((k) => SHORT[k])],
      months.map((mo) => [mo.month, fmtNum(mo.blocks), fmtBytes(mo.size), ...CARRIERS.map((k) => fmtBytes(mo[k]))])));
    const shareRows = months.map((mo) => ({ label: mo.month, title: mo.month,
      value: mo.size ? (CARRIERS.reduce((a, k) => a + (mo[k] || 0), 0) / mo.size) * 100 : 0 }));
    lineChart(share, shareRows, "spam", { yFmt: (v) => fmtPct(v), axisFmt: (v) => v.toFixed(0) + " %", seriesLabel: "Spam share" });
    const full = months.filter((mo) => mo.utxo_count !== 0 || mo.utxo_bytes !== 0);
    lineChart(utxo, months.map((mo) => ({ label: mo.month, title: mo.month, value: mo.utxo_count })), "p2tr_dust",
      { yFmt: fmtNum, axisFmt: fmtCompact, seriesLabel: "Spam UTXO entries",
        empty: full.length ? undefined : "Available once the full history scan has progressed" });
  }

  function viewUtxo() {
    const sum = S.summary;
    if (!sum || sum.empty) return viewShell(emptyState());
    const u = sum.utxo;
    const kinds = ["p2tr_dust", "data_key"];
    const kindBars = h("div", { class: "compare" });
    const maxC = Math.max(1, ...kinds.map((k) => (u.by_kind[k] || {}).count || 0));
    for (const k of kinds) {
      const v = u.by_kind[k] || { count: 0, bytes: 0 };
      const track = h("div", { class: "bar-track" }, h("div", { class: `b-${k}` }));
      track.firstChild.style.width = (v.count / maxC) * 100 + "%";
      kindBars.append(h("div", { class: "compare-row" },
        h("div", { class: "lbl" }, h("span", null, h("i", { class: `swatch b-${k}` }), " ", LABEL[k]),
          h("b", { text: `${fmtNum(v.count)} · ${fmtBytes(v.bytes * 1.35)}` })), track));
    }
    const bandBox = h("div", { class: "compare" });
    const maxB = Math.max(1, ...u.age_bands.map((b) => b.count));
    for (const b of u.age_bands) {
      const track = h("div", { class: "bar-track" }, h("div", { class: "b-p2tr_dust" }));
      track.firstChild.style.width = (b.count / maxB) * 100 + "%";
      bandBox.append(h("div", { class: "compare-row" }, h("div", { class: "lbl" }, h("span", { text: b.band }), h("b", { text: fmtNum(b.count) })), track));
    }
    const nodeU = u.node_utxo;
    viewShell(
      !u.full_done ? h("div", { class: "banner warn", style: { marginBottom: "16px" } }, h("span", { class: "ico", text: "!" }),
        h("div", { text: "These figures are still being built: every spam output is tracked from the block that created it until it is spent, so the exact current number is known once the full history scan has reached the tip." })) : null,
      h("div", { class: "grid grid-4" },
        kpi("Spam entries in the UTXO set", fmtNum(u.count), u.complete ? "Exact, current" : "So far", "p2tr_dust"),
        kpi("Chainstate they occupy", fmtBytes(u.disk_estimate), `${fmtBytes(u.bytes)} serialized × 1.35 database overhead`),
        kpi("Share of all UTXOs", u.share_of_entries_pct != null ? fmtPct(u.share_of_entries_pct) : "–",
          nodeU ? `of ${fmtNum(nodeU.txouts)} entries in your node's UTXO set` : "Waiting for the node's UTXO set size"),
        kpi("Your node's UTXO set", nodeU ? fmtBytes(nodeU.disk_size) : "–", nodeU ? `on disk · read ${timeAgo(nodeU.time)}` : "Read once a day (read-only)")),
      h("div", { class: "grid grid-2", style: { marginTop: "16px" } },
        h("div", { class: "card" }, h("h2", { text: "By type" }),
          h("p", { class: "sub", text: "A Monetary Node keeps these out of its UTXO database. Stamps outputs are removed from blocks too; P2TR dust stays in block storage, where it costs less, and is looked up there if it is ever spent." }),
          kindBars),
        h("div", { class: "card" }, h("h2", { text: "How old they are" }),
          h("p", { class: "sub", text: "Spam outputs are almost never spent — they sit in RAM-hungry chainstate forever" }), bandBox)));
  }

  function viewAbout() {
    const st = S.status || {};
    const r = st.rules || {};
    const P = (t) => h("p", { text: t });
    viewShell(h("div", { class: "card prose" },
      h("h2", { text: "What this app shows" }),
      P("BitcoinMonetaryView reads every block from your own Bitcoin Core or Knots node and applies the rules of the Monetary Node project to it. It shows how much of your node's storage and UTXO set is taken up by data that is not money — and what a Monetary Node would store instead."),
      P("It is strictly read-only: it only uses read-only RPC calls (a fixed whitelist enforced inside the app), never touches your node's files and keeps its own results in a separate database."),
      h("h2", { text: "What is a Monetary Node?" }),
      P("A Monetary Node is a Bitcoin node that validates every consensus rule and every block, and then stores no spam. Inscription witness data, oversized OP_RETURN outputs, Stamps-style fake-key outputs and oversized scriptSigs are removed from block storage after validation. No fork, no confiscation, nobody's permission required."),
      P("The blocks still verify against their headers and real proof-of-work: a block's merkle root is computed over txids, and the Monetary Node keeps the 32-byte txid of every transaction it changes. Removed outputs keep a small filter entry, so anything removed can still be spent and validated locally."),
      h("h2", { text: "What counts as spam" }),
      h("ul", null,
        h("li", null, h("strong", { text: "Inscriptions: " }), "data inside OP_FALSE OP_IF … OP_ENDIF envelopes in taproot script-path and P2WSH witnesses."),
        h("li", null, h("strong", { text: "OP_RETURN over 83 bytes: " }), "oversized data carriers. Verified payment-protocol envelopes (Shielded Bitcoin) are kept when the carrier policy is on."),
        h("li", null, h("strong", { text: "Stamps / fake keys: " }), "bare multisig or P2PK outputs whose \"public keys\" are not points on the secp256k1 curve — data disguised as keys."),
        h("li", null, h("strong", { text: "Oversized scriptSig: " }), "input scripts over 1,650 bytes."),
        h("li", null, h("strong", { text: "Inscription-era P2TR dust: " }), "taproot outputs under 1,000 sats since block 767,430. They stay in block storage but leave the UTXO database.")),
      h("h2", { text: "Honest limitations" }),
      h("ul", null,
        h("li", { text: "A stripped block cannot be served to a conventional node. Monetary Nodes can serve each other; who serves full blocks if they become a large share of the network is an open question." }),
        h("li", { text: "Transactions whose outputs were removed can no longer have their signatures re-verified from the stripped store (they were fully validated once, when the block arrived)." }),
        h("li", { text: "The dust figures are a proxy: they count small taproot outputs from the inscription era, which includes some ordinary small payments." }),
        h("li", { text: "Monetary Node is early-stage software. This app informs your decision; it does not make it for you." })),
      h("h2", { text: "Core, Knots and this app" }),
      P("Knots' mempool filters stop relaying spam, but spam that is already in blocks is stored by every full node — Core and Knots alike. The numbers here apply to both."),
      h("h2", { text: "Rules in use" }),
      h("dl", { class: "kv" },
        h("dt", { text: "Upstream" }), h("dd", null, h("a", { href: (st.upstream || {}).repo || "#", rel: "noopener noreferrer", target: "_blank", text: "sambitcoin/BitcoinMonetaryNode" }), ` @ ${(r.upstream_commit || "").slice(0, 12)}`),
        h("dt", { text: "Carrier policy" }), h("dd", { text: r.carrier_policy ? `on (${(r.policy_id || "").slice(0, 16)}…)` : "off" }),
        h("dt", { text: "P2TR dust from" }), h("dd", { text: r.dust_start_height != null ? `block ${fmtNum(r.dust_start_height)}` : "–" }),
        h("dt", { text: "Units" }), h("dd", { text: "Decimal: 1 GB = 1,000,000,000 bytes" }))));
  }

  async function viewConnection() {
    const st = S.status || {};
    const node = st.node || {};
    const [settings, ct] = await Promise.all([api("/api/settings").catch(() => null), api("/api/connection-test").catch(() => null)]);
    S.settings = settings;
    const byName = {};
    (settings ? settings.settings : []).forEach((x) => { byName[x.name] = x; });

    const nodeCard = h("div", { class: "card" }, h("h2", { text: "Your node" }), h("p", { class: "sub", text: "Connection used for all read-only requests" }),
      h("dl", { class: "kv" },
        h("dt", { text: "Address" }), h("dd", { text: node.url || "–" }),
        h("dt", { text: "Software" }), h("dd", { text: node.version || "–" }),
        h("dt", { text: "Network" }), h("dd", { text: st.network || "–" }),
        h("dt", { text: "Block height" }), h("dd", { text: fmtNum(node.tip) }),
        h("dt", { text: "Blocks fetched via" }), h("dd", { text: node.rest ? "REST (binary, faster)" : "RPC" }),
        h("dt", { text: "Pruned" }), h("dd", { text: node.pruned ? `yes, from block ${fmtNum(node.prune_height)}` : "no" }),
        h("dt", { text: "App database" }), h("dd", { text: st.db_size ? fmtBytes(st.db_size) : "–" })));

    const testBox = h("div");
    const renderTest = (t) => {
      clear(testBox);
      if (t && t.result) {
        for (const c of t.result) {
          testBox.append(h("div", { class: "check" }, h("span", { class: `st ${c.status}`, text: c.status === "ok" ? "✓" : "!" }),
            h("div", null, h("div", null, h("strong", { text: c.name + ": " }), c.message), c.hint ? h("div", { class: "hint", text: c.hint }) : null)));
        }
        testBox.append(h("p", { class: "sub", style: { marginTop: "8px" }, text: `Tested ${timeAgo(t.time)}` }));
      } else testBox.append(h("p", { class: "sub", text: t && t.running ? "Running…" : "Not run yet." }));
    };
    renderTest(ct);
    const testBtn = h("button", { class: "btn", type: "button", text: "Run connection test" });
    testBtn.addEventListener("click", async () => {
      testBtn.disabled = true;
      try {
        await post("/api/connection-test", {});
        for (let i = 0; i < 60; i++) {
          await new Promise((r) => setTimeout(r, 1000));
          const t = await api("/api/connection-test");
          renderTest(t);
          if (!t.running && t.result) break;
        }
      } catch (e) { testBox.append(h("p", { class: "sub", text: e.message })); }
      testBtn.disabled = false;
    });
    const testCard = h("div", { class: "card" }, h("div", { class: "card-head" },
      h("div", null, h("h2", { text: "Connection test" }), h("p", { class: "sub", text: "Checks reachability, credentials, sync state and speed" })), testBtn), testBox);

    // scan controls
    const ctrl = h("div");
    const prof = byName.speed_profile;
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Speed profile" });
    for (const [v, l] of [["eco", "Eco"], ["balanced", "Balanced"], ["full", "Full speed"]]) {
      seg.append(h("button", { type: "button", "aria-pressed": String(prof && prof.value === v), disabled: !(prof && prof.editable), text: l,
        onclick: () => saveSetting({ speed_profile: v }) }));
    }
    const win = byName.scan_window, tz = byName.timezone;
    const winInput = h("input", { type: "text", value: win ? win.value : "", placeholder: "e.g. 01:00-07:00 (empty = always)", disabled: !(win && win.editable) });
    const tzInput = h("input", { type: "text", value: tz ? tz.value : "UTC", placeholder: "e.g. Europe/Berlin", disabled: !(tz && tz.editable) });
    const saveWin = h("button", { class: "btn", type: "button", text: "Save", disabled: !(win && win.editable),
      onclick: () => saveSetting({ scan_window: winInput.value.trim(), timezone: tzInput.value.trim() || "UTC" }) });
    const msg = h("p", { class: "sub", style: { margin: "8px 0 0" } });
    async function saveSetting(changes) {
      try { await post("/api/settings", { changes }); msg.textContent = "Saved."; setTimeout(viewConnection, 300); }
      catch (e) { msg.textContent = `Not saved: ${e.message}`; }
    }
    const pinnedNote = (x) => x && !x.editable ? h("span", { class: "src", text: st.managed ? "managed" : `set via ${x.source}` }) : null;
    ctrl.append(
      h("div", { class: "form-row" }, h("label", null, "Speed profile", pinnedNote(prof)), seg,
        h("div", { class: "help", text: "Eco is gentlest on your node (default). Full speed uses parallel requests. The scanner also backs off automatically when your node responds slowly." })),
      h("div", { class: "form-row" }, h("label", null, "Scan window", pinnedNote(win)), h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } }, winInput, tzInput, saveWin),
        h("div", { class: "help", text: "Optional: only scan history during these hours. New blocks are always analysed." })),
      h("div", { class: "form-row" }, h("label", { text: "Scanning" }), h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
        st.can_pause ? h("button", { class: "btn", type: "button", text: st.paused ? "Resume" : "Pause", onclick: togglePause }) : null,
        st.can_rescan ? h("button", { class: "btn btn-danger", type: "button", text: "Rescan from scratch", onclick: async () => {
          if (!confirm("Delete this app's results and scan the whole chain again? Your node is not affected.")) return;
          await post("/api/control", { action: "rescan" }).catch((e) => alert(e.message));
        } }) : null)),
      msg);
    const ctrlCard = h("div", { class: "card" }, h("h2", { text: "Scan settings" }),
      h("p", { class: "sub", text: st.managed ? "Managed by the platform — change these in the service's settings." : "These only affect this app." }), ctrl);

    // all settings (read-only list)
    const setTable = settings ? dataTable(["Setting", "Value", "Source"], settings.settings.map((x) => [x.name,
      Array.isArray(x.value) ? x.value.join(", ") : x.value === "" || x.value == null ? "—" : String(x.value), x.source])) : h("p", { text: "–" });
    const allCard = h("div", { class: "card" }, h("h2", { text: "All settings" }),
      h("p", { class: "sub", text: "Set them via command line, environment variables (BMV_…) or settings.json in the data directory. Secrets are never shown." }), setTable);

    const act = h("ul", { class: "activity" }, (st.activity || []).map((a) => h("li", null, h("time", { text: clock(a.time) }), h("span", { class: a.level, text: a.message }))));
    const actCard = h("div", { class: "card" }, h("h2", { text: "Activity" }), h("p", { class: "sub", text: "What the scanner has been doing" }), act);

    viewShell(h("div", { class: "grid grid-2" }, nodeCard, testCard, ctrlCard, actCard, h("div", { class: "span-2" }, allCard)));
  }

  // ------------------------------------------------------------------ share card
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function drawShareCard(sum) {
    const W = 1200, H = 630;
    const c = h("canvas", { width: W, height: H });
    const x = c.getContext("2d");
    const bg = "#0d0f13", fg = "#f3f4f6", fg2 = "#b4b8c2", muted = "#7d8290";
    const col = { monetary: "#d27508", envelope: "#3987e5", op_return: "#199e70", multisig: "#9085e9", scriptsig: "#d55181" };
    x.fillStyle = bg; x.fillRect(0, 0, W, H);
    const grd = x.createRadialGradient(W, 0, 10, W, 0, 700);
    grd.addColorStop(0, "rgba(247,147,26,0.25)"); grd.addColorStop(1, "rgba(247,147,26,0)");
    x.fillStyle = grd; x.fillRect(0, 0, W, H);
    const font = (w, s2) => `${w} ${s2}px ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif`;
    x.fillStyle = "#f7931a"; x.font = font(700, 26); x.fillText("Monetary View", 64, 76);
    x.fillStyle = muted; x.font = font(500, 20); x.fillText("My Bitcoin node, analysed with the Monetary Node rules", 64, 108);
    x.fillStyle = fg; x.font = font(800, 76); x.fillText(fmtBytes(sum.spam_bytes), 64, 230);
    x.fillStyle = fg2; x.font = font(600, 30); x.fillText(`of spam in my node's blocks (${fmtPct(sum.spam_pct)})`, 64, 280);
    const stats = [
      ["A Monetary Node saves", sum.saved_bytes > 0 ? `${fmtBytes(sum.saved_bytes)} (${fmtPct(sum.saved_pct)})` : "—"],
      ["Spam UTXO entries", sum.utxo.count ? fmtNum(sum.utxo.count) : "—"],
      ["Transactions touched", fmtPct(sum.modified_tx_pct)],
    ];
    stats.forEach(([l, v], i) => {
      const y = 350 + i * 78;
      x.fillStyle = muted; x.font = font(500, 20); x.fillText(l, 64, y);
      x.fillStyle = fg; x.font = font(700, 32); x.fillText(v, 64, y + 38);
    });
    // donut
    const cx = 930, cy = 330, R = 170, r = 112;
    const items = CARRIERS.map((k) => [k, sum.by_carrier[k] || 0]).filter((i) => i[1] > 0);
    const tot = items.reduce((a, b) => a + b[1], 0) || 1;
    let a0 = -Math.PI / 2;
    for (const [k, v] of items) {
      const a1 = a0 + (v / tot) * Math.PI * 2;
      const g = items.length > 1 && a1 - a0 > 0.03 ? 0.012 : 0;
      const s0 = a0 + g, s1 = Math.max(s0 + 0.004, a1 - g);
      x.beginPath(); x.arc(cx, cy, R, s0, s1, false); x.arc(cx, cy, r, s1, s0, true); x.closePath();
      x.fillStyle = col[k]; x.fill(); a0 = a1;
    }
    x.textAlign = "center"; x.fillStyle = fg; x.font = font(800, 40); x.fillText(fmtPct(sum.spam_pct), cx, cy + 8);
    x.fillStyle = muted; x.font = font(500, 18); x.fillText("of block data", cx, cy + 36);
    x.textAlign = "left";
    let lx = 760;
    items.forEach(([k], i) => {
      const y = 538 + Math.floor(i / 2) * 30, xx = lx + (i % 2) * 200;
      x.fillStyle = col[k]; x.fillRect(xx, y - 13, 14, 14);
      x.fillStyle = fg2; x.font = font(500, 17); x.fillText(SHORT[k], xx + 22, y);
    });
    const st = S.status || {};
    const scope = sum.meta && sum.meta.full_done === "1" ? `Full chain · ${fmtNum(sum.blocks_scanned)} blocks` : `${fmtNum(sum.blocks_scanned)} blocks scanned so far`;
    x.fillStyle = muted; x.font = font(500, 17);
    x.fillText(`${st.network || ""} · ${scope}`, 64, H - 40);
    x.fillText("Rules: github.com/sambitcoin/BitcoinMonetaryNode", 64, H - 16);
    return c;
  }
  function shareText(sum) {
    const full = sum.meta && sum.meta.full_done === "1";
    return [`My Bitcoin node stores ${fmtBytes(sum.spam_bytes)} of spam — ${fmtPct(sum.spam_pct)} of its block data${full ? "" : " (in the blocks scanned so far)"}.`,
      sum.saved_bytes > 0 ? `A Monetary Node would store ${fmtBytes(sum.saved_bytes)} (${fmtPct(sum.saved_pct)}) less${sum.utxo.count ? ` and keep ${fmtNum(sum.utxo.count)} spam entries out of its UTXO set` : ""}.` : "",
      "Same blocks, same proof-of-work, every consensus rule validated.",
      "Measured on my own node with BitcoinMonetaryView. https://github.com/sambitcoin/BitcoinMonetaryNode"].filter(Boolean).join(" ");
  }
  function openShare() {
    const sum = S.summary;
    if (!sum || sum.empty) return;
    const dlg = $("#dialog"), body = clear($("#dialog-body"));
    const canvas = drawShareCard(sum);
    const img = h("img", { class: "share-preview", alt: "Summary card preview" });
    const dl = h("a", { class: "btn btn-primary", download: "my-node-spam.png", text: "Download image" });
    canvas.toBlob((blob) => { const u = URL.createObjectURL(blob); img.src = u; dl.href = u; });
    const ta = h("textarea", { class: "share-text", readonly: true });
    ta.value = shareText(sum);
    const copy = h("button", { class: "btn", type: "button", text: "Copy text" });
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(ta.value); copy.textContent = "Copied ✓"; }
      catch (e) { ta.select(); copy.textContent = "Press Ctrl+C"; }
    });
    body.append(h("div", { class: "dlg" },
      h("div", { class: "dlg-head" }, h("h2", { text: "Share my results" }),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Close", text: "✕", onclick: () => dlg.close() })),
      img, ta,
      h("p", { class: "sub", style: { marginTop: "10px" }, text: "Created on this device — nothing is uploaded. Contains chain statistics only, nothing that identifies your node." }),
      h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } }, dl, copy)));
    dlg.showModal();
  }

  // ------------------------------------------------------------------ router & polling
  const VIEWS = { overview: viewOverview, blocks: viewBlocks, history: viewHistory, utxo: viewUtxo, about: viewAbout, connection: viewConnection };
  function route(force) {
    const hash = location.hash || "#/overview";
    const m = hash.match(/^#\/block\/(\d+)$/);
    if (m) {
      if (S.view !== "blocks") { S.view = "blocks"; markNav(); viewBlocks(); }
      openBlock(parseInt(m[1], 10));
      return;
    }
    const v = (hash.match(/^#\/(\w+)/) || [])[1];
    const view = VIEWS[v] ? v : "overview";
    if (view !== S.view || force) {
      S.view = view;
      markNav();
      VIEWS[view]();
      if (!force) main().focus({ preventScroll: true });
    }
  }
  function markNav() {
    document.querySelectorAll(".tabs a").forEach((a) => {
      if (a.dataset.view === S.view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  async function togglePause() {
    const st = S.status;
    try { await post("/api/control", { action: st && st.paused ? "resume" : "pause" }); await pollStatus(); }
    catch (e) { alert(e.message); }
  }
  async function pollStatus() {
    try {
      S.status = await api("/api/status");
      renderStatus();
      const h0 = S.status.height;
      const stale = Date.now() - S.lastDataFetch > (["full", "quick", "sample"].includes(S.status.phase) ? 15000 : 60000);
      if (h0 !== S.lastHeight && (S.lastHeight == null || stale || S.status.phase === "live")) {
        S.lastHeight = h0;
        await refreshData();
      } else if (stale) await refreshData();
    } catch (e) {
      $("#phase-label").textContent = "Offline";
      $("#status-detail").textContent = "Cannot reach the BitcoinMonetaryView server.";
      $("#phase-pill").dataset.tone = "error";
    }
  }
  async function refreshData() {
    S.lastDataFetch = Date.now();
    const [sum, latest] = await Promise.all([api("/api/summary").catch(() => null), api("/api/blocks?limit=8").catch(() => [])]);
    S.summary = sum; S.latestBlocks = latest;
    // re-render data views only when no dialog is open and the user is not interacting with a form
    const typing = document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    if (!$("#dialog").open && !typing && ["overview", "utxo"].includes(S.view)) VIEWS[S.view]();
  }

  async function init() {
    initTheme();
    $("#pause-btn").addEventListener("click", togglePause);
    window.addEventListener("hashchange", () => route(false));
    let rz = null, lastW = innerWidth;
    window.addEventListener("resize", () => {
      clearTimeout(rz);
      rz = setTimeout(() => { if (Math.abs(innerWidth - lastW) > 40 && !$("#dialog").open) { lastW = innerWidth; route(true); } }, 250);
    });
    await pollStatus();
    await refreshData();
    route(true);
    // no polling while the tab is hidden; catch up immediately when it becomes visible again
    setInterval(() => { if (!document.hidden) pollStatus(); }, 2000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) pollStatus(); });
  }
  init();
})();
