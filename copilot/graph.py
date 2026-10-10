"""Analysts (parallel) -> bull/bear -> research manager -> trader -> risk -> portfolio manager."""
import pathlib
from concurrent.futures import ThreadPoolExecutor

from . import agents as A
from . import dataflows as D
from .llm import LLM, Usage
from .memory import DecisionLog
from .reporting import write_report_tree


class Cancelled(Exception):
    pass


def check_levels(action, price, entry, stop, target, risk_pct, lot=1):
    w, ref = [], entry or price
    act = (action or "").upper()
    is_buy = any(x in act for x in ("BUY CE", "BUY PE", "FUT LONG", "BUY"))
    is_sell = any(x in act for x in ("SELL CE", "SELL PE", "FUT SHORT")) and "BUY" not in act
    if price and entry and abs(entry / price - 1) > 0.6 and "FUT" in act:
        w.append(f"Entry ₹{entry:,.2f} is far from reference ₹{price:,.2f}")
    if is_buy and ref and stop and stop >= ref:
        w.append("Stop-loss is on the wrong side of a long")
    if is_sell and ref and stop and stop <= ref:
        w.append("Stop-loss is on the wrong side of a short")
    sizing = None
    if ref and stop and abs(ref - stop) > 0:
        risk_amt = 100000 * risk_pct / 100
        per = abs(ref - stop) * (lot or 1)
        sizing = {"capital": 100000, "risk_amt": risk_amt, "lots": max(int(risk_amt // per), 0) if per else 0,
                  "rr": (abs((target or ref) - ref) / abs(ref - stop)) if target else None}
    return w, sizing


def reconcile(action, entry, stop, target, primary):
    """The LLM may mis-parse a strike as a premium. If the AI entry is wildly off the rule engine's live premium for the
    same kind of trade, fall back to the rule-engine levels and say so."""
    notes = []
    if not primary or primary.get("entry") is None or entry is None:
        return entry, stop, target, notes
    act = (action or "").upper().replace("  ", " ")
    kind = (primary.get("kind") or "").replace("_", " ")
    same = kind and (kind in act or act in kind or act.replace(" ", "") == kind.replace(" ", ""))
    ref = primary["entry"]
    if same and ref and abs(entry / ref - 1) > 0.35:
        notes.append(f"AI entry \u20b9{entry:,.2f} is far from live rule-engine entry \u20b9{ref:,.2f}; showing rule-engine levels.")
        return primary["entry"], primary.get("stop"), primary.get("target"), notes
    return entry, stop, target, notes


def run_pipeline(job, req, cfg, log: DecisionLog, make_llm=LLM):
    usage = Usage()
    deep = make_llm(cfg["llm_provider"], cfg["deep_think_llm"], cfg, usage)
    quick = make_llm(cfg["llm_provider"], cfg["quick_think_llm"], cfg, usage)
    job.usage = usage
    sel = [a for a in A.ANALYST_FN if a in req["analysts"]]
    if not sel:
        raise ValueError("Select at least one analyst")

    def guard():
        if job.cancelled:
            raise Cancelled()

    job.phase("Fetching option chain & futures")
    bundle = D.collect(req["symbol"], req["exchange"], req["trade_date"], cfg, sel, req.get("expiry"))
    job.provenance = bundle["provenance"]
    ysym, ctx = bundle["ysym"], bundle["context"]
    job.ysym, job.name = ysym, bundle["identity"].get("name") or req["symbol"]
    guard()
    try:
        log.resolve(ysym, quick, cfg)
    except Exception:  # noqa: BLE001
        pass

    st = {"trade_date": req["trade_date"], "chain_report": "", "volatility_report": "", "flow_report": "", "news_report": "",
          "investment_debate_state": {"history": "", "bull_history": "", "bear_history": "", "current_response": "", "count": 0},
          "risk_debate_state": {"history": "", "aggressive_history": "", "conservative_history": "", "neutral_history": "", "latest": "", "count": 0}}

    job.phase("Analyst team")
    for a in sel:
        job.agent(a, "in_progress")

    def run_analyst(a):
        try:
            guard()
            pack_key = {"chain": "chain_pack", "volatility": "volatility_pack", "flow": "flow_pack", "news": "news_pack"}[a]
            if bundle.get(pack_key) is None:
                raise RuntimeError("its data could not be fetched, see Data provenance")
            text = A.ANALYST_FN[a](quick, bundle, cfg)
            st[f"{a}_report"] = text
            job.say(a, A.ANALYST_LABEL[a], text)
            job.agent(a, "completed")
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            job.say(a, A.ANALYST_LABEL[a], f"_{A.ANALYST_LABEL[a]} unavailable: {e}_", kind="error")
            job.agent(a, "error")

    with ThreadPoolExecutor(len(sel)) as pool:
        list(pool.map(run_analyst, sel))
    guard()
    if not any(st[f"{a}_report"] for a in sel):
        raise RuntimeError("Every analyst failed. Check Data provenance and your API key.")
    reports = A._reports({k: st[f"{k}_report"] for k in A.ANALYST_FN}, A._budget(cfg, "report_char_budget", 2600))

    job.phase("Research debate")
    d, rounds = st["investment_debate_state"], int(cfg["max_debate_rounds"])
    while d["count"] < 2 * rounds:
        guard()
        is_bull = d["count"] % 2 == 0
        who = "bull" if is_bull else "bear"
        job.agent(who, "in_progress")
        arg = (A.bull if is_bull else A.bear)(quick, ctx, reports, d["history"], d["current_response"], cfg)
        d["history"] += "\n" + arg
        d[f"{who}_history"] += "\n" + arg
        d["current_response"], d["count"] = arg, d["count"] + 1
        job.say(who, ("Bull" if is_bull else "Bear") + f" · round {d['count'] // 2 + d['count'] % 2}", arg)
        job.agent(who, "completed")
    guard()
    job.agent("research_manager", "in_progress")
    st["investment_plan"] = A.research_manager(deep, ctx, d["history"], cfg)
    job.say("research_manager", "Research Manager", st["investment_plan"])
    job.agent("research_manager", "completed")

    guard()
    job.phase("Trader")
    job.agent("trader", "in_progress")
    meta = bundle["market_meta"]
    px = meta.get("price")
    q = meta.get("quote") or {}
    live_note = f"Spot ₹{px}" if px else ""
    if q:
        live_note += f" (from {q.get('source')}, {q.get('as_of_label')})"
    sig = bundle.get("signal") or {}
    st["trader_investment_plan"] = A.trader(deep, ctx, st["investment_plan"], st["flow_report"], live_note, sig, cfg)
    job.say("trader", "Trader", st["trader_investment_plan"])
    job.agent("trader", "completed")

    job.phase("Risk debate")
    rk, order, rr = st["risk_debate_state"], ["aggressive", "conservative", "neutral"], int(cfg["max_risk_discuss_rounds"])
    last = {}
    while rk["count"] < 3 * rr:
        guard()
        who = order[rk["count"] % 3]
        job.agent(who, "in_progress")
        arg = A.risk(quick, who, ctx, reports, st["trader_investment_plan"], rk["history"], last, cfg)
        rk["history"] += "\n" + arg
        rk[f"{who}_history"] += "\n" + arg
        last[who], rk["count"] = arg, rk["count"] + 1
        job.say(who, f"{who.capitalize()} Analyst · round {(rk['count'] - 1) // 3 + 1}", arg)
        job.agent(who, "completed")

    guard()
    job.phase("Portfolio manager")
    job.agent("portfolio_manager", "in_progress")
    lessons = log.lessons(ysym)
    st["final_trade_decision"] = A.portfolio_manager(deep, ctx, st["investment_plan"], st["trader_investment_plan"], rk["history"], lessons, cfg)
    st["final_rating"] = A.parse_rating(st["final_trade_decision"], "Rating")
    job.say("portfolio_manager", "Portfolio Manager", st["final_trade_decision"])
    job.agent("portfolio_manager", "completed")

    tr = st["trader_investment_plan"]
    action = A.parse_action(tr)
    entry, stop, target = A.parse_price(tr, "Entry"), A.parse_price(tr, "Stop Loss"), A.parse_price(tr, "Target")
    instrument = A.parse_field(tr, "Instrument")
    exit_txt = A.parse_field(tr, "Exit")
    lot = (bundle.get("chain") or {}).get("lot_size") or 1
    primary = (sig.get("primary") or {})
    entry, stop, target, rec_notes = reconcile(action, entry, stop, target, primary)
    warns, sizing = check_levels(action, px, entry, stop, target, float(cfg.get("risk_per_trade_pct", 1)), lot)
    warns = rec_notes + warns + list(sig.get("warnings") or [])
    if rec_notes and primary.get("instrument"):
        instrument = primary["instrument"]  # levels came from the rule engine, so the contract must match them
    result = {"rating": st["final_rating"], "research_rating": A.parse_rating(st["investment_plan"], "Recommendation"),
              "action": action, "instrument": instrument or primary.get("instrument"),
              "entry": entry or primary.get("entry"), "stop": stop or primary.get("stop"),
              "target": target or primary.get("target"), "exit": exit_txt or primary.get("exit"),
              "price": px, "warnings": warns, "sizing": sizing, "setups": sig.get("setups") or [], "rule_bias": sig.get("bias"),
              "rule_confidence": sig.get("confidence"), "lessons_used": bool(lessons)}
    settings = {**cfg, "analysts": sel, "data_sources": [f"{p['item']}: {p['source']} ({p['status']})" for p in bundle["provenance"]]}
    path = write_report_tree(st, ysym, pathlib.Path(job.dir), settings)
    log.add({"id": job.id, "ysym": ysym, "trade_date": req["trade_date"], "rating": st["final_rating"], "ref_price": px,
             "status": "pending", "decision": st["final_trade_decision"]})
    job.report_path = str(path)
    return result
