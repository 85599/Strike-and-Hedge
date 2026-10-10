"""Load a normalised option chain for any supported underlying and attach BS Greeks."""
import datetime as dt

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
        chain = bse_mod.option_chain(sym, scrip_cd=info.get("bse_scrip") or 1, expiry=expiry)
    else:
        chain = nse_mod.option_chain(sym, kind=info["oc"], expiry=expiry)
    if not chain.get("lot_size"):
        chain["lot_size"] = info.get("lot")
    return _attach_greeks(chain)


def load_futures(symbol):
    info = und_meta(symbol)
    if info["exchange"] != "NSE":
        return None
    try:
        fut = nse_mod.futures_quote(info["symbol"])
    except Exception:
        return None
    if fut and not fut.get("lot_size"):
        fut["lot_size"] = info.get("lot")
    return fut
