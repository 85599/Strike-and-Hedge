"""BSE Sensex option chain + index quotes (unofficial, personal use).

BSE's JSON API is Akamai-guarded the same way NSE's is, so this borrows the Chrome TLS
fingerprint from copilot.nse. Endpoints were recovered from BSE's 2026 Angular bundles:

  /BseIndiaAPI/api/IndexMasterNew_ng/w     → live quote for every BSE index
  /BseIndiaAPI/api/GetMaxdateForSensex/w   → latest date BSE has data for
  /BseIndiaAPI/api/DerivOptionChain/w      → option chain for one expiry

Two quirks the exchange leaves for us: the chain only answers when Expiry is spelled
"%d %b %Y" (a dash-separated date silently returns an empty table), and BSE publishes no
expiry list at all — Sensex weeklies trade on Thursdays, so expiries are discovered by
asking the chain which Thursdays are live. Unknown endpoints return HTTP 200 with an
HTML "page moved" body, hence the JSON guard in BSEClient.get.
"""
import datetime as dt
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from .nse import IST, UA, _live_expiries, _num

try:
    from curl_cffi import requests as _req
    _IMPERSONATE = "chrome"
except ImportError:
    import requests as _req
    _IMPERSONATE = None

BASE = "https://api.bseindia.com"
API = "/BseIndiaAPI/api"

# BSE's own numeric code for the Sensex index-option contract series.
SENSEX_SCRIP = 1
INDEX_CODES = {"SENSEX": "BSE SENSEX", "BANKEX": "BSE BANKEX"}
PROBE_WEEKS = 6  # Thursdays to test for live contracts
EXPIRY_TTL = 1800


class BSEClient:
    def __init__(self):
        self.s, self.t, self.lock = None, 0.0, threading.Lock()

    def _fresh(self):
        s = _req.Session(impersonate=_IMPERSONATE) if _IMPERSONATE else _req.Session()
        s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.bseindia.com/derivatives/OptionChain",
            "Origin": "https://www.bseindia.com",
        })
        try:
            s.get("https://www.bseindia.com/", timeout=12)
        except Exception:  # noqa: BLE001
            pass
        return s

    def get(self, path, params=None):
        with self.lock:
            if self.s is None or time.time() - self.t > 240:
                self.s, self.t = self._fresh(), time.time()
            s = self.s
        r = s.get(BASE + path, params=params, timeout=20)
        body = (r.text or "").strip()
        if r.status_code in (401, 403) or body[:1] == "<":
            # a moved/unknown endpoint answers 200 + HTML; retry once with fresh cookies
            with self.lock:
                self.s, self.t = self._fresh(), time.time()
                s = self.s
            r = s.get(BASE + path, params=params, timeout=20)
            body = (r.text or "").strip()
        r.raise_for_status()
        if body[:1] not in ("{", "["):
            raise LookupError(f"BSE has no endpoint at {path}")
        return json.loads(body)


BSE = BSEClient()


def _api_date(d):
    return d.strftime("%d %b %Y")


def _display_date(d):
    return d.strftime("%d-%b-%Y")


def _upcoming_thursdays(count, now=None):
    now = now or dt.datetime.now(IST)
    day, out = now.date(), []
    while len(out) < count:
        day += dt.timedelta(days=1)
        if day.weekday() == 3:  # BSE moved index expiry from Tuesday to Thursday
            out.append(day)
    return out


def _rows_for(scrip_cd, when):
    j = BSE.get(API + "/DerivOptionChain/w",
                {"scrip_cd": scrip_cd, "ProductType": "IO", "Expiry": _api_date(when)})
    return j.get("Table") or [], (j.get("ASON") or [{}])[0].get("DT_TM")


_expiries_cache, _expiries_lock = {}, threading.Lock()


def expiries(scrip_cd=SENSEX_SCRIP, now=None):
    """Live expiry dates as "DD-Mon-YYYY" (the shape the rest of the app uses)."""
    now = now or dt.datetime.now(IST)
    key = (scrip_cd, now.strftime("%Y-%m-%d"))
    with _expiries_lock:
        hit = _expiries_cache.get(key)
        if hit and time.time() - hit[0] < EXPIRY_TTL:
            return list(hit[1])
    found = []
    candidates = _upcoming_thursdays(PROBE_WEEKS, now)
    with ThreadPoolExecutor(6) as pool:
        for when, rows in pool.map(lambda d: (d, _rows_for(scrip_cd, d)[0]), candidates):
            if rows:
                found.append(_display_date(when))
    out = _live_expiries(sorted(found, key=lambda s: dt.datetime.strptime(s, "%d-%b-%Y")))
    with _expiries_lock:
        _expiries_cache[key] = (time.time(), out)
    return list(out)


_CE = {"oi": "C_Open_Interest", "oi_chg": "C_Absolute_Change_OI", "volume": "C_Vol_Traded",
       "ltp": "C_Last_Trd_Price", "change": "C_NetChange", "bid": "C_BidPrice", "ask": "C_OfferPrice",
       "bid_qty": "C_BIdQty", "ask_qty": "C_OfferQty", "identifier": "C_Series_Code"}
# BSE leaves the put columns unprefixed and spells its one contract-code field lowercase.
_PE = {"oi": "Open_Interest", "oi_chg": "Absolute_Change_OI", "volume": "Vol_Traded",
       "ltp": "Last_Trd_Price", "change": "NetChange", "bid": "BidPrice", "ask": "OfferPrice",
       "bid_qty": "BIdQty", "ask_qty": "OfferQty", "identifier": "p_Series_Code"}


def _leg(row, fields):
    leg = {k: (row.get(f) if k == "identifier" else _num(row.get(f))) for k, f in fields.items()}
    leg["iv"] = None  # BSE's chain carries no IV; chain._attach_greeks solves it from the premium
    return leg


def option_chain(symbol="SENSEX", scrip_cd=SENSEX_SCRIP, expiry=None):
    exps = expiries(scrip_cd)
    chosen = expiry if expiry in exps else (exps[0] if exps else None)
    if not chosen:
        raise LookupError(f"BSE lists no live {symbol} option expiries")
    when = dt.datetime.strptime(chosen, "%d-%b-%Y").date()
    table, stamped = _rows_for(scrip_cd, when)
    rows, spot = [], None
    for row in table:
        k = _num(row.get("Strike_Price"))
        if k is None:
            continue
        spot = spot or _num(row.get("UlaValue"))
        rows.append({"strike": k, "ce": _leg(row, _CE), "pe": _leg(row, _PE)})
    if not rows:
        raise LookupError(f"BSE returned no option-chain rows for {symbol}")
    rows.sort(key=lambda r: r["strike"])
    return {
        "underlying": symbol, "spot": spot, "expiry": chosen, "expiries": exps,
        "rows": rows, "lot_size": None, "source": "BSE.com",
        "as_of": stamped, "kind": "bse",
    }


def indices():
    """Live quote for every BSE index, keyed by display name. BSE's payload carries no
    day OHLC here, so high/low/open stay None — the chain's UlaValue covers the spot."""
    j = BSE.get(API + "/IndexMasterNew_ng/w", {"flag": 1})
    out = {}
    for row in j.get("table") or []:
        price = _num(row.get("LTP"))
        if price is None:
            continue
        out[str(row.get("INDEXNAME", "")).upper()] = {
            "price": price, "prev_close": _num(row.get("Prev_Close")),
            "change": _num(row.get("CHANGE")), "change_pct": _num(row.get("PERCENTCHG")),
            "high": None, "low": None, "open": None, "code": row.get("code"),
        }
    return out


def quote(symbol):
    name = INDEX_CODES.get(str(symbol).upper())
    if not name:
        raise LookupError(f"{symbol} is not a BSE index")
    row = indices().get(name)
    if not row or not row.get("price"):
        raise LookupError(f"BSE has no live quote for {symbol}")
    return {"price": row["price"], "prev_close": row["prev_close"], "open": None,
            "high": None, "low": None, "volume": None,
            "as_of": dt.datetime.now(IST), "name": name, "currency": "INR"}
