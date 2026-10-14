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


def score_bias(metrics: dict, fut: dict | None, vix: dict | None, tech: dict | None,
               tape: dict | None = None) -> dict:
    s = 0
    reasons = []
    flow, flow_reasons = 0, []
    pcr = _n(metrics.get("pcr_oi"))
    spot = _n(metrics.get("spot")) or 0
    mp = _n(metrics.get("max_pain"))
    put_wall = _n(metrics.get("put_wall"))
    call_wall = _n(metrics.get("call_wall"))
    pe_w, ce_w = _n(metrics.get("pe_writing")) or 0, _n(metrics.get("ce_writing")) or 0

    # The tape itself, measured in ATRs of the underlying. OI structure is sticky: it says where
    # shorts are positioned, not what price is doing today, so without this term a sharp session
    # move still scores ~0 and prints RANGE. 0.3% means something different on NIFTY than on a
    # 3%-vol name, hence ATR units rather than raw percent.
    chg = _n((tape or {}).get("change_pct"))
    atr = _n((tech or {}).get("atr"))
    if chg is not None:
        day_pct = abs(atr) / spot * 100 if atr and spot else None
        if day_pct:
            units = abs(chg) / day_pct
            mag = next((m for cut, m in ((0.6, 3), (0.3, 2), (0.15, 1)) if units >= cut), 0)
        else:
            units, mag = None, next((m for cut, m in ((0.75, 3), (0.35, 2), (0.15, 1)) if abs(chg) >= cut), 0)
        if mag:
            s += mag if chg > 0 else -mag
            move = f"{chg:+.2f}%"
            tail = f" — {units:.2f} ATR of a {day_pct:.2f}% daily range" if units is not None else ""
            reasons.append(f"Underlying {move} today{tail}")

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
        flow += 2
        flow_reasons.append("Net put writing near ATM (shorts defending support)")
    elif ce_w > pe_w * 1.25 and ce_w > 0:
        flow -= 2
        flow_reasons.append("Net call writing near ATM (shorts defending resistance)")

    # Longs on the same strikes. Put *buying* is the fingerprint of a sell-off — a premium that
    # rises while OI rises — but the old score only looked at writing, so a real down day moved it
    # not at all. Weaker evidence than writing (no margin locked up), so it counts half as much.
    ce_b, pe_b = _n(metrics.get("ce_buying")) or 0, _n(metrics.get("pe_buying")) or 0
    ce_u, pe_u = _n(metrics.get("ce_unwinding")) or 0, _n(metrics.get("pe_unwinding")) or 0
    ce_c, pe_c = _n(metrics.get("ce_covering")) or 0, _n(metrics.get("pe_covering")) or 0
    bull_longs, bear_longs = ce_b + pe_u + pe_c, pe_b + ce_u + ce_c
    if bull_longs > bear_longs * 1.25 and bull_longs > 0:
        flow += 1
        flow_reasons.append("Net call buying / put unwinding near ATM")
    elif bear_longs > bull_longs * 1.25 and bear_longs > 0:
        flow -= 1
        flow_reasons.append("Net put buying / call unwinding near ATM")

    # One idea, not three: writing and buying on the same move usually say the same thing, so the
    # block is capped instead of stacking to -6 on an ordinary directional day.
    capped = max(-3, min(3, flow))
    if capped:
        s += capped
        reasons.extend(flow_reasons)

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
    vix_chg = _n((vix or {}).get("change_pct"))
    # A VIX bid alongside a falling underlying is the same direction being paid for twice, so it
    # confirms rather than doubles the tape term.
    if vix_chg is not None and chg is not None:
        if vix_chg >= 3 and chg < -0.1:
            s -= 1
            reasons.append(f"India VIX {vix_chg:+.1f}% while the underlying sells off (risk-off confirmation)")
        elif vix_chg <= -3 and chg > 0.1:
            s += 1
            reasons.append(f"India VIX {vix_chg:+.1f}% on an up session (volatility unwinding)")
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


def _spread_stop(credit, width, d_spot, short_leg, hedge_leg):
    """What the spread costs to buy back once spot has travelled to the short strike.

    Priced off the vertical's own delta and gamma, so the stop widens for a near-money short and
    tightens for a far-out one. None when either leg carries no delta, since there is then no
    honest way to convert an underlying move into a premium move.
    """
    ds, dh = abs(_n(short_leg.get("delta")) or 0.0), abs(_n(hedge_leg.get("delta")) or 0.0)
    nd = ds - dh
    if nd <= 0:
        return None
    gs, gh = abs(_n(short_leg.get("gamma")) or 0.0), abs(_n(hedge_leg.get("gamma")) or 0.0)
    risk = nd * d_spot + 0.5 * max(gs - gh, 0.0) * d_spot * d_spot
    return min(credit + risk, width)


def _credit_spread(chain, row, opt, steps, lot, expiry, und, scored):
    """Defined-risk credit spread: sell `row`, buy the strike `steps` further out. No naked shorts."""
    step = _step(chain)
    k = _n(row.get("strike"))
    spot = _n(chain.get("spot"))
    if not step or k is None or not spot:
        return None
    hk = k + steps * step if opt == "CE" else k - steps * step
    hrow = _row_at(chain, hk)
    if not hrow:
        return None
    key = "ce" if opt == "CE" else "pe"
    short_leg, hedge_leg = row.get(key) or {}, hrow.get(key) or {}
    sell, buy = _n(short_leg.get("ltp")), _n(hedge_leg.get("ltp"))
    if not sell or buy is None:
        return None
    credit = round(sell - buy, 2)
    width = abs(hk - k)
    # Both ends of the credit range are degenerate. Above half the width the short leg is at or
    # inside the money, where the spread already trades near its expiry value and "buy it back at
    # half the credit" cannot happen. Below 2% of the width it is dust: the max profit is a rounding
    # error against the margin the short leg locks up, and half of it does not survive paisa
    # rounding, which would print a target sitting on top of the entry.
    if credit > width * 0.5 or credit < max(width * 0.02, 0.1):
        return None
    d_spot = abs(k - spot)
    stop = _spread_stop(credit, width, d_spot, short_leg, hedge_leg)
    # A stop this tight next to the credit is model noise, not a level anyone would be stopped at.
    if stop is None or stop - credit < credit * 0.2:
        return None
    max_loss = width - credit
    target = round(credit * 0.5, 2)
    stop = round(stop, 2)
    return {
        "kind": f"SELL_{opt}_SPREAD", "instrument": f"{und} {int(k)}/{int(hk)} {opt} credit spread {expiry}",
        "expiry": expiry, "strike": k, "option_type": opt, "side": "SELL", "entry": credit, "stop": stop, "target": target,
        "width": width,
        "legs": [{"action": "SELL", "instrument": f"{und} {int(k)} {opt}", "ltp": sell},
                 {"action": "BUY", "instrument": f"{und} {int(hk)} {opt} (hedge)", "ltp": buy}],
        "exit": (f"Net credit \u20b9{credit:.2f} on a \u20b9{width:.0f} wide spread. Buy it back at \u20b9{target:.2f} "
                 f"to keep half the credit, or at \u20b9{stop:.2f} - about a spot touch of {k:.0f}, where the short "
                 f"leg is put to the test. Close before expiry-day gamma and any event. "
                 f"The hedge leg caps the loss at \u20b9{max_loss * lot:,.0f} per lot even if the stop is skipped."),
        "rr": _rr(credit, stop, target, "SELL"), "lot_size": lot, "premium_risk_rs": round((stop - credit) * lot, 2),
        "max_loss_rs": round(max_loss * lot, 2), "max_profit_rs": round(credit * lot, 2),
        "iv": _n(short_leg.get("iv")), "delta": _n(short_leg.get("delta")),
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
        # Defined-risk premium selling just outside the OI walls (iron-condor style, two independent
        # spreads). Each side walks outward past strikes whose spread is degenerate — deep in the
        # money, unquoted, or with a stop tight enough to be noise — but stops early rather than
        # selling so far out that the credit is not worth the margin.
        rows = sorted(chain.get("rows") or [], key=lambda r: _n(r.get("strike")) or 0)
        call_wall, put_wall = _n(metrics.get("call_wall")), _n(metrics.get("put_wall"))
        up_floor, dn_ceiling = spot * 1.008, spot * 0.992
        if call_wall and call_wall >= up_floor:
            up_floor = call_wall
        if put_wall and put_wall <= dn_ceiling:
            dn_ceiling = put_wall
        ce_rows = [r for r in rows if (_n(r.get("strike")) or 0) >= up_floor][:6]
        pe_rows = [r for r in reversed(rows) if (_n(r.get("strike")) or 0) <= dn_ceiling][:6]
        for side_rows, opt in ((ce_rows, "CE"), (pe_rows, "PE")):
            for row in side_rows:
                s = _credit_spread(chain, row, opt, 2, lot, expiry, und, scored)
                if s:
                    setups.append(s)
                    break

    if not setups:
        setups.append({
            "kind": "AVOID", "instrument": und, "side": "HOLD", "entry": None, "stop": None, "target": None,
            "exit": "No new F&O risk until PCR, VIX and OI walls agree." if bias == "AVOID" else
                    "No clean level-based setup in this chain. Wait.",
            "rationale": "; ".join(scored["reasons"][:5]),
        })
    return setups


def instant_signal(chain: dict, fut: dict | None = None, vix: dict | None = None, tech: dict | None = None,
                   market: dict | None = None, tape: dict | None = None) -> dict:
    metrics = summarise_chain(chain)
    if metrics.get("error"):
        return {"bias": "AVOID", "confidence": 0, "metrics": metrics, "setups": [], "error": metrics["error"]}
    scored = score_bias(metrics, fut, vix, tech, tape)
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
