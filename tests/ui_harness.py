"""Dev harness: serves the real UI + API with synthetic chains and a fake LLM (no network, no keys).
   python tests/ui_harness.py   then open http://127.0.0.1:8765"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import uvicorn  # noqa: E402

import server  # noqa: E402
from copilot import dataflows as D, graph, market as mk, signals  # noqa: E402
from copilot.jobs import JobManager  # noqa: E402
from fixtures import FakeLLM, TECH_BEAR, TECH_BULL, VIX_OK, make_chain, make_fut  # noqa: E402
from test_all import fake_bundle  # noqa: E402

LEAN = {"NIFTY": ("bull", 25000, 50, 75), "BANKNIFTY": ("bear", 54000, 100, 35), "SENSEX": ("range", 81000, 100, 20),
        "FINNIFTY": ("bull", 26000, 50, 65)}


def instant(sym, expiry=None):
    sym = sym.upper()
    if sym == "BAD":
        raise LookupError("NSE returned 403")
    lean, spot, step, lot = LEAN.get(sym, ("range", 2500, 20, 500))
    ch = make_chain(spot=spot, step=step, lean=lean, lot=lot, name=sym)
    fut = {"bull": make_fut(spot), "bear": make_fut(spot, "short buildup", -0.12)}.get(lean)
    sig = signals.instant_signal(ch, fut, VIX_OK, TECH_BEAR if lean == "bear" else TECH_BULL, mk.market_status())
    return {"symbol": sym, "kind": "index" if sym in LEAN else "stock", "exchange": "BSE" if sym == "SENSEX" else "NSE",
            "chain": ch, "futures": fut, "vix": VIX_OK, "tech": TECH_BULL, "signal": sig}


D.instant = instant
graph.D.collect = fake_bundle
server.jobs = JobManager("runs_harness", "data_harness", make_llm=FakeLLM)
row = lambda n, p: {"name": n, "price": p, "change": 10, "change_pct": .4, "source": "TEST", "as_of_label": "now", "stale": False}
mk.get_indices = lambda force=False: {"indices": [row("NIFTY 50", 25000.5), row("SENSEX", 81000.2), row("BANK NIFTY", 54000), row("INDIA VIX", 13.5)],
                                      "macro": [row("USD/INR", 88.1), row("BRENT", 70.2)], "market": mk.market_status()}
mk.search = lambda q: [{"symbol": q.upper(), "exchange": "NSE", "name": "Demo " + q, "type": "FNO"}]

if __name__ == "__main__":
    uvicorn.run(server.app, host="127.0.0.1", port=8765, log_level="warning")
