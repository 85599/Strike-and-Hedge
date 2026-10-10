"""Rule-based option-chain metrics. Numbers are computed in code; the LLM only interprets them."""
from __future__ import annotations

import math

R_FREE = 0.065
_SQRT_2PI = math.sqrt(2 * math.pi)


def _n(x):
    try:
        x = float(x)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _leg(row, side):
    return row.get(side) or {}


def _iv_dec(leg):
    """Implied vol as a decimal, tolerating both NSE percent and already-decimal feeds."""
    iv = _n(leg.get("iv_dec"))
    if iv and iv > 0:
        return iv
    raw = _n(leg.get("iv"))
    if not raw or raw <= 0:
        return None
    return raw / 100.0 if raw > 3 else raw


def _gamma(s, k, t, sigma):
    """Black-Scholes gamma, identical to greeks.greeks() but without the rest of the dict."""
    if not sigma or not t or t <= 0 or not s or not k:
        return 0.0
    v = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (R_FREE + 0.5 * sigma * sigma) * t) / v
    return math.exp(-0.5 * d1 * d1) / (_SQRT_2PI * s * v)


def _gex_terms(rows):
    """(strike, kind, iv, oi) for every leg with usable open interest and IV."""
    out = []
    for r in rows:
        k = _n(r.get("strike"))
        if k is None:
            continue
        for side, kind in (("ce", "CE"), ("pe", "PE")):
            leg = _leg(r, side)
            oi, iv = _n(leg.get("oi")) or 0.0, _iv_dec(leg)
            if oi > 0 and iv:
                out.append((k, kind, iv, oi))
    return out


def _gex_at(terms, s, t, lot):
    """Dealer gamma exposure in rupees of delta-hedging flow per 1% move in the underlying.

    Dealer books are not observable. We use the standard convention that dealers are long
    calls and short puts, so call gamma enters positive and put gamma negative."""
    call = put = 0.0
    scale = lot * s * s * 0.01
    for k, kind, iv, oi in terms:
        g = _gamma(s, k, t, iv) * oi * scale
        if kind == "CE":
            call += g
        else:
            put += g
    return call - put, call, put


def _gamma_flip(terms, spot, t, lot, steps=64):
    """Spot level where net dealer gamma crosses zero, nearest to spot; None if there is none.

    Gamma decays to nothing outside the quoted strike range, so the profile is scanned across
    that range rather than a fixed band around spot — a crossing there would be numerical dust."""
    ks = [k for k, _, _, _ in terms]
    lo, hi = min(ks), max(ks)
    if hi <= lo:
        return None
    grid = [lo + (hi - lo) * i / steps for i in range(steps + 1)]
    vals = [_gex_at(terms, p, t, lot)[0] for p in grid]
    if not any(vals):
        return None  # calls and puts cancel everywhere, so there is no level to flip at
    best = None
    for i in range(steps):
        a, b, va, vb = grid[i], grid[i + 1], vals[i], vals[i + 1]
        if (va > 0) == (vb > 0) or vb == 0.0:
            continue
        x, y = a, b
        for _ in range(24):
            m = (x + y) / 2
            fm = _gex_at(terms, m, t, lot)[0]
            if fm == 0.0:
                x = y = m
                break
            if (fm > 0) == (va > 0):
                x = m
            else:
                y = m
        cross = (x + y) / 2
        if best is None or abs(cross - spot) < abs(best - spot):
            best = cross
    return round(best, 2) if best is not None else None


def _gex_levels(terms, spot, t, lot, each_side=8):
    """Per-strike signed GEX for the strikes nearest spot, for the terminal's gamma ladder.

    Bounded by strike count rather than a percent band so the payload stays the same size
    for a 50-point index step and a 20-rupee stock step alike."""
    agg = {}
    scale = lot * spot * spot * 0.01
    for k, kind, iv, oi in terms:
        g = _gamma(spot, k, t, iv) * oi * scale
        a = agg.setdefault(k, {"strike": k, "ce": 0.0, "pe": 0.0})
        a["ce" if kind == "CE" else "pe"] += g if kind == "CE" else -g
    near = sorted(agg, key=lambda k: (abs(k - spot), k))[: each_side * 2 + 1]
    out = []
    for k in sorted(near):
        a = agg[k]
        a["ce"], a["pe"] = round(a["ce"]), round(a["pe"])
        a["net"] = a["ce"] + a["pe"]
        out.append(a)
    return out


def summarise_chain(chain: dict) -> dict:
    """PCR, max pain, OI walls, ATM IV, expected move, dealer gamma, OI buildup."""
    rows = list(chain.get("rows") or [])
    spot = _n(chain.get("spot")) or 0.0
    if not rows or not spot:
        return {"error": "empty chain", "spot": spot}

    ce_oi = pe_oi = ce_vol = pe_vol = ce_oi_chg = pe_oi_chg = 0.0
    pain = []
    walls_ce, walls_pe = [], []
    for r in rows:
        k = _n(r.get("strike"))
        if k is None:
            continue
        ce, pe = _leg(r, "ce"), _leg(r, "pe")
        coi, poi = _n(ce.get("oi")) or 0, _n(pe.get("oi")) or 0
        cvi, pvi = _n(ce.get("volume")) or 0, _n(pe.get("volume")) or 0
        cch, pch = _n(ce.get("oi_chg")) or 0, _n(pe.get("oi_chg")) or 0
        ce_oi += coi
        pe_oi += poi
        ce_vol += cvi
        pe_vol += pvi
        ce_oi_chg += cch
        pe_oi_chg += pch
        walls_ce.append((coi, k, _n(ce.get("ltp")), cch, _n(ce.get("iv"))))
        walls_pe.append((poi, k, _n(pe.get("ltp")), pch, _n(pe.get("iv"))))
        # Max pain: sum of intrinsic * OI at expiry for every strike
        loss = 0.0
        for r2 in rows:
            k2 = _n(r2.get("strike"))
            if k2 is None:
                continue
            loss += max(k - k2, 0) * (_n(_leg(r2, "ce").get("oi")) or 0)
            loss += max(k2 - k, 0) * (_n(_leg(r2, "pe").get("oi")) or 0)
        pain.append((loss, k))

    pain.sort()
    max_pain = pain[0][1] if pain else None
    walls_ce.sort(reverse=True)
    walls_pe.sort(reverse=True)
    call_wall = walls_ce[0][1] if walls_ce else None
    put_wall = walls_pe[0][1] if walls_pe else None

    atm = min(rows, key=lambda r: abs((_n(r.get("strike")) or 0) - spot))
    atm_k = _n(atm.get("strike"))
    atm_ce_leg, atm_pe_leg = _leg(atm, "ce"), _leg(atm, "pe")
    atm_iv = None
    ivs = [x for x in (_n(atm_ce_leg.get("iv")), _n(atm_pe_leg.get("iv"))) if x]
    if ivs:
        atm_iv = sum(ivs) / len(ivs)
    straddle = (_n(atm_ce_leg.get("ltp")) or 0) + (_n(atm_pe_leg.get("ltp")) or 0)
    expected_move_pct = (straddle / spot * 100) if spot and straddle else None

    pcr_oi = (pe_oi / ce_oi) if ce_oi else None
    pcr_vol = (pe_vol / ce_vol) if ce_vol else None

    # OI-change interpretation around ATM ± 4 strikes. Standard buildup classification, applied
    # symmetrically to both legs: OI up + price down = writing (shorts adding), OI up + price up =
    # buying (longs adding), OI down + price up = covering (shorts exiting), OI down + price down =
    # unwinding (longs exiting). `change` is the session change, so early in the day it is noisy.
    nearby = sorted(rows, key=lambda r: abs((_n(r.get("strike")) or 0) - spot))[:9]
    buckets = ("writing", "buying", "covering", "unwinding")
    flow = {(side, b): 0.0 for side in ("ce", "pe") for b in buckets}
    for r in nearby:
        for side in ("ce", "pe"):
            leg = _leg(r, side)
            ch, px = _n(leg.get("oi_chg")) or 0.0, _n(leg.get("change")) or 0.0
            if ch == 0:
                continue
            bucket = ("writing" if px <= 0 else "buying") if ch > 0 else ("covering" if px > 0 else "unwinding")
            flow[(side, bucket)] += abs(ch)

    lot = int(chain.get("lot_size") or 1)
    t = _n(chain.get("dte_years")) or 7 / 365.0
    terms = _gex_terms(rows)
    gex, gex_call, gex_put = _gex_at(terms, spot, t, lot)
    flip = _gamma_flip(terms, spot, t, lot) if terms else None
    if not terms:
        regime = None
    elif gex == 0:
        regime = "flat gamma"
    elif flip is not None:
        regime = "long gamma" if spot >= flip else "short gamma"
    elif gex > 0:
        regime = "long gamma"
    else:
        regime = "short gamma"

    iv_smile = []
    for r in sorted(rows, key=lambda x: _n(x.get("strike")) or 0):
        k = _n(r.get("strike"))
        if k is None:
            continue
        ce, pe = _leg(r, "ce"), _leg(r, "pe")
        iv_smile.append({
            "strike": k,
            "ce_iv": _n(ce.get("iv")), "pe_iv": _n(pe.get("iv")),
            "ce_oi": _n(ce.get("oi")) or 0, "pe_oi": _n(pe.get("oi")) or 0,
        })

    return {
        "spot": spot,
        "expiry": chain.get("expiry"),
        "pcr_oi": pcr_oi,
        "pcr_vol": pcr_vol,
        "ce_oi": ce_oi, "pe_oi": pe_oi,
        "ce_vol": ce_vol, "pe_vol": pe_vol,
        "ce_oi_chg": ce_oi_chg, "pe_oi_chg": pe_oi_chg,
        "max_pain": max_pain,
        "spot_vs_max_pain": (spot - max_pain) if max_pain else None,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "call_walls": [{"strike": k, "oi": oi, "ltp": ltp, "oi_chg": ch} for oi, k, ltp, ch, _ in walls_ce[:5]],
        "put_walls": [{"strike": k, "oi": oi, "ltp": ltp, "oi_chg": ch} for oi, k, ltp, ch, _ in walls_pe[:5]],
        "atm_strike": atm_k,
        "atm_ce": _compact(atm_ce_leg),
        "atm_pe": _compact(atm_pe_leg),
        "atm_iv": atm_iv,
        "atm_straddle": straddle or None,
        "expected_move_pct": expected_move_pct,
        "pe_writing": flow[("pe", "writing")], "ce_writing": flow[("ce", "writing")],
        "pe_buying": flow[("pe", "buying")], "ce_buying": flow[("ce", "buying")],
        "pe_covering": flow[("pe", "covering")], "ce_covering": flow[("ce", "covering")],
        "pe_unwinding": flow[("pe", "unwinding")], "ce_unwinding": flow[("ce", "unwinding")],
        # GEX is rupees of dealer delta-hedging flow per 1% move in the underlying.
        "gex": round(gex), "gex_call": round(gex_call), "gex_put": round(-gex_put),
        "gamma_flip": flip, "gamma_regime": regime,
        "gex_levels": _gex_levels(terms, spot, t, lot),
        "iv_smile": iv_smile,
        "lot_size": chain.get("lot_size"),
        "underlying": chain.get("underlying"),
    }


def _compact(leg):
    if not leg:
        return {}
    keys = ("ltp", "change", "iv", "oi", "oi_chg", "volume", "bid", "ask", "delta", "gamma", "theta", "vega")
    return {k: _n(leg.get(k)) for k in keys if leg.get(k) is not None}


def pcr_label(pcr):
    if pcr is None:
        return "n/a"
    if pcr >= 1.3:
        return "put-heavy (often constructive for bulls, until it isn't)"
    if pcr >= 1.0:
        return "mild put bias"
    if pcr >= 0.8:
        return "balanced"
    if pcr >= 0.6:
        return "mild call bias"
    return "call-heavy (often constructive for bears)"
