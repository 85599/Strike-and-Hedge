"""Live Indian market data for F&O underlyings. Unofficial NSE/Yahoo endpoints; personal use only."""
import datetime as dt
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote as urlquote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from . import bse as bse_mod
from .universe import INDEX_FNO, STOCK_FNO, canonical, meta as und_meta

IST = ZoneInfo("Asia/Kolkata")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
QUOTE_TTL = 5
_cache, _lock = {}, threading.Lock()


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def now_ist():
    return dt.datetime.now(IST)


def market_status(now=None):
    now = now or now_ist()
    t, wd = now.time(), now.weekday()
    T = dt.time
    if wd >= 5:
        state, label = "closed", "Closed (weekend)"
    elif T(9, 0) <= t < T(9, 15):
        state, label = "preopen", "Pre-open"
    elif T(9, 15) <= t < T(15, 30):
        state, label = "open", "Market open"
    elif T(15, 30) <= t < T(16, 0):
        state, label = "closing", "Closing session"
    else:
        state, label = "closed", "Market closed"
    return {"state": state, "label": label, "now_ist": now.strftime("%d %b %Y %H:%M:%S IST"),
            "hours": "Mon-Fri 09:15-15:30 IST"}


def resolve_symbol(sym, ex="NSE"):
    info = und_meta(sym)
    if info.get("yahoo"):
        return info["yahoo"]
    s = canonical(sym)
    if s.startswith("^") or "." in s or "=" in s:
        return s
    return s + (".BO" if ex == "BSE" else ".NS")


def _plain(sym):
    return not (sym.startswith("^") or "." in sym or "=" in sym)


def _num(x):
    try:
        x = float(x)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _parse_nse_time(txt):
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime(txt, fmt).replace(tzinfo=IST)
        except (TypeError, ValueError):
            continue
    return None


def nse_quote(symbol):
    from .nse import NSE
    d = NSE.get("/api/quote-equity", {"symbol": symbol})
    pi = d["priceInfo"]
    price = _num(pi.get("lastPrice"))
    if not price:
        raise ValueError("NSE returned no lastPrice")
    hl = pi.get("intraDayHighLow") or {}
    return {"price": price, "prev_close": _num(pi.get("previousClose")), "open": _num(pi.get("open")),
            "high": _num(hl.get("max")), "low": _num(hl.get("min")), "volume": None,
            "as_of": _parse_nse_time((d.get("metadata") or {}).get("lastUpdateTime")),
            "name": (d.get("info") or {}).get("companyName"), "currency": "INR"}


def nse_indices():
    from .nse import NSE
    d = NSE.get("/api/allIndices")
    ts = _parse_nse_time(d.get("timestamp"))
    return {r["index"].upper(): {"price": _num(r.get("last")), "prev_close": _num(r.get("previousClose")),
                                 "high": _num(r.get("high")), "low": _num(r.get("low")),
                                 "open": _num(r.get("open")), "as_of": ts} for r in d.get("data", [])}


def yahoo_chart(ysym, rng="1d", interval="1m"):
    r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/" + urlquote(ysym, safe=""),
                     params={"range": rng, "interval": interval, "includePrePost": "false"},
                     headers={"User-Agent": UA}, timeout=8)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result")
    if not res:
        raise ValueError(f"Yahoo has no data for {ysym}")
    res = res[0]
    return res["meta"], res.get("timestamp") or [], res["indicators"]["quote"][0]


def _last_valid(arr):
    for i in range(len(arr) - 1, -1, -1):
        if arr[i] is not None:
            return i
    return None


def yahoo_quote(ysym):
    meta, ts, q = yahoo_chart(ysym, "1d", "1m")
    i = _last_valid(q.get("close") or [])
    price = _num(meta.get("regularMarketPrice")) or (_num(q["close"][i]) if i is not None else None)
    if not price:
        raise ValueError("Yahoo returned no price")
    t = meta.get("regularMarketTime") or (ts[i] if i is not None else None)
    first_open = next((v for v in (q.get("open") or []) if v is not None), None)
    return {"price": price, "prev_close": _num(meta.get("previousClose")) or _num(meta.get("chartPreviousClose")),
            "open": _num(first_open), "high": _num(meta.get("regularMarketDayHigh")),
            "low": _num(meta.get("regularMarketDayLow")), "volume": _num(meta.get("regularMarketVolume")),
            "as_of": dt.datetime.fromtimestamp(t, IST) if t else None,
            "name": meta.get("longName") or meta.get("shortName"), "currency": meta.get("currency", "INR")}


def yf_quote(ysym):
    import yfinance as yf
    fi = yf.Ticker(ysym).fast_info
    price = _num(fi["last_price"])
    if not price:
        raise ValueError("yfinance returned no price")
    return {"price": price, "prev_close": _num(fi["previous_close"]), "open": _num(fi["open"]),
            "high": _num(fi["day_high"]), "low": _num(fi["day_low"]), "volume": _num(fi["last_volume"]),
            "as_of": None, "name": None, "currency": "INR"}


def yahoo_day_stats(ysym):
    """One 1y/1d chart call gives CMP, day OHLC, volume and 52w high/low for the screener."""
    def build():
        meta, ts, q = yahoo_chart(ysym, "1y", "1d")
        h, l, c, v, o = (q.get(k) or [] for k in ("high", "low", "close", "volume", "open"))
        i = _last_valid(c)
        if i is None:
            raise ValueError(f"Yahoo has no closes for {ysym}")
        last = _num(meta.get("regularMarketPrice")) or _num(c[i])
        j = i - 1
        while j >= 0 and c[j] is None:
            j -= 1
        prev = _num(c[j]) if j >= 0 else (_num(meta.get("previousClose")) or _num(meta.get("chartPreviousClose")))
        oi = _last_valid(o)
        hs = [x for x in h if x is not None]
        ls = [x for x in l if x is not None]
        return {
            "last": last, "prev_close": prev,
            "open": _num(o[oi]) if oi is not None else None,
            "day_high": _num(meta.get("regularMarketDayHigh")) or (_num(h[i]) if i < len(h) else None),
            "day_low": _num(meta.get("regularMarketDayLow")) or (_num(l[i]) if i < len(l) else None),
            "volume": _num(meta.get("regularMarketVolume")) or (_num(v[i]) if i < len(v) else None),
            "year_high": max(hs) if hs else None, "year_low": min(ls) if ls else None,
        }
    return _cached(("ystats", ysym), 120, build)


def _finish(raw, symbol, ex, ysym, source, errors):
    price, prev = raw["price"], raw.get("prev_close")
    chg = price - prev if prev else None
    as_of = raw.get("as_of")
    age = (now_ist() - as_of).total_seconds() if as_of else None
    ms = market_status()
    stale = bool(ms["state"] in ("open", "closing") and (age is None or age > 180))
    return {"symbol": symbol, "exchange": ex, "ysym": ysym, "name": raw.get("name") or symbol,
            "price": price, "prev_close": prev, "change": chg,
            "change_pct": (chg / prev * 100) if prev else None,
            "open": raw.get("open"), "high": raw.get("high"), "low": raw.get("low"), "volume": raw.get("volume"),
            "currency": raw.get("currency", "INR"), "source": source, "tried_errors": errors,
            "as_of": as_of.isoformat() if as_of else None,
            "as_of_label": as_of.strftime("%d %b %H:%M:%S IST") if as_of else "time unknown",
            "age_sec": age, "stale": stale, "market": ms}


def get_quote(symbol, ex="NSE", force=False):
    info = und_meta(symbol)
    symbol = info["symbol"]
    ysym = resolve_symbol(symbol, info.get("exchange") or ex)
    ex = info.get("exchange") or ex

    def build():
        chain = []
        if info["kind"] == "index" and info.get("nse_name"):
            def idx():
                all_i = nse_indices()
                row = all_i.get(info["nse_name"].upper())
                if not row or not row.get("price"):
                    raise ValueError("index missing")
                return row | {"name": symbol, "currency": "INR"}
            chain.append(("NSE.com", idx))
        elif info["kind"] == "index" and ex == "BSE":
            chain.append(("BSE.com", lambda: bse_mod.quote(symbol)))
        elif ex == "NSE" and info["kind"] == "stock":
            chain.append(("NSE.com", lambda: nse_quote(symbol)))
        chain += [("Yahoo", lambda: yahoo_quote(ysym)), ("yfinance", lambda: yf_quote(ysym))]
        errors = []
        for name, fn in chain:
            try:
                return _finish(fn(), symbol, ex, ysym, name, errors)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
        raise LookupError(f"No live quote for {symbol} on {ex}. " + " | ".join(errors))

    return _cached(("q", ysym, ex), 0 if force else QUOTE_TTL, build)


def diag(symbol, ex="NSE"):
    info = und_meta(symbol)
    symbol, ex = info["symbol"], info.get("exchange") or ex
    ysym = resolve_symbol(symbol, ex)
    srcs = []
    if info["kind"] == "stock" and ex == "NSE":
        srcs.append(("NSE.com", lambda: nse_quote(symbol)))
    srcs += [("Yahoo chart", lambda: yahoo_quote(ysym)), ("yfinance", lambda: yf_quote(ysym))]
    rows = []
    for name, fn in srcs:
        t0 = time.time()
        try:
            r = fn()
            rows.append({"source": name, "ok": True, "price": r["price"], "prev_close": r.get("prev_close"),
                         "as_of": r["as_of"].isoformat() if r.get("as_of") else None,
                         "ms": int((time.time() - t0) * 1000)})
        except Exception as e:  # noqa: BLE001
            rows.append({"source": name, "ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}",
                         "ms": int((time.time() - t0) * 1000)})
    prices = [r["price"] for r in rows if r["ok"]]
    spread = (max(prices) - min(prices)) / min(prices) * 100 if len(prices) > 1 else None
    return {"symbol": symbol, "exchange": ex, "ysym": ysym, "sources": rows, "spread_pct": spread,
            "market": market_status()}


INDEX_LIST = [("NIFTY 50", "^NSEI", "NIFTY 50"), ("SENSEX", "^BSESN", None), ("BANK NIFTY", "^NSEBANK", "NIFTY BANK"),
              ("INDIA VIX", "^INDIAVIX", "INDIA VIX")]
MACRO_LIST = [("USD/INR", "INR=X"), ("BRENT", "BZ=F")]


def get_indices(force=False):
    def build():
        nse = {}
        try:
            nse = nse_indices()
        except Exception:  # noqa: BLE001
            pass

        def one(item):
            name, ysym = item[0], item[1]
            nse_key = item[2] if len(item) > 2 else None
            raw, src = None, None
            if nse_key and nse.get(nse_key, {}).get("price"):
                raw, src = nse[nse_key], "NSE.com"
            else:
                try:
                    raw, src = yahoo_quote(ysym), "Yahoo"
                except Exception:  # noqa: BLE001
                    return {"name": name, "error": True}
            q = _finish(raw, name, "IDX", ysym, src, [])
            return {k: q[k] for k in ("price", "change", "change_pct", "high", "low", "source", "as_of_label", "stale")} | {"name": name, "ysym": ysym}

        items = [(n, y, k) for n, y, k in INDEX_LIST] + [(n, y, None) for n, y in MACRO_LIST]
        with ThreadPoolExecutor(6) as ex:
            rows = list(ex.map(one, items))
        return {"indices": rows[:len(INDEX_LIST)], "macro": rows[len(INDEX_LIST):], "market": market_status()}

    return _cached(("idx",), 0 if force else QUOTE_TTL, build)


def history(ysym, start=None, end=None, period="2y"):
    def build():
        df = pd.DataFrame()
        try:
            import yfinance as yf
            kw = {"start": start, "end": end} if start else {"period": period}
            df = yf.Ticker(ysym).history(auto_adjust=False, **kw)
        except Exception:  # noqa: BLE001
            df = pd.DataFrame()
        if df is None or df.empty:
            try:
                meta, ts, q = yahoo_chart(ysym, "5y" if period in ("2y", "5y") else period, "1d")
                df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"],
                                   "Volume": q["volume"]}, index=pd.to_datetime(ts, unit="s", utc=True).tz_convert(IST))
            except Exception:  # noqa: BLE001
                name = NIFTY_INDEX_BY_YAHOO.get(ysym)
                if not name:
                    raise
                df = nifty_history(name, period)
        if start:
            df = df[df.index >= pd.Timestamp(start, tz=IST).normalize()]
        if end:
            df = df[df.index < pd.Timestamp(end, tz=IST).normalize()]
        df = df.dropna(subset=["Close"])
        df.index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(None) if pd.Timestamp(d).tzinfo else pd.Timestamp(d)
                                     for d in df.index]).normalize()
        return df[["Open", "High", "Low", "Close", "Volume"]]

    return _cached(("h", ysym, start, end, period), 60, build)


NIFTY_INDEX_BY_YAHOO = {v["yahoo"]: v["nifty_index"] for v in INDEX_FNO.values() if v.get("nifty_index")}
NIFTY_SITE = "https://www.niftyindices.com/BackPage/getHistoricaldatatabletoString"
_PERIOD_DAYS = {"6mo": 200, "1y": 400, "2y": 760, "5y": 1850}


def nifty_history(name, period="1y"):
    """Daily OHLC for an NSE index straight from NSE's own index site.

    Yahoo delisted some index tickers (^CNXFINANCE), and NSE's api/chart endpoints now
    answer empty for every index, so this is the remaining source of index history.
    Index series carry no exchange volume, hence the NaN Volume column.
    """
    end = now_ist().date()
    start = end - dt.timedelta(days=_PERIOD_DAYS.get(period, 400))
    cinfo = "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}" % (
        name, start.strftime("%d-%b-%Y"), end.strftime("%d-%b-%Y"), name)
    r = requests.post(NIFTY_SITE, data=json.dumps({"cinfo": cinfo}),
                      headers={"User-Agent": UA, "Content-Type": "application/json; charset=utf-8",
                               "X-Requested-With": "XMLHttpRequest",
                               "Referer": "https://www.niftyindices.com/", "Origin": "https://www.niftyindices.com"},
                      timeout=20)
    r.raise_for_status()
    rows = r.json()
    if isinstance(rows, dict):
        rows = json.loads(rows["d"])
    if not rows:
        raise ValueError(f"niftyindices has no history for {name}")
    df = pd.DataFrame(rows)
    df.index = pd.DatetimeIndex(pd.to_datetime(df["HistoricalDate"], format="%d %b %Y")).normalize()
    out = df[["OPEN", "HIGH", "LOW", "CLOSE"]].apply(pd.to_numeric, errors="coerce")
    out.columns = ["Open", "High", "Low", "Close"]
    out["Volume"] = float("nan")
    return out.sort_index()


RANGES = {"1d": ("1d", "5m"), "5d": ("5d", "15m"), "1mo": ("1mo", "1d"), "6mo": ("6mo", "1d"),
          "1y": ("1y", "1d"), "5y": ("5y", "1wk")}


_DAILY_BARS = {"1d": 8, "5d": 8, "1mo": 23, "6mo": 126, "1y": 250, "2y": 500, "5y": 1250}


def _chart_from_history(ysym, rng, iv):
    """Daily charts for tickers Yahoo no longer serves (^CNXFINANCE) — same shape as the
    Yahoo payload so the frontend needs no special case. Index history has no volume.
    NSE publishes no intraday index series either, so 1d/5d degrade to recent daily bars."""
    n = _DAILY_BARS.get(rng)
    df = history(ysym, period=rng if rng in _PERIOD_DAYS else "2y").tail(n)
    if df.empty:
        raise ValueError(f"no daily history for {ysym}")
    if iv == "1wk":
        df = df.resample("W").agg({"Open": "first", "High": "max", "Low": "min",
                                   "Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
        # sum() turns an all-NaN week into 0 — indices publish no volume, not zero volume.
        df["Volume"] = df["Volume"].where(df["Volume"] > 0)
    intraday = iv not in ("1d", "1wk")
    vol = [None if v != v else float(v) for v in df["Volume"]]
    return {"range": rng, "interval": iv if not intraday else "1d",
            "note": "Daily bars — NSE and Yahoo publish no intraday series for this index" if intraday else None,
            "prev_close": round(float(df["Close"].iloc[-2]), 2) if len(df) > 1 else None,
            "t": [int(d.tz_localize(IST).timestamp()) for d in df.index],
            "c": [round(float(c), 2) for c in df["Close"]],
            "h": [float(x) for x in df["High"]], "l": [float(x) for x in df["Low"]], "v": vol}


def chart_series(ysym, rng="1d"):
    r, iv = RANGES.get(rng, RANGES["1d"])

    def build():
        try:
            meta, ts, q = yahoo_chart(ysym, r, iv)
        except Exception:  # noqa: BLE001
            return _chart_from_history(ysym, rng, iv)
        rows = [(t, c, h, l, v) for t, c, h, l, v in zip(ts, q["close"], q["high"], q["low"], q["volume"]) if c is not None]
        return {"range": rng, "interval": iv, "note": None, "prev_close": _num(meta.get("chartPreviousClose")),
                "t": [x[0] for x in rows], "c": [round(x[1], 2) for x in rows],
                "h": [x[2] for x in rows], "l": [x[3] for x in rows], "v": [x[4] for x in rows]}

    return _cached(("c", ysym, rng), 15 if rng in ("1d", "5d") else 300, build)


def india_vix():
    try:
        all_i = nse_indices()
        row = all_i.get("INDIA VIX")
        if row and row.get("price"):
            return _finish(row | {"name": "INDIA VIX", "currency": "INR"}, "INDIA VIX", "NSE", "^INDIAVIX", "NSE.com", [])
    except Exception:
        pass
    try:
        q = yahoo_quote("^INDIAVIX")
        return _finish(q, "INDIA VIX", "NSE", "^INDIAVIX", "Yahoo", [])
    except Exception as e:  # noqa: BLE001
        return {"name": "INDIA VIX", "error": str(e)[:160], "price": None}


INDEX_ALIASES = {
    "NIFTY": ("NIFTY", "NSE", ["nifty", "nifty50", "nifty 50"]),
    "BANKNIFTY": ("BANKNIFTY", "NSE", ["banknifty", "bank nifty", "nifty bank"]),
    "FINNIFTY": ("FINNIFTY", "NSE", ["finnifty", "fin nifty", "nifty fin"]),
    "MIDCPNIFTY": ("MIDCPNIFTY", "NSE", ["midcpnifty", "midcap nifty"]),
    "SENSEX": ("SENSEX", "BSE", ["sensex", "bse sensex"]),
    "BANKEX": ("BANKEX", "BSE", ["bankex", "bse bankex"]),
}


def search(q):
    ql = q.strip().lower()
    out = []
    seen = set()
    for sym, (name, ex, aliases) in INDEX_ALIASES.items():
        hay = [name.lower(), *aliases]
        if any(ql in a or a in ql for a in hay if len(ql) >= 2):
            out.append({"symbol": sym, "exchange": ex, "ysym": INDEX_FNO[sym]["yahoo"], "name": name, "type": "INDEX"})
            seen.add(sym)
    if len(ql) >= 2:
        for s in sorted(STOCK_FNO):
            if ql in s.lower() and s not in seen:
                out.append({"symbol": s, "exchange": "NSE", "ysym": s + ".NS", "name": s, "type": "FNO"})
                seen.add(s)
    try:
        r = requests.get("https://query2.finance.yahoo.com/v1/finance/search",
                         params={"q": q, "quotesCount": 12, "newsCount": 0}, headers={"User-Agent": UA}, timeout=8)
        r.raise_for_status()
        raw = r.json().get("quotes", [])
    except Exception:  # noqa: BLE001
        raw = []
    for it in raw:
        s = it.get("symbol", "")
        name = it.get("longname") or it.get("shortname") or s
        if s.endswith(".NS"):
            sym = s.rsplit(".", 1)[0]
            row = {"symbol": sym, "exchange": "NSE", "ysym": s, "name": name, "type": "EQUITY"}
        else:
            continue
        if row["symbol"] in seen:
            continue
        seen.add(row["symbol"])
        out.append(row)
    return out[:20]


def news(query, n=12):
    try:
        r = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                         params={"q": query, "quotesCount": 0, "newsCount": n}, headers={"User-Agent": UA}, timeout=8)
        r.raise_for_status()
        items = r.json().get("news") or []
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160], "items": []}
    out = []
    for it in items[:n]:
        ts = it.get("providerPublishTime")
        when = dt.datetime.fromtimestamp(ts, IST).strftime("%d %b %H:%M IST") if ts else ""
        out.append({"title": it.get("title"), "publisher": it.get("publisher"), "when": when, "link": it.get("link")})
    return {"items": out}


def bench(ysym):
    return ("^BSESN", "Sensex") if (ysym or "").endswith(".BO") or ysym == "^BSESN" else ("^NSEI", "Nifty 50")
