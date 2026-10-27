/* BitcoinMonetaryView — SPDX-License-Identifier: AGPL-3.0-or-later
 * Vanilla JS, no dependencies, no external requests. All DOM text is set via
 * textContent; HTML strings are never parsed. */
"use strict";

(async () => {
  // ------------------------------------------------------------------ language
  // English is the source text; i18n/<lang>.js holds each translation and only the chosen one is
  // loaded. The language follows the browser unless the viewer picked one in the language menu
  // (remembered in this browser).
  const LANGS = {
    en: { name: "English", locale: "en-US", clock: "en-GB" },
    de: { name: "Deutsch", locale: "de-DE" },
    es: { name: "Español", locale: "es-ES" },
    fr: { name: "Français", locale: "fr-FR" },
    pl: { name: "Polski", locale: "pl-PL" },
  };
  function pickLang() {
    let saved = null;
    try { saved = localStorage.getItem("bmv-lang"); } catch (e) { /* storage blocked */ }
    if (saved && LANGS[saved]) return saved;
    for (const l of navigator.languages || [navigator.language || "en"]) {
      const base = String(l).toLowerCase().split("-")[0];
      if (LANGS[base]) return base;
    }
    return "en";
  }
  /** Load i18n/<lang>.js; if it cannot be loaded the dashboard stays in English. */
  function loadLang(lang) {
    if (lang === "en") return Promise.resolve(null);
    return new Promise((resolve) => {
      const el = document.createElement("script");
      el.src = `i18n/${lang}.js`;
      el.onload = () => resolve((window.BMV_I18N || {})[lang] || null);
      el.onerror = () => resolve(null);
      document.head.append(el);
    });
  }
  const I18N = await loadLang(pickLang());
  const LANG = I18N ? pickLang() : "en";
  const LOCALE = LANGS[LANG].locale;
  const PLURAL = new Intl.PluralRules(LOCALE);
  /** Translate an English UI text; {name} placeholders are filled from vars. A translation can
   *  also be {one, few, many, other}: the form is chosen by the number in vars.n (Polish needs
   *  three forms where English has two). */
  function _t(en, vars) {
    let out = (I18N && I18N.strings[en]) || en;
    if (typeof out === "object") {
      const n = vars && vars.n != null ? Number(String(vars.n).replace(/[^0-9]/g, "")) : NaN;
      out = out[isNaN(n) ? "other" : PLURAL.select(n)] || out.other;
    }
    if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (vars[k] != null ? String(vars[k]) : m));
    return out;
  }
  /** Translate a text the server sent (status, activity, connection test); unknown ones stay English. */
  const _ts = (msg) => (I18N && msg ? I18N.server(String(msg)) : msg);

  // ------------------------------------------------------------------ constants
  const PLATFORM_NAMES = { startos: "StartOS", umbrel: "Umbrel" };
  const CARRIERS = ["envelope", "op_return", "multisig", "scriptsig"];
  const LABEL = {
    monetary: _t("Monetary data"),
    envelope: _t("Inscriptions (witness envelopes)"),
    op_return: _t("OP_RETURN over 83 bytes"),
    multisig: _t("Fake-key outputs (Stamps)"),
    scriptsig: _t("Oversized scriptSig"),
    data_key: _t("Fake-key outputs (Stamps)"),
    p2tr_dust: _t("Inscription-era P2TR dust"),
  };
  const SHORT = {
    monetary: _t("Monetary"), envelope: _t("Inscriptions"), op_return: "OP_RETURN", multisig: "Stamps",
    scriptsig: "scriptSig", data_key: "Stamps", p2tr_dust: _t("P2TR dust"),
  };
  const MILESTONES = [
    { month: "2014-01", label: _t("Counterparty") },
    { month: "2017-08", label: _t("SegWit") },
    { month: "2021-11", label: _t("Taproot") },
    { month: "2022-12", label: _t("Ordinals") },
    { month: "2023-03", label: _t("Stamps") },
  ];
  const NF = new Intl.NumberFormat(LOCALE);
  const DEC = [0, 1, 2, 3].map((d) => new Intl.NumberFormat(LOCALE, { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: false }));
  const fixed = (v, d) => DEC[d].format(v);
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
    return (neg ? "−" : "") + fixed(b, d) + " " + u[i];
  }
  const fmtNum = (n) => (n == null || isNaN(n) ? "–" : NF.format(Math.round(n)));
  function fmtCompact(n) {
    if (n == null) return "–";
    const a = Math.abs(n);
    if (a >= 1e9) return fixed(n / 1e9, a >= 1e10 ? 0 : 1) + _t("B");
    if (a >= 1e6) return fixed(n / 1e6, a >= 1e7 ? 0 : 1) + _t("M");
    if (a >= 1e3) return fixed(n / 1e3, a >= 1e4 ? 0 : 1) + _t("k");
    return String(Math.round(n));
  }
  function fmtPct(p, d = 1) { return p == null || isNaN(p) ? "–" : fixed(p, d) + " %"; }
  function fmtDur(sec) {
    if (sec == null || !isFinite(sec)) return "–";
    sec = Math.max(0, Math.round(sec));
    const d = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
    if (d) return _t("{d}d {h}h", { d, h: hh });
    if (hh) return _t("{h}h {m}m", { h: hh, m });
    if (m) return _t("{m}m {s}s", { m, s: sec % 60 });
    return _t("{s}s", { s: sec });
  }
  function timeAgo(t) {
    const d = Date.now() / 1000 - t;
    if (d < 60) return _t("just now");
    if (d < 3600) return _t("{n} min ago", { n: Math.floor(d / 60) });
    if (d < 86400) return _t("{n} h ago", { n: Math.floor(d / 3600) });
    return new Date(t * 1000).toISOString().slice(0, 10);
  }
  const fmtDate = (t) => new Date(t * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
  const clock = (t) => new Date(t * 1000).toLocaleTimeString(LANGS[LANG].clock || LOCALE, { hour12: false });

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
    if (!rows.length) { container.append(h("div", { class: "chart-empty", text: opts.empty || _t("No data yet") })); return; }
    const W = chartWidth(container), H = opts.height || 260, m = { l: 52, r: 8, t: 10, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const totals = rows.map((r) => keys.reduce((a, k) => a + (r.values[k] || 0), 0));
    const max = niceMax(Math.max(...totals));
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || _t("Stacked bar chart") });
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
  /** Grey band over the rows the scan has not reached yet ([first, last] index). */
  function pendingBand(svg, X, step, top, height, range) {
    if (!range) return;
    const x0 = Math.max(X(0), X(range[0]) - step / 2), x1 = Math.min(X(range[2]), X(range[1]) + step / 2);
    svg.append(s("rect", { class: "pending-band", x: x0, y: top, width: Math.max(2, x1 - x0), height }));
    if (x1 - x0 > 70) {
      const t = s("text", { class: "pending-label", x: (x0 + x1) / 2, y: top + height / 2, "text-anchor": "middle" });
      t.textContent = _t("Not scanned yet");
      svg.append(t);
    }
  }

  function stackedArea(container, rows, keys, opts = {}) {
    clear(container);
    if (rows.length < 2) { container.append(h("div", { class: "chart-empty", text: opts.empty || _t("Not enough data yet") })); return; }
    const W = chartWidth(container), H = opts.height || 280, m = { l: 56, r: 12, t: 34, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const totals = rows.map((r) => keys.reduce((a, k) => a + (r.values[k] || 0), 0));
    const max = niceMax(Math.max(...totals));
    const X = (i) => m.l + (rows.length === 1 ? iw / 2 : (iw * i) / (rows.length - 1));
    const Y = (v) => m.t + ih - (v / max) * ih;
    const fmt = opts.yFmt || ((v) => fmtBytes(v, 0));
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || _t("Area chart") });
    for (let i = 0; i <= 4; i++) {
      const y = m.t + ih - (ih * i) / 4;
      svg.append(s("line", { class: i ? "grid-line" : "baseline", x1: m.l, x2: W - m.r, y1: y, y2: y }));
      const t = s("text", { class: "axis", x: m.l - 8, y: y + 4, "text-anchor": "end" });
      t.textContent = fmt((max * i) / 4);
      svg.append(t);
    }
    if (opts.pending) pendingBand(svg, X, iw / Math.max(1, rows.length - 1), m.t, ih, [...opts.pending, rows.length - 1]);
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
      const rowEnd = [-1e9, -1e9];          // right edge of the last label in each of the two label rows
      opts.milestones.forEach((ms) => {
        const i = rows.findIndex((r) => r.key >= ms.month);
        if (i < 0) return;
        const x = X(i), w = ms.label.length * 6.2 + 8;
        const level = rowEnd.findIndex((end) => x > end + 4);
        const g = s("g", { class: "milestone" });
        g.append(s("line", { x1: x, x2: x, y1: level < 0 ? m.t : m.t - 18 + level * 14, y2: m.t + ih }));
        const tt = s("title"); tt.textContent = `${ms.label} (${ms.month})`; g.append(tt);
        if (level >= 0 && x + w <= W) {
          rowEnd[level] = x + w;
          const t = s("text", { x: x + 4, y: m.t - 22 + level * 14 });
          t.textContent = ms.label;
          g.append(t);
        }
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
    if (rows.length < 2) { container.append(h("div", { class: "chart-empty", text: opts.empty || _t("Not enough data yet") })); return; }
    const W = chartWidth(container), H = opts.height || 220, m = { l: 56, r: 12, t: 12, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const max = niceMax(Math.max(0, ...rows.filter((r) => r.value != null).map((r) => r.value)));
    const X = (i) => m.l + (iw * i) / (rows.length - 1);
    const Y = (v) => m.t + ih - (v / max) * ih;
    const fmt = opts.yFmt || fmtNum;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || _t("Line chart") });
    for (let i = 0; i <= 4; i++) {
      const y = m.t + ih - (ih * i) / 4;
      svg.append(s("line", { class: i ? "grid-line" : "baseline", x1: m.l, x2: W - m.r, y1: y, y2: y }));
      const t = s("text", { class: "axis", x: m.l - 8, y: y + 4, "text-anchor": "end" });
      t.textContent = (opts.axisFmt || fmt)((max * i) / 4);
      svg.append(t);
    }
    if (opts.pending) pendingBand(svg, X, iw / Math.max(1, rows.length - 1), m.t, ih, [...opts.pending, rows.length - 1]);
    let d = "", pen = false;      // months without data (not scanned yet) break the line
    rows.forEach((r, i) => {
      if (r.value == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(i)},${Y(r.value)}`; pen = true;
    });
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
      if (rows[i].value == null) {
        dot.setAttribute("visibility", "hidden");
        showTip(e, rows[i].title || rows[i].label, [{ label: _t("Not scanned yet"), value: "" }]);
        return;
      }
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
    const svg = s("svg", { viewBox: "0 0 200 200", role: "img", "aria-label": _t("Donut chart") });
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
        path.addEventListener("mousemove", (e) => showTip(e, LABEL[it.key], [{ key: it.key, label: _t("Bytes"), value: fmtBytes(it.value) }, { label: _t("Share"), value: fmtPct(frac * 100) }]));
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
    const btn = h("button", { class: "toggle-table", type: "button", text: _t("Show as table") });
    btn.addEventListener("click", () => {
      shown = !shown;
      if (shown) { tableEl = makeTable(); card.append(tableEl); btn.textContent = _t("Hide table"); }
      else { tableEl && tableEl.remove(); btn.textContent = _t("Show as table"); }
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
    $("#phase-label").textContent = _ts(st.phase_label || st.phase);
    $("#status-detail").textContent = _ts(st.detail || "");
    $("#status-detail").title = _ts(st.detail || "");
    const prog = st.phase === "live" ? 100 : st.progress != null ? st.progress : 0;
    $("#progress-fill").style.transform = `scaleX(${Math.max(0, Math.min(100, prog)) / 100})`;
    $("#progress").setAttribute("aria-valuenow", String(Math.round(prog)));
    $("#m-height").textContent = st.height != null ? `${fmtNum(st.height)} / ${fmtNum(st.tip)}` : "–";
    $("#m-progress").textContent = ["quick", "sample"].includes(st.phase) ? `${fmtPct(prog)} (${_t(st.phase)})` : st.progress != null ? fmtPct(prog, 2) : "–";
    $("#m-speed").textContent = ["full", "quick", "sample"].includes(st.phase) && st.bytes_per_s
      ? `${fmtBytes(st.bytes_per_s)}/s · ${fixed(st.blocks_per_s, 1)} ${_t("blk/s")}` : "–";
    $("#m-elapsed").textContent = fmtDur(st.now - (st.full_started_at || st.started_at));
    $("#m-eta").textContent = st.phase === "live" ? _t("done") : st.eta_seconds ? "~" + fmtDur(st.eta_seconds) : "–";
    const pb = $("#pause-btn");
    pb.hidden = !st.can_pause || st.phase === "live";
    pb.textContent = st.paused ? _t("Resume") : _t("Pause");
    $("#app-version").textContent = st.version || "";
    renderBanners();
  }
  function renderBanners() {
    const st = S.status, box = clear($("#banners"));
    const add = (kind, text) => box.append(h("div", { class: `banner ${kind}` },
      h("span", { class: "ico", text: kind === "info" ? "i" : "!" }), h("div", { text })));
    if (st.node && st.node.plaintext_remote) add("warn", _t("Your RPC connection uses plain HTTP to another machine, so the RPC password travels unencrypted. Prefer an https:// URL, Tor or an SSH tunnel."));
    if (st.node && st.node.pruned) add("warn", _t("Your node is pruned: only blocks from {h} on can be analysed, and the spam UTXO figure will be incomplete.", { h: fmtNum(st.node.prune_height) }));
    for (const w of st.warnings || []) add("warn", _ts(w));
    if (st.phase === "error" && st.last_error) add("error", _ts(st.last_error));
  }

  // ------------------------------------------------------------------ views
  const main = () => $("#main");
  // Fade in only when the user navigates; background data refreshes swap the content silently.
  function viewShell(...kids) { const m = clear(main()); m.append(h("div", { class: S.quiet ? null : "fade-in" }, kids)); }
  /** The one style for notes in page content (same as the UTXO page's banner). */
  function notice(kind, text, icon, extraClass) {
    return h("div", { class: `banner ${kind}${extraClass ? " " + extraClass : ""}` },
      h("span", { class: "ico", text: icon || (kind === "info" ? "i" : "!") }), h("div", { text }));
  }
  /** A translated sentence with one highlighted value: "{x}" marks where the value goes. */
  function highlight(sentence, value) {
    const [a, b = ""] = sentence.split("{x}");
    return [a, h("span", { class: "hl", text: value }), b];
  }
  function partialNote(sum) {
    const st = S.status || {};
    if (sum.meta && sum.meta.full_done === "1") return null;
    const est = sum.estimate;
    if (est) {
      const m = est.spam_margin_pct != null && est.spam_margin_pct >= 0.1 ? ` ±${fmtPct(est.spam_margin_pct)}` : "";
      return notice("warn", _t("Estimate for the whole chain{m}, from {n} sample blocks spread across its history. " +
        "Exact so far: {spam} of spam in {share} of the data — the estimate is replaced by exact figures as the full history scan proceeds.",
        { m, n: fmtNum(est.samples), spam: fmtBytes(sum.spam_bytes), share: fmtPct(est.exact_share_pct) }), null, "in-card");
    }
    if (sum.estimate_pending) {
      return notice("warn", _t("Based on the {n} blocks scanned so far, mostly the latest ones. " +
        "The estimate for the whole chain follows in a few minutes, once the sample pass is done.", { n: fmtNum(sum.blocks_scanned) }), null, "in-card");
    }
    const tipH = st.tip != null ? st.tip + 1 : null;
    return notice("warn", tipH
      ? _t("Based on {n} of {total} blocks scanned so far — the full history scan is still running", { n: fmtNum(sum.blocks_scanned), total: fmtNum(tipH) })
      : _t("Based on {n} blocks scanned so far — the full history scan is still running", { n: fmtNum(sum.blocks_scanned) }), null, "in-card");
  }
  function emptyState(text) {
    return h("div", { class: "card", style: { textAlign: "center", padding: "48px 20px" } },
      h("h2", { text: _t("Scanning your node…") }),
      h("p", { class: "sub", text: text || _t("The first results appear after a few blocks. You can watch the progress in the bar above.") }));
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
      h("h2", { text: _t("Spam by carrier") }),
      h("p", { class: "sub", text: est ? _t("Spam in your blocks by type — estimated for the whole chain") : _t("Spam in your blocks by type") }),
      h("div", { class: "donut-wrap" }, donutBox,
        h("div", { class: "donut-legend" }, items.map((it) => h("div", { class: "row" },
          h("i", { class: `swatch b-${it.key}` }), h("span", { text: LABEL[it.key] }),
          h("span", { class: "v num", text: fmtBytes(it.value) }),
          h("span", { class: "p num", text: fmtPct(B.spam_bytes ? (it.value / B.spam_bytes) * 100 : 0) }))))));
    donut(donutBox, items, [ap + fmtPct(B.spam_pct), _t("of block data")]);
    withTableToggle(donutCard, () => dataTable([_t("Carrier"), _t("Bytes"), _t("Share of spam")],
      items.map((it) => [LABEL[it.key], ap + fmtBytes(it.value), fmtPct(B.spam_bytes ? (it.value / B.spam_bytes) * 100 : 0)])));

    const maxB = Math.max(t.size, t.stored) || 1;
    const compare = h("div", { class: "card" },
      h("h2", { text: _t("Block storage: today vs. Monetary Node") }),
      h("p", { class: "sub", text: est ? _t("Same blocks, same proof-of-work — only the data carriers removed (estimated for the whole chain)") : _t("Same blocks, same proof-of-work — only the data carriers removed") }),
      h("div", { class: "compare" },
        compareRow(_t("Your node (Core/Knots)"), t.size, maxB, [["monetary", t.size - B.spam_bytes], ...CARRIERS.map((k) => [k, t[k]])]),
        compareRow("Monetary Node", t.stored, maxB, [["monetary", t.stored]])),
      h("p", { class: "sub", style: { marginTop: "14px", marginBottom: 0 },
        text: _t("The saving is usually larger than the spam itself: in transactions that carry spam, a Monetary Node also drops the witness data (signatures and other data already verified when the block was accepted), and transactions that are only spam are reduced to their txid. Its own bookkeeping (stored txids, a record of removed outputs) is included. Undo files and optional indexes are not.") }));

    const u = sum.utxo;
    const kpis = h("div", { class: "grid grid-4" },
      kpi(_t("Spam in your blocks"), ap + fmtBytes(B.spam_bytes), _t("{pct} of {size} block data", { pct: ap + fmtPct(B.spam_pct), size: ap + fmtBytes(t.size) })),
      kpi(_t("Storage a Monetary Node saves"), saved >= 0 ? ap + fmtBytes(saved) : "—", saved >= 0 ? _t("{pct} less block storage", { pct: ap + fmtPct(B.saved_pct) }) : _t("No saving in the blocks scanned so far")),
      kpi(_t("Spam entries in the UTXO set"), u.full_done || u.count ? fmtCompact(u.count) : "–", utxoFoot(u)),
      kpi(_t("Transactions with spam"), ap + fmtPct(B.modified_tx_pct), est ? _t("of all transactions modified or reduced to a txid") : _t("{n} of {total} modified or reduced to a txid", { n: fmtNum(t.modified + t.stripped), total: fmtNum(t.tx_count) })));

    viewShell(
      h("section", { class: "hero" },
        sum.estimate_pending && !est
          ? h("h1", null, ...highlight(_t("Spam makes up {x} of recent blocks."), fmtPct(sum.spam_pct)))
          : h("h1", null, ...highlight(_t("Your node stores {x} of spam."), (est ? "≈ " : "") + fmtBytes(B.spam_bytes))),
        h("p", { text: est
          ? _t("Estimated for the whole chain ({n} blocks): a Monetary Node would store about {stored} instead of {size} — {saved} ({pct}) less.",
            { n: fmtNum(est.blocks), stored: fmtBytes(t.stored), size: fmtBytes(t.size), saved: fmtBytes(Math.max(0, saved)), pct: fmtPct(Math.max(0, est.saved_pct)) })
          : saved > 0
          ? _t("A Monetary Node validating the same {n} blocks would store {stored} instead of {size} — {saved} ({pct}) less — and keep {utxo} spam entries out of its UTXO set.",
            { n: fmtNum(sum.blocks_scanned), stored: fmtBytes(t.stored), size: fmtBytes(t.size), saved: fmtBytes(saved), pct: fmtPct(sum.saved_pct), utxo: fmtNum(u.count) })
          : _t("In the {n} blocks scanned so far, spam makes up {pct} of the data.", { n: fmtNum(sum.blocks_scanned), pct: fmtPct(sum.spam_pct) }) }),
        partialNote(sum),
        h("div", { class: "hero-actions" },
          h("button", { class: "btn btn-primary", type: "button", onclick: openShare, text: _t("Share my results") }),
          h("a", { class: "btn", href: "/api/export.csv", text: _t("Download CSV") }),
          h("a", { class: "btn", href: "/api/export.json", text: _t("Download JSON") }),
          h("a", { class: "btn btn-ghost", href: "#/about", text: _t("What is a Monetary Node?") }))),
      kpis,
      h("div", { class: "grid grid-2", style: { marginTop: "16px" } }, donutCard, compare),
      trendsCard(S.trends),
      h("div", { class: "section-title", text: _t("Latest blocks") }),
      blockStrip());
  }
  /** Pace and records, from the blocks scanned so far (no extra requests to the node). */
  function trendsCard(tr) {
    if (!tr || tr.empty) return null;
    const lm = tr.last_month, rc = tr.recent, ya = tr.year_ago, tb = tr.top_block, tm = tr.top_month;
    const tiles = [];
    if (lm) {
      tiles.push(kpi(_t("Spam in the last 30 days"), fmtBytes(lm.spam_bytes),
        _t("≈ {size} per year at this pace", { size: fmtBytes(lm.per_year_bytes) })));
    }
    if (rc) {
      let foot = _t("of the data in the last {n} blocks", { n: fmtNum(rc.blocks) });
      if (ya) {
        const d = rc.spam_pct - ya.spam_pct;
        foot = _t("a year earlier: {pct}", { pct: fmtPct(ya.spam_pct) }) + " · " +
          (Math.abs(d) < 0.05 ? _t("unchanged") : (d > 0 ? "▲ " : "▼ ") + _t("{d} points", { d: fixed(Math.abs(d), 1) }));
      }
      tiles.push(kpi(_t("Spam share of recent blocks"), fmtPct(rc.spam_pct), foot));
    }
    if (tm) tiles.push(kpi(_t("Biggest spam month"), fmtBytes(tm.spam_bytes), _t("{month} · {n} blocks", { month: tm.month, n: fmtNum(tm.blocks) })));
    if (tb) {
      const t = kpi(_t("Biggest spam block"), fmtBytes(tb.spam_bytes),
        _t("block {h} · {pct} spam", { h: fmtNum(tb.height), pct: fmtPct(tb.spam_pct, 0) }));
      tiles.push(h("a", { class: "kpi-link", href: `#/block/${tb.height}` }, t));
    }
    if (!tiles.length) return null;
    return h("div", { class: "card trends", style: { marginTop: "16px" } },
      h("h2", { text: _t("Trends and records") }),
      h("p", { class: "sub", text: tr.complete ? _t("From the whole chain") : _t("From the blocks scanned so far — records can still change") }),
      h("div", { class: "grid grid-4 period-kpis" }, tiles));
  }
  function utxoFoot(u) {
    if (!u.full_done) return _t("Exact figure available after the full history scan");
    let s2 = _t("≈ {size} of chainstate", { size: fmtBytes(u.disk_estimate) });
    if (u.share_of_entries_pct != null) s2 += " · " + _t("{pct} of all UTXOs", { pct: fmtPct(u.share_of_entries_pct) });
    return s2;
  }
  function kpi(label, value, foot) {
    const parts = String(value).split(" ");
    return h("div", { class: "card kpi" },
      h("div", { class: "kpi-label" }, label),
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
    if (!blocks.length) wrap.append(h("div", { class: "muted", text: _t("No blocks yet.") }));
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
      wrap.append(h("a", { class: "blockcard", href: `#/block/${b.height}`, title: _t("Block {h}", { h: fmtNum(b.height) }) },
        h("div", { class: "top" }, h("div", { class: "h", text: fmtNum(b.height) }), h("div", { class: "t", text: timeAgo(b.time) })),
        h("div", { class: "zone" }, fill),
        h("div", { class: "bottom" }, h("div", { class: "pct" }, fmtPct(b.spam_pct, 0), h("small", { text: _t("spam · {size}", { size: fmtBytes(b.size, 2) }) })))));
    }
    return wrap;
  }

  function coverageText(cov) {
    if (!cov || cov.complete || !cov.ranges || !cov.ranges.length) return null;
    const parts = cov.ranges.map(([a, b]) => (a === b ? fmtNum(a) : `${fmtNum(a)}–${fmtNum(b)}`));
    const list = parts.length > 1 ? parts.slice(0, -1).join(", ") + _t(" and ") + parts[parts.length - 1] : parts[0];
    const several = cov.ranges.length > 1 || cov.ranges[0][0] !== cov.ranges[0][1];
    return (several ? _t("Scanned so far: blocks {list}.", { list }) : _t("Scanned so far: block {list}.", { list })) +
      (cov.gap ? " " + _t("The full history scan continues from block {h}.", { h: fmtNum(cov.gap.from) }) : "");
  }
  function goToBlock(q) {
    q = String(q || "").trim();
    if (/^\d{1,9}$/.test(q) || /^[0-9a-fA-F]{64}$/.test(q)) { location.hash = `#/block/${q.toLowerCase()}`; return true; }
    return false;
  }

  async function viewBlocks() {
    const chartBox = h("div", { class: "chart" });
    const seg = h("div", { class: "seg", role: "group", "aria-label": _t("Range") });
    const ranges = [["144", _t("1 day")], ["1008", _t("1 week")], ["4320", _t("1 month")], ["all", _t("All scanned")]];
    for (const [v, l] of ranges) {
      seg.append(h("button", { type: "button", "aria-pressed": String(S.range === v), text: l,
        onclick: () => { S.range = v; viewBlocks(); } }));
    }
    const chartCard = h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("div", null, h("h2", { text: _t("Spam per block") }), h("p", { class: "sub", text: _t("Block bytes by type. Click a bar to open the block.") })), seg),
      legend(["monetary", ...CARRIERS]), chartBox);
    const tableBox = h("div");
    const tableCard = h("div", { class: "card" },
      h("div", { class: "card-head" }, h("div", null, h("h2", { text: _t("Analysed blocks") }), h("p", { class: "sub", text: _t("Newest first") }))),
      tableBox);
    const search = h("input", { type: "text", class: "find-input", placeholder: _t("Block height or hash"), "aria-label": _t("Block height or hash"),
      autocomplete: "off", spellcheck: "false" });
    const findMsg = h("span", { class: "find-msg", role: "status" });
    const find = () => { findMsg.textContent = goToBlock(search.value) ? "" : _t("Enter a block height or a 64-character block hash."); };
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") find(); });
    const cov = coverageText(S.summary && S.summary.coverage);
    const findCard = h("div", { class: "card find-card" },
      h("div", { class: "find-row" },
        h("div", null, h("h2", { text: _t("Find a block") }),
          h("p", { class: "sub", text: _t("Look at any block by its height or hash — also blocks the scan has not reached yet.") })),
        h("div", { class: "find-form" }, search, h("button", { class: "btn btn-primary", type: "button", text: _t("Show block"), onclick: find }))),
      findMsg,
      cov ? notice("info", cov, null, "in-card") : null);
    viewShell(findCard, h("div", { style: { height: "16px" } }), blockStrip(), h("div", { style: { height: "16px" } }), chartCard,
      h("div", { style: { height: "16px" } }), tableCard);

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
            title: b.from === b.to ? _t("Block {h}", { h: fmtNum(b.from) }) : _t("Blocks {a}–{b} (average)", { a: fmtNum(b.from), b: fmtNum(b.to) }),
            values: vals, extra: [{ label: _t("Spam share"), value: fmtPct(b.size ? (spam / b.size) * 100 : 0) }],
          };
        });
        stackedBars(chartBox, rows, ["monetary", ...CARRIERS], {
          aria: _t("Bytes per block by type"), onClick: (row) => { location.hash = `#/block/${row.height}`; },
        });
        withTableToggle(chartCard, () => dataTable([_t("Blocks"), SHORT.monetary, ...CARRIERS.map((k) => SHORT[k])],
          rows.map((r) => [r.title, fmtBytes(r.values.monetary), ...CARRIERS.map((k) => fmtBytes(r.values[k]))])));
      } catch (e) { chartBox.append(h("div", { class: "chart-empty", text: String(e.message) })); }
    }
    S.tableBlocks = await api("/api/blocks?limit=50").catch(() => []);
    renderBlockTable(tableBox);
  }
  function renderBlockTable(box) {
    clear(box);
    const rows = S.tableBlocks;
    if (!rows.length) { box.append(h("div", { class: "chart-empty", text: _t("No blocks analysed yet") })); return; }
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
      h("thead", null, h("tr", null, [_t("Height"), _t("Time (UTC)"), _t("Size"), "Spam", ...CARRIERS.map((k) => SHORT[k]), "Monetary Node", _t("Txs with spam")]
        .map((x, i) => h("th", { class: i ? null : "l", text: x })))), tbody)));
    const last = rows[rows.length - 1];
    if (last && last.height > (S.summary ? S.summary.lowest : 0)) {
      box.append(h("button", { class: "btn", type: "button", style: { marginTop: "12px" }, text: _t("Load more"),
        onclick: async () => {
          const more = await api(`/api/blocks?limit=50&before=${last.height}`).catch(() => []);
          S.tableBlocks = S.tableBlocks.concat(more);
          renderBlockTable(box);
        } }));
    }
  }

  function blockEta(height) {
    const st = S.status || {}, cov = S.summary && S.summary.coverage;
    if (!cov || !cov.gap || height < cov.gap.from || (cov.gap.to != null && height > cov.gap.to)) return "";
    // Blocks get bigger towards the tip, so a block count divided by the current rate is far too
    // optimistic; the scan's own byte-based ETA for the whole history is an honest upper bound.
    const eta = st.phase === "full" && st.total_bytes_estimate ? st.eta_seconds : null;
    if (!eta) return " " + _t("The full history scan will get there.");
    return " " + _t("At the current speed the full history scan gets there within about {t}.", { t: fmtDur(eta) });
  }
  async function openBlock(key) {
    let b = null;
    try { b = await api(`/api/block/${key}`); } catch (e) { b = null; }
    const dlg = $("#dialog"), body = clear($("#dialog-body"));
    const close = h("button", { class: "icon-btn", type: "button", "aria-label": _t("Close"), text: "✕", onclick: () => { dlg.close(); } });
    const isHeight = /^\d+$/.test(String(key));
    const title = _t("Block {h}", { h: isHeight ? fmtNum(parseInt(key, 10)) : String(key).slice(0, 12) + "…" });
    if (b) renderBlockDetail(body, b, close);
    else {
      const msg = h("p", { class: "find-msg", role: "status" });
      const btn = h("button", { class: "btn btn-primary", type: "button", text: _t("Analyse this block now") });
      btn.addEventListener("click", async () => {
        btn.disabled = true; msg.textContent = _t("Fetching the block from your node and analysing it…");
        try {
          const res = await post("/api/lookup", { q: String(key) });
          // the dialog may have been closed or reused for another block or the share card meanwhile
          if (!dlg.open || !body.contains(btn)) return;
          renderBlockDetail(clear(body), res, close);
        } catch (e) { msg.textContent = _ts(e.message); btn.disabled = false; }
      });
      body.append(h("div", { class: "dlg" }, h("div", { class: "dlg-head" }, h("h2", { text: title }), close),
        h("p", { class: "muted", text: _t("This block has not been scanned yet.") + (isHeight ? blockEta(parseInt(key, 10)) : "") }),
        h("p", { class: "muted", text: _t("You can analyse it right now: the app reads this one block from your node (read-only) and shows its exact figures.") }),
        h("div", { class: "hero-actions" }, btn), msg));
    }
    if (!dlg.open) dlg.showModal();
    dlg.addEventListener("close", () => { if (location.hash.startsWith("#/block/")) history.replaceState(null, "", "#/blocks"); }, { once: true });
  }
  function renderBlockDetail(body, b, close) {
      const items = CARRIERS.map((k) => ({ key: k, value: b[k] || 0 }));
      const dn = h("div", { class: "chart" });
      donut(dn, [{ key: "monetary", value: b.size - b.spam_bytes }, ...items], [fmtPct(b.spam_pct), _t("spam")]);
      body.append(h("div", { class: "dlg" },
        h("div", { class: "dlg-head" }, h("h2", { text: _t("Block {h}", { h: fmtNum(b.height) }) }), close),
        b.on_demand ? notice("info", _t("Analysed on demand — the figures are exact. The full history scan will add it to the totals when it gets there."), "✓", "in-card") : null,
        h("div", { class: "donut-wrap" }, dn, h("div", { class: "donut-legend" },
          [{ key: "monetary", value: b.size - b.spam_bytes }, ...items].map((it) => h("div", { class: "row" },
            h("i", { class: `swatch b-${it.key}` }), h("span", { text: LABEL[it.key] }), h("span", { class: "v", text: fmtBytes(it.value) }),
            h("span", { class: "p", text: fmtPct(b.size ? (it.value / b.size) * 100 : 0) }))))),
        h("dl", { class: "kv", style: { marginTop: "18px" } },
          h("dt", { text: _t("Hash") }), h("dd", { class: "num", text: b.hash }),
          h("dt", { text: _t("Time") }), h("dd", { text: fmtDate(b.time) }),
          h("dt", { text: _t("Size / weight") }), h("dd", { text: `${fmtBytes(b.size, 3)} · ${fmtNum(b.weight)} WU` }),
          h("dt", { text: _t("Monetary Node stores") }), h("dd", { text: `${fmtBytes(b.stored, 3)} (${b.size - b.stored >= 0 ? _t("{size} less", { size: fmtBytes(b.size - b.stored) }) : _t("{size} more — format overhead", { size: fmtBytes(b.stored - b.size) })})` }),
          h("dt", { text: _t("Transactions") }), h("dd", { text: _t("{n} total · {whole} kept whole · {mod} modified · {stripped} reduced to a txid", { n: fmtNum(b.tx_count), whole: fmtNum(b.whole), mod: fmtNum(b.modified), stripped: fmtNum(b.stripped) }) }),
          h("dt", { text: _t("Outputs") }), h("dd", { text: _t("{n} data outputs dropped (filter entry kept) · {dust} P2TR dust", { n: fmtNum(b.filter_entries), dust: fmtNum(b.dust_outputs) }) }),
          b.utxo_done ? h("dt", { text: _t("Spam UTXO change") }) : null,
          b.utxo_done ? h("dd", { text: _t("+{a} created · −{s} spent", { a: fmtNum(b.utxo_added), s: fmtNum(b.utxo_spent) }) }) : null,
          b.retained_protocol ? h("dt", { text: _t("Kept by carrier policy") }) : null,
          b.retained_protocol ? h("dd", { text: _t("{n} payment-protocol OP_RETURN(s)", { n: fmtNum(b.retained_protocol) }) }) : null)));
  }

  const monthOf = (t) => new Date(t * 1000).toISOString().slice(0, 7);
  function monthSeq(a, b) {
    const out = [];
    let [y, m] = a.split("-").map(Number);
    const [y2, m2] = b.split("-").map(Number);
    while (y < y2 || (y === y2 && m <= m2)) { out.push(`${y}-${String(m).padStart(2, "0")}`); m++; if (m > 12) { m = 1; y++; } }
    return out;
  }

  async function viewHistory() {
    const area = h("div", { class: "chart" }), share = h("div", { class: "chart" }), utxo = h("div", { class: "chart" });
    const note = h("div");
    const c1 = h("div", { class: "card" }, h("h2", { text: _t("Spam per month") }),
      h("p", { class: "sub", text: _t("Spam by type, for each month of blocks scanned") }), legend(CARRIERS), area);
    const c2 = h("div", { class: "card" }, h("h2", { text: _t("Spam share of block data") }),
      h("p", { class: "sub", text: _t("Percentage of each month's block bytes that are spam") }), share);
    const c3 = h("div", { class: "card" }, h("h2", { text: _t("Spam entries in the UTXO set over time") }),
      h("p", { class: "sub", text: _t("Unspent spam and dust outputs, built up block by block during the full history scan") }), utxo);
    const rangeCard = periodCard();
    viewShell(note, c1, h("div", { style: { height: "16px" } }), rangeCard, h("div", { style: { height: "16px" } }),
      h("div", { class: "grid grid-2" }, c2, c3));
    loadPeriod();
    const data = await api("/api/history").catch(() => ({ months: [] }));
    const byMonth = new Map(data.months.map((mo) => [mo.month, mo]));
    const mainnet = S.status && S.status.network === "mainnet";
    const gap = data.coverage && data.coverage.gap;
    // Continuous month axis; months the scan has not reached are shown as a grey band, not as zero spam.
    let first = data.months.length ? data.months[0].month : null;
    const last = data.months.length ? data.months[data.months.length - 1].month : null;
    let pFrom = null, pTo = null;
    if (gap && last) {
      pFrom = gap.from_time ? monthOf(gap.from_time) : (gap.from === 0 && mainnet ? "2009-01" : first);
      pTo = gap.to_time ? monthOf(gap.to_time) : last;
      if (pFrom < first) first = pFrom;
    }
    const seq = first ? monthSeq(first, last) : [];
    let pending = null;
    if (pFrom) {
      const i0 = seq.indexOf(pFrom), i1 = seq.indexOf(pTo);
      pending = [Math.max(0, i0), i1 < 0 ? seq.length - 1 : i1];
    }
    const inGap = (mo) => pFrom && mo >= pFrom && mo <= pTo;
    if (pFrom) {
      note.append(notice("warn", _t("The full history scan has not reached {period} yet. That period is greyed out below; " +
        "its spam appears as the scan proceeds. The overview shows an estimate for the whole chain in the meantime.",
        { period: pFrom === pTo ? pFrom : `${pFrom} – ${pTo}` }), null, "page-top"));
    }
    const rows = seq.map((mk) => {
      const mo = byMonth.get(mk);
      const vals = {}; CARRIERS.forEach((k) => { vals[k] = mo ? mo[k] || 0 : 0; });
      return { key: mk, label: mk, values: vals,
        title: mo ? `${mk} · ` + _t("blocks {a}–{b}", { a: fmtNum(mo.from), b: fmtNum(mo.to) }) + (inGap(mk) ? " " + _t("(partly scanned)") : "")
          : `${mk} · ` + _t("not scanned yet"),
        extra: mo ? [{ label: _t("Block data"), value: fmtBytes(mo.size) }] : [] };
    });
    stackedArea(area, rows, CARRIERS, { milestones: mainnet ? MILESTONES : null, aria: _t("Spam bytes per month by carrier"), pending });
    withTableToggle(c1, () => dataTable([_t("Month"), _t("Blocks"), _t("Block data"), ...CARRIERS.map((k) => SHORT[k])],
      data.months.map((mo) => [mo.month, fmtNum(mo.blocks), fmtBytes(mo.size), ...CARRIERS.map((k) => fmtBytes(mo[k]))])));
    const shareRows = seq.map((mk) => {
      const mo = byMonth.get(mk);
      return { label: mk, title: mk, value: mo && mo.size ? (CARRIERS.reduce((a, k) => a + (mo[k] || 0), 0) / mo.size) * 100 : (mo ? 0 : null) };
    });
    lineChart(share, shareRows, "spam", { yFmt: (v) => fmtPct(v), axisFmt: (v) => fixed(v, 0) + " %", seriesLabel: _t("Spam share"), pending });
    const full = data.months.filter((mo) => mo.utxo_count !== 0 || mo.utxo_bytes !== 0);
    lineChart(utxo, seq.map((mk) => {
      const mo = byMonth.get(mk);
      // the spam UTXO set is only known for the chronologically scanned part of the history
      return { label: mk, title: mk, value: mo && (!pFrom || mk <= pFrom) ? mo.utxo_count : null };
    }), "p2tr_dust", { yFmt: fmtNum, axisFmt: fmtCompact, seriesLabel: _t("Spam UTXO entries"), pending,
      empty: full.length ? undefined : _t("Available once the full history scan has progressed") });
  }

  // ------------------------------------------------------------------ custom range
  const ORDINALS_HEIGHT = 767430;
  function periodPresets() {
    const now = Math.floor(Date.now() / 1000), day = 86400;
    const yearStart = Date.UTC(new Date().getUTCFullYear(), 0, 1) / 1000;
    const p = [
      { id: "24h", label: _t("24 hours"), q: { start: now - day, end: now } },
      { id: "7d", label: _t("7 days"), q: { start: now - 7 * day, end: now } },
      { id: "30d", label: _t("30 days"), q: { start: now - 30 * day, end: now } },
      { id: "ytd", label: _t("This year"), q: { start: yearStart, end: now } },
    ];
    if (S.status && S.status.network === "mainnet") p.push({ id: "ord", label: _t("Since Ordinals"), q: { from: ORDINALS_HEIGHT, to: 1e9 } });
    p.push({ id: "all", label: _t("All time"), q: { from: 0, to: 1e9 } });
    return p;
  }
  function periodCard() {
    if (!S.period) S.period = { id: "30d" };
    const seg = h("div", { class: "seg seg-wrap", role: "group", "aria-label": _t("Range shortcuts") });
    for (const p of periodPresets()) {
      seg.append(h("button", { type: "button", "aria-pressed": String(S.period.id === p.id), text: p.label,
        onclick: () => { S.period = { id: p.id }; refreshPeriodButtons(); loadPeriod(); } }));
    }
    const year = h("select", { "aria-label": _t("Year") }, h("option", { value: "", text: _t("Year…") }));
    for (let y = new Date().getUTCFullYear(); y >= 2009; y--) year.append(h("option", { value: String(y), text: String(y) }));
    if (isYearPeriod(S.period.id)) year.value = S.period.id.slice(1);
    year.addEventListener("change", () => {
      if (!year.value) return;
      const y = parseInt(year.value, 10);
      S.period = { id: "y" + y, q: { start: Date.UTC(y, 0, 1) / 1000, end: Date.UTC(y + 1, 0, 1) / 1000 - 1 } };
      refreshPeriodButtons(); loadPeriod();
    });
    const mode = h("select", { "aria-label": _t("Range type") }, h("option", { value: "date", text: _t("Dates") }), h("option", { value: "height", text: _t("Block heights") }));
    const a = h("input", { type: "date", "aria-label": _t("From") }), b = h("input", { type: "date", "aria-label": _t("To") });
    mode.addEventListener("change", () => {
      const t = mode.value === "date" ? "date" : "number";
      a.type = b.type = t; a.value = b.value = "";
      a.placeholder = t === "number" ? _t("From height") : ""; b.placeholder = t === "number" ? _t("To height") : "";
      if (t === "number") { a.min = b.min = "0"; a.inputMode = b.inputMode = "numeric"; }
    });
    const msg = h("span", { class: "find-msg", role: "status" });
    const apply = () => {
      msg.textContent = "";
      if (mode.value === "date") {
        const t0 = Date.parse(a.value + "T00:00:00Z") / 1000, t1 = Date.parse(b.value + "T23:59:59Z") / 1000;
        if (!isFinite(t0) || !isFinite(t1)) { msg.textContent = _t("Choose a start and an end date."); return; }
        S.period = { id: "custom", q: { start: Math.min(t0, t1), end: Math.max(t0, t1) } };
      } else {
        const x = parseInt(a.value, 10), y = parseInt(b.value, 10);
        if (!(x >= 0) || !(y >= 0)) { msg.textContent = _t("Enter two block heights."); return; }
        S.period = { id: "custom", q: { from: Math.min(x, y), to: Math.max(x, y) } };
      }
      year.value = ""; refreshPeriodButtons(); loadPeriod();
    };
    function refreshPeriodButtons() {
      for (const btn of seg.querySelectorAll("button")) btn.setAttribute("aria-pressed", "false");
      const i = periodPresets().findIndex((p) => p.id === S.period.id);
      if (i >= 0) seg.querySelectorAll("button")[i].setAttribute("aria-pressed", "true");
      if (!isYearPeriod(S.period.id)) year.value = "";
    }
    S.periodBox = h("div", { class: "period-result", "aria-live": "polite" });
    return h("div", { class: "card" },
      h("h2", { text: _t("Custom range") }),
      h("p", { class: "sub", text: _t("Spam statistics for any period — pick a shortcut or enter dates or block heights") }),
      h("div", { class: "period-controls" }, seg, year),
      h("div", { class: "period-controls period-inputs" }, mode, a, h("span", { class: "muted", text: _t("to") }), b,
        h("button", { class: "btn", type: "button", text: _t("Show"), onclick: apply }), msg),
      S.periodBox);
  }
  const isYearPeriod = (id) => /^y\d{4}$/.test(String(id));
  async function loadPeriod() {
    const box = S.periodBox;
    if (!box || !S.period) return;
    // shortcuts are relative to now: work out their range on every load, not once when clicked
    const preset = periodPresets().find((p) => p.id === S.period.id);
    const q = preset ? preset.q : S.period.q;
    if (!q) return;
    const qs = q.from != null ? `from=${q.from}&to=${q.to}` : `start=${Math.floor(q.start)}&end=${Math.floor(q.end)}`;
    const seq = (S.periodSeq = (S.periodSeq || 0) + 1);
    let r, err = null;
    try { r = await api(`/api/period?${qs}`); } catch (e) { err = e; }
    if (seq !== S.periodSeq || box !== S.periodBox) return;      // a newer choice was made meanwhile
    if (err) { clear(box).append(h("p", { class: "muted", text: _ts(err.message) })); return; }
    clear(box);
    if (r.empty) { box.append(h("p", { class: "muted", text: _t("No scanned blocks in this range yet.") + (r.blocks_total ? " " + _t("The full history scan will get there.") : "") })); return; }
    const span = _t("Blocks {a}–{b}", { a: fmtNum(r.first), b: fmtNum(r.last) }) + ` · ${fmtDate(r.first_time).slice(0, 10)} – ${fmtDate(r.last_time).slice(0, 10)}`;
    box.append(h("p", { class: "sub", text: span }));
    if (r.coverage_pct < 99.95) {
      box.append(notice("warn", _t("About {pct} of this range is scanned so far — the figures cover only the scanned blocks.", { pct: fmtPct(Math.min(99.9, r.coverage_pct)) }), null, "in-card"));
    }
    box.append(h("div", { class: "grid grid-4 period-kpis" },
      kpi("Spam", fmtBytes(r.spam_bytes), _t("{pct} of {size} block data", { pct: fmtPct(r.spam_pct), size: fmtBytes(r.totals.size) })),
      kpi(_t("Monetary Node saves"), fmtBytes(Math.max(0, r.saved_bytes)), _t("{pct} less block storage", { pct: fmtPct(Math.max(0, r.saved_pct)) })),
      kpi(_t("Transactions with spam"), fmtPct(r.modified_tx_pct), _t("{n} of {total}", { n: fmtNum(r.totals.modified + r.totals.stripped), total: fmtNum(r.totals.tx_count) })),
      kpi(_t("Blocks"), fmtNum(r.blocks_scanned), r.coverage_pct < 99.95 ? _t("scanned of ≈ {n} in this range", { n: fmtNum(r.blocks_total) }) : _t("in this range"))));
    const items = CARRIERS.map((k) => ({ key: k, value: r.by_carrier[k] || 0 }));
    box.append(h("div", { class: "donut-legend period-legend" }, items.map((it) => h("div", { class: "row" },
      h("i", { class: `swatch b-${it.key}` }), h("span", { text: LABEL[it.key] }),
      h("span", { class: "v num", text: fmtBytes(it.value) }),
      h("span", { class: "p num", text: fmtPct(r.spam_bytes ? (it.value / r.spam_bytes) * 100 : 0) })))));
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
      bandBox.append(h("div", { class: "compare-row" }, h("div", { class: "lbl" }, h("span", { text: _ts(b.band) }), h("b", { text: fmtNum(b.count) })), track));
    }
    const nodeU = u.node_utxo;
    viewShell(
      !u.full_done ? notice("warn", _t("These figures are still being built: every spam output is tracked from the block that created it until it is spent, so the exact current number is known once the full history scan has reached the tip."), null, "page-top") : null,
      h("div", { class: "grid grid-4" },
        kpi(_t("Spam entries in the UTXO set"), fmtNum(u.count), u.complete ? _t("Exact, current") : _t("So far")),
        kpi(_t("Chainstate they occupy"), fmtBytes(u.disk_estimate), _t("{size} serialized × 1.35 database overhead", { size: fmtBytes(u.bytes) })),
        kpi(_t("Share of all UTXOs"), u.share_of_entries_pct != null ? fmtPct(u.share_of_entries_pct) : "–",
          nodeU ? _t("of {n} entries in your node's UTXO set", { n: fmtNum(nodeU.txouts) }) : _t("Waiting for the node's UTXO set size")),
        kpi(_t("Your node's UTXO set"), nodeU ? fmtBytes(nodeU.disk_size) : "–", nodeU ? _t("on disk · read {ago}", { ago: timeAgo(nodeU.time) }) : _t("Read once a day (read-only)"))),
      h("div", { class: "grid grid-2", style: { marginTop: "16px" } },
        h("div", { class: "card" }, h("h2", { text: _t("By type") }),
          h("p", { class: "sub", text: _t("A Monetary Node keeps these out of its UTXO database. Stamps outputs are removed from blocks too; P2TR dust stays in block storage, where it costs less, and is looked up there if it is ever spent.") }),
          kindBars),
        h("div", { class: "card" }, h("h2", { text: _t("How old they are") }),
          h("p", { class: "sub", text: _t("Spam outputs are rarely spent — most stay in the chainstate for years") }), bandBox)));
  }

  function viewAbout() {
    const st = S.status || {};
    const r = st.rules || {};
    const P = (t) => h("p", { text: t });
    viewShell(h("div", { class: "card prose" },
      h("h2", { text: _t("What this app shows") }),
      P(_t("BitcoinMonetaryView reads every block from your own Bitcoin Core or Knots node and applies the rules of the Monetary Node project to it. It shows how much of your node's storage and UTXO set is taken up by data that is not money — and what a Monetary Node would store instead.")),
      P(_t("It is strictly read-only: it only uses read-only RPC calls (a fixed whitelist enforced inside the app), never touches your node's files and keeps its own results in a separate database.")),
      h("h2", { text: _t("What is a Monetary Node?") }),
      P(_t("A Monetary Node is a Bitcoin node that validates every consensus rule and every block, and then drops the spam from its block storage: inscription witness data, oversized OP_RETURN outputs and Stamps-style fake-key outputs are removed after validation. No fork, no confiscation, nobody's permission required.")),
      P(_t("The blocks still verify against their headers and real proof-of-work: a block's merkle root is computed over txids, and the Monetary Node keeps the 32-byte txid of every transaction it changes. Removed outputs keep a small filter entry, so anything removed can still be spent and validated locally.")),
      h("h2", { text: _t("What counts as spam") }),
      h("ul", null,
        h("li", null, h("strong", { text: _t("Inscriptions: ") }), _t("data inside OP_FALSE OP_IF … OP_ENDIF or OP_TRUE OP_NOTIF … OP_ENDIF envelopes in taproot script-path and P2WSH witnesses.")),
        h("li", null, h("strong", { text: _t("OP_RETURN over 83 bytes: ") }), _t("oversized data carriers. Verified payment-protocol envelopes (Shielded Bitcoin) are kept when the carrier policy is on.")),
        h("li", null, h("strong", { text: _t("Stamps / fake keys: ") }), _t("bare multisig or P2PK outputs whose \"public keys\" are not points on the secp256k1 curve — data disguised as keys.")),
        h("li", null, h("strong", { text: _t("Oversized scriptSig: ") }), _t("input scripts over 1,650 bytes. Counted as spam, but the Monetary Node keeps them in block storage, so they save nothing.")),
        h("li", null, h("strong", { text: _t("Inscription-era P2TR dust: ") }), _t("taproot outputs under 1,000 sats since block 767,430. They stay in block storage but leave the UTXO database."))),
      h("h2", { text: _t("Honest limitations") }),
      h("ul", null,
        h("li", { text: _t("A stripped block cannot be served to a conventional node. Monetary Nodes can serve each other; who serves full blocks if they become a large share of the network is an open question.") }),
        h("li", { text: _t("Transactions whose outputs were removed can no longer have their signatures re-verified from the stripped store (they were fully validated once, when the block arrived).") }),
        h("li", { text: _t("The dust figures are a proxy: they count small taproot outputs from the inscription era, which includes some ordinary small payments.") }),
        h("li", { text: _t("Monetary Node is early-stage software. This app informs your decision; it does not make it for you.") })),
      h("h2", { text: _t("Spam vs. storage saved") }),
      P(_t("“Spam” counts only the data itself: inscription content, oversized OP_RETURN data, fake keys and oversized scriptSigs. “Storage saved” is the real difference in disk space, and it is usually larger: in a transaction that carries spam, a Monetary Node also drops the witness data (signatures already verified when the block arrived), and a transaction that is nothing but spam is reduced to its 32-byte txid. The Monetary Node's own bookkeeping is subtracted. Undo files and optional indexes are not counted on either side.")),
      P(_t("“Transactions with spam” is the share of transactions a Monetary Node changes: it removes their spam part, or keeps only the txid of a transaction that is nothing but spam. Upstream's results call them “modified or stripped”.")),
      h("h2", { text: _t("While the scan is running") }),
      P(_t("The full history scan reads every block in order, which takes hours (days in Eco mode). So that the first impression is not dominated by the early, almost spam-free years, the app first analyses the latest 1,000 blocks and then every 100th block of the rest. From that sample the overview shows an estimate for the whole chain (marked ≈) with its margin of error, next to the exact figure so far. The estimate is replaced by exact figures as the scan proceeds. History, Blocks, the UTXO set, the share card and the exports always show exact figures; parts of the history that are not scanned yet are marked as such.")),
      P(_t("Any block can be opened on the Blocks page by its height or hash. If the scan has not reached it yet, “Analyse this block now” reads just that block from your node and shows its exact figures.")),
      P(_t("Trends and records on the overview come from the blocks already scanned, without extra requests to your node. A comparison appears once 95 % of its blocks are scanned: the last 30 days (4,320 blocks), and the last 1,000 blocks against the same 1,000 blocks a year earlier.")),
      h("h2", { text: _t("Core, Knots and this app") }),
      P(_t("Knots' mempool filters stop relaying spam, but spam that is already in blocks is stored by every full node — Core and Knots alike. The numbers here apply to both.")),
      h("h2", { text: _t("Languages") }),
      P(_t("The dashboard is available in English, German, Spanish, French and Polish. It follows your browser's language, and the menu in the header switches it. The translations are new: if something reads wrong, please report it with the link at the bottom of the page.")),
      h("h2", { text: _t("Rules in use") }),
      h("dl", { class: "kv" },
        h("dt", { text: "Upstream" }), h("dd", null, h("a", { href: (st.upstream || {}).repo || "#", rel: "noopener noreferrer", target: "_blank", text: _t("sambitcoin/BitcoinMonetaryNode") }), ` @ ${(r.upstream_commit || "").slice(0, 12)}`),
        h("dt", { text: _t("Carrier policy") }), h("dd", { text: r.carrier_policy ? _t("on ({id}…)", { id: (r.policy_id || "").slice(0, 16) }) : _t("off") }),
        h("dt", { text: _t("P2TR dust from") }), h("dd", { text: r.dust_start_height != null ? _t("block {h}", { h: fmtNum(r.dust_start_height) }) : "–" }),
        h("dt", { text: _t("Units") }), h("dd", { text: _t("Decimal: 1 GB = 1,000,000,000 bytes") }))));
  }

  async function viewConnection() {
    const st = S.status || {};
    const node = st.node || {};
    const [settings, ct] = await Promise.all([api("/api/settings").catch(() => null), api("/api/connection-test").catch(() => null)]);
    S.settings = settings;
    const byName = {};
    (settings ? settings.settings : []).forEach((x) => { byName[x.name] = x; });

    const platformName = PLATFORM_NAMES[st.platform] || st.platform;
    const nodeCard = h("div", { class: "card" }, h("h2", { text: _t("Your node") }), h("p", { class: "sub", text: _t("Connection used for all read-only requests") }),
      st.platform && !st.managed ? notice("info", _t("Running on {p}: it connects this app to your Bitcoin node and protects this dashboard with its own login.", { p: platformName }), null, "in-card") : null,
      h("dl", { class: "kv" },
        h("dt", { text: _t("Address") }), h("dd", { text: node.url || "–" }),
        h("dt", { text: _t("Software") }), h("dd", { text: node.version || "–" }),
        h("dt", { text: _t("Network") }), h("dd", { text: st.network || "–" }),
        h("dt", { text: _t("Block height") }), h("dd", { text: fmtNum(node.tip) }),
        h("dt", { text: _t("Blocks fetched via") }), h("dd", { text: node.rest ? _t("REST (binary, faster)") : "RPC" }),
        h("dt", { text: _t("Pruned") }), h("dd", { text: node.pruned ? _t("yes, from block {h}", { h: fmtNum(node.prune_height) }) : _t("no") }),
        h("dt", { text: _t("App database") }), h("dd", { text: st.db_size ? fmtBytes(st.db_size) : "–" })));

    const testBox = h("div");
    const renderTest = (t) => {
      clear(testBox);
      if (t && t.result) {
        for (const c of t.result) {
          testBox.append(h("div", { class: "check" }, h("span", { class: `st ${c.status}`, text: c.status === "ok" ? "✓" : "!" }),
            h("div", null, h("div", null, h("strong", { text: _ts(c.name) + ": " }), _ts(c.message)), c.hint ? h("div", { class: "hint", text: _ts(c.hint) }) : null)));
        }
        testBox.append(h("p", { class: "sub", style: { marginTop: "8px" }, text: _t("Tested {ago}", { ago: timeAgo(t.time) }) }));
      } else testBox.append(h("p", { class: "sub", text: t && t.running ? _t("Running…") : _t("Not run yet.") }));
    };
    renderTest(ct);
    const testBtn = h("button", { class: "btn", type: "button", text: _t("Run connection test") });
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
      } catch (e) { testBox.append(h("p", { class: "sub", text: _ts(e.message) })); }
      testBtn.disabled = false;
    });
    const testCard = h("div", { class: "card" }, h("div", { class: "card-head" },
      h("div", null, h("h2", { text: _t("Connection test") }), h("p", { class: "sub", text: _t("Checks reachability, credentials, sync state and speed") })), testBtn), testBox);

    // scan controls
    const ctrl = h("div");
    const prof = byName.speed_profile;
    const seg = h("div", { class: "seg", role: "group", "aria-label": _t("Speed profile") });
    for (const [v, l] of [["eco", "Eco"], ["balanced", _t("Balanced")], ["full", _t("Full speed")]]) {
      seg.append(h("button", { type: "button", "aria-pressed": String(prof && prof.value === v), disabled: !(prof && prof.editable), text: l,
        onclick: () => saveSetting({ speed_profile: v }) }));
    }
    const win = byName.scan_window, tz = byName.timezone;
    const winInput = h("input", { type: "text", value: win ? win.value : "", placeholder: _t("e.g. 01:00-07:00 (empty = always)"), disabled: !(win && win.editable) });
    const tzInput = h("input", { type: "text", value: tz ? tz.value : "UTC", placeholder: _t("e.g. Europe/Berlin"), disabled: !(tz && tz.editable) });
    const saveWin = h("button", { class: "btn", type: "button", text: _t("Save"), disabled: !(win && win.editable),
      onclick: () => saveSetting({ scan_window: winInput.value.trim(), timezone: tzInput.value.trim() || "UTC" }) });
    const msg = h("p", { class: "sub", style: { margin: "8px 0 0" } });
    async function saveSetting(changes) {
      try { await post("/api/settings", { changes }); msg.textContent = _t("Saved."); setTimeout(viewConnection, 300); }
      catch (e) { msg.textContent = _t("Not saved: {err}", { err: _ts(e.message) }); }
    }
    const pinnedNote = (x) => x && !x.editable ? h("span", { class: "src", text: st.managed ? _t("managed") : st.platform && x.source === "env" ? _t("set by {p}", { p: platformName }) : _t("set via {src}", { src: _t(x.source) }) }) : null;
    ctrl.append(
      h("div", { class: "form-row" }, h("label", null, _t("Speed profile"), pinnedNote(prof)), seg,
        h("div", { class: "help", text: _t("Eco is gentlest on your node (default). Full speed uses parallel requests. The scanner also backs off automatically when your node responds slowly.") })),
      h("div", { class: "form-row" }, h("label", null, _t("Scan window"), pinnedNote(win)), h("div", { class: "inline-fields" }, winInput, tzInput, saveWin),
        h("div", { class: "help", text: _t("Optional: only scan history during these hours. New blocks are always analysed.") })),
      h("div", { class: "form-row" }, h("label", { text: _t("Scanning") }), h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
        st.can_pause ? h("button", { class: "btn", type: "button", text: st.paused ? _t("Resume") : _t("Pause"), onclick: togglePause }) : null,
        st.can_rescan ? h("button", { class: "btn btn-danger", type: "button", text: _t("Rescan from scratch"), onclick: async () => {
          if (!confirm(_t("Delete this app's results and scan the whole chain again? Your node is not affected."))) return;
          await post("/api/control", { action: "rescan" }).catch((e) => alert(_ts(e.message)));
        } }) : null)),
      msg);
    const ctrlCard = h("div", { class: "card" }, h("h2", { text: _t("Scan settings") }),
      h("p", { class: "sub", text: st.managed ? _t("Managed by {p} — change these in the service's Actions.", { p: platformName }) : _t("These only affect this app.") }), ctrl);

    // all settings (read-only list)
    const setTable = settings ? dataTable([_t("Setting"), _t("Value"), _t("Source")], settings.settings.map((x) => [x.name,
      Array.isArray(x.value) ? x.value.join(", ") || "—" : x.value === "" || x.value == null ? "—" : String(x.value), _t(x.source)])) : h("p", { text: "–" });
    const allCard = h("div", { class: "card" }, h("h2", { text: _t("All settings") }),
      h("p", { class: "sub", text: _t("Set them via command line, environment variables (BMV_…) or settings.json in the data directory. Secrets are never shown.") }), setTable);

    const act = h("ul", { class: "activity" }, (st.activity || []).map((a) => h("li", null, h("time", { text: clock(a.time) }), h("span", { class: a.level, text: _ts(a.message) }))));
    const actCard = h("div", { class: "card" }, h("h2", { text: _t("Activity") }), h("p", { class: "sub", text: _t("What the scanner has been doing") }), act);

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
    /** Text left of the donut: shrink the font until it fits (translations can be longer). */
    const fit = (text, w, size, px, py, maxW = 670) => {      // the donut starts at x = 760
      let sz = size;
      do { x.font = font(w, sz); } while (x.measureText(text).width > maxW && --sz > 10);
      x.fillText(text, px, py);
    };
    x.fillStyle = "#f7931a"; x.font = font(700, 26); x.fillText("Monetary View", 64, 76);
    x.fillStyle = muted; fit(_t("My Bitcoin node, analysed with the Monetary Node rules"), 500, 20, 64, 108);
    x.fillStyle = fg; x.font = font(800, 76); x.fillText(fmtBytes(sum.spam_bytes), 64, 230);
    x.fillStyle = fg2; fit(_t("of spam in my node's blocks ({pct})", { pct: fmtPct(sum.spam_pct) }), 600, 30, 64, 280);
    const stats = [
      [_t("A Monetary Node saves"), sum.saved_bytes > 0 ? `${fmtBytes(sum.saved_bytes)} (${fmtPct(sum.saved_pct)})` : "—"],
      [_t("Spam UTXO entries"), sum.utxo.count ? fmtNum(sum.utxo.count) : "—"],
      [_t("Transactions with spam"), fmtPct(sum.modified_tx_pct)],
    ];
    stats.forEach(([l, v], i) => {
      const y = 350 + i * 78;
      x.fillStyle = muted; fit(l, 500, 20, 64, y);
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
    x.fillStyle = muted; x.font = font(500, 18); x.fillText(_t("of block data"), cx, cy + 36);
    x.textAlign = "left";
    let lx = 760;
    items.forEach(([k], i) => {
      const y = 538 + Math.floor(i / 2) * 30, xx = lx + (i % 2) * 200;
      x.fillStyle = col[k]; x.fillRect(xx, y - 13, 14, 14);
      x.fillStyle = fg2; x.font = font(500, 17); x.fillText(SHORT[k], xx + 22, y);
    });
    const st = S.status || {};
    const scope = sum.meta && sum.meta.full_done === "1" ? _t("Full chain · {n} blocks", { n: fmtNum(sum.blocks_scanned) }) : _t("{n} blocks scanned so far", { n: fmtNum(sum.blocks_scanned) });
    x.fillStyle = muted; x.font = font(500, 17);
    x.fillText(`${st.network || ""} · ${scope}`, 64, H - 40);
    x.fillText(_t("Rules: {url}", { url: "github.com/sambitcoin/BitcoinMonetaryNode" }), 64, H - 16);
    return c;
  }
  function shareText(sum) {
    const full = sum.meta && sum.meta.full_done === "1";
    const v = { spam: fmtBytes(sum.spam_bytes), pct: fmtPct(sum.spam_pct), saved: fmtBytes(sum.saved_bytes), spct: fmtPct(sum.saved_pct), utxo: fmtNum(sum.utxo.count) };
    return [full ? _t("My Bitcoin node stores {spam} of spam — {pct} of its block data.", v)
      : _t("My Bitcoin node stores {spam} of spam — {pct} of its block data (in the blocks scanned so far).", v),
      sum.saved_bytes > 0 ? (sum.utxo.count ? _t("A Monetary Node would store {saved} ({spct}) less and keep {utxo} spam entries out of its UTXO set.", v)
        : _t("A Monetary Node would store {saved} ({spct}) less.", v)) : "",
      _t("Same blocks, same proof-of-work, every consensus rule validated."),
      _t("Measured on my own node with BitcoinMonetaryView ({app}) using the Monetary Node rules ({rules}).",
        { app: "https://github.com/MarkSierra/BitcoinMonetaryView", rules: "https://github.com/sambitcoin/BitcoinMonetaryNode" })].filter(Boolean).join(" ");
  }
  function openShare() {
    const sum = S.summary;
    if (!sum || sum.empty) return;
    const dlg = $("#dialog"), body = clear($("#dialog-body"));
    const canvas = drawShareCard(sum);
    const img = h("img", { class: "share-preview", alt: _t("Summary card preview") });
    const dl = h("a", { class: "btn btn-primary", download: "my-node-spam.png", text: _t("Download image") });
    canvas.toBlob((blob) => { const u = URL.createObjectURL(blob); img.src = u; dl.href = u; });
    const ta = h("textarea", { class: "share-text", readonly: true });
    ta.value = shareText(sum);
    const copy = h("button", { class: "btn", type: "button", text: _t("Copy text") });
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(ta.value); copy.textContent = _t("Copied ✓"); }
      catch (e) { ta.select(); copy.textContent = _t("Press Ctrl+C"); }
    });
    body.append(h("div", { class: "dlg" },
      h("div", { class: "dlg-head" }, h("h2", { text: _t("Share my results") }),
        h("button", { class: "icon-btn", type: "button", "aria-label": _t("Close"), text: "✕", onclick: () => dlg.close() })),
      sum.meta && sum.meta.full_done !== "1" ? notice("warn", _t("The share card uses exact figures only. While the full history scan runs, they cover only the part of the chain scanned so far — the estimate on the overview is not included. For your complete result, share again once the scan is finished."), null, "share-note") : null,
      img, ta,
      h("p", { class: "sub", style: { marginTop: "10px" }, text: _t("Created on this device — nothing is uploaded. Contains chain statistics only, nothing that identifies your node.") }),
      h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } }, dl, copy)));
    dlg.showModal();
  }

  // ------------------------------------------------------------------ router & polling
  const VIEWS = { overview: viewOverview, blocks: viewBlocks, history: viewHistory, utxo: viewUtxo, about: viewAbout, connection: viewConnection };
  function route(force) {
    const hash = location.hash || "#/overview";
    const m = hash.match(/^#\/block\/(\d{1,9}|[0-9a-f]{64})$/);
    if (m) {
      if (S.view !== "blocks") { S.view = "blocks"; markNav(); viewBlocks(); }
      openBlock(m[1]);
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
    // narrow screens: the tabs are one row you can swipe; keep the current one in view
    const cur = $(".tabs a[aria-current=page]"), tabs = $(".tabs");
    if (cur && tabs.scrollWidth > tabs.clientWidth) {
      tabs.scrollLeft = Math.max(0, cur.offsetLeft - (tabs.clientWidth - cur.offsetWidth) / 2);
    }
  }
  async function togglePause() {
    const st = S.status;
    try { await post("/api/control", { action: st && st.paused ? "resume" : "pause" }); await pollStatus(); }
    catch (e) { alert(_ts(e.message)); }
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
      $("#phase-label").textContent = _t("Offline");
      $("#status-detail").textContent = _t("Cannot reach the BitcoinMonetaryView server.");
      $("#phase-pill").dataset.tone = "error";
    }
  }
  async function refreshData() {
    S.lastDataFetch = Date.now();
    const [sum, latest, trends] = await Promise.all([api("/api/summary").catch(() => null),
      api("/api/blocks?limit=8").catch(() => []), api("/api/trends").catch(() => null)]);
    // nothing changed (e.g. paused, or no new block): leave the page alone
    const sig = JSON.stringify([sum, latest, trends]);
    if (sig === S.dataSig && !S.redrawPending) return;
    S.dataSig = sig;
    S.summary = sum; S.latestBlocks = latest; S.trends = trends;
    redrawDataView();
  }
  // re-render data views only when no dialog is open and the user is not interacting with a form;
  // otherwise remember it, so the new data appears as soon as that is over
  function redrawDataView() {
    S.redrawPending = false;
    if (!["overview", "utxo"].includes(S.view)) return;
    const typing = document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    if ($("#dialog").open || typing) { S.redrawPending = true; return; }
    S.quiet = true;
    try { VIEWS[S.view](); } finally { S.quiet = false; }
  }

  /** Static texts in index.html: data-i18n translates the text, data-i18n-attr the named attributes. */
  function translatePage() {
    document.documentElement.lang = LANG;
    for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = _t(el.textContent.trim());
    for (const el of document.querySelectorAll("[data-i18n-attr]")) {
      for (const a of el.dataset.i18nAttr.split(" ")) if (el.hasAttribute(a)) el.setAttribute(a, _t(el.getAttribute(a)));
    }
    initLangMenu();
    if (I18N) {         // translations are new: invite corrections
      $("#footer-lang").append(" · ", h("a", { href: "https://github.com/MarkSierra/BitcoinMonetaryView/issues/new?template=translation.yml",
        rel: "noopener noreferrer", target: "_blank", text: _t("New translation — report a mistake") }));
    }
  }
  /** The language menu in the header: the current language's code; the list shows each name in its own language. */
  function initLangMenu() {
    const btn = $("#lang-toggle"), menu = $("#lang-menu");
    btn.textContent = LANG.toUpperCase();
    btn.setAttribute("aria-label", _t("Language: {name}", { name: LANGS[LANG].name }));
    btn.title = btn.getAttribute("aria-label");
    for (const [code, l] of Object.entries(LANGS)) {
      menu.append(h("button", { type: "button", role: "menuitemradio", lang: code, "aria-checked": String(code === LANG), text: l.name,
        onclick: () => {
          try { localStorage.setItem("bmv-lang", code); } catch (e) { /* storage blocked: only this page */ }
          if (code === LANG) close(); else location.reload();
        } }));
    }
    const items = () => [...menu.querySelectorAll("button")];
    function open() {
      menu.hidden = false; btn.setAttribute("aria-expanded", "true");
      (items().find((b) => b.getAttribute("aria-checked") === "true") || items()[0]).focus();
    }
    function close(focus) { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); if (focus) btn.focus(); }
    btn.addEventListener("click", () => (menu.hidden ? open() : close()));
    menu.addEventListener("keydown", (e) => {
      const list = items(), i = list.indexOf(document.activeElement);
      if (e.key === "Escape") { e.preventDefault(); close(true); }
      else if (e.key === "ArrowDown") { e.preventDefault(); list[(i + 1) % list.length].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
      else if (e.key === "Tab") close();
    });
    document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) close(); });
  }

  async function init() {
    translatePage();
    initTheme();
    $("#pause-btn").addEventListener("click", togglePause);
    $("#dialog").addEventListener("close", () => { if (S.redrawPending) setTimeout(redrawDataView, 0); });
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
