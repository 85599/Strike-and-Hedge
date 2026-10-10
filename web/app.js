"use strict";
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const f=(n,d=2)=>n==null||isNaN(n)?"\u2014":Number(n).toLocaleString("en-IN",{minimumFractionDigits:d,maximumFractionDigits:d});
const f0=n=>f(n,0),pc=n=>n==null?"\u2014":(n>=0?"+":"")+n.toFixed(2)+"%",sg=n=>n==null?"":n>=0?"up":"dn";
const store={get(k,d){try{return JSON.parse(localStorage.getItem(k))||d}catch(e){return d}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
async function api(path,opt){const r=await fetch(path,opt);if(!r.ok){let m=r.status;try{m=(await r.json()).detail||m}catch(e){}throw new Error(typeof m==="string"?m:JSON.stringify(m))}return r.json()}
const S={cur:"NIFTY",expiry:null,scanSyms:store.get("scan1",["NIFTY","BANKNIFTY","SENSEX","FINNIFTY"]),scan:{},d:null,cfg:null,tab:"signals",
  runId:null,msgs:[],pollT:null,sigT:null,market:"closed",capital:store.get("capital",100000),riskPct:store.get("riskpct",1)};

/* ---------- markdown (escaped; tables, lists, bold) ---------- */
function md(src){const L=String(src||"").replace(/\r/g,"").split("\n"),o=[];let i=0;
  const inl=s=>esc(s).replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>");
  const isRow=l=>/^\s*\|.*\|\s*$/.test(l),cells=r=>r.trim().replace(/^\||\|$/g,"").split("|").map(c=>c.trim());
  const blk=/^(#{1,4}\s|\s*[-*]\s|\s*\d+[.)]\s|\s*\|)/;
  while(i<L.length){const l=L[i];let m;
    if(isRow(l)&&i+1<L.length&&/^\s*\|[\s:|-]+\|\s*$/.test(L[i+1])){const h=cells(l),rows=[];i+=2;while(i<L.length&&isRow(L[i]))rows.push(cells(L[i++]));
      o.push("<div class=tw><table><thead><tr>"+h.map(c=>`<th>${inl(c)}</th>`).join("")+"</tr></thead><tbody>"+rows.map(r=>"<tr>"+r.map(c=>`<td>${inl(c)}</td>`).join("")+"</tr>").join("")+"</tbody></table></div>");continue}
    if(m=l.match(/^(#{1,4})\s+(.*)/)){o.push(`<h3>${inl(m[2])}</h3>`);i++;continue}
    if(/^\s*[-*]\s+/.test(l)){const it=[];while(i<L.length&&/^\s*[-*]\s+/.test(L[i]))it.push(`<li>${inl(L[i++].replace(/^\s*[-*]\s+/,""))}</li>`);o.push("<ul>"+it.join("")+"</ul>");continue}
    if(/^\s*\d+[.)]\s+/.test(l)){const it=[];while(i<L.length&&/^\s*\d+[.)]\s+/.test(L[i]))it.push(`<li>${inl(L[i++].replace(/^\s*\d+[.)]\s+/,""))}</li>`);o.push("<ol>"+it.join("")+"</ol>");continue}
    if(!l.trim()){i++;continue}
    const p=[];while(i<L.length&&L[i].trim()&&!blk.test(L[i]))p.push(inl(L[i++]));
    if(p.length)o.push("<p>"+p.join("<br>")+"</p>");else{o.push(`<p>${inl(l)}</p>`);i++}}
  return o.join("")}

/* ---------- header + index strip ---------- */
function renderMkt(m){$("mkt").innerHTML=`<span class="dot ${esc(m.state)}"></span><span>${esc(m.label)}</span><span>${esc(m.now_ist)}</span>`}
function renderStrip(d){const c=r=>r.error?`<div><span>${esc(r.name)}</span><b>n/a</b></div>`:
  `<div title="${esc(r.source)} \u00b7 ${esc(r.as_of_label)}${r.stale?" \u00b7 STALE":""}"><span>${esc(r.name)}${r.stale?" \u00b7 stale":""}</span><b>${f(r.price)}</b><i class="${sg(r.change_pct)}">${pc(r.change_pct)}</i></div>`;
  $("strip").innerHTML=d.indices.map(c).join("")+d.macro.map(c).join("")}
async function tickIndices(){try{const d=await api("/api/indices");S.market=d.market.state;renderStrip(d);renderMkt(d.market)}catch(e){$("strip").innerHTML=`<div><span>Rates unavailable</span><b>${esc(e.message).slice(0,80)}</b></div>`}
  setTimeout(tickIndices,S.market==="closed"?30000:5000)}

/* ---------- scanner ---------- */
function renderScan(){$("scan").innerHTML=S.scanSyms.map(s=>{const r=S.scan[s];
  const right=!r?"<small>\u2026</small>":r.error?`<span class="err" title="${esc(r.error)}">n/a</span>`:`<span class="tag ${esc(r.bias)}">${esc(r.bias)}</span><small>${f0(r.confidence)}% \u00b7 PCR ${f(r.pcr)}</small>`;
  const sub=r&&!r.error?`<small>${f(r.spot)}</small>`:"";
  return `<button class="sc" data-s="${esc(s)}" aria-pressed="${S.cur===s}"><span><b>${esc(s)}</b>${sub}</span><span class="r">${right}</span></button>`}).join("")}
async function loadScan(){try{(await api("/api/scan?symbols="+encodeURIComponent(S.scanSyms.join(",")))).forEach(r=>S.scan[r.symbol]=r)}catch(e){}
  renderScan();clearTimeout(loadScan.t);loadScan.t=setTimeout(loadScan,30000)}
$("scan").onclick=e=>{const b=e.target.closest(".sc");if(b)selectSym(b.dataset.s)};
$("scan-reload").onclick=()=>loadScan();

/* ---------- search ---------- */
let sugT;$("q").addEventListener("input",e=>{clearTimeout(sugT);const v=e.target.value.trim();if(v.length<2){$("sug").innerHTML="";return}
  sugT=setTimeout(async()=>{try{const r=await api("/api/search?q="+encodeURIComponent(v));
    $("sug").innerHTML=r.map(x=>`<option value="${esc(x.symbol)}">${esc(x.name)} \u00b7 ${esc(x.type)}</option>`).join("")}catch(_){}},250)});
$("q").addEventListener("keydown",e=>{if(e.key!=="Enter")return;const v=e.target.value.trim().toUpperCase();if(!v)return;
  if(!S.scanSyms.includes(v)){S.scanSyms.push(v);store.set("scan1",S.scanSyms)}e.target.value="";selectSym(v);loadScan()});

/* ---------- tabs ---------- */
function showTab(t){S.tab=t;document.querySelectorAll(".tabs button").forEach(b=>b.setAttribute("aria-selected",b.dataset.tab===t));
  ["signals","chain","desk"].forEach(x=>$("p-"+x).hidden=x!==t)}
document.querySelector(".tabs").onclick=e=>{const b=e.target.closest("button");if(b)showTab(b.dataset.tab)};

/* ---------- signals ---------- */
const KIND_LABEL={BUY_CE:"BUY CALL (CE)",BUY_PE:"BUY PUT (PE)",FUT_LONG:"FUTURES LONG",FUT_SHORT:"FUTURES SHORT",SELL_CE_SPREAD:"SELL CALL SPREAD",SELL_PE_SPREAD:"SELL PUT SPREAD",AVOID:"NO TRADE"};
function lotsFor(s){const risk=s.kind&&s.kind.includes("SPREAD")?s.max_loss_rs:s.premium_risk_rs;if(!risk)return null;
  const budget=S.capital*S.riskPct/100;return{lots:Math.floor(budget/risk),budget,risk}}
function planText(s,d){return [`${d.symbol} ${KIND_LABEL[s.kind]||s.kind}`,s.instrument,`Entry ${s.entry}  SL ${s.stop}  Target ${s.target}  RR ${s.rr??"-"}`,`Exit: ${s.exit}`,"Educational research, not advice."].join("\n")}
function setupCard(s,i,d){if(s.kind==="AVOID")return `<div class="setup"><div class="top"><span class="tag big AVOID">NO TRADE</span></div><p class="xt">${esc(s.exit)}</p><p class="why">${esc(s.rationale)}</p></div>`;
  const L=lotsFor(s),legs=s.legs?`<ul class="legs">${s.legs.map(l=>`<li><b>${esc(l.action)}</b> ${esc(l.instrument)} @ \u20b9${f(l.ltp)}</li>`).join("")}</ul>`:"";
  const lbl=s.kind.includes("SPREAD")?"credit":"entry";
  return `<div class="setup"><div class="top"><span class="tag big ${esc(s.kind)}">${esc(KIND_LABEL[s.kind]||s.kind)}</span><button class="btn ghost sm cp" data-i="${i}" type="button">Copy plan</button></div>
  <div class="ins">${esc(s.instrument)}</div>${legs}
  <div class="lv"><div><span>${lbl==="credit"?"Net credit":"Entry"}</span><b>\u20b9${f(s.entry)}</b></div><div class="sl"><span>Stop loss</span><b>\u20b9${f(s.stop)}</b></div><div class="tg"><span>Target</span><b>\u20b9${f(s.target)}</b></div></div>
  <div class="kv"><span>RR <b>${s.rr==null?"\u2014":f(s.rr)}</b></span><span>Lot <b>${f0(s.lot_size)}</b></span><span>Risk/lot <b>\u20b9${f0(s.kind.includes("SPREAD")?s.max_loss_rs:s.premium_risk_rs)}</b></span>${s.delta!=null?`<span>\u0394 <b>${f(s.delta,2)}</b></span>`:""}${s.iv!=null?`<span>IV <b>${f(s.iv,1)}%</b></span>`:""}${s.max_profit_rs?`<span>Max profit <b>\u20b9${f0(s.max_profit_rs)}</b></span>`:""}</div>
  ${L?(L.lots>0?`<div class="kv"><span>For \u20b9${f0(S.capital)} at ${S.riskPct}% risk: <b>${L.lots} lot${L.lots===1?"":"s"}</b> (budget \u20b9${f0(L.budget)})</span></div>`:`<div class="kv"><span class="dn">1 lot risks \u20b9${f0(L.risk)}, more than your \u20b9${f0(L.budget)} budget. Skip it, or pick a cheaper strike or a spread.</span></div>`):""}
  <p class="xt"><b>Exit:</b> ${esc(s.exit)}</p><p class="why">${esc(s.rationale)}</p></div>`}
function renderSignals(){const d=S.d,el=$("p-signals");if(!d){return}
  const sg_=d.signal,m=sg_.metrics||{},ch=d.chain,fut=d.futures,t=d.tech||{};
  const exps=(ch.expiries||[]).map(x=>`<option ${x===ch.expiry?"selected":""}>${esc(x)}</option>`).join("");
  el.innerHTML=`<section class="pn"><div class="hd"><div><h1>${esc(d.symbol)} <span class="tag big ${esc(sg_.bias)}">${esc(sg_.bias)}</span></h1>
    <div class="chips"><span class="chip">${esc(d.exchange)} \u00b7 ${esc(d.kind)}</span><span class="chip">${esc(ch.source)}</span><span class="chip">Lot ${f0(ch.lot_size)}</span><span class="chip">DTE ${f(ch.dte_days,1)} d</span>${d.market.state!=="open"?`<span class="chip warn">${esc(d.market.label)}</span>`:""}</div>
    <div class="conf" title="Rule-engine confidence"><i style="width:${Math.min(100,sg_.confidence||0)}%"></i></div><div class="sub">Rule confidence ${f0(sg_.confidence)}% \u00b7 score ${sg_.score??"\u2014"}</div></div>
    <div style="text-align:right"><div class="px">\u20b9${f(ch.spot)}</div><div class="sub">Expiry <select id="exp" aria-label="Expiry">${exps}</select></div></div></div>
    ${(sg_.warnings||[]).length?`<ul class="warns">${sg_.warnings.map(w=>`<li>${esc(w)}</li>`).join("")}</ul>`:""}</section>
  <section class="pn"><h2>Trade ideas (rules, no LLM)</h2><div class="calc"><label>Capital \u20b9 <input id="cap" type="number" min="1000" step="1000" value="${S.capital}"></label>
    <label>Risk per trade % <input id="rsk" type="number" min="0.1" max="5" step="0.1" value="${S.riskPct}"></label></div><div class="setups" style="margin-top:12px">${(sg_.setups||[]).map((s,i)=>setupCard(s,i,d)).join("")}</div>
    <p class="hint">${esc(sg_.disclaimer||"")}</p></section>
  <section class="pn"><h2>Why this bias</h2><div class="cols" style="margin-top:0"><ul class="rs">${(sg_.reasons||[]).map(r=>`<li>${esc(r)}</li>`).join("")||"<li>No reasons (empty chain)</li>"}</ul>
    <div class="grid4" style="margin:0;grid-template-columns:repeat(2,1fr)">
    <div><span>PCR (OI)</span><b>${f(m.pcr_oi)}</b></div><div><span>Max pain</span><b>${f0(m.max_pain)}</b></div>
    <div><span>Call wall</span><b class="dn">${f0(m.call_wall)}</b></div><div><span>Put wall</span><b class="up">${f0(m.put_wall)}</b></div>
    <div><span>ATM IV</span><b>${f(m.atm_iv,1)}%</b></div><div><span>Expected move</span><b>${f(m.expected_move_pct)}%</b></div>
    <div><span>India VIX</span><b>${f(d.vix.price)}</b></div><div><span>RSI / ATR</span><b>${f(t.rsi,0)} / ${f(t.atr,0)}</b></div>
    <div><span>Futures</span><b>${fut?f(fut.ltp):"\u2014"}</b><span>${fut?esc(fut.buildup)+" \u00b7 "+f(fut.basis_pct)+"%":"no quote"}</span></div>
    <div><span>PE vs CE writing</span><b>${f0(m.pe_writing)} / ${f0(m.ce_writing)}</b></div></div></div></section>`;
  $("exp").onchange=e=>{S.expiry=e.target.value;loadSignal()};
  const keep=()=>{S.capital=+$("cap").value||100000;S.riskPct=+$("rsk").value||1;store.set("capital",S.capital);store.set("riskpct",S.riskPct);renderSignals()};
  $("cap").onchange=keep;$("rsk").onchange=keep;
  el.querySelectorAll(".cp").forEach(b=>b.onclick=()=>{const s=sg_.setups[+b.dataset.i];navigator.clipboard&&navigator.clipboard.writeText(planText(s,d)).then(()=>{b.textContent="Copied";setTimeout(()=>b.textContent="Copy plan",1200)})})}
function renderChain(){const d=S.d,el=$("p-chain");if(!d)return;const ch=d.chain,rows=ch.rows||[],m=d.signal.metrics||{};
  const mx=k=>Math.max(1,...rows.map(r=>Math.max((r.ce||{})[k]||0,(r.pe||{})[k]||0)));const mo=mx("oi");
  const atm=m.atm_strike,cw=m.call_wall,pw=m.put_wall;
  const bar=(leg,side)=>{const o=(leg||{}).oi||0;return `<td class="bar ${side}"><i style="width:${Math.round(o/mo*100)}%"></i><span>${f0(o)}</span></td>`};
  const chg=v=>`<td class="${sg(v)}">${v==null?"\u2014":(v>0?"+":"")+f0(v)}</td>`;
  el.innerHTML=`<section class="pn"><h2>${esc(d.symbol)} option chain \u00b7 ${esc(ch.expiry)} <span class="sub">${esc(ch.source)} \u00b7 ${esc(ch.as_of||"")}</span></h2><div class="tw"><table class="oc"><thead><tr>
    <th>OI</th><th>\u0394 OI</th><th>IV</th><th>LTP</th><th style="text-align:center">Strike</th><th>LTP</th><th>IV</th><th>\u0394 OI</th><th>OI</th></tr></thead><tbody>
    ${rows.map(r=>{const c=r.ce||{},p=r.pe||{};return `<tr class="${r.strike===atm?"atm":""}">${bar(c,"ce")}${chg(c.oi_chg)}<td>${f(c.iv,1)}</td><td>${f(c.ltp)}</td>
    <td class="k ${r.strike===cw||r.strike===pw?"wall":""}" title="${r.strike===cw?"Call OI wall (resistance)":r.strike===pw?"Put OI wall (support)":""}">${f0(r.strike)}</td>
    <td>${f(p.ltp)}</td><td>${f(p.iv,1)}</td>${chg(p.oi_chg)}${bar(p,"pe")}</tr>`}).join("")}</tbody></table></div>
    <p class="hint">Amber outline = biggest call OI (resistance) and put OI (support). Blue lines mark the ATM strike. Red bars are call OI, green bars are put OI.</p></section>`}
async function loadSignal(){clearTimeout(S.sigT);const sym=S.cur;
  try{const d=await api(`/api/signal/${encodeURIComponent(sym)}`+(S.expiry?`?expiry=${encodeURIComponent(S.expiry)}`:""));if(sym!==S.cur)return;S.d=d;
    renderSignals();renderChain();$("prov").innerHTML=provRows(d)}
  catch(e){if(sym!==S.cur)return;$("p-signals").innerHTML=`<section class="pn"><h1>${esc(sym)}</h1><div class="errbox" style="margin-top:10px">${esc(e.message)}</div>
    <p class="hint">NSE/BSE block requests now and then. Wait a few seconds and press Refresh; if it persists, open /api/diag/${esc(sym)} to see which source fails.</p></section>`;$("p-chain").innerHTML=""}
  S.sigT=setTimeout(loadSignal,S.market==="closed"?60000:20000)}
const provRows=d=>[["Option chain",d.chain.source,"ok "+(d.chain.expiry||"")],["Futures",d.futures?"NSE.com":"n/a",d.futures?"ok":"missing"],["India VIX",d.vix.source||"n/a",d.vix.price?"ok":"missing"],["Daily bars",d.tech?"Yahoo":"n/a",d.tech?"ok":"missing"]]
  .map(r=>`<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join("");
function selectSym(s){S.cur=s.toUpperCase();S.expiry=null;S.d=null;renderScan();$("p-signals").innerHTML=`<section class="pn"><h1>${esc(S.cur)}</h1><p class="note">Loading option chain\u2026</p></section>`;loadSignal();syncForm()}

/* ---------- AI desk ---------- */
function buildDesk(){const c=S.cfg,d=c.defaults;
  $("p-desk").innerHTML=`<section class="pn"><h2>Multi-agent F&amp;O desk</h2>
  <form class="f" id="rf"><label>Underlying<input id="r-sym" value="${esc(S.cur)}"></label>
  <label>Provider<select id="r-prov">${c.providers.map(p=>`<option value="${esc(p.id)}" ${p.id===d.llm_provider?"selected":""}>${esc(p.label)}${p.has_key?"":" (no key)"}</option>`).join("")}</select></label>
  <label>Deep model<input id="r-deep" value="${esc(d.deep_think_llm)}"></label><label>Quick model<input id="r-quick" value="${esc(d.quick_think_llm)}"></label>
  <label>Language<select id="r-lang">${["Hinglish","English","Hindi"].map(x=>`<option ${x===d.output_language?"selected":""}>${x}</option>`).join("")}</select></label>
  <label>Depth<select id="r-depth">${Object.keys(c.depths).map(x=>`<option>${esc(x)}</option>`).join("")}</select></label>
  ${c.analysts.map(a=>`<label class="cb"><input type="checkbox" class="an" value="${esc(a)}" checked>${esc(a)}</label>`).join("")}
  <button class="btn" id="r-go" type="submit">Run analysis</button><button class="btn ghost" id="r-stop" type="button" hidden>Cancel</button></form>
  <p class="hint">Needs one API key in .env. Takes 1\u20135 minutes. The rule engine's numbers are passed to the trader, and the final levels are checked against live premiums.</p></section><div id="run" class="col"></div>`;
  $("r-prov").onchange=()=>{const p=c.providers.find(x=>x.id===$("r-prov").value);if(p){$("r-deep").value=p.deep||"";$("r-quick").value=p.quick||""}};
  $("rf").onsubmit=async e=>{e.preventDefault();const body={symbol:$("r-sym").value.trim(),exchange:"NSE",provider:$("r-prov").value,deep_model:$("r-deep").value.trim(),quick_model:$("r-quick").value.trim(),
    language:$("r-lang").value,depth:$("r-depth").value,analysts:[...document.querySelectorAll(".an:checked")].map(x=>x.value),expiry:S.d&&S.d.chain.expiry||null};
    $("r-go").disabled=true;try{const r=await api("/api/runs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});openRun(r.id)}catch(err){$("run").innerHTML=`<div class="errbox">${esc(err.message)}</div>`;$("r-go").disabled=false}};
  $("r-stop").onclick=()=>S.runId&&api(`/api/runs/${S.runId}/cancel`,{method:"POST"})}
function syncForm(){const e=$("r-sym");if(e)e.value=S.cur}
function openRun(id){S.runId=id;S.msgs=[];clearTimeout(S.pollT);$("run").innerHTML="";showTab("desk");pollRun()}
async function pollRun(){const id=S.runId;if(!id)return;let r;try{r=await api(`/api/runs/${encodeURIComponent(id)}?since=${S.msgs.length}`)}catch(e){$("run").innerHTML=`<div class="errbox">${esc(e.message)}</div>`;return}
  if(id!==S.runId)return;S.msgs.push(...r.messages);renderRun(r);
  const live=r.status==="queued"||r.status==="running";const go=$("r-go"),st=$("r-stop");if(go)go.disabled=live;if(st)st.hidden=!live;
  if(live)S.pollT=setTimeout(pollRun,2000);else{loadRuns();loadDecisions()}}
function renderRun(r){const res=r.result||{};
  const head=`<section class="pn"><div class="hd"><div><h1>${esc(r.symbol)} <span class="sub">${esc(r.status)} \u00b7 ${esc(r.phase)}</span></h1></div><div class="sub">${f0(r.usage.calls)} calls \u00b7 ${f0(r.usage.tokens_in)}/${f0(r.usage.tokens_out)} tokens</div></div>
  <div class="ag">${Object.entries(r.agents).map(([k,v])=>`<span class="${esc(v)}">${esc(k.replace("_"," "))}</span>`).join("")}</div>${r.error?`<div class="errbox">${esc(r.error)}</div>`:""}</section>`;
  const fin=r.status==="done"?`<section class="pn"><div class="hd"><div><span class="tag big r-${esc(res.rating)}">${esc(res.rating)}</span> <b style="font-size:18px;margin-left:8px">${esc(res.action)}</b><div class="ins" style="margin-top:6px">${esc(res.instrument||"")}</div></div>
    <div class="sub">Rule engine: ${esc(res.rule_bias||"\u2014")} (${f0(res.rule_confidence)}%)</div></div>
    <div class="lv" style="margin-top:12px"><div><span>Entry</span><b>\u20b9${f(res.entry)}</b></div><div class="sl"><span>Stop loss</span><b>\u20b9${f(res.stop)}</b></div><div class="tg"><span>Target</span><b>\u20b9${f(res.target)}</b></div></div>
    <p class="xt" style="margin-top:10px"><b>Exit:</b> ${esc(res.exit||"\u2014")}</p>
    ${res.sizing?`<p class="why">Sizing: \u20b9${f0(res.sizing.risk_amt)} risk on \u20b9${f0(res.sizing.capital)} capital = ${res.sizing.lots} lot(s)${res.sizing.lots===0?" (1 lot risks more than your budget)":""}${res.sizing.rr?` \u00b7 RR ${f(res.sizing.rr)}`:""}</p>`:""}
    ${(res.warnings||[]).length?`<ul class="warns">${res.warnings.map(w=>`<li>${esc(w)}</li>`).join("")}</ul>`:""}
    <p><a href="/api/runs/${encodeURIComponent(r.id)}/report?download=true">Download full report (.md)</a></p></section>`:"";
  const msgs=[...S.msgs].reverse().map(m=>`<details class="msg" ${m.kind==="error"||["portfolio_manager","trader"].includes(m.agent)?"open":""}><summary>${esc(m.label)}<span class="t">${esc(m.at)}</span></summary>${md(m.text)}</details>`).join("");
  $("run").innerHTML=head+fin+msgs;$("prov").innerHTML=(r.provenance||[]).map(p=>`<tr><td>${esc(p.item)}</td><td>${esc(p.source)}</td><td>${esc(p.status)}</td></tr>`).join("")||$("prov").innerHTML}

/* ---------- runs + decision log ---------- */
async function loadRuns(){try{const r=await api("/api/runs");$("runs").innerHTML=r.slice(0,8).map(x=>`<button class="row" data-id="${esc(x.id)}"><span>${esc(x.symbol)}<br><small>${esc(x.trade_date)} \u00b7 ${esc(x.status)}</small></span>${x.rating?`<span class="tag r-${esc(x.rating)}">${esc(x.rating)}</span>`:""}</button>`).join("")||'<p class="note">No runs yet.</p>'}catch(e){}}
$("runs").onclick=e=>{const b=e.target.closest(".row");if(b)openRun(b.dataset.id)};
async function loadDecisions(){try{const r=await api("/api/decisions");$("dlog").innerHTML=r.slice(0,8).map(x=>`<div class="row" style="cursor:default"><span>${esc(x.ysym)}<br><small>${esc(x.trade_date)} \u00b7 ${esc(x.status)}${x.hit===true?" \u00b7 hit":x.hit===false?" \u00b7 miss":""}</small></span><span class="tag r-${esc(x.rating)}">${esc(x.rating)}</span></div>`).join("")||'<p class="note">Nothing logged yet.</p>'}catch(e){}}

/* ---------- boot ---------- */
(async function(){try{S.cfg=await api("/api/config");renderMkt(S.cfg.market);S.market=S.cfg.market.state;buildDesk()}catch(e){$("p-desk").innerHTML=`<div class="errbox">${esc(e.message)}</div>`}
  tickIndices();renderScan();loadScan();loadSignal();loadRuns();loadDecisions()})();
