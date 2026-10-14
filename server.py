"""Strike & Hedge — Supercharge Your Stock Trading.
Run:  uvicorn server:app --port 8000      then open http://localhost:8000
Data: NSE / BSE / Yahoo unofficial endpoints (personal use). Agents: copilot/graph.py.
Signals (/api/signal, /api/scan) are rule-based and need no LLM key; the AI desk (/api/runs) does."""
import pathlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from copilot import config, market as mk
from copilot import dataflows as D
from copilot import nse as mk_nse
from copilot.jobs import JobManager
from copilot.universe import INDEX_FNO, STOCK_FNO, canonical, meta as und_meta

app = FastAPI(title="Strike & Hedge")
jobs = JobManager(config.DEFAULTS["results_dir"], config.DEFAULTS["data_dir"])
EXCH = "^(NSE|BSE)$"
SIGNAL_TTL = 15
MAX_CACHE = 64
_cache, _lock = {}, threading.Lock()
DEFAULT_SCAN = ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"]


def _instant(sym, expiry=None):
    """15 s cache so the dashboard refresh + scanner never hammer NSE."""
    key = (canonical(sym), expiry or "")
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < SIGNAL_TTL:
            return hit[1]
    val = D.instant(sym, expiry)
    with _lock:
        if len(_cache) >= MAX_CACHE:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            del _cache[oldest]
        _cache[key] = (now, val)
    return val


def _trim(chain, n=12):
    """Rows nearest ATM only (the browser does not need 80 strikes)."""
    spot = chain.get("spot") or 0
    rows = sorted(chain.get("rows") or [], key=lambda r: r.get("strike") or 0)
    if not rows or not spot:
        return rows
    i = min(range(len(rows)), key=lambda j: abs((rows[j].get("strike") or 0) - spot))
    return rows[max(i - n, 0): i + n + 1]


def _load_or_http(sym, expiry=None):
    try:
        return _instant(sym, expiry)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not load the option chain for {canonical(sym)}: {type(e).__name__}: {str(e)[:200]}")


@app.get("/api/health")
def health():
    return {"ok": True, "market": mk.market_status()}


@app.get("/api/config")
def get_config():
    cfg = config.build_config()
    return {"providers": config.provider_status(), "depths": config.DEPTHS, "analysts": config.ANALYSTS,
            "defaults": {k: cfg[k] for k in ("llm_provider", "deep_think_llm", "quick_think_llm", "output_language",
                                              "holding_period_days", "risk_per_trade_pct")},
            "default_scan": DEFAULT_SCAN, "today": mk.now_ist().date().isoformat(), "market": mk.market_status()}


@app.get("/api/underlyings")
def underlyings():
    idx = [{"symbol": s, "exchange": m["exchange"], "lot": m["lot"], "kind": "index"} for s, m in INDEX_FNO.items()]
    stk = [{"symbol": s, "exchange": "NSE", "kind": "stock"} for s in sorted(STOCK_FNO)]
    return {"indices": idx, "stocks": stk}


@app.get("/api/indices")
def indices(force: bool = False):
    return mk.get_indices(force)


@app.get("/api/quote/{sym}")
def quote(sym: str, force: bool = False):
    try:
        return mk.get_quote(sym, und_meta(sym).get("exchange") or "NSE", force)
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.get("/api/chart/{sym}")
def chart(sym: str, range: str = "1d"):
    try:
        return mk.chart_series(mk.resolve_symbol(sym, und_meta(sym).get("exchange") or "NSE"), range)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"No chart data for {sym} from the Yahoo feed")


@app.get("/api/search")
def search(q: str = Query(..., min_length=1)):
    try:
        return mk.search(q)
    except Exception:  # noqa: BLE001
        return []


@app.get("/api/diag/{sym}")
def diag(sym: str, ex: str = Query("NSE", pattern=EXCH)):
    """What each price source returns for this symbol; use it when a price looks wrong."""
    return mk.diag(sym, ex)


@app.get("/api/signal/{sym}")
def signal(sym: str, expiry: str | None = None, rows: int = Query(12, ge=3, le=40)):
    """Rule-based Buy/Sell CE/PE/Futures with entry, stop, target and exit. No LLM."""
    d = _load_or_http(sym, expiry)
    ch = d["chain"]
    return {"symbol": d["symbol"], "kind": d["kind"], "exchange": d["exchange"], "signal": d["signal"],
            "futures": d["futures"], "tape": d.get("tape") or {}, "tech": d["tech"],
            "vix": {k: d["vix"].get(k) for k in ("price", "change_pct", "source", "as_of_label", "stale")},
            "chain": {"underlying": ch.get("underlying"), "spot": ch.get("spot"), "expiry": ch.get("expiry"),
                      "expiries": ch.get("expiries"), "lot_size": ch.get("lot_size"), "source": ch.get("source"),
                      "as_of": ch.get("as_of"), "dte_days": ch.get("dte_days"), "rows": _trim(ch, rows)},
            "market": mk.market_status()}


@app.get("/api/scan")
def scan(symbols: str = Query(",".join(DEFAULT_SCAN), description="NIFTY,BANKNIFTY,SENSEX,RELIANCE")):
    syms = []
    for s in symbols.split(","):
        c = canonical(s)
        if c and c not in syms:
            syms.append(c)
    syms = syms[:12]

    def one(s):
        try:
            d = _instant(s)
            sig, p = d["signal"], d["signal"].get("primary") or {}
            fut = d.get("futures")
            qf = d.get("_quote_fallback") or {}
            spot = d["chain"].get("spot") or qf.get("price")
            change_pct = qf.get("change_pct")
            if fut and not change_pct:
                change_pct = fut.get("change_pct")
            if sig.get("error"):
                return {"symbol": s, "spot": spot, "change_pct": change_pct, "error": sig["error"]}
            return {"symbol": s, "spot": spot, "expiry": d["chain"].get("expiry"), "bias": sig.get("bias"),
                    "confidence": sig.get("confidence"), "pcr": (sig.get("metrics") or {}).get("pcr_oi"),
                    "kind": p.get("kind"), "instrument": p.get("instrument"), "entry": p.get("entry"),
                    "stop": p.get("stop"), "target": p.get("target"), "rr": p.get("rr"),
                    "buildup": fut.get("buildup") if fut else None,
                    "change_pct": change_pct,
                    "volume": fut.get("volume") if fut else None}
        except Exception as e:  # noqa: BLE001
            return {"symbol": s, "error": f"{type(e).__name__}: {str(e)[:140]}"}

    with ThreadPoolExecutor(4) as pool:
        return list(pool.map(one, syms))


@app.post("/api/runs")
def start_run(body: dict = Body(...)):
    try:
        job = jobs.submit(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": job.id}


@app.get("/api/runs")
def list_runs():
    return jobs.history()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, since: int = 0):
    s = jobs.get(run_id, since)
    if not s:
        raise HTTPException(404, "Run not found")
    return s


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    return {"cancelled": jobs.cancel(run_id)}


@app.get("/api/runs/{run_id}/report")
def run_report(run_id: str, download: bool = False):
    p = jobs.report_file(run_id)
    if not p:
        raise HTTPException(404, "Report not ready")
    return FileResponse(p, media_type="text/markdown; charset=utf-8",
                        filename=f"{run_id}_complete_report.md" if download else None)


@app.get("/api/decisions")
def decisions():
    return list(reversed(jobs.log.all()))[:100]


_MO_TTL = 60
_mo_cache = {}
AD_INDICES = ["NIFTY 50", "NIFTY BANK", "NIFTY IT", "NIFTY AUTO", "NIFTY PHARMA",
              "NIFTY FMCG", "NIFTY METAL", "NIFTY FIN SERVICE"]


def _ttl_get(key, ttl, fn):
    now = time.time()
    hit = _mo_cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _mo_cache[key] = (now, val)
    if len(_mo_cache) > 32:
        oldest = min(_mo_cache, key=lambda k: _mo_cache[k][0])
        del _mo_cache[oldest]
    return val


def _stat_row(sym):
    try:
        st = mk.yahoo_day_stats(sym + ".NS")
    except Exception:  # noqa: BLE001  one bad symbol must not sink the whole index
        return None
    last, prev = st.get("last"), st.get("prev_close")
    chg = (last - prev) if (last and prev) else None
    return {
        "symbol": sym, "open": st.get("open"), "day_high": st.get("day_high"), "day_low": st.get("day_low"),
        "last": last, "prev_close": prev, "change": chg,
        "change_pct": (chg / prev * 100) if (chg is not None and prev) else None,
        "volume": st.get("volume"), "year_high": st.get("year_high"), "year_low": st.get("year_low"),
    }


def _index_rows(index):
    def build():
        members = und_meta_members(index)
        rows, failed = [], 0
        with ThreadPoolExecutor(10) as pool:
            for r in pool.map(_stat_row, members):
                if r and r.get("last"):
                    rows.append(r)
                else:
                    failed += 1
        buckets = mk_nse.classify_rows(rows)
        adv = sum(1 for r in rows if (r["change_pct"] or 0) > 0)
        dec = sum(1 for r in rows if (r["change_pct"] or 0) < 0)
        return {"index": index, "rows": rows, "buckets": {k: v[:30] for k, v in buckets.items()},
                "counts": {k: len(v) for k, v in buckets.items()},
                "advance": adv, "decline": dec, "unchanged": len(rows) - adv - dec, "failed": failed}
    return _ttl_get("mo:" + index, _MO_TTL, build)


def und_meta_members(index):
    from copilot.universe import YAHOO_INDEX_MEMBERS
    return YAHOO_INDEX_MEMBERS.get(index) or []


@app.get("/api/market-overview")
def market_overview(index: str = Query("NIFTY 50")):
    """Strike-style screener: constituents + gainer/loser/52w/gap/volume buckets."""
    if not und_meta_members(index):
        raise HTTPException(404, f"No member list for {index}")
    return _index_rows(index)


def _oi_buildup_proxy(sym):
    """Fallback when NSE futures OI is unavailable (region-blocked / closed market).

    Derives a buildup read from data that DOES work here: total option-chain ΔOI
    (fresh positioning) crossed with the underlying's spot direction from Yahoo.
    Same sign logic as futures buildup — long/short buildup on rising OI,
    covering/unwinding on falling OI.
    """
    info = und_meta(sym)
    raw = mk_nse.option_chain(info["symbol"], kind=info["oc"])
    rows = raw.get("rows") or []
    oi_chg = 0.0
    for r in rows:
        for side in ("ce", "pe"):
            leg = r.get(side) or {}
            oi_chg += leg.get("oi_chg") or 0
    st = mk.yahoo_day_stats(info["yahoo"])
    last, prev = st.get("last"), st.get("prev_close")
    chg = (last - prev) if (last and prev) else None
    chg_pct = (chg / prev * 100) if (chg is not None and prev) else None
    buildup = mk_nse._buildup(chg_pct, oi_chg)
    return {
        "symbol": sym, "ltp": last, "change": chg, "change_pct": chg_pct,
        "oi": None, "oi_chg": oi_chg, "buildup": buildup, "spot": last,
        "expiry": raw.get("expiry"), "proxy": True,
    }


@app.get("/api/futures-activity")
def futures_activity():
    """Long/short buildup, short covering, long unwinding across liquid F&O contracts."""
    def build():
        from copilot.universe import FUT_ACTIVE_POOL
        cats = {"long buildup": [], "short buildup": [], "short covering": [], "long unwinding": []}
        live = tried = 0

        def one(sym):
            nonlocal live, tried
            try:
                fut = mk_nse.futures_quote(sym)
                if fut and fut.get("oi") is not None:
                    fut = dict(fut)
                    fut["symbol"] = sym
                    fut["proxy"] = False
                    live += 1
                    return fut
            except Exception:  # noqa: BLE001
                pass
            tried += 1
            return None

        rows = []
        with ThreadPoolExecutor(6) as pool:
            rows = [r for r in pool.map(one, FUT_ACTIVE_POOL) if r]

        # No live futures OI (region-blocked or closed market): derive from option ΔOI.
        source, note = "futures", None
        if not rows:
            source = "option-oi"
            note = ("Live futures OI is unavailable from this network/market hours — "
                    "buildup is estimated from option-chain ΔOI vs spot direction.")

            def proxy(sym):
                try:
                    return _oi_buildup_proxy(sym)
                except Exception:  # noqa: BLE001
                    return None

            with ThreadPoolExecutor(6) as pool:
                rows = [r for r in pool.map(proxy, FUT_ACTIVE_POOL) if r]

        for fut in rows:
            b = fut.get("buildup")
            if b in cats:
                cats[b].append(fut)
        for k in cats:
            cats[k].sort(key=lambda c: -(abs(c.get("change") or c.get("oi_chg") or 0)))
        return {"categories": {k: v[:20] for k, v in cats.items()},
                "counts": {k: len(v) for k, v in cats.items()},
                "source": source, "note": note}
    return _ttl_get("futact", 90, build)


@app.get("/api/advance-decline")
def advance_decline():
    """Advance/decline breadth for major indices (shared cache with market overview)."""
    def build():
        out = []
        for ix in AD_INDICES:
            try:
                r = _index_rows(ix)
                out.append({"index": ix, "advance": r["advance"], "decline": r["decline"],
                            "unchanged": r["unchanged"], "total": len(r["rows"])})
            except Exception:  # noqa: BLE001
                continue
        return {"indices": out}
    return _ttl_get("ad", 90, build)


app.mount("/", StaticFiles(directory=str(pathlib.Path(__file__).resolve().parent / "web"), html=True), name="web")
