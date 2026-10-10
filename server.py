"""Options & Futures Copilot Pro server.
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
from copilot.jobs import JobManager
from copilot.universe import INDEX_FNO, STOCK_FNO, canonical, meta as und_meta

app = FastAPI(title="Options & Futures Copilot Pro")
jobs = JobManager(config.DEFAULTS["results_dir"], config.DEFAULTS["data_dir"])
EXCH = "^(NSE|BSE)$"
SIGNAL_TTL = 15
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
        raise HTTPException(404, f"No chart data for {sym}: {e}")


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
            "futures": d["futures"], "vix": {k: d["vix"].get(k) for k in ("price", "change_pct", "source", "as_of_label", "stale")},
            "tech": d["tech"],
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
            return {"symbol": s, "spot": d["chain"].get("spot"), "expiry": d["chain"].get("expiry"), "bias": sig.get("bias"),
                    "confidence": sig.get("confidence"), "pcr": (sig.get("metrics") or {}).get("pcr_oi"),
                    "kind": p.get("kind"), "instrument": p.get("instrument"), "entry": p.get("entry"),
                    "stop": p.get("stop"), "target": p.get("target"), "rr": p.get("rr")}
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


app.mount("/", StaticFiles(directory=str(pathlib.Path(__file__).resolve().parent / "web"), html=True), name="web")
