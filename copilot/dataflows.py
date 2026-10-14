"""Data packs for F&O analysts. Every number is computed in code."""
from . import chain as ch
from . import indicators as ind
from . import market as mk
from .analytics import pcr_label, summarise_chain
from .nse import fii_dii
from .signals import instant_signal
from .universe import meta as und_meta


def _fmt(n, d=2):
    if n is None:
        return "n/a"
    try:
        return f"{float(n):,.{d}f}"
    except (TypeError, ValueError):
        return str(n)


def collect(symbol, exchange, trade_date, cfg, analysts, expiry=None):
    info = und_meta(symbol)
    symbol = info["symbol"]
    ysym = mk.resolve_symbol(symbol, info.get("exchange") or exchange)
    provenance = []
    quote = None
    try:
        quote = mk.get_quote(symbol, info.get("exchange") or exchange)
        provenance.append({"item": "spot", "source": quote.get("source"), "status": "ok"})
    except Exception as e:  # noqa: BLE001
        provenance.append({"item": "spot", "source": "n/a", "status": f"error: {e}"[:120]})

    chain, fut, vix = None, None, None
    try:
        chain = ch.load(symbol, expiry)
        provenance.append({"item": "option_chain", "source": chain.get("source"), "status": f"ok {chain.get('expiry')}"})
    except Exception as e:  # noqa: BLE001
        provenance.append({"item": "option_chain", "source": "exchange", "status": f"error: {e}"[:160]})

    try:
        fut = ch.load_futures(symbol, chain)
        provenance.append({"item": "futures", "source": "NSE.com" if fut else "n/a", "status": "ok" if fut else "missing"})
    except Exception as e:  # noqa: BLE001
        provenance.append({"item": "futures", "source": "NSE.com", "status": f"error: {e}"[:120]})

    vix = mk.india_vix()
    provenance.append({"item": "india_vix", "source": vix.get("source") or "n/a",
                       "status": "ok" if vix.get("price") else vix.get("error") or "missing"})

    tech = None
    try:
        df = mk.history(ysym, period="1y")
        tech = ind.latest(ind.compute_all(df))
        tech["last_close"] = float(df["Close"].iloc[-1])
        provenance.append({"item": "history", "source": "Yahoo/yfinance", "status": "ok"})
    except Exception as e:  # noqa: BLE001
        provenance.append({"item": "history", "source": "Yahoo", "status": f"error: {e}"[:120]})

    signal = (instant_signal(chain, fut, vix, tech, mk.market_status(),
                             {"change_pct": (quote or {}).get("change_pct")})
              if chain else {"bias": "AVOID", "setups": [], "error": "no chain"})

    news_pack = None
    if "news" in analysts:
        news_pack = mk.news(symbol if info["kind"] == "stock" else f"{symbol} India FNO")
        try:
            flows = fii_dii()
        except Exception:
            flows = None
        news_pack = {**(news_pack or {}), "fii_dii": flows}
        provenance.append({"item": "news", "source": "Yahoo", "status": "ok" if news_pack.get("items") else "thin"})

    identity = {"symbol": symbol, "name": (quote or {}).get("name") or symbol, "kind": info["kind"],
                "exchange": info.get("exchange"), "ysym": ysym}
    ctx = (f"Underlying {symbol} ({info['kind']}, {info.get('exchange')}). Analysis date {trade_date}. "
           f"Spot {(quote or {}).get('price')}. This is an F&O research desk, not investment advice.")
    bundle = {
        "identity": identity, "ysym": ysym, "trade_date": trade_date, "context": ctx,
        "quote": quote, "chain": chain, "futures": fut, "vix": vix, "tech": tech,
        "signal": signal, "provenance": provenance, "market_meta": {"price": (quote or {}).get("price"), "quote": quote},
    }
    if "chain" in analysts and chain:
        bundle["chain"] = chain
        bundle["chain_pack"] = _chain_pack(chain, signal)
    if "volatility" in analysts:
        bundle["volatility_pack"] = _vol_pack(chain, vix, signal)
    if "flow" in analysts:
        bundle["flow_pack"] = _flow_pack(fut, signal, tech)
    if "news" in analysts:
        bundle["news_pack"] = _news_pack(news_pack)
    return bundle


def _chain_pack(chain, signal):
    m = (signal or {}).get("metrics") or summarise_chain(chain)
    lines = [
        f"UNDERLYING {chain.get('underlying')}  SPOT {_fmt(chain.get('spot'))}  EXPIRY {chain.get('expiry')}",
        f"LOT {chain.get('lot_size')}  SOURCE {chain.get('source')}  AS OF {chain.get('as_of')}",
        f"PCR OI {_fmt(m.get('pcr_oi'))} ({pcr_label(m.get('pcr_oi'))})  PCR VOL {_fmt(m.get('pcr_vol'))}",
        f"MAX PAIN {_fmt(m.get('max_pain'), 0)}  SPOT vs PAIN {_fmt(m.get('spot_vs_max_pain'))}",
        f"PUT WALL {_fmt(m.get('put_wall'), 0)}  CALL WALL {_fmt(m.get('call_wall'), 0)}",
        f"ATM {m.get('atm_strike')}  STRADDLE {_fmt(m.get('atm_straddle'))}  EXPECTED MOVE {_fmt(m.get('expected_move_pct'))}%",
        f"PE writing {_fmt(m.get('pe_writing'), 0)}  CE writing {_fmt(m.get('ce_writing'), 0)}",
        f"ATM CE {m.get('atm_ce')}  ATM PE {m.get('atm_pe')}",
        "TOP CALL OI: " + ", ".join(f"{x['strike']}({_fmt(x['oi'], 0)})" for x in (m.get('call_walls') or [])[:4]),
        "TOP PUT OI: " + ", ".join(f"{x['strike']}({_fmt(x['oi'], 0)})" for x in (m.get('put_walls') or [])[:4]),
        "Nearby strikes (strike | CE LTP/IV/OI | PE LTP/IV/OI):",
    ]
    spot = chain.get("spot") or 0
    nearby = sorted(chain.get("rows") or [], key=lambda r: abs((r.get("strike") or 0) - spot))[:11]
    nearby.sort(key=lambda r: r.get("strike") or 0)
    for r in nearby:
        ce, pe = r.get("ce") or {}, r.get("pe") or {}
        lines.append(
            f"  {r.get('strike')} | CE {_fmt(ce.get('ltp'))} iv={_fmt(ce.get('iv'))} oi={_fmt(ce.get('oi'), 0)} dlt={_fmt(ce.get('delta'), 3)} "
            f"| PE {_fmt(pe.get('ltp'))} iv={_fmt(pe.get('iv'))} oi={_fmt(pe.get('oi'), 0)} dlt={_fmt(pe.get('delta'), 3)}"
        )
    return "\n".join(lines)


def _vol_pack(chain, vix, signal):
    m = (signal or {}).get("metrics") or {}
    vix_px = (vix or {}).get("price")
    lines = [
        f"INDIA VIX {_fmt(vix_px)}  change {_fmt((vix or {}).get('change_pct'))}%  source {(vix or {}).get('source')}",
        f"ATM IV {_fmt(m.get('atm_iv'))} (exchange percent)  ATM straddle {_fmt(m.get('atm_straddle'))}",
        f"Expected move to expiry (straddle/spot) {_fmt(m.get('expected_move_pct'))}%",
        f"DTE days {_fmt((chain or {}).get('dte_days'), 1)}",
        "If VIX is elevated, prefer defined-risk or skip. Do not invent an IV rank unless a history is present.",
    ]
    return "\n".join(lines)


def _flow_pack(fut, signal, tech):
    lines = ["FUTURES:"]
    if fut:
        lines.append(
            f"  LTP {_fmt(fut.get('ltp'))}  chg {_fmt(fut.get('change'))}  OI {_fmt(fut.get('oi'), 0)} "
            f"OI chg {_fmt(fut.get('oi_chg'), 0)}  basis {_fmt(fut.get('basis'))} ({_fmt(fut.get('basis_pct'))}%) "
            f"buildup {fut.get('buildup') or 'n/a'}  source {fut.get('source')}  expiry {fut.get('expiry')}"
        )
    else:
        lines.append("  (no futures quote)")
    if tech:
        lines.append(
            f"SPOT TECH: RSI {_fmt(tech.get('rsi'), 1)}  MACD hist {_fmt(tech.get('macdh'), 2)}  "
            f"ATR {_fmt(tech.get('atr'))}  EMA10 {_fmt(tech.get('close_10_ema'))}  SMA50 {_fmt(tech.get('close_50_sma'))}"
        )
    sig = signal or {}
    lines.append(f"RULE BIAS {sig.get('bias')} score {sig.get('score')} confidence {sig.get('confidence')}")
    for r in sig.get("reasons") or []:
        lines.append(f"  - {r}")
    for s in sig.get("setups") or []:
        lines.append(
            f"SETUP {s.get('kind')} {s.get('instrument')} {s.get('side')} entry={s.get('entry')} "
            f"SL={s.get('stop')} tgt={s.get('target')} RR={s.get('rr')} risk₹={s.get('premium_risk_rs')}"
        )
    return "\n".join(lines)


def _news_pack(pack):
    if not pack:
        return "(no news pack)"
    lines = []
    for it in pack.get("items") or []:
        lines.append(f"- {it.get('when')} {it.get('publisher')}: {it.get('title')}")
    if pack.get("error"):
        lines.append(f"News error: {pack['error']}")
    if pack.get("fii_dii"):
        lines.append(f"FII/DII raw: {str(pack['fii_dii'])[:1200]}")
    return "\n".join(lines) or "(no headlines)"


def instant(symbol, expiry=None):
    """Rule-based signal only (no LLM). Used by the dashboard and the scanner."""
    info = und_meta(symbol)
    symbol = info["symbol"]
    ysym = mk.resolve_symbol(symbol, info.get("exchange"))
    chain = None
    try:
        chain = ch.load(symbol, expiry)
    except Exception:  # noqa: BLE001
        pass
    if chain is None:
        quote = None
        try:
            quote = mk.get_quote(symbol, info.get("exchange") or "NSE")
        except Exception:  # noqa: BLE001
            pass
        spot = (quote or {}).get("price")
        change_pct = (quote or {}).get("change_pct")
        return {"symbol": symbol, "kind": info["kind"], "exchange": info.get("exchange"),
                "chain": {"spot": spot, "underlying": symbol, "source": "fallback", "rows": [], "expiries": []},
                "futures": None, "vix": mk.india_vix(), "tech": None,
                "signal": {"bias": "AVOID", "confidence": 0, "metrics": {"spot": spot}, "setups": [],
                           "error": "Option chain unavailable from exchange",
                           "reasons": ["Option chain unavailable from exchange"], "warnings": ["Chain data unavailable; showing quote only."],
                           "disclaimer": "Educational research only."},
                "_quote_fallback": {"price": spot, "change_pct": change_pct}}
    fut = None
    try:
        fut = ch.load_futures(symbol, chain)
    except Exception:  # noqa: BLE001
        pass
    vix = mk.india_vix()
    tape = {}
    try:
        q = mk.get_quote(symbol, info.get("exchange") or "NSE")
        tape = {"change_pct": q.get("change_pct"), "price": q.get("price")}
    except Exception:  # noqa: BLE001
        pass
    tech = None
    try:
        df = mk.history(ysym, period="1y")
        tech = ind.latest(ind.compute_all(df))
    except Exception:  # noqa: BLE001
        pass
    sig = instant_signal(chain, fut, vix, tech, mk.market_status(), tape)
    return {"symbol": symbol, "kind": info["kind"], "exchange": info.get("exchange"), "chain": chain, "futures": fut,
            "vix": vix, "tech": tech, "tape": tape, "signal": sig}
