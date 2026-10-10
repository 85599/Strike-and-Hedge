"""Rule-based option-chain metrics. Numbers are computed in code; the LLM only interprets them."""
from __future__ import annotations


def _n(x):
    try:
        x = float(x)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _leg(row, side):
    return row.get(side) or {}


def summarise_chain(chain: dict) -> dict:
    """PCR, max pain, OI walls, ATM IV, expected move, GEX-style gamma, OI buildup."""
    rows = list(chain.get("rows") or [])
    spot = _n(chain.get("spot")) or 0.0
    if not rows or not spot:
        return {"error": "empty chain", "spot": spot}

    ce_oi = pe_oi = ce_vol = pe_vol = ce_oi_chg = pe_oi_chg = 0.0
    pain = []
    walls_ce, walls_pe = [], []
    gex = 0.0
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
        # Crude GEX: call gamma positive for dealers short calls? Use OI * sign vs spot
        # Dealer-short-gamma approximation: calls above spot add negative GEX if dealers short,
        # puts below spot add negative GEX. We report call_oi-weighted distance instead.
        gex += coi * max(spot - k, 0) - poi * max(k - spot, 0)

    pain.sort()
    max_pain = pain[0][1] if pain else None
    walls_ce.sort(reverse=True)
    walls_pe.sort(reverse=True)
    call_wall = walls_ce[0][1] if walls_ce else None
    put_wall = walls_pe[0][1] if walls_pe else None

    atm = min(rows, key=lambda r: abs((_n(r.get("strike")) or 0) - spot))
    atm_k = _n(atm.get("strike"))
    ce, pe = _leg(atm, "ce"), _leg(atm, "pe")
    atm_iv = None
    ivs = [x for x in (_n(ce.get("iv")), _n(pe.get("iv"))) if x]
    if ivs:
        atm_iv = sum(ivs) / len(ivs)
    straddle = (_n(ce.get("ltp")) or 0) + (_n(pe.get("ltp")) or 0)
    expected_move_pct = (straddle / spot * 100) if spot and straddle else None

    pcr_oi = (pe_oi / ce_oi) if ce_oi else None
    pcr_vol = (pe_vol / ce_vol) if ce_vol else None

    # OI-change interpretation around ATM ± 4 strikes
    nearby = sorted(rows, key=lambda r: abs((_n(r.get("strike")) or 0) - spot))[:9]
    pe_write = ce_write = pe_cover = ce_cover = 0.0
    for r in nearby:
        ce, pe = _leg(r, "ce"), _leg(r, "pe")
        cch, pch = _n(ce.get("oi_chg")) or 0, _n(pe.get("oi_chg")) or 0
        cltp, pltp = _n(ce.get("change")) or 0, _n(pe.get("change")) or 0
        if pch > 0 and pltp <= 0:
            pe_write += pch
        if pch < 0:
            pe_cover += -pch
        if cch > 0 and cltp <= 0:
            ce_write += cch
        if cch < 0:
            ce_cover += -cch

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
        "atm_ce": _compact(ce),
        "atm_pe": _compact(pe),
        "atm_iv": atm_iv,
        "atm_straddle": straddle or None,
        "expected_move": straddle or None,
        "expected_move_pct": expected_move_pct,
        "pe_writing": pe_write, "ce_writing": ce_write,
        "pe_covering": pe_cover, "ce_covering": ce_cover,
        "gex_proxy": gex,
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
