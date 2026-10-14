"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const f = (n, d = 2) => n == null || isNaN(n) ? "\u2014" : Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const f0 = n => f(n, 0), pc = n => n == null ? "\u2014" : (n >= 0 ? "+" : "") + n.toFixed(2) + "%", sg = n => n == null ? "" : n >= 0 ? "up" : "dn";
const store = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) || d } catch (e) { return d } }, set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)) } catch (e) { } } };
async function api(path, opt) { const r = await fetch(path, opt); if (!r.ok) { let m = r.status; try { m = (await r.json()).detail || m } catch (e) { } throw new Error(typeof m === "string" ? m : JSON.stringify(m)) } return r.json() }

const S = {
  cur: "NIFTY", expiry: null,
  scanSyms: store.get("scan1", ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"]),
  scan: {}, scanFilter: "all",
  d: null, cfg: null, tab: "signals",
  runId: null, msgs: [], pollT: null, sigT: null, market: "closed",
  capital: store.get("capital", 100000), riskPct: store.get("riskpct", 1),
  futActivity: null, advDecline: null,
  moIndex: store.get("moIndex", "NIFTY 50"), moFilter: "gainers", moData: null,
  chartRange: "1d"
};

/* ---------- markdown (escaped; tables, lists, bold) ---------- */
function md(src) {
  const L = String(src || "").replace(/\r/g, "").split("\n"), o = []; let i = 0;
  const inl = s => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  const isRow = l => /^\s*\|.*\|\s*$/.test(l), cells = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
  const blk = /^(#{1,4}\s|\s*[-*]\s|\s*\d+[.)]\s|\s*\|)/;
  while (i < L.length) {
    const l = L[i]; let m;
    if (isRow(l) && i + 1 < L.length && /^\s*\|[\s:|-]+\|\s*$/.test(L[i + 1])) { const h = cells(l), rows = []; i += 2; while (i < L.length && isRow(L[i])) rows.push(cells(L[i++])); o.push("<div class=tw><table><thead><tr>" + h.map(c => `<th>${inl(c)}</th>`).join("") + "</tr></thead><tbody>" + rows.map(r => "<tr>" + r.map(c => `<td>${inl(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table></div>"); continue }
    if (m = l.match(/^(#{1,4})\s+(.*)/)) { o.push(`<h3>${inl(m[2])}</h3>`); i++; continue }
    if (/^\s*[-*]\s+/.test(l)) { const it = []; while (i < L.length && /^\s*[-*]\s+/.test(L[i])) it.push(`<li>${inl(L[i++].replace(/^\s*[-*]\s+/, ""))}</li>`); o.push("<ul>" + it.join("") + "</ul>"); continue }
    if (/^\s*\d+[.)]\s+/.test(l)) { const it = []; while (i < L.length && /^\s*\d+[.)]\s+/.test(L[i])) it.push(`<li>${inl(L[i++].replace(/^\s*\d+[.)]\s+/, ""))}</li>`); o.push("<ol>" + it.join("") + "</ol>"); continue }
    if (!l.trim()) { i++; continue }
    const p = []; while (i < L.length && L[i].trim() && !blk.test(L[i])) p.push(inl(L[i++]));
    if (p.length) o.push("<p>" + p.join("<br>") + "</p>"); else { o.push(`<p>${inl(l)}</p>`); i++ }
  }
  return o.join("")
}

/* ---------- header + index strip ---------- */
function renderMkt(m) { $("mkt").innerHTML = `<span class="dot ${esc(m.state)}"></span><span>${esc(m.label)}</span><span>${esc(m.now_ist)}</span>` }
function renderStrip(d) {
  const c = r => r.error ? `<div><span>${esc(r.name)}</span><b>n/a</b></div>` :
    `<div title="${esc(r.source)} \u00b7 ${esc(r.as_of_label)}${r.stale ? " \u00b7 STALE" : ""}"><span>${esc(r.name)}${r.stale ? " \u00b7 stale" : ""}</span><b>${f(r.price)}</b><i class="${sg(r.change_pct)}">${pc(r.change_pct)}</i></div>`;
  $("strip").innerHTML = d.indices.map(c).join("") + d.macro.map(c).join("")
}
async function tickIndices() {
  try { const d = await api("/api/indices"); S.market = d.market.state; renderStrip(d); renderMkt(d.market) } catch (e) { $("strip").innerHTML = `<div><span>Rates unavailable</span><b>${esc(e.message).slice(0, 80)}</b></div>` }
  setTimeout(tickIndices, S.market === "closed" ? 30000 : 5000)
}

/* ---------- scanner with filters ---------- */
function applyScanFilter() {
  const syms = S.scanSyms;
  const filtered = syms.filter(s => {
    const r = S.scan[s];
    if (S.scanFilter === "all") return true;
    if (!r || r.error) return false;
    if (S.scanFilter === "bullish") return r.bias === "BULLISH";
    if (S.scanFilter === "bearish") return r.bias === "BEARISH";
    if (S.scanFilter === "gainers") return r.spot && r.changePct > 0;
    if (S.scanFilter === "losers") return r.spot && r.changePct < 0;
    return true;
  });
  return filtered;
}

function renderScan() {
  const filtered = applyScanFilter();
  $("scan").innerHTML = filtered.map(s => {
    const r = S.scan[s];
    const right = !r ? "<small>\u2026</small>" : r.error ? `<span class="err" title="${esc(r.error)}">n/a</span>` : `<span class="tag ${esc(r.bias)}">${esc(r.bias)}</span><small>${f0(r.confidence)}% \u00b7 PCR ${f(r.pcr)}</small>`;
    const sub = r && !r.error ? `<small>${f(r.spot)}</small>` : "";
    return `<button class="sc" data-s="${esc(s)}" aria-pressed="${S.cur === s}"><span><b>${esc(s)}</b>${sub}</span><span class="r">${right}</span></button>`
  }).join("") || '<p class="note" style="padding:8px 0">No matches for this filter.</p>';
}

$("scan-filters").onclick = e => {
  const b = e.target.closest(".sf");
  if (!b) return;
  S.scanFilter = b.dataset.filter;
  $("scan-filters").querySelectorAll(".sf").forEach(x => x.classList.toggle("active", x === b));
  renderScan();
};

async function loadScan() {
  try { (await api("/api/scan?symbols=" + encodeURIComponent(S.scanSyms.join(",")))).forEach(r => S.scan[r.symbol] = r) } catch (e) { }
  renderScan(); clearTimeout(loadScan.t); loadScan.t = setTimeout(loadScan, 30000)
}
$("scan").onclick = e => { const b = e.target.closest(".sc"); if (b) selectSym(b.dataset.s) };
$("scan-reload").onclick = () => loadScan();

/* ---------- search ---------- */
let sugT;
$("q").addEventListener("input", e => {
  clearTimeout(sugT); const v = e.target.value.trim(); if (v.length < 2) { $("sug").innerHTML = ""; return }
  sugT = setTimeout(async () => {
    try { const r = await api("/api/search?q=" + encodeURIComponent(v)); $("sug").innerHTML = r.map(x => `<option value="${esc(x.symbol)}">${esc(x.name)} \u00b7 ${esc(x.type)}</option>`).join("") } catch (_) { }
  }, 250)
});
$("q").addEventListener("keydown", e => {
  if (e.key !== "Enter") return; const v = e.target.value.trim().toUpperCase(); if (!v) return;
  if (!S.scanSyms.includes(v)) { S.scanSyms.push(v); store.set("scan1", S.scanSyms) } e.target.value = ""; selectSym(v); loadScan()
});

/* ---------- tabs ---------- */
function showTab(t) {
  S.tab = t; document.querySelectorAll(".tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === t));
  ["signals", "chain", "market", "desk"].forEach(x => $("p-" + x).hidden = x !== t);
  if (t === "market" && !S.moData) loadMarketOverview();
}
document.querySelector(".tabs").onclick = e => { const b = e.target.closest("button"); if (b) showTab(b.dataset.tab) };

/* ---------- signals ---------- */
const KIND_LABEL = { BUY_CE: "BUY CALL (CE)", BUY_PE: "BUY PUT (PE)", FUT_LONG: "FUTURES LONG", FUT_SHORT: "FUTURES SHORT", SELL_CE_SPREAD: "SELL CALL SPREAD", SELL_PE_SPREAD: "SELL PUT SPREAD", AVOID: "NO TRADE" };
function lotsFor(s) { const risk = s.kind && s.kind.includes("SPREAD") ? s.max_loss_rs : s.premium_risk_rs; if (!risk) return null; const budget = S.capital * S.riskPct / 100; return { lots: Math.floor(budget / risk), budget, risk } }
function planText(s, d) { return [`${d.symbol} ${KIND_LABEL[s.kind] || s.kind}`, s.instrument, `Entry ${s.entry}  SL ${s.stop}  Target ${s.target}  RR ${s.rr ?? "-"}`, `Exit: ${s.exit}`, "Educational research, not advice."].join("\n") }

function setupCard(s, i, d) {
  if (s.kind === "AVOID") return `<div class="setup"><div class="top"><span class="tag big AVOID">NO TRADE</span></div><p class="xt">${esc(s.exit)}</p><p class="why">${esc(s.rationale)}</p></div>`;
  const L = lotsFor(s), legs = s.legs ? `<ul class="legs">${s.legs.map(l => `<li><b>${esc(l.action)}</b> ${esc(l.instrument)} @ \u20b9${f(l.ltp)}</li>`).join("")}</ul>` : "";
  const lbl = s.kind.includes("SPREAD") ? "credit" : "entry";
  return `<div class="setup"><div class="top"><span class="tag big ${esc(s.kind)}">${esc(KIND_LABEL[s.kind] || s.kind)}</span><button class="btn ghost sm cp" data-i="${i}" type="button">Copy plan</button></div>
  <div class="ins">${esc(s.instrument)}</div>${legs}
  <div class="lv"><div><span>${lbl === "credit" ? "Net credit" : "Entry"}</span><b>\u20b9${f(s.entry)}</b></div><div class="sl"><span>Stop loss</span><b>\u20b9${f(s.stop)}</b></div><div class="tg"><span>Target</span><b>\u20b9${f(s.target)}</b></div></div>
  <div class="kv"><span>RR <b>${s.rr == null ? "\u2014" : f(s.rr)}</b></span><span>Lot <b>${f0(s.lot_size)}</b></span><span>Risk/lot <b>\u20b9${f0(s.kind.includes("SPREAD") ? s.max_loss_rs : s.premium_risk_rs)}</b></span>${s.delta != null ? `<span>\u0394 <b>${f(s.delta, 2)}</b></span>` : ""}${s.iv != null ? `<span>IV <b>${f(s.iv, 1)}%</b></span>` : ""}${s.max_profit_rs ? `<span>Max profit <b>\u20b9${f0(s.max_profit_rs)}</b></span>` : ""}</div>
  ${L ? (L.lots > 0 ? `<div class="kv"><span>For \u20b9${f0(S.capital)} at ${S.riskPct}% risk: <b>${L.lots} lot${L.lots === 1 ? "" : "s"}</b> (budget \u20b9${f0(L.budget)})</span></div>` : `<div class="kv"><span class="dn">1 lot risks \u20b9${f0(L.risk)}, more than your \u20b9${f0(L.budget)} budget. Skip it, or pick a cheaper strike or a spread.</span></div>`) : ""}
  <p class="xt"><b>Exit:</b> ${esc(s.exit)}</p><p class="why">${esc(s.rationale)}</p></div>`
}

function renderSignals() {
  const d = S.d, el = $("p-signals"); if (!d) { return }
  const sg_ = d.signal, m = sg_.metrics || {}, ch = d.chain, fut = d.futures, t = d.tech || {};
  const exps = (ch.expiries || []).map(x => `<option ${x === ch.expiry ? "selected" : ""}>${esc(x)}</option>`).join("");
  el.innerHTML = `<section class="pn"><div class="hd"><div><h1>${esc(d.symbol)} <span class="tag big ${esc(sg_.bias)}">${esc(sg_.bias)}</span></h1>
    <div class="chips"><span class="chip">${esc(d.exchange)} \u00b7 ${esc(d.kind)}</span><span class="chip">${esc(ch.source)}</span><span class="chip">Lot ${f0(ch.lot_size)}</span><span class="chip">DTE ${f(ch.dte_days, 1)} d</span>${d.market.state !== "open" ? `<span class="chip warn">${esc(d.market.label)}</span>` : ""}</div>
    <div class="conf" title="Rule-engine confidence"><i style="width:${Math.min(100, sg_.confidence || 0)}%"></i></div><div class="sub">Rule confidence ${f0(sg_.confidence)}% \u00b7 score ${sg_.score ?? "\u2014"}</div></div>
    <div style="text-align:right"><div class="px">\u20b9${f(ch.spot)}</div><div class="sub">Expiry <select id="exp" aria-label="Expiry">${exps}</select></div></div></div>
    ${(sg_.warnings || []).length ? `<ul class="warns">${sg_.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}</section>
  <section class="pn"><h2>Price Chart <span class="chart-rngs">${["1d", "5d", "1mo", "6mo", "1y"].map(r => `<button class="sf ${r === S.chartRange ? "active" : ""}" data-r="${r}">${r}</button>`).join("")}</span></h2>
    <div class="chartbox"><canvas id="pxchart" style="width:100%;height:160px;display:block"></canvas></div></section>
  <section class="pn"><h2>Trade Ideas (Rules, No LLM)</h2><div class="calc"><label>Capital \u20b9 <input id="cap" type="number" min="1000" step="1000" value="${S.capital}"></label>
    <label>Risk per trade % <input id="rsk" type="number" min="0.1" max="5" step="0.1" value="${S.riskPct}"></label></div><div class="setups" style="margin-top:14px">${(sg_.setups || []).map((s, i) => setupCard(s, i, d)).join("")}</div>
    <p class="hint">${esc(sg_.disclaimer || "")}</p></section>
  <section class="pn"><h2>Why This Bias</h2><div class="cols" style="margin-top:0"><ul class="rs">${(sg_.reasons || []).map(r => `<li>${esc(r)}</li>`).join("") || "<li>No reasons (empty chain)</li>"}</ul>
    <div class="grid4" style="margin:0;grid-template-columns:repeat(2,1fr)">
    <div><span>PCR (OI)</span><b>${f(m.pcr_oi)}</b></div><div><span>Max Pain</span><b>${f0(m.max_pain)}</b></div>
    <div><span>Call Wall</span><b class="dn">${f0(m.call_wall)}</b></div><div><span>Put Wall</span><b class="up">${f0(m.put_wall)}</b></div>
    <div><span>ATM IV</span><b>${f(m.atm_iv, 1)}%</b></div><div><span>Expected Move</span><b>${f(m.expected_move_pct)}%</b></div>
    <div><span>India VIX</span><b>${f(d.vix.price)}</b></div><div><span>RSI / ATR</span><b>${f(t.rsi, 0)} / ${f(t.atr, 0)}</b></div>
    <div><span>Futures</span><b>${fut ? f(fut.ltp) : "\u2014"}</b><span>${fut ? esc(fut.buildup) + " \u00b7 " + f(fut.basis_pct) + "%" : "no quote"}</span></div>
    <div><span>PE vs CE Writing</span><b>${f0(m.pe_writing)} / ${f0(m.ce_writing)}</b></div></div></div></section>`;
  $("exp").onchange = e => { S.expiry = e.target.value; loadSignal() };
  const keep = () => { S.capital = +$("cap").value || 100000; S.riskPct = +$("rsk").value || 1; store.set("capital", S.capital); store.set("riskpct", S.riskPct); renderSignals() };
  $("cap").onchange = keep; $("rsk").onchange = keep;
  el.querySelectorAll(".chart-rngs .sf").forEach(b => b.onclick = () => {
    S.chartRange = b.dataset.r;
    el.querySelectorAll(".chart-rngs .sf").forEach(x => x.classList.toggle("active", x === b));
    drawPriceChart(d.symbol, S.chartRange);
  });
  drawPriceChart(d.symbol, S.chartRange);
  el.querySelectorAll(".cp").forEach(b => b.onclick = () => { const s = sg_.setups[+b.dataset.i]; navigator.clipboard && navigator.clipboard.writeText(planText(s, d)).then(() => { b.textContent = "Copied"; setTimeout(() => b.textContent = "Copy plan", 1200) }) })
}

function renderChain() {
  const d = S.d, el = $("p-chain"); if (!d) return; const ch = d.chain, rows = ch.rows || [], m = d.signal.metrics || {};
  const mx = k => Math.max(1, ...rows.map(r => Math.max((r.ce || {})[k] || 0, (r.pe || {})[k] || 0))); const mo = mx("oi");
  const atm = m.atm_strike, cw = m.call_wall, pw = m.put_wall;
  const bar = (leg, side) => { const o = (leg || {}).oi || 0; return `<td class="bar ${side}"><i style="width:${Math.round(o / mo * 100)}%"></i><span>${f0(o)}</span></td>` };
  const chg = v => `<td class="${sg(v)}">${v == null ? "\u2014" : (v > 0 ? "+" : "") + f0(v)}</td>`;
  const oiChart = rows.length ? `<div class="oi-chart">${rows.map(r => {
    const c = (r.ce || {}).oi || 0, p = (r.pe || {}).oi || 0;
    const isAtm = r.strike === atm;
    return `<div class="oi-row ${isAtm ? "atm" : ""}" title="Strike ${f0(r.strike)}: CE OI ${f0(c)} / PE OI ${f0(p)}">
      <div class="oi-side ce"><i style="width:${Math.round(c / mo * 100)}%"></i></div>
      <div class="oi-k ${r.strike === cw ? "wall-c" : r.strike === pw ? "wall-p" : ""}">${f0(r.strike)}</div>
      <div class="oi-side pe"><i style="width:${Math.round(p / mo * 100)}%"></i></div></div>`;
  }).join("")}</div>
  <div class="oi-legend"><span><i class="dotc"></i> Call OI (resistance)</span><span><i class="dotp"></i> Put OI (support)</span><span class="note">ATM row highlighted</span></div>` : "";
  el.innerHTML = `<section class="pn"><h2>${esc(d.symbol)} Option Chain \u00b7 ${esc(ch.expiry)} <span class="sub">${esc(ch.source)} \u00b7 ${esc(ch.as_of || "")}</span></h2>
    ${oiChart}
    <div class="tw"><table class="oc"><thead><tr>
    <th>OI</th><th>\u0394 OI</th><th>IV</th><th>LTP</th><th style="text-align:center">Strike</th><th>LTP</th><th>IV</th><th>\u0394 OI</th><th>OI</th></tr></thead><tbody>
    ${rows.map(r => { const c = r.ce || {}, p = r.pe || {}; return `<tr class="${r.strike === atm ? "atm" : ""}">${bar(c, "ce")}${chg(c.oi_chg)}<td>${f(c.iv, 1)}</td><td>${f(c.ltp)}</td>
    <td class="k ${r.strike === cw || r.strike === pw ? "wall" : ""}" title="${r.strike === cw ? "Call OI wall (resistance)" : r.strike === pw ? "Put OI wall (support)" : ""}">${f0(r.strike)}</td>
    <td>${f(p.ltp)}</td><td>${f(p.iv, 1)}</td>${chg(p.oi_chg)}${bar(p, "pe")}</tr>` }).join("")}</tbody></table></div>
    <p class="hint">Amber outline = biggest call OI (resistance) and put OI (support). Blue lines mark the ATM strike. Red bars are call OI, green bars are put OI.</p></section>`
}

async function loadSignal() {
  clearTimeout(S.sigT); const sym = S.cur;
  try {
    const d = await api(`/api/signal/${encodeURIComponent(sym)}` + (S.expiry ? `?expiry=${encodeURIComponent(S.expiry)}` : "")); if (sym !== S.cur) return; S.d = d;
    renderSignals(); renderChain(); $("prov").innerHTML = provRows(d)
  }
  catch (e) {
    if (sym !== S.cur) return; $("p-signals").innerHTML = `<section class="pn"><h1>${esc(sym)}</h1><div class="errbox" style="margin-top:10px">${esc(e.message)}</div>
    <p class="hint">NSE/BSE block requests now and then. Wait a few seconds and press Refresh; if it persists, open /api/diag/${esc(sym)} to see which source fails.</p></section>`; $("p-chain").innerHTML = ""
  }
  S.sigT = setTimeout(loadSignal, S.market === "closed" ? 60000 : 20000)
}
const provRows = d => [["Option chain", d.chain.source, "ok " + (d.chain.expiry || "")], ["Futures", d.futures ? "NSE.com" : "n/a", d.futures ? "ok" : "missing"], ["India VIX", d.vix.source || "n/a", d.vix.price ? "ok" : "missing"], ["Daily bars", d.tech ? "Yahoo" : "n/a", d.tech ? "ok" : "missing"]]
  .map(r => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join("");
function selectSym(s) { S.cur = s.toUpperCase(); S.expiry = null; S.d = null; renderScan(); $("p-signals").innerHTML = `<section class="pn"><h1>${esc(S.cur)}</h1><p class="note">Loading option chain\u2026</p></section>`; loadSignal(); syncForm() }

/* ---------- Futures Activity (live NSE F&O buildup) ---------- */
const FUT_CAT = { long: "long buildup", short: "short buildup", covering: "short covering", unwinding: "long unwinding" };
const FUT_HINT = {
  long: "Price up + OI up: fresh longs entering. Trend-following strength.",
  short: "Price down + OI up: fresh shorts entering. Bearish pressure.",
  covering: "Price up + OI down: shorts exiting. Bounce / squeeze.",
  unwinding: "Price down + OI down: longs exiting. Profit booking / weakness."
};
let _ftTab = "long";
async function loadFutActivity() {
  try {
    const r = await api("/api/futures-activity");
    S.futActivity = r;
    renderFutActivity();
  } catch (e) { $("fut-data").innerHTML = `<p class="note">${esc(e.message).slice(0, 90)}</p>` }
  setTimeout(loadFutActivity, 60000);
}
function renderFutActivity() {
  const r = S.futActivity;
  if (!r) { $("fut-data").innerHTML = '<p class="note">Loading futures data\u2026</p>'; return }
  const items = (r.categories || {})[FUT_CAT[_ftTab]] || [];
  const count = (r.counts || {})[FUT_CAT[_ftTab]] || 0;
  let html = `<div class="fut-count">${count}</div><p class="hint" style="margin:-4px 0 8px">${FUT_HINT[_ftTab]}</p>`;
  if (r.note) html += `<p class="note" style="margin:-2px 0 8px">${esc(r.note)}</p>`;
  if (!items.length) {
    html += `<p class="note">No active contracts in this category right now.</p>`;
  } else {
    html += items.slice(0, 10).map(x => {
      let chg = "";
      if (x.change_pct != null) chg = `<span class="${x.change_pct >= 0 ? "up" : "dn"}">${pc(x.change_pct)}</span>`;
      else if (x.change != null) chg = `<span class="${x.change >= 0 ? "up" : "dn"}">${f(x.change)}</span>`;
      const oi = x.oi_chg != null ? `<small style="color:var(--mu)"> ${x.proxy ? "ΔOI" : "OI"} ${x.oi_chg >= 0 ? "+" : ""}${f0(x.oi_chg)}</small>` : "";
      const tag = x.proxy ? `<small style="color:var(--mu)"> ~</small>` : "";
      return `<div class="fut-row"><span><b>${esc(x.symbol)}</b>${tag}<small style="color:var(--mu)"> ${esc(x.expiry || "")}</small></span><span>${f(x.ltp)} ${chg}${oi}</span></div>`;
    }).join("");
  }
  $("fut-data").innerHTML = html;
}
document.querySelector(".fut-tabs").onclick = e => {
  const b = e.target.closest(".ft");
  if (!b) return;
  _ftTab = b.dataset.ft;
  document.querySelectorAll(".ft").forEach(x => x.classList.toggle("active", x === b));
  renderFutActivity();
};

/* ---------- Advance / Decline (multi-index breadth) ---------- */
async function loadAdvDec() {
  try {
    const r = await api("/api/advance-decline");
    S.advDecline = r;
    renderAdvDec();
  } catch (e) { }
  setTimeout(loadAdvDec, 120000);
}
function renderAdvDec() {
  const r = S.advDecline;
  const el = $("adv-dec-data");
  if (!r || !(r.indices || []).length) { el.innerHTML = '<p class="note">Loading breadth\u2026</p>'; return }
  el.innerHTML = (r.indices || []).slice(0, 8).map(x => {
    const tot = x.total || 1;
    const ap = Math.round(x.advance / tot * 100);
    return `<div class="ad-row">
      <div class="ad-head"><span>${esc(x.index)}</span><span><b class="up">${x.advance}</b> / <b class="dn">${x.decline}</b></span></div>
      <div class="ad-bar-wrap" style="height:8px;margin:4px 0 8px">
        <div class="ad-adv" style="width:${ap}%"></div><div class="ad-dec" style="width:${100 - ap}%"></div>
      </div></div>`;
  }).join("") + `<p class="hint">Advances / declines per index, live from NSE constituents.</p>`;
}

/* ---------- Market Overview (Strike-style screener) ---------- */
const MO_FILTERS = [["gainers", "Gainers"], ["losers", "Losers"], ["high52", "52w High"], ["low52", "52w Low"],
["open_high", "Open = High"], ["open_low", "Open = Low"], ["gap_up", "Gap Up"], ["gap_down", "Gap Down"], ["volume", "Volume Buzzers"]];
const MO_INDICES = ["NIFTY 50", "NIFTY BANK", "NIFTY IT", "NIFTY AUTO", "NIFTY PHARMA", "NIFTY FMCG", "NIFTY METAL", "NIFTY FIN SERVICE"];

async function loadMarketOverview() {
  const el = $("p-market");
  el.innerHTML = `<section class="pn"><div class="hd"><div><h1>Market Overview</h1>
    <div class="chips"><span class="chip">Live NSE constituents</span></div></div>
    <div><select id="mo-idx" aria-label="Index">${MO_INDICES.map(x => `<option ${x === S.moIndex ? "selected" : ""}>${esc(x)}</option>`).join("")}</select></div></div>
    <div class="mo-filters" id="mo-filters">${MO_FILTERS.map(([k, l]) => `<button class="sf ${k === S.moFilter ? "active" : ""}" data-f="${k}">${l} <span class="cnt" data-c="${k}"></span></button>`).join("")}</div>
    <div id="mo-body"><p class="note">Loading\u2026</p></div></section>`;
  $("mo-idx").onchange = e => { S.moIndex = e.target.value; store.set("moIndex", S.moIndex); loadMarketOverview() };
  $("mo-filters").onclick = e => {
    const b = e.target.closest(".sf"); if (!b) return;
    S.moFilter = b.dataset.f;
    $("mo-filters").querySelectorAll(".sf").forEach(x => x.classList.toggle("active", x === b));
    renderMarketTable();
  };
  try {
    S.moData = await api("/api/market-overview?index=" + encodeURIComponent(S.moIndex));
    MO_FILTERS.forEach(([k]) => { const c = document.querySelector(`[data-c="${k}"]`); if (c) c.textContent = `(${(S.moData.counts || {})[k] ?? 0})` });
    renderMarketTable();
  } catch (e) { $("mo-body").innerHTML = `<div class="errbox">${esc(e.message)}</div>` }
}
function renderMarketTable() {
  const d = S.moData; if (!d) return;
  const rows = (d.buckets || {})[S.moFilter] || [];
  const breadth = d.advance != null ? `<div class="ad-bar-wrap" style="height:10px;margin:10px 0 4px">
      <div class="ad-adv" style="width:${Math.round(d.advance / Math.max(1, d.advance + d.decline) * 100)}%">${d.advance}</div>
      <div class="ad-dec" style="width:${Math.round(d.decline / Math.max(1, d.advance + d.decline) * 100)}%">${d.decline}</div></div>
    <div class="ad-stats"><span>Advances <b class="up">${d.advance}</b></span><span>Unchanged <b>${d.unchanged}</b></span><span>Declines <b class="dn">${d.decline}</b></span></div>` : "";
  $("mo-body").innerHTML = breadth + (d.error ? `<div class="errbox" style="margin-top:10px">${esc(d.error)}</div>` : "") +
    `<div class="tw" style="margin-top:10px"><table class="mo"><thead><tr><th>Symbol</th><th>CMP</th><th>Change %</th><th>Volume</th><th>52w High</th><th>52w Low</th><th></th></tr></thead><tbody>
    ${rows.map(r => `<tr><td><b>${esc(r.symbol)}</b></td><td>${f(r.last)}</td><td class="${sg(r.change_pct)}">${pc(r.change_pct)}</td>
      <td>${f0(r.volume)}</td><td>${f0(r.year_high)}</td><td>${f0(r.year_low)}</td>
      <td><button class="btn ghost sm mo-add" data-s="${esc(r.symbol)}" type="button">Track</button></td></tr>`).join("") || `<tr><td colspan="7" class="note">No stocks match this filter right now.</td></tr>`}
    </tbody></table></div>
    <p class="hint">Click Track to add a stock to your scanner and pull its full F&amp;O signal.</p>`;
  $("mo-body").querySelectorAll(".mo-add").forEach(b => b.onclick = () => {
    const s = b.dataset.s;
    if (!S.scanSyms.includes(s)) { S.scanSyms.push(s); store.set("scan1", S.scanSyms) }
    selectSym(s); loadScan(); showTab("signals");
  });
}

/* ---------- Price chart (canvas, from /api/chart) ---------- */
async function drawPriceChart(sym, range) {
  const cv = $("pxchart"); if (!cv) return;
  try {
    const d = await api(`/api/chart/${encodeURIComponent(sym)}?range=${range}`);
    const box = cv.closest(".chartbox"), old = box && box.querySelector(".chart-note");
    if (old) old.remove();
    if (d.note && box) box.insertAdjacentHTML("afterbegin", `<p class="note chart-note">${esc(d.note)}</p>`);
    const ts = d.t || [], cs0 = d.c || [];
    const pts = ts.map((t, i) => ({ t, c: cs0[i] })).filter(p => p.c != null);
    if (pts.length < 2) { cv.closest(".chartbox").innerHTML = '<p class="note">No intraday chart data.</p>'; return }
    const ctx = cv.getContext("2d"), W = cv.width = cv.clientWidth * 2, H = cv.height = 160 * 2;
    ctx.scale(1, 1);
    const cs = pts.map(p => p.c), mn = Math.min(...cs), mx = Math.max(...cs), pad = (mx - mn) * 0.08 || 1;
    const X = i => i / (pts.length - 1) * (W - 20) + 10, Y = v => H - 14 - ((v - (mn - pad)) / ((mx + pad) - (mn - pad))) * (H - 28);
    const up = cs[cs.length - 1] >= cs[0];
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--ln").trim() || "#263241";
    ctx.lineWidth = 1;
    for (let g = 0; g <= 3; g++) { const y = 14 + g * (H - 28) / 3; ctx.beginPath(); ctx.moveTo(10, y); ctx.lineTo(W - 10, y); ctx.stroke() }
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, up ? "rgba(16,185,129,0.28)" : "rgba(239,68,68,0.28)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.beginPath(); ctx.moveTo(X(0), Y(cs[0]));
    pts.forEach((p, i) => ctx.lineTo(X(i), Y(p.c)));
    ctx.lineTo(X(pts.length - 1), H - 14); ctx.lineTo(X(0), H - 14); ctx.closePath();
    ctx.fillStyle = grad; ctx.fill();
    ctx.beginPath(); ctx.moveTo(X(0), Y(cs[0]));
    pts.forEach((p, i) => ctx.lineTo(X(i), Y(p.c)));
    ctx.strokeStyle = up ? "#10b981" : "#ef4444"; ctx.lineWidth = 2.4; ctx.stroke();
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--mu").trim() || "#7a8ba3";
    ctx.font = "20px Inter, sans-serif";
    ctx.fillText(f(mx), 12, 26); ctx.fillText(f(mn), 12, H - 20);
  } catch (e) { const b = cv.closest(".chartbox"); if (b) b.innerHTML = `<p class="note">Chart unavailable: ${esc(e.message).slice(0, 70)}</p>` }
}

/* ---------- AI desk ---------- */
function buildDesk() {
  const c = S.cfg, d = c.defaults;
  $("p-desk").innerHTML = `<section class="pn"><h2>Multi-Agent F&amp;O Desk</h2>
  <form class="f" id="rf"><label>Underlying<input id="r-sym" value="${esc(S.cur)}"></label>
  <label>Provider<select id="r-prov">${c.providers.map(p => `<option value="${esc(p.id)}" ${p.id === d.llm_provider ? "selected" : ""}>${esc(p.label)}${p.has_key ? "" : " (no key)"}</option>`).join("")}</select></label>
  <label>Deep model<input id="r-deep" value="${esc(d.deep_think_llm)}"></label><label>Quick model<input id="r-quick" value="${esc(d.quick_think_llm)}"></label>
  <label>Language<select id="r-lang">${["Hinglish", "English", "Hindi"].map(x => `<option ${x === d.output_language ? "selected" : ""}>${x}</option>`).join("")}</select></label>
  <label>Depth<select id="r-depth">${Object.keys(c.depths).map(x => `<option>${esc(x)}</option>`).join("")}</select></label>
  ${c.analysts.map(a => `<label class="cb"><input type="checkbox" class="an" value="${esc(a)}" checked>${esc(a)}</label>`).join("")}
  <button class="btn" id="r-go" type="submit">Run Analysis</button><button class="btn ghost" id="r-stop" type="button" hidden>Cancel</button></form>
  <p class="hint">Needs one API key in .env. Takes 1\u20135 minutes. The rule engine's numbers are passed to the trader, and the final levels are checked against live premiums.</p></section><div id="run" class="col"></div>`;
  $("r-prov").onchange = () => { const p = c.providers.find(x => x.id === $("r-prov").value); if (p) { $("r-deep").value = p.deep || ""; $("r-quick").value = p.quick || "" } };
  $("rf").onsubmit = async e => {
    e.preventDefault(); const body = { symbol: $("r-sym").value.trim(), exchange: "NSE", provider: $("r-prov").value, deep_model: $("r-deep").value.trim(), quick_model: $("r-quick").value.trim(), language: $("r-lang").value, depth: $("r-depth").value, analysts: [...document.querySelectorAll(".an:checked")].map(x => x.value), expiry: S.d && S.d.chain.expiry || null };
    $("r-go").disabled = true; try { const r = await api("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); openRun(r.id) } catch (err) { $("run").innerHTML = `<div class="errbox">${esc(err.message)}</div>`; $("r-go").disabled = false }
  };
  $("r-stop").onclick = () => S.runId && api(`/api/runs/${S.runId}/cancel`, { method: "POST" })
}
function syncForm() { const e = $("r-sym"); if (e) e.value = S.cur }
function openRun(id) { S.runId = id; S.msgs = []; clearTimeout(S.pollT); $("run").innerHTML = ""; showTab("desk"); pollRun() }
async function pollRun() {
  const id = S.runId; if (!id) return; let r; try { r = await api(`/api/runs/${encodeURIComponent(id)}?since=${S.msgs.length}`) } catch (e) { $("run").innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return }
  if (id !== S.runId) return; S.msgs.push(...r.messages); renderRun(r);
  const live = r.status === "queued" || r.status === "running"; const go = $("r-go"), st = $("r-stop"); if (go) go.disabled = live; if (st) st.hidden = !live;
  if (live) S.pollT = setTimeout(pollRun, 2000); else { loadRuns(); loadDecisions() }
}
function renderRun(r) {
  const res = r.result || {};
  const head = `<section class="pn"><div class="hd"><div><h1>${esc(r.symbol)} <span class="sub">${esc(r.status)} \u00b7 ${esc(r.phase)}</span></h1></div><div class="sub">${f0(r.usage.calls)} calls \u00b7 ${f0(r.usage.tokens_in)}/${f0(r.usage.tokens_out)} tokens</div></div>
  <div class="ag">${Object.entries(r.agents).map(([k, v]) => `<span class="${esc(v)}">${esc(k.replace("_", " "))}</span>`).join("")}</div>${r.error ? `<div class="errbox">${esc(r.error)}</div>` : ""}</section>`;
  const fin = r.status === "done" ? `<section class="pn"><div class="hd"><div><span class="tag big r-${esc(res.rating)}">${esc(res.rating)}</span> <b style="font-size:18px;margin-left:8px">${esc(res.action)}</b><div class="ins" style="margin-top:6px">${esc(res.instrument || "")}</div></div>
    <div class="sub">Rule engine: ${esc(res.rule_bias || "\u2014")} (${f0(res.rule_confidence)}%)</div></div>
    <div class="lv" style="margin-top:12px"><div><span>Entry</span><b>\u20b9${f(res.entry)}</b></div><div class="sl"><span>Stop loss</span><b>\u20b9${f(res.stop)}</b></div><div class="tg"><span>Target</span><b>\u20b9${f(res.target)}</b></div></div>
    <p class="xt" style="margin-top:10px"><b>Exit:</b> ${esc(res.exit || "\u2014")}</p>
    ${res.sizing ? `<p class="why">Sizing: \u20b9${f0(res.sizing.risk_amt)} risk on \u20b9${f0(res.sizing.capital)} capital = ${res.sizing.lots} lot(s)${res.sizing.lots === 0 ? " (1 lot risks more than your budget)" : ""}${res.sizing.rr ? ` \u00b7 RR ${f(res.sizing.rr)}` : ""}</p>` : ""}
    ${(res.warnings || []).length ? `<ul class="warns">${res.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
    <p><a href="/api/runs/${encodeURIComponent(r.id)}/report?download=true">Download full report (.md)</a></p></section>` : "";
  const msgs = [...S.msgs].reverse().map(m => `<details class="msg" ${m.kind === "error" || ["portfolio_manager", "trader"].includes(m.agent) ? "open" : ""}><summary>${esc(m.label)}<span class="t">${esc(m.at)}</span></summary>${md(m.text)}</details>`).join("");
  $("run").innerHTML = head + fin + msgs; $("prov").innerHTML = (r.provenance || []).map(p => `<tr><td>${esc(p.item)}</td><td>${esc(p.source)}</td><td>${esc(p.status)}</td></tr>`).join("") || $("prov").innerHTML
}

/* ---------- runs + decision log ---------- */
async function loadRuns() { try { const r = await api("/api/runs"); $("runs").innerHTML = r.slice(0, 8).map(x => `<button class="row" data-id="${esc(x.id)}"><span>${esc(x.symbol)}<br><small>${esc(x.trade_date)} \u00b7 ${esc(x.status)}</small></span>${x.rating ? `<span class="tag r-${esc(x.rating)}">${esc(x.rating)}</span>` : ""}</button>`).join("") || '<p class="note">No runs yet.</p>' } catch (e) { } }
$("runs").onclick = e => { const b = e.target.closest(".row"); if (b) openRun(b.dataset.id) };
async function loadDecisions() { try { const r = await api("/api/decisions"); $("dlog").innerHTML = r.slice(0, 8).map(x => `<div class="row" style="cursor:default"><span>${esc(x.ysym)}<br><small>${esc(x.trade_date)} \u00b7 ${esc(x.status)}${x.hit === true ? " \u00b7 hit" : x.hit === false ? " \u00b7 miss" : ""}</small></span><span class="tag r-${esc(x.rating)}">${esc(x.rating)}</span></div>`).join("") || '<p class="note">Nothing logged yet.</p>' } catch (e) { } }

/* ---------- boot ---------- */
(async function () {
  try { S.cfg = await api("/api/config"); renderMkt(S.cfg.market); S.market = S.cfg.market.state; buildDesk() } catch (e) { $("p-desk").innerHTML = `<div class="errbox">${esc(e.message)}</div>` }
  tickIndices(); renderScan(); loadScan(); loadSignal(); loadRuns(); loadDecisions();
  loadFutActivity(); loadAdvDec();
})();
