"""BSE Sensex / Bankex option chain (unofficial). Best-effort; NSE indices remain the primary feed."""
import threading
import time

import requests

from .nse import UA, _num

BASE = "https://api.bseindia.com"


class BSEClient:
    def __init__(self):
        self.s, self.t, self.lock = None, 0.0, threading.Lock()

    def _fresh(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA, "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
        })
        s.get("https://www.bseindia.com/", timeout=10)
        return s

    def get(self, path, params=None):
        with self.lock:
            if self.s is None or time.time() - self.t > 240:
                self.s, self.t = self._fresh(), time.time()
            s = self.s
        r = s.get(BASE + path, params=params, timeout=12)
        if r.status_code in (401, 403):
            with self.lock:
                self.s, self.t = self._fresh(), time.time()
                s = self.s
            r = s.get(BASE + path, params=params, timeout=12)
        r.raise_for_status()
        return r.json()


BSE = BSEClient()


def expiries(scrip_cd=1):
    j = BSE.get("/BseIndiaAPI/api/ddlExpiry_new/w", {"DerivType": 2, "scrip_cd": scrip_cd})
    # payload shapes vary; collect strings that look like dates
    out = []
    blob = j if isinstance(j, list) else (j.get("Table") or j.get("data") or j.get("Expiry") or [])
    if isinstance(j, dict) and not blob:
        blob = j
    if isinstance(blob, dict):
        blob = blob.get("Table") or blob.get("data") or list(blob.values())
    for it in blob or []:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            out.append(it.get("Expiry") or it.get("expiry") or it.get("ExpiryDate") or it.get("Value") or it.get("Text"))
    return [x for x in out if x]


def _leg(row, prefix):
    return {
        "oi": _num(row.get(prefix + "OI") or row.get(prefix + "OpenInterest") or row.get("CallOI" if prefix == "CE" else "PutOI")),
        "oi_chg": _num(row.get(prefix + "OIChange") or row.get(prefix + "ChangeInOI")),
        "volume": _num(row.get(prefix + "Volume") or row.get(prefix + "Vol")),
        "iv": _num(row.get(prefix + "IV") or row.get(prefix + "ImpliedVolatility")),
        "ltp": _num(row.get(prefix + "LTP") or row.get(prefix + "LastPrice") or row.get(prefix + "Last")),
        "change": _num(row.get(prefix + "Change")),
        "bid": _num(row.get(prefix + "Bid") or row.get(prefix + "BuyPrice")),
        "ask": _num(row.get(prefix + "Ask") or row.get(prefix + "SellPrice")),
    }


def option_chain(symbol="SENSEX", scrip_cd=1, expiry=None):
    exps = expiries(scrip_cd)
    chosen = expiry if expiry in exps else (exps[0] if exps else expiry)
    params = {"ProductType": "IO", "Expiry": chosen, "scrip_cd": scrip_cd}
    try:
        raw = BSE.get("/BseIndiaAPI/api/GetOptionChain/w", params)
    except Exception:
        raw = BSE.get("/BseIndiaAPI/api/GetOptionChainLive/w", {
            "ProductType": "IO", "ExpiryDate": chosen, "UnderlyingScripCode": scrip_cd,
        })
    table = raw.get("Table") or raw.get("data") or raw.get("OptionChain") or []
    if isinstance(raw, list):
        table = raw
    rows, spot, lot = [], None, None
    for row in table:
        if not isinstance(row, dict):
            continue
        k = _num(row.get("StrikePrice") or row.get("Strike") or row.get("strikePrice"))
        if k is None:
            continue
        spot = spot or _num(row.get("UnderlyingValue") or row.get("Spot") or row.get("LTP"))
        lot = lot or _num(row.get("MarketLot") or row.get("LotSize"))
        # some payloads nest CE/PE; others flatten Call*/Put*
        if row.get("CE") or row.get("PE"):
            from .nse import _leg_from_nse
            rows.append({"strike": k, "ce": _leg_from_nse(row.get("CE")), "pe": _leg_from_nse(row.get("PE"))})
        else:
            rows.append({"strike": k, "ce": _leg(row, "CE") if any(x in row for x in ("CELTP", "CE_LTP", "CallLTP")) else {
                "oi": _num(row.get("CallOI")), "oi_chg": _num(row.get("CallOIChange")),
                "volume": _num(row.get("CallVolume")), "iv": _num(row.get("CallIV")),
                "ltp": _num(row.get("CallLTP")), "change": _num(row.get("CallChange")),
                "bid": _num(row.get("CallBid")), "ask": _num(row.get("CallAsk")),
            }, "pe": {
                "oi": _num(row.get("PutOI")), "oi_chg": _num(row.get("PutOIChange")),
                "volume": _num(row.get("PutVolume")), "iv": _num(row.get("PutIV")),
                "ltp": _num(row.get("PutLTP")), "change": _num(row.get("PutChange")),
                "bid": _num(row.get("PutBid")), "ask": _num(row.get("PutAsk")),
            }})
    rows.sort(key=lambda r: r["strike"] or 0)
    if not rows:
        raise LookupError(f"BSE returned no option-chain rows for {symbol}")
    return {
        "underlying": symbol, "spot": spot, "expiry": chosen, "expiries": exps,
        "rows": rows, "lot_size": int(lot) if lot else None, "source": "BSE.com",
        "as_of": None, "kind": "bse",
    }
