"""NSE session + option-chain / futures JSON (unofficial personal-use endpoints).

NSE retired /api/option-chain-indices and /api/option-chain-equities (they now return
Akamai 404/403). Current flow (Oct 2026):

  1. GET /api/option-chain-contract-info?symbol=NIFTY   → expiry list
  2. GET /api/option-chain-v3?type=Indices&symbol=NIFTY&expiry=DD-Mon-YYYY  → chain

Uses curl_cffi (Chrome TLS fingerprint) when installed; falls back to requests.
"""
import datetime as dt
import threading
import time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")

# Prefer curl_cffi for Akamai bypass; plain requests as fallback
try:
    from curl_cffi import requests as _req
    _IMPERSONATE = "chrome"
    _HAS_CFFI = True
except ImportError:
    import requests as _req
    _IMPERSONATE = None
    _HAS_CFFI = False


def _num(x):
    try:
        x = float(x)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


class NSEClient:
    base = "https://www.nseindia.com"

    def __init__(self):
        self.s, self.t, self.lock = None, 0.0, threading.Lock()

    def _fresh(self):
        if _HAS_CFFI:
            s = _req.Session(impersonate=_IMPERSONATE)
        else:
            s = _req.Session()
        s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": self.base + "/option-chain",
            "Origin": self.base,
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        })
        # Warm cookies (home may 403 on some IPs — non-fatal)
        try:
            s.get(self.base, timeout=12)
        except Exception:
            pass
        try:
            s.get(self.base + "/option-chain", timeout=12)
        except Exception:
            pass
        return s

    def get(self, path, params=None):
        with self.lock:
            if self.s is None or time.time() - self.t > 240:
                self.s, self.t = self._fresh(), time.time()
            s = self.s
        r = s.get(self.base + path, params=params, timeout=15)
        if r.status_code in (401, 403, 404):
            with self.lock:
                self.s, self.t = self._fresh(), time.time()
                s = self.s
            r = s.get(self.base + path, params=params, timeout=15)
        r.raise_for_status()
        return r.json()


NSE = NSEClient()


def _parse_expiry(txt):
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(txt, fmt).replace(tzinfo=IST, hour=15, minute=30)
        except (TypeError, ValueError):
            continue
    return None


def _live_expiries(expiries, now=None):
    """Drop expiries that have already settled. NSE/BSE keep yesterday's expiry at the head of the list for hours
    after the 15:30 close, and that chain is all ₹0.05 premiums with greeks that are pure noise."""
    now = now or dt.datetime.now(IST)
    return [d for d in expiries if (_parse_expiry(d) or now + dt.timedelta(days=1)) > now]


def _leg_from_nse(raw):
    if not raw:
        return None
    return {
        "oi": _num(raw.get("openInterest")),
        "oi_chg": _num(raw.get("changeinOpenInterest")),
        "volume": _num(raw.get("totalTradedVolume")),
        "iv": _num(raw.get("impliedVolatility")),
        "ltp": _num(raw.get("lastPrice")),
        "change": _num(raw.get("change")),
        "pchange": _num(raw.get("pChange")),
        "bid": _num(raw.get("bidprice") or raw.get("bidPrice")),
        "ask": _num(raw.get("askPrice")),
        "bid_qty": _num(raw.get("bidQty")),
        "ask_qty": _num(raw.get("askQty")),
        "buy_qty": _num(raw.get("totalBuyQuantity")),
        "sell_qty": _num(raw.get("totalSellQuantity")),
        "identifier": raw.get("identifier"),
    }


def _expiries_for(symbol):
    """Fetch available expiry dates via the current contract-info endpoint."""
    try:
        info = NSE.get("/api/option-chain-contract-info", {"symbol": symbol})
    except Exception:
        return []
    dates = (
        info.get("expiryDates")
        or (info.get("records") or {}).get("expiryDates")
        or info.get("data")
        or []
    )
    if isinstance(dates, dict):
        dates = dates.get("expiryDates") or []
    return [d for d in dates if isinstance(d, str)]


def option_chain(symbol, kind="indices", expiry=None):
    """
    Load option chain using the current NSE v3 API.
    kind: "indices" → type=Indices ; anything else → type=Equity
    """
    type_param = "Indices" if kind == "indices" else "Equity"

    # 1) Resolve expiry list
    expiries = _live_expiries(_expiries_for(symbol))
    chosen = expiry if expiry in expiries else (expiries[0] if expiries else None)

    # 2) Fetch chain (with expiry if we have one)
    params = {"type": type_param, "symbol": symbol}
    if chosen:
        params["expiry"] = chosen

    raw = NSE.get("/api/option-chain-v3", params)
    rec = raw.get("records") or {}
    # Prefer filtered.data (already expiry-scoped) when present; else records.data
    data_rows = (raw.get("filtered") or {}).get("data") or rec.get("data") or []

    if not expiries:
        expiries = _live_expiries(rec.get("expiryDates") or [])
    if not chosen:
        chosen = expiry if expiry in expiries else (expiries[0] if expiries else None)

    spot = _num(rec.get("underlyingValue") or (raw.get("filtered") or {}).get("underlyingValue"))
    rows, lot = [], None
    for row in data_rows:
        # When using records.data (no filtered), honour the chosen expiry
        if chosen and row.get("expiryDate") and row.get("expiryDate") != chosen:
            continue
        ce, pe = row.get("CE") or {}, row.get("PE") or {}
        lot = lot or _num(ce.get("marketLot")) or _num(pe.get("marketLot"))
        rows.append({
            "strike": _num(row.get("strikePrice")),
            "ce": _leg_from_nse(ce),
            "pe": _leg_from_nse(pe),
        })
    rows.sort(key=lambda r: r["strike"] or 0)
    ts = rec.get("timestamp")
    return {
        "underlying": symbol, "spot": spot, "expiry": chosen, "expiries": expiries,
        "rows": rows, "lot_size": int(lot) if lot else None, "source": "NSE.com",
        "as_of": ts, "kind": kind,
    }


def _buildup(price_chg, oi_chg):
    if price_chg is None or oi_chg is None:
        return "n/a"
    if price_chg > 0 and oi_chg > 0:
        return "long buildup"
    if price_chg < 0 and oi_chg > 0:
        return "short buildup"
    if price_chg > 0 and oi_chg < 0:
        return "short covering"
    if price_chg < 0 and oi_chg < 0:
        return "long unwinding"
    return "mixed"


def futures_quote(symbol):
    """Nearest futures from NSE quote-derivative."""
    raw = NSE.get("/api/quote-derivative", {"symbol": symbol})
    stocks = raw.get("stocks") or []
    spot = _num((raw.get("underlyingValue") if not isinstance(raw.get("underlyingValue"), dict) else None))
    fut = None
    for st in stocks:
        md = st.get("metadata") or {}
        if (md.get("instrumentType") or "").upper() in ("FUTIDX", "FUTSTK", "INDEX FUTURES", "STOCK FUTURES") \
                or str(md.get("optionType") or "").upper() in ("XX", "-", "FF"):
            inst = (md.get("instrumentType") or "") + " " + (md.get("identifier") or "")
            if "FUT" not in inst.upper() and md.get("optionType") not in (None, "XX", "-", ""):
                continue
            trade = (st.get("marketDeptOrderBook") or {}).get("tradeInfo") or {}
            if md.get("optionType") in ("CE", "PE", "Call", "Put"):
                continue
            ltp = _num(md.get("lastPrice"))
            if not ltp:
                continue
            lot = _num(trade.get("marketLot")) or _num(md.get("marketLot"))
            oi = _num(trade.get("openInterest")) or _num(md.get("openInterest"))
            oi_chg = _num(trade.get("changeinOpenInterest"))
            chg = _num(md.get("change"))
            expiry = md.get("expiryDate")
            fut = {
                "expiry": expiry, "ltp": ltp, "change": chg, "oi": oi, "oi_chg": oi_chg,
                "lot_size": int(lot) if lot else None, "volume": _num(md.get("numberOfContractsTraded")),
                "identifier": md.get("identifier"), "buildup": _buildup(chg, oi_chg),
            }
            if not spot:
                spot = _num(md.get("underlyingValue"))
            break
    if not fut:
        # fallback: first record whose identifier contains FUT
        for st in stocks:
            md = st.get("metadata") or {}
            ident = str(md.get("identifier") or md.get("instrumentType") or "")
            if "FUT" not in ident.upper():
                continue
            trade = (st.get("marketDeptOrderBook") or {}).get("tradeInfo") or {}
            ltp = _num(md.get("lastPrice"))
            if not ltp:
                continue
            chg = _num(md.get("change"))
            oi_chg = _num(trade.get("changeinOpenInterest"))
            fut = {
                "expiry": md.get("expiryDate"), "ltp": ltp, "change": chg,
                "oi": _num(trade.get("openInterest")) or _num(md.get("openInterest")),
                "oi_chg": oi_chg, "lot_size": int(_num(trade.get("marketLot")) or 0) or None,
                "volume": _num(md.get("numberOfContractsTraded")),
                "identifier": md.get("identifier"), "buildup": _buildup(chg, oi_chg),
            }
            break
    if fut and spot:
        fut["spot"] = spot
        fut["basis"] = fut["ltp"] - spot
        fut["basis_pct"] = (fut["basis"] / spot * 100) if spot else None
    return fut


def fii_dii():
    return NSE.get("/api/fiidiiTradeReact")


def classify_rows(rows):
    """Strike-style screener buckets computed from index constituents."""
    buckets = {"gainers": [], "losers": [], "high52": [], "low52": [], "open_high": [],
               "open_low": [], "gap_up": [], "gap_down": [], "volume": []}
    vols = sorted([r["volume"] or 0 for r in rows], reverse=True)
    vol_cut = vols[min(9, len(vols) - 1)] if vols else 0
    for r in rows:
        cp, op, pc = r["change_pct"], r["open"], r["prev_close"]
        if cp is not None and cp > 0:
            buckets["gainers"].append(r)
        if cp is not None and cp < 0:
            buckets["losers"].append(r)
        if r["last"] and r["year_high"] and r["last"] >= r["year_high"] * 0.998:
            buckets["high52"].append(r)
        if r["last"] and r["year_low"] and r["last"] <= r["year_low"] * 1.002:
            buckets["low52"].append(r)
        if op and r["day_high"] and abs(op - r["day_high"]) < 1e-9:
            buckets["open_high"].append(r)
        if op and r["day_low"] and abs(op - r["day_low"]) < 1e-9:
            buckets["open_low"].append(r)
        if op and pc and op > pc * 1.004:
            buckets["gap_up"].append(r)
        if op and pc and op < pc * 0.996:
            buckets["gap_down"].append(r)
        if r["volume"] and vol_cut and r["volume"] >= vol_cut:
            buckets["volume"].append(r)
    for k in buckets:
        buckets[k].sort(key=lambda r: -(r["change_pct"] or 0) if k in ("gainers", "volume") else (r["change_pct"] or 0))
    return buckets
