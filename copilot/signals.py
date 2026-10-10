"""Deterministic F&O setups: Buy/Sell CE/PE/Futures with entry, stop, target, exit.
No LLM. Educational research — not a broker signal service."""
from __future__ import annotations

from .analytics import pcr_label, summarise_chain


def _n(x):
    try:
        x = float(x)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _rr(entry, stop, target, side):
    if not all(v is not None for v in (entry, stop, target)):
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    reward = (target - entry) if side == "BUY" else (entry - target)
    return round(reward / risk, 2) if reward else None


def score_bias(metrics: dict, fut: dict | None, vix: dict | None, tech: dict | None) -> dict:
    s = 0
    reasons = []
    pcr = _n(metrics.get("pcr_oi"))
    spot = _n(metrics.get("spot")) or 0
    mp = _n(metrics.get("max_pain"))
    put_wall = _n(metrics.get("put_wall"))
    call_wall = _n(metrics.get("call_wall"))
    pe_w, ce_w = _n(metrics.get("pe_writing")) or 0, _n(metrics.get("ce_writing")) or 0

    if pcr is not None:
        if pcr >= 1.25:
            s += 2
            reasons.append(f"PCR OI {pcr:.2f} is put-heavy")
        elif pcr <= 0.7:
            s -= 2
            reasons.append(f"PCR OI {pcr:.2f} is call-heavy")
        else:
            reasons.append(f"PCR OI {pcr:.2f} is {pcr_label(pcr)}")

    if mp and spot:
        if spot > mp * 1.001:
            s += 1
            reasons.append(f"Spot {spot:.2f} is above max pain {mp:.0f}")
        elif spot < mp * 0.999:
            s -= 1
            reasons.append(f"Spot {spot:.2f} is below max pain {mp:.0f}")

    # Walls: bounce off support / reject at resistance, or break through them.
    if put_wall and spot:
        if spot < put_wall * 0.998:
            s -= 1
            reasons.append(f"Spot {spot:.2f} broke below put OI wall {put_wall:.0f}")
        elif spot <= put_wall * 1.006:
            s += 1
            reasons.append(f"Spot is sitting on put OI wall {put_wall:.0f} (support)")
    if call_wall and spot:
        if spot > call_wall * 1.002:
            s += 1
            reasons.append(f"Spot {spot:.2f} broke above call OI wall {call_wall:.0f}")
        elif spot >= call_wall * 0.994:
            s -= 1
            reasons.append(f"Spot is pressing call OI wall {call_wall:.0f} (resistance)")

    if pe_w > ce_w * 1.25 and pe_w > 0:
        s += 2
        reasons.append("Net put writing near ATM (shorts defending support)")
    elif ce_w > pe_w * 1.25 and ce_w > 0:
        s -= 2
        reasons.append("Net call writing near ATM (shorts defending resistance)")

    if fut:
        basis = _n(fut.get("basis_pct"))
        if basis is not None:
            if basis > 0.08:
                s += 1
                reasons.append(f"Futures at premium {basis:+.2f}%")
            elif basis < -0.08:
                s -= 1
                reasons.append(f"Futures at discount {basis:+.2f}%")
        bld = (fut.get("buildup") or "").lower()
        if bld == "long buildup":
            s += 2
            reasons.append("Futures long buildup")
        elif bld == "short buildup":
            s -= 2
            reasons.append("Futures short buildup")
        elif bld == "short covering":
            s += 1
            reasons.append("Futures short covering")
        elif bld == "long unwinding":
            s -= 1
            reasons.append("Futures long unwinding")

    if tech:
        rsi = _n(tech.get("rsi"))
        macdh = _n(tech.get("macdh"))
        if rsi is not None:
            if rsi >= 60:
                s += 1
                reasons.append(f"RSI {rsi:.0f} is strong")
            elif rsi <= 40:
                s -= 1
                reasons.append(f"RSI {rsi:.0f} is weak")
        if macdh is not None:
            s += 1 if macdh > 0 else -1
            reasons.append("MACD histogram " + ("positive" if macdh > 0 else "negative"))

    vix_px = _n((vix or {}).get("price"))
    avoid = False
    if vix_px and vix_px >= 20:
        avoid = abs(s) < 3
        reasons.append(f"India VIX {vix_px:.1f} is elevated — size down / skip if the tape is two-sided")

    if s >= 3:
        bias = "BULLISH"
    elif s <= -3:
        bias = "BEARISH"
    else:
        bias = "RANGE"
    if avoid:
        bias = "AVOID"
    conf = min(92, 42 + abs(s) * 8)
    return {"bias": bias, "score": s, "confidence": conf, "reasons": reasons}


def _step(chain):
    ks = sorted({_n(r.get("strike")) for r in chain.get("rows") or [] if _n(r.get("strike")) is not None})
    gaps = [b - a for a, b in zip(ks, ks[1:]) if b > a]
    return min(gaps) if gaps else None


def _row_at(chain, strike):
    for r in chain.get("rows") or []:
        if _n(r.get("strike")) == strike:
            return r
    return None


def _levels_for_buy(ltp, delta, spot, atr, bias, metrics):
    """Underlying stop/target (ATR + OI walls) translated to premium through delta.
    Falls back to fixed premium percentages when delta/ATR are missing."""
    put_wall, call_wall = _n(metrics.get("put_wall")), _n(metrics.get("call_wall"))
    d = abs(delta) if delta else None
    if atr and spot and d:
        stop_d, tgt_d = 0.6 * atr, 1.2 * atr
        if bias == "BULLISH":
            u_stop, u_tgt = spot - stop_d, spot + tgt_d
            if call_wall and spot < call_wall < u_tgt:
                u_tgt = max(call_wall, spot + 0.6 * atr)
        else:
            u_stop, u_tgt = spot + stop_d, spot - tgt_d
            if put_wall and u_tgt < put_wall < spot:
                u_tgt = min(put_wall, spot - 0.6 * atr)
        stop = max(ltp - d * abs(spot - u_stop), ltp * 0.5)
        target = ltp + d * abs(u_tgt - spot)
        return round(ltp, 2), round(stop, 2), round(target, 2), round(u_stop, 2), round(u_tgt, 2)
    stop, target = ltp * 0.65, ltp * 1.75
    return round(ltp, 2), round(stop, 2), round(target, 2), None, None


def _buy_option_setup(chain, metrics, scored, row, opt, bias, tech, lot, expiry, und):
    leg = (row.get("ce") if opt == "CE" else row.get("pe")) or {}
    ltp, strike = _n(leg.get("ltp")), _n(row.get("strike"))
    if not ltp or ltp <= 0 or strike is None:
        return None
    spot = _n(metrics.get("spot")) or 0
    atr = _n((tech or {}).get("atr"))
    entry, stop, target, u_stop, u_tgt = _levels_for_buy(ltp, _n(leg.get("delta")), spot, atr, bias, metrics)
    wall = _n(metrics.get("put_wall")) if opt == "CE" else _n(metrics.get("call_wall"))
    wall_txt = (f" or spot closes {'below' if opt == 'CE' else 'above'} the {'put' if opt == 'CE' else 'call'} OI wall {wall:.0f}"
                if wall else "")
    u_txt = f" Underlying invalidation {u_stop:.0f}, first objective {u_tgt:.0f}." if u_stop else ""
    exit_txt = (f"Book ~50% at \u20b9{target:.2f}, trail the rest to cost. Hard exit if premium trades through "
                f"\u20b9{stop:.2f}{wall_txt}. Do not average down.{u_txt}")
    return {
        "kind": f"BUY_{opt}", "instrument": f"{und} {int(strike)} {opt} {expiry}", "expiry": expiry, "strike": strike,
        "option_type": opt, "side": "BUY", "entry": entry, "stop": stop, "target": target, "exit": exit_txt,
        "rr": _rr(entry, stop, target, "BUY"), "lot_size": lot, "premium_risk_rs": round(abs(entry - stop) * lot, 2),
        "max_loss_rs": round(entry * lot, 2), "iv": _n(leg.get("iv")), "delta": _n(leg.get("delta")),
        "underlying_stop": u_stop, "underlying_target": u_tgt, "rationale": "; ".join(scored["reasons"][:4]),
    }


def _credit_spread(chain, row, opt, steps, lot, expiry, und, scored):
    """Defined-risk credit spread: sell `row`, buy the strike `steps` further out. No naked shorts."""
    step = _step(chain)
    k = _n(row.get("strike"))
    if not step or k is None:
        return None
    hk = k + steps * step if opt == "CE" else k - steps * step
    hrow = _row_at(chain, hk)
    if not hrow:
        return None
    key = "ce" if opt == "CE" else "pe"
    sell, buy = _n((row.get(key) or {}).get("ltp")), _n((hrow.get(key) or {}).get("ltp"))
    if not sell or buy is None:
        return None
    credit = round(sell - buy, 2)
    width = abs(hk - k)
    if credit <= 0 or credit >= width:
        return None
    stop, target = round(credit * 1.6, 2), round(credit * 0.4, 2)
    return {
        "kind": f"SELL_{opt}_SPREAD", "instrument": f"{und} {int(k)}/{int(hk)} {opt} credit spread {expiry}",
        "expiry": expiry, "strike": k, "option_type": opt, "side": "SELL", "entry": credit, "stop": stop, "target": target,
        "legs": [{"action": "SELL", "instrument": f"{und} {int(k)} {opt}", "ltp": sell},
                 {"action": "BUY", "instrument": f"{und} {int(hk)} {opt} (hedge)", "ltp": buy}],
        "exit": (f"Net credit \u20b9{credit:.2f}. Buy the spread back at \u20b9{target:.2f} (keep ~60%) or if it costs "
                 f"\u20b9{stop:.2f}. Close before expiry-day gamma and any event. The hedge leg caps loss at "
                 f"\u20b9{(width - credit) * lot:,.0f} per lot."),
        "rr": _rr(credit, stop, target, "SELL"), "lot_size": lot, "premium_risk_rs": round((stop - credit) * lot, 2),
        "max_loss_rs": round((width - credit) * lot, 2), "max_profit_rs": round(credit * lot, 2),
        "iv": _n((row.get(key) or {}).get("iv")), "delta": _n((row.get(key) or {}).get("delta")),
        "rationale": "; ".join(scored["reasons"][:4]),
    }


def _futures_setup(fut, metrics, scored, bias, tech, lot, und, expiry):
    ltp = _n(fut.get("ltp"))
    if not ltp:
        return None
    atr = _n((tech or {}).get("atr")) or ltp * 0.008
    put_wall, call_wall = _n(metrics.get("put_wall")), _n(metrics.get("call_wall"))
    long_ = bias == "BULLISH"
    # Walls are used only when they sit on the correct side of entry and are not absurdly far.
    if long_:
        stop = put_wall if put_wall and ltp - 1.5 * atr <= put_wall < ltp else ltp - 0.8 * atr
        target = call_wall if call_wall and ltp + 0.6 * atr < call_wall <= ltp + 2.5 * atr else ltp + 1.6 * atr
        side, kind = "BUY", "FUT_LONG"
        exit_txt = "Exit on a closing break of the stop, or if futures buildup flips to long unwinding. Trail to cost after 1R."
    else:
        stop = call_wall if call_wall and ltp < call_wall <= ltp + 1.5 * atr else ltp + 0.8 * atr
        target = put_wall if put_wall and ltp - 2.5 * atr <= put_wall < ltp - 0.6 * atr else ltp - 1.6 * atr
        side, kind = "SELL", "FUT_SHORT"
        exit_txt = "Cover on a closing break of the stop, or if buildup flips to short covering. Trail to cost after 1R."
    entry, stop, target = round(ltp, 2), round(stop, 2), round(target, 2)
    return {
        "kind": kind, "instrument": f"{und} FUT {fut.get('expiry') or expiry}", "expiry": fut.get("expiry") or expiry,
        "strike": None, "option_type": "FUT", "side": side, "entry": entry, "stop": stop, "target": target,
        "exit": exit_txt, "rr": _rr(entry, stop, target, side), "lot_size": lot,
        "premium_risk_rs": round(abs(entry - stop) * lot, 2), "rationale": "; ".join(scored["reasons"][:4]),
    }


def _pick_strike(chain, spot, bias):
    """Slightly OTM single-leg buy: the first strike beyond ATM in the trade direction."""
    rows = sorted(chain.get("rows") or [], key=lambda r: abs((_n(r.get("strike")) or 0) - spot))
    if not rows:
        return None
    atm = rows[0]
    if bias == "BULLISH":
        otm = sorted([r for r in rows if (_n(r.get("strike")) or 0) > spot], key=lambda r: _n(r.get("strike")))
        return (otm[0] if otm else atm), "CE"
    otm = sorted([r for r in rows if (_n(r.get("strike")) or 0) < spot], key=lambda r: -(_n(r.get("strike")) or 0))
    return (otm[0] if otm else atm), "PE"


def build_setups(chain: dict, metrics: dict, scored: dict, fut: dict | None = None, tech: dict | None = None) -> list:
    spot = _n(metrics.get("spot")) or _n(chain.get("spot")) or 0
    expiry = chain.get("expiry") or ""
    und = chain.get("underlying") or ""
    lot = int(chain.get("lot_size") or 1)
    bias = scored["bias"]
    setups = []

    if bias in ("BULLISH", "BEARISH"):
        picked = _pick_strike(chain, spot, bias)
        if picked:
            row, opt = picked
            s = _buy_option_setup(chain, metrics, scored, row, opt, bias, tech, lot, expiry, und)
            if s:
                setups.append(s)
        if fut and _n(fut.get("ltp")):
            s = _futures_setup(fut, metrics, scored, bias, tech, lot, und, expiry)
            if s:
                setups.append(s)
    elif bias == "RANGE":
        # Defined-risk premium selling just outside the OI walls (iron-condor style, two independent spreads).
        rows = sorted(chain.get("rows") or [], key=lambda r: _n(r.get("strike")) or 0)
        call_wall, put_wall = _n(metrics.get("call_wall")), _n(metrics.get("put_wall"))
        up_floor, dn_ceiling = spot * 1.008, spot * 0.992
        if call_wall and call_wall >= up_floor:
            up_floor = call_wall
        if put_wall and put_wall <= dn_ceiling:
            dn_ceiling = put_wall
        ce_row = next((r for r in rows if (_n(r.get("strike")) or 0) >= up_floor), None)
        pe_row = next((r for r in reversed(rows) if (_n(r.get("strike")) or 0) <= dn_ceiling), None)
        for row, opt in ((ce_row, "CE"), (pe_row, "PE")):
            if row:
                s = _credit_spread(chain, row, opt, 2, lot, expiry, und, scored)
                if s:
                    setups.append(s)

    if not setups:
        setups.append({
            "kind": "AVOID", "instrument": und, "side": "HOLD", "entry": None, "stop": None, "target": None,
            "exit": "No new F&O risk until PCR, VIX and OI walls agree." if bias == "AVOID" else
                    "No clean level-based setup in this chain. Wait.",
            "rationale": "; ".join(scored["reasons"][:5]),
        })
    return setups


def instant_signal(chain: dict, fut: dict | None = None, vix: dict | None = None, tech: dict | None = None,
                   market: dict | None = None) -> dict:
    metrics = summarise_chain(chain)
    if metrics.get("error"):
        return {"bias": "AVOID", "confidence": 0, "metrics": metrics, "setups": [], "error": metrics["error"]}
    scored = score_bias(metrics, fut, vix, tech)
    setups = build_setups(chain, metrics, scored, fut, tech)
    primary = next((s for s in setups if s.get("kind") != "AVOID"), setups[0] if setups else None)
    warnings = []
    dte = chain.get("dte_days")
    if dte is not None and dte < 1:
        warnings.append("Expiry day: gamma and theta are extreme. Premiums can move 50%+ in minutes; size down or skip.")
    elif dte is not None and dte < 2:
        warnings.append("Less than 2 days to expiry: theta decay is steep for option buyers.")
    if market and market.get("state") in ("closed",):
        warnings.append("Market is closed: this uses the last available chain. Re-check levels after the open.")
    if market and market.get("state") == "preopen":
        warnings.append("Pre-open: option prices are not reliable yet.")
    for s in setups:
        if s.get("rr") is not None and s["rr"] < 1.0 and s["kind"] != "AVOID" and "SPREAD" not in s["kind"]:
            warnings.append(f"{s['kind']}: reward/risk is only {s['rr']:.2f}. Skip unless you have an extra edge.")
    return {
        "bias": scored["bias"], "score": scored["score"], "confidence": scored["confidence"],
        "reasons": scored["reasons"], "metrics": {k: metrics[k] for k in metrics if k != "iv_smile"},
        "setups": setups, "primary": primary, "warnings": warnings,
        "disclaimer": "Educational research only. Not investment advice. Options can expire worthless; futures can gap through stops.",
    }
