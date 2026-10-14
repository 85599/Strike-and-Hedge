"""Load a normalised option chain for any supported underlying and attach BS Greeks."""
import datetime as dt
import math

from . import bse as bse_mod
from . import nse as nse_mod
from .greeks import greeks as bs_greeks, implied_vol, year_fraction
from .nse import IST
from .universe import meta as und_meta


def _num(x):
    try:
        x = float(x)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _attach_greeks(chain, r=0.065):
    spot = _num(chain.get("spot"))
    exp = nse_mod._parse_expiry(chain.get("expiry") or "")
    now = dt.datetime.now(IST)
    if not exp:
        exp = now + dt.timedelta(days=7)
    t = year_fraction(exp, now)
    chain["dte_years"] = t
    chain["dte_days"] = max((exp - now).total_seconds() / 86400, 0)
    if not spot:
        return chain
    for row in chain.get("rows") or []:
        k = _n(row.get("strike"))
        if k is None:
            continue
        for side, kind in (("ce", "CE"), ("pe", "PE")):
            leg = row.get(side)
            if not leg:
                continue
            ltp = _num(leg.get("ltp"))
            iv = _num(leg.get("iv"))
            if iv is not None and iv > 3:  # NSE sends percent
                iv = iv / 100.0
            if iv is None or iv <= 0:
                iv = implied_vol(ltp, spot, k, t, r, kind)
            if iv is None:
                continue
            g = bs_greeks(spot, k, t, r, iv, kind)
            leg["iv_dec"] = iv
            leg["delta"] = round(g["delta"], 4)
            leg["gamma"] = round(g["gamma"], 6)
            leg["theta"] = round(g["theta"], 2)
            leg["vega"] = round(g["vega"], 2)
            if leg.get("iv") is None:
                leg["iv"] = round(iv * 100, 2)
    return chain


def _n(x):
    return _num(x)


def load(symbol, expiry=None):
    info = und_meta(symbol)
    sym = info["symbol"]
    if info["oc"] == "bse":
        scrip_cd = info.get("bse_scrip")
        if not scrip_cd:
            raise LookupError(f"BSE lists no option contracts for {sym}")
        chain = bse_mod.option_chain(sym, scrip_cd=scrip_cd, expiry=expiry)
    else:
        chain = nse_mod.option_chain(sym, kind=info["oc"], expiry=expiry)
    if not chain.get("lot_size"):
        chain["lot_size"] = info.get("lot")
    return _attach_greeks(chain)


def synthetic_futures(chain, r=0.065):
    """Futures level implied by put-call parity on the ATM strike: F = K·e^(−rT) + CE − PE.

    NSE retired /api/quote-derivative along with quote-v2 and last-price-v5, so there is no public
    futures quote left to read. Parity recovers a level, but it recovers it from *last traded*
    option prices rather than mids, so it carries tens of points of error on a wide two-sided
    market. That is fine for a strike-relative stop and target; it is why the caller must not read
    the resulting basis as a directional signal, and why futures OI and buildup stay None rather
    than being invented from option OI.
    """
    rows = chain.get("rows") or []
    spot = _num(chain.get("spot"))
    if not rows or not spot:
        return None
    atm = min(rows, key=lambda r: abs((_num(r.get("strike")) or 0) - spot))
    k = _num(atm.get("strike"))
    ce, pe = atm.get("ce") or {}, atm.get("pe") or {}
    c, p = _num(ce.get("ltp")), _num(pe.get("ltp"))
    if not k or c is None or p is None:
        return None
    t = _num(chain.get("dte_years")) or 0.0
    ltp = k * math.exp(-r * t) + c - p
    dc, dp = _num(ce.get("change")), _num(pe.get("change"))
    chg = dc - dp if dc is not None and dp is not None else None
    prev = ltp - chg if chg is not None else None
    change_pct = chg / prev * 100 if chg is not None and prev else None
    return {
        "expiry": chain.get("expiry"), "ltp": round(ltp, 2),
        "change": round(chg, 2) if chg is not None else None,
        "change_pct": round(change_pct, 3) if change_pct is not None else None,
        "oi": None, "oi_chg": None, "lot_size": chain.get("lot_size"),
        "basis": round(ltp - spot, 2), "basis_pct": round((ltp - spot) / spot * 100, 3),
        "buildup": None, "identifier": None, "source": "put-call parity on ATM strike",
        "synthetic": True,
    }


def load_futures(symbol, chain=None):
    info = und_meta(symbol)
    fut = None
    if info["exchange"] == "NSE":
        try:
            fut = nse_mod.futures_quote(info["symbol"])
        except Exception:  # noqa: BLE001
            fut = None
    if fut:
        if not fut.get("lot_size"):
            fut["lot_size"] = info.get("lot")
        return fut
    if chain is None:
        try:
            chain = load(symbol)
        except Exception:  # noqa: BLE001
            return None
    return synthetic_futures(chain)
