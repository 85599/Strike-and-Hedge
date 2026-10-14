"""Offline tests: no network, no API keys.   python -m pytest tests -q"""
import datetime as dt
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from copilot import analytics, config, graph, greeks, nse, signals, universe  # noqa: E402
from copilot import dataflows as D  # noqa: E402
from copilot.jobs import JobManager  # noqa: E402
from copilot.memory import DecisionLog  # noqa: E402
from fixtures import (FakeLLM, TECH_BEAR, TECH_BULL, VIX_HIGH, VIX_OK, expiry_str, make_chain, make_fut)  # noqa: E402


# ------------------------------------------------------------------ greeks
def test_put_call_parity_and_iv_roundtrip():
    s, k, t, r, v = 25000, 25100, 7 / 365, 0.065, 0.15
    c, p = greeks.bs_price(s, k, t, r, v, "CE"), greeks.bs_price(s, k, t, r, v, "PE")
    assert c - p == pytest.approx(s - k * pow(2.718281828, -r * t), abs=0.5)
    assert greeks.implied_vol(c, s, k, t, r, "CE") == pytest.approx(v, abs=1e-3)
    g = greeks.greeks(s, k, t, r, v, "CE")
    assert 0 < g["delta"] < 1 and g["gamma"] > 0 and g["theta"] < 0 and g["vega"] > 0
    assert greeks.greeks(s, k, t, r, v, "PE")["delta"] < 0


def test_greeks_expired_does_not_crash():
    assert greeks.greeks(100, 90, 0, 0.06, 0.2, "CE")["delta"] == 1.0
    assert greeks.implied_vol(None, 100, 90, 0.1) is None


def test_implied_vol_rejects_quotes_outside_the_model_range():
    """BSE sends no IV, so deep-ITM legs are solved from the premium. Those bids sit *below* the
    lowest-vol BS price; returning the search ceiling surfaced them as a fake 500% IV."""
    s, k, t, r = 72988, 71800, 1.2 / 365, 0.065
    cheap = greeks.bs_price(s, k, t, r, 1e-4, "CE") - 4   # a few rupees under intrinsic
    assert greeks.implied_vol(cheap, s, k, t, r, "CE") is None
    dear = greeks.bs_price(s, k, t, r, 5.0, "CE") + 400   # above anything BS can price
    assert greeks.implied_vol(dear, s, k, t, r, "CE") is None
    mid = greeks.bs_price(s, k, t, r, 0.18, "CE")
    assert greeks.implied_vol(mid, s, k, t, r, "CE") == pytest.approx(0.18, abs=1e-3)


# ------------------------------------------------------------------ universe
def test_universe_aliases():
    assert universe.canonical("Bank Nifty") == "BANKNIFTY"
    assert universe.canonical("reliance.ns") == "RELIANCE"
    assert universe.meta("SENSEX")["oc"] == "bse" and universe.meta("TCS")["kind"] == "stock"
    assert universe.is_index("nifty 50") and not universe.is_index("INFY")


# ------------------------------------------------------------------ analytics
def test_summarise_chain_bull_lean():
    m = analytics.summarise_chain(make_chain(lean="bull"))
    assert m["pcr_oi"] > 1.0 and m["put_wall"] < m["spot"] and m["atm_strike"] == 25000
    assert m["expected_move_pct"] > 0 and m["pe_writing"] > m["ce_writing"]


def test_summarise_empty_chain():
    assert "error" in analytics.summarise_chain({"rows": [], "spot": 0})


# ------------------------------------------------------------------ signals
def _sig(lean, tech, vix=VIX_OK, fut=None):
    return signals.instant_signal(make_chain(lean=lean), fut, vix, tech, {"state": "open"})


def test_bullish_buys_call_with_sane_levels():
    s = _sig("bull", TECH_BULL, fut=make_fut())
    assert s["bias"] == "BULLISH" and s["primary"]["kind"] == "BUY_CE"
    p = s["primary"]
    assert p["stop"] < p["entry"] < p["target"] and p["rr"] >= 1.0
    assert p["strike"] > 25000 and p["max_loss_rs"] == pytest.approx(p["entry"] * p["lot_size"], rel=1e-3)
    assert "put OI wall" in p["exit"]
    fut = next(x for x in s["setups"] if x["kind"] == "FUT_LONG")
    assert fut["stop"] < fut["entry"] < fut["target"]


def test_bearish_buys_put_and_exit_mentions_call_wall():
    s = _sig("bear", TECH_BEAR, fut=make_fut(buildup="short buildup", basis_pct=-0.12))
    assert s["bias"] == "BEARISH" and s["primary"]["kind"] == "BUY_PE"
    p = s["primary"]
    assert p["strike"] < 25000 and p["stop"] < p["entry"] < p["target"]
    assert "call OI wall" in p["exit"] and p["underlying_stop"] > 25000 > p["underlying_target"]
    short = next(x for x in s["setups"] if x["kind"] == "FUT_SHORT")
    assert short["target"] < short["entry"] < short["stop"]


def test_range_market_sells_only_defined_risk_spreads():
    s = _sig("range", {"rsi": 50, "macdh": 0.0, "atr": 150})
    assert s["bias"] == "RANGE"
    kinds = {x["kind"] for x in s["setups"]}
    assert kinds <= {"SELL_CE_SPREAD", "SELL_PE_SPREAD"} and kinds
    for x in s["setups"]:
        assert len(x["legs"]) == 2 and x["legs"][1]["action"] == "BUY"
        assert x["entry"] > 0 and x["max_loss_rs"] > 0 and x["target"] < x["entry"] < x["stop"]
        assert x["max_loss_rs"] < (x["strike"] * 0.01) * x["lot_size"] * 5  # hedge caps the loss


def test_high_vix_with_mixed_signals_is_avoid():
    s = _sig("range", {"rsi": 50, "macdh": 0.0, "atr": 150}, vix=VIX_HIGH)
    assert s["bias"] == "AVOID" and s["primary"]["kind"] == "AVOID" and s["primary"]["entry"] is None


def test_expiry_day_warning():
    ch = make_chain(lean="bull", days=0)
    ch["dte_days"] = 0.3
    s = signals.instant_signal(ch, None, VIX_OK, TECH_BULL, {"state": "closed"})
    assert any("Expiry day" in w for w in s["warnings"]) and any("closed" in w for w in s["warnings"])


def test_futures_wall_on_wrong_side_falls_back_to_atr():
    ch = make_chain(lean="bull")
    metrics = analytics.summarise_chain(ch)
    metrics["put_wall"], metrics["call_wall"] = 26000.0, 24000.0  # both on the wrong side
    scored = {"bias": "BULLISH", "reasons": ["x"]}
    st = signals._futures_setup(make_fut(), metrics, scored, "BULLISH", {"atr": 180.0}, 75, "NIFTY", "x")
    assert st["stop"] < st["entry"] < st["target"]


def test_empty_chain_returns_error_signal():
    s = signals.instant_signal({"rows": [], "spot": 0}, None, None, None)
    assert s["bias"] == "AVOID" and s["setups"] == [] and s["error"]


# ------------------------------------------------------------------ graph helpers
def test_reconcile_rejects_strike_mistaken_for_premium():
    primary = {"kind": "BUY_CE", "entry": 120.0, "stop": 80.0, "target": 200.0}
    assert graph.reconcile("BUY CE", 25100.0, 22.0, 90.0, primary)[:3] == (120.0, 80.0, 200.0)
    e, s_, t, notes = graph.reconcile("BUY CE", 118.0, 78.0, 190.0, primary)
    assert (e, s_, t) == (118.0, 78.0, 190.0) and not notes
    assert graph.reconcile("BUY PE", 25100.0, 1.0, 2.0, primary)[0] == 25100.0  # different structure: leave it


def test_check_levels_flags_wrong_side_stop_and_sizes():
    w, sizing = graph.check_levels("BUY CE", 100, 100, 110, 130, 1.0, lot=75)
    assert any("wrong side" in x for x in w)
    w, sizing = graph.check_levels("BUY CE", 100, 100, 90, 130, 1.0, lot=50)
    assert not w and sizing["lots"] == 2 and sizing["rr"] == pytest.approx(3.0)
    w, _ = graph.check_levels("FUT SHORT", 100, 100, 95, 90, 1.0)
    assert any("short" in x for x in w)


def test_parsers():
    from copilot import agents as A
    t = "- **Action**: BUY CE\n**Entry**: \u20b9120.50\n**Stop Loss**: 80\n**Target**: 180-200\n**Instrument**: NIFTY 25100 CE"
    assert A.parse_action(t) == "BUY CE" and A.parse_price(t, "Entry") == 120.5
    assert A.parse_price(t, "Target") is None  # ranges are ambiguous, rule engine fills in
    assert A.parse_rating("**Rating**: Overweight") == "Overweight" and A.parse_rating("nothing") == "Hold"


# ------------------------------------------------------------------ NSE payloads
def test_nse_option_chain_and_futures_parse(monkeypatch):
    e1, e2 = expiry_str(14), expiry_str(21)
    def fake_get(path, params=None):
        if "option-chain" in path:
            leg = lambda p: {"openInterest": 10, "changeinOpenInterest": 1, "totalTradedVolume": 5, "impliedVolatility": 14,
                             "lastPrice": p, "change": 1, "bidprice": 1, "askPrice": 2, "marketLot": 75}
            return {"records": {"expiryDates": [e1, e2], "underlyingValue": 25010, "timestamp": "09-Oct-2026 11:00:00",
                                "data": [{"expiryDate": e1, "strikePrice": 25000, "CE": leg(120), "PE": leg(95)},
                                         {"expiryDate": e2, "strikePrice": 25000, "CE": leg(1), "PE": leg(1)}]}}
        return {"stocks": [{"metadata": {"instrumentType": "Index Futures", "identifier": "FUTIDXNIFTY", "lastPrice": 25050,
                                         "change": 30, "expiryDate": "28-Oct-2026"},
                            "marketDeptOrderBook": {"tradeInfo": {"openInterest": 100, "changeinOpenInterest": 5, "marketLot": 75}}},
                           {"metadata": {"instrumentType": "Index Options", "optionType": "Call", "lastPrice": 5}}],
                "underlyingValue": 25010}
    monkeypatch.setattr(nse.NSE, "get", fake_get)
    ch = nse.option_chain("NIFTY")
    assert ch["expiry"] == e1 and len(ch["rows"]) == 1 and ch["lot_size"] == 75 and ch["rows"][0]["ce"]["ltp"] == 120
    assert nse.option_chain("NIFTY", expiry=e2)["rows"][0]["ce"]["ltp"] == 1
    fut = nse.futures_quote("NIFTY")
    assert fut["ltp"] == 25050 and fut["buildup"] == "long buildup" and fut["basis"] == pytest.approx(40)


def test_settled_expiry_is_not_used_as_default(monkeypatch):
    """After the 15:30 close NSE still lists the settled expiry first; using it yields a ₹0.05 chain and nonsense greeks."""
    dead, live = expiry_str(-1), expiry_str(7)
    monkeypatch.setattr(nse, "_expiries_for", lambda symbol: [dead, live])
    seen = {}
    def fake_get(path, params=None):
        seen.update(params or {})
        return {"records": {"expiryDates": [dead, live], "underlyingValue": 25000,
                            "data": [{"expiryDate": live, "strikePrice": 25000, "CE": {"lastPrice": 100}, "PE": {"lastPrice": 90}}]}}
    monkeypatch.setattr(nse.NSE, "get", fake_get)
    assert nse.option_chain("NIFTY")["expiry"] == live
    assert seen["expiry"] == live          # the dead one is never even requested
    assert nse.option_chain("NIFTY", expiry=dead)["expiry"] == live


def test_buildup_matrix():
    assert [nse._buildup(1, 1), nse._buildup(-1, 1), nse._buildup(1, -1), nse._buildup(-1, -1)] == \
        ["long buildup", "short buildup", "short covering", "long unwinding"]


# ------------------------------------------------------------------ BSE payloads
def test_bse_chain_maps_call_and_put_columns(monkeypatch):
    """BSE prefixes only the call side, spells the put contract code `p_Series_Code`, and
    only answers the chain endpoint when Expiry is "15 Oct 2026" — not the "15-Oct-2026" the app uses."""
    from copilot import bse
    monkeypatch.setattr(bse, "expiries", lambda scrip_cd=1, now=None: ["15-Oct-2026", "22-Oct-2026"])
    seen = []

    def fake_get(path, params=None):
        seen.append(dict(params or {}))
        if params["Expiry"] != "15 Oct 2026":
            return {"ASON": [{"DT_TM": "15-Oct-2026 18:00:00"}], "Table": []}
        row = {"Strike_Price": 73000, "UlaValue": 72978.5,
               "C_Series_Code": "SENSEX26O1573000CE", "C_Open_Interest": 133741, "C_Vol_Traded": 1768636,
               "C_Last_Trd_Price": 297.35, "C_BidPrice": 297.5, "C_OfferPrice": 298.15,
               "p_Series_Code": "SENSEX26O1573000PE", "Open_Interest": 163521, "Vol_Traded": 2011705,
               "Last_Trd_Price": 327.6, "BidPrice": 327.4, "OfferPrice": 328.3}
        return {"ASON": [{"DT_TM": "15-Oct-2026 18:00:00"}], "Table": [row]}

    monkeypatch.setattr(bse.BSE, "get", fake_get)
    ch = bse.option_chain("SENSEX")
    assert seen[0]["Expiry"] == "15 Oct 2026"
    assert ch["spot"] == 72978.5 and ch["expiry"] == "15-Oct-2026" and ch["source"] == "BSE.com"
    ce, pe = ch["rows"][0]["ce"], ch["rows"][0]["pe"]
    assert (ce["ltp"], ce["oi"], ce["volume"]) == (297.35, 133741.0, 1768636.0)
    assert ce["identifier"] == "SENSEX26O1573000CE"
    assert (pe["ltp"], pe["oi"], pe["bid"], pe["ask"]) == (327.6, 163521.0, 327.4, 328.3)
    assert pe["identifier"] == "SENSEX26O1573000PE"
    assert ce["iv"] is None and pe["iv"] is None      # BSE publishes no IV; greeks solve it from the premium


def test_bse_index_without_contracts_does_not_load_sensex(monkeypatch):
    """BANKEX has no index-option series; defaulting the scrip would silently serve the SENSEX chain."""
    from copilot import chain
    with pytest.raises(LookupError):
        chain.load("BANKEX")


def test_bse_html_page_move_is_a_lookup_error():
    """BSE answers unknown endpoints with HTTP 200 + an HTML redirect page, not a 404."""
    from copilot import bse

    class _Html:
        status_code, text = 200, "<html><body>page moved</body></html>"

        def raise_for_status(self):
            pass

    class _Sess:
        def get(self, url, params=None, timeout=None):
            return _Html()

    client = bse.BSEClient()
    client._fresh = lambda: _Sess()
    with pytest.raises(LookupError):
        client.get(bse.API + "/ExpiryNew/w")


# ------------------------------------------------------------------ index history
def test_nifty_history_fallback_for_delisted_yahoo_ticker(monkeypatch):
    """Yahoo delisted ^CNXFINANCE, so FINNIFTY daily bars come from niftyindices instead."""
    import pandas as pd
    from copilot import market as mk
    calls = []

    def nifty(name, period="1y"):
        calls.append((name, period))
        idx = pd.DatetimeIndex(pd.to_datetime(["12 Oct 2026", "13 Oct 2026"])).normalize()
        return pd.DataFrame({"Open": [24900.0, 25000.0], "High": [25100.0, 25300.0],
                             "Low": [24800.0, 24900.0], "Close": [24866.35, 25228.55],
                             "Volume": [float("nan")] * 2}, index=idx)

    monkeypatch.setattr(mk, "NIFTY_INDEX_BY_YAHOO", {"^CNXFINANCE": "NIFTY FIN SERVICE"})
    monkeypatch.setattr(mk, "nifty_history", nifty)
    monkeypatch.setattr(mk, "yahoo_chart", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("404")))
    import yfinance
    monkeypatch.setattr(yfinance.Ticker, "history", lambda self, **kw: pd.DataFrame())
    mk._cache.pop(("h", "^CNXFINANCE", None, None, "1y"), None)
    df = mk.history("^CNXFINANCE", period="1y")
    assert calls == [("NIFTY FIN SERVICE", "1y")]
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df["Close"].iloc[-1] == pytest.approx(25228.55)


def test_nifty_history_accepts_wrapped_payload(monkeypatch):
    """niftyindices returns a bare array or a {"d": "<json string>"} wrapper depending on the endpoint."""
    import json

    import pandas as pd
    from copilot import market as mk

    payload = json.dumps({"d": json.dumps([
        {"HistoricalDate": "13 Oct 2026", "OPEN": "24834.75", "HIGH": "25269.50",
         "LOW": "24742.80", "CLOSE": "25228.55"},
        {"HistoricalDate": "09 Oct 2026", "OPEN": "24722.15", "HIGH": "25046.85",
         "LOW": "24682.05", "CLOSE": "24968.50"}])})

    class _R:
        text = payload

        def raise_for_status(self):
            pass

        def json(self):
            return json.loads(self.text)

    monkeypatch.setattr(mk.requests, "post", lambda *a, **k: _R())
    df = mk.nifty_history("NIFTY FIN SERVICE", "6mo")
    assert isinstance(df, pd.DataFrame) and len(df) == 2
    assert df.index.max() == pd.Timestamp("2026-10-13")
    assert df["Close"].iloc[-1] == pytest.approx(25228.55)


def test_chart_falls_back_to_history_for_daily_ranges(monkeypatch):
    from copilot import market as mk
    import pandas as pd

    idx = pd.DatetimeIndex(pd.to_datetime(["2026-10-12", "2026-10-13"]))
    mk._cache.pop(("c", "^CNXFINANCE", "1mo"), None)
    monkeypatch.setattr(mk, "yahoo_chart", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("404")))
    monkeypatch.setattr(mk, "history", lambda *a, **k: pd.DataFrame(
        {"Open": [1.0, 2.0], "High": [3.0, 4.0], "Low": [0.5, 1.5], "Close": [3.5, 2.5],
         "Volume": [float("nan")] * 2}, index=idx))
    out = mk.chart_series("^CNXFINANCE", "1mo")
    assert out["interval"] == "1d" and out["c"] == [3.5, 2.5]
    assert out["prev_close"] == pytest.approx(3.5) and out["v"] == [None, None]
    assert out["note"] is None


def test_intraday_chart_labels_the_daily_fallback(monkeypatch):
    """Neither NSE nor Yahoo has an intraday index series for ^CNXFINANCE; the panel must say so."""
    from copilot import market as mk
    import pandas as pd

    idx = pd.DatetimeIndex(pd.to_datetime(["2026-10-09", "2026-10-12", "2026-10-13"]))
    mk._cache.pop(("c", "^CNXFINANCE", "1d"), None)
    monkeypatch.setattr(mk, "yahoo_chart", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("404")))
    monkeypatch.setattr(mk, "history", lambda *a, **k: pd.DataFrame(
        {"Open": [1.0, 2.0, 3.0], "High": [3.0, 4.0, 5.0], "Low": [0.5, 1.5, 2.5],
         "Close": [2.5, 3.5, 4.5], "Volume": [float("nan")] * 3}, index=idx))
    out = mk.chart_series("^CNXFINANCE", "1d")
    assert out["interval"] == "1d" and "Daily bars" in out["note"]
    assert len(out["t"]) == 3


# ------------------------------------------------------------------ pipeline (fake LLM + fake data)
def fake_bundle(symbol, exchange, trade_date, cfg, analysts, expiry=None):
    ch = make_chain(lean="bull")
    sig = signals.instant_signal(ch, make_fut(), VIX_OK, TECH_BULL, {"state": "open"})
    return {"identity": {"name": symbol}, "ysym": "^NSEI", "trade_date": trade_date, "context": "ctx", "chain": ch,
            "chain_pack": "chain data", "volatility_pack": "vol data", "flow_pack": "flow data", "news_pack": "news data",
            "signal": sig, "provenance": [{"item": "option_chain", "source": "TEST", "status": "ok"}],
            "market_meta": {"price": 25000.0, "quote": {"source": "TEST", "as_of_label": "now"}}}


def _run(tmp_path, monkeypatch, symbol="NIFTY"):
    monkeypatch.setattr(graph.D, "collect", fake_bundle)
    FakeLLM.calls = []
    jm = JobManager(tmp_path / "runs", tmp_path / "data", make_llm=FakeLLM)
    job = jm.submit({"symbol": symbol, "provider": "ollama", "deep_model": "m", "quick_model": "m", "depth": "shallow"})
    for _ in range(100):
        if job.status in ("done", "error", "cancelled"):
            break
        time.sleep(0.05)
    return jm, job


def test_pipeline_end_to_end(tmp_path, monkeypatch):
    jm, job = _run(tmp_path, monkeypatch)
    assert job.status == "done", job.error
    r = job.result
    assert r["rating"] == "Buy" and r["action"] == "BUY CE"
    p = fake_bundle("NIFTY", "NSE", "x", {}, [])["signal"]["primary"]
    assert r["entry"] == p["entry"] and r["stop"] < r["entry"] < r["target"]  # strike-as-premium parse was reconciled
    assert any("rule-engine" in w for w in r["warnings"]) and r["instrument"] == p["instrument"]
    assert (pathlib.Path(job.dir) / "complete_report.md").exists() and jm.report_file(job.id)
    assert jm.log.all()[0]["status"] == "pending" and jm.history()[0]["rating"] == "Buy"
    assert set(job.agents.values()) == {"completed"}


def test_job_rejects_bad_symbol(tmp_path):
    jm = JobManager(tmp_path / "r", tmp_path / "d", make_llm=FakeLLM)
    with pytest.raises(ValueError):
        jm.submit({"symbol": "NIFTY; DROP"})
    assert jm.get("../etc/passwd") is None


def test_analyst_failure_is_reported_not_fatal(tmp_path, monkeypatch):
    def partial(*a, **k):
        b = fake_bundle(*a, **k)
        b["news_pack"] = None
        return b
    monkeypatch.setattr(graph.D, "collect", partial)
    jm = JobManager(tmp_path / "r", tmp_path / "d", make_llm=FakeLLM)
    job = jm.submit({"symbol": "BANKNIFTY", "provider": "ollama", "deep_model": "m", "quick_model": "m"})
    for _ in range(100):
        if job.status in ("done", "error"):
            break
        time.sleep(0.05)
    assert job.status == "done" and job.agents["news"] == "error"


def test_decision_log_marks_hit(tmp_path, monkeypatch):
    import pandas as pd
    from copilot import market as mk
    idx = pd.bdate_range("2026-09-01", periods=10)
    df = pd.DataFrame({"Close": [100 + i for i in range(10)]}, index=idx)
    monkeypatch.setattr(mk, "history", lambda *a, **k: df)
    log = DecisionLog(tmp_path)
    log.add({"id": "a", "ysym": "^NSEI", "trade_date": "2026-09-02", "rating": "Buy", "ref_price": 101.0, "status": "pending", "decision": "d"})
    log.add({"id": "b", "ysym": "^NSEI", "trade_date": "2026-09-02", "rating": "Sell", "ref_price": 101.0, "status": "pending", "decision": "d"})
    log.resolve("^NSEI", FakeLLM("p", "m", {}), {"holding_period_days": 1})
    rec = {r["id"]: r for r in log.all()}
    assert rec["a"]["hit"] is True and rec["b"]["hit"] is False


# ------------------------------------------------------------------ config
def test_config_overrides(monkeypatch):
    monkeypatch.setenv("COPILOT_RISK_PER_TRADE_PCT", "2")
    assert config.build_config()["risk_per_trade_pct"] == 2.0
    cfg = config.build_config({"llm_provider": "openai"})
    assert cfg["deep_think_llm"] == "gpt-5.5"
    with pytest.raises(ValueError):
        config.build_config({"llm_provider": "nope"})


# ------------------------------------------------------------------ LLM transport
class _Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text, self.headers = code, text, {}

    def json(self):
        import json
        return json.loads(self.text)


def _groq(cfg):
    """LLM over the *same* dict the agents read, the way graph.run_pipeline wires it."""
    from copilot.llm import LLM
    cfg.update({"temperature": None, "max_tokens": 4096, "llm_max_retries": 3})
    return LLM("groq", "openai/gpt-oss-120b", cfg)


def test_oversized_prompt_shrinks_and_retries(monkeypatch):
    from copilot import agents as A
    cfg = {"debate_history_chars": 6000, "report_char_budget": 2600}
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json["messages"][1]["content"])
        return _Resp(413, "Request too large for model") if len(sent) < 3 else _Resp(200, '{"choices":[{"message":{"content":"ok"}}]}')

    monkeypatch.setattr("copilot.llm.requests.post", post)
    big = "strike data\n" * 20000
    reports = {"chain": big, "volatility": big, "flow": big, "news": big}
    assert A.risk(_groq(cfg), "neutral", "ctx", reports, "plan", "hist", {}, cfg).endswith("ok")
    assert len(sent) == 3
    assert len(sent[1]) < len(sent[0]) and len(sent[2]) < len(sent[1])
    assert cfg["debate_history_chars"] < 6000 and cfg["report_char_budget"] < 2600


def test_oversized_prompt_reports_clearly_when_it_cannot_shrink(monkeypatch):
    from copilot.llm import LLMError
    monkeypatch.setattr("copilot.llm.requests.post", lambda *a, **k: _Resp(413, "Request too large for model"))
    with pytest.raises(LLMError, match="too large"):
        _groq({"debate_history_chars": 900, "report_char_budget": 300}).invoke("sys", lambda: "prompt")


# ------------------------------------------------------------------ API
@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    from copilot import market as mk

    def fake_instant(sym, expiry=None):
        lean = "bear" if sym.upper() == "BANKNIFTY" else "bull"
        if sym.upper() == "BAD":
            raise LookupError("NSE blocked")
        ch = make_chain(lean=lean, name=sym.upper())
        fut = make_fut() if lean == "bull" else make_fut(buildup="short buildup", basis_pct=-0.12)
        sig = signals.instant_signal(ch, fut, VIX_OK, TECH_BULL if lean == "bull" else TECH_BEAR, {"state": "open"})
        return {"symbol": sym.upper(), "kind": "index", "exchange": "NSE", "chain": ch, "futures": fut, "vix": VIX_OK,
                "tech": TECH_BULL, "signal": sig}
    monkeypatch.setattr(D, "instant", fake_instant)
    server._cache.clear()
    monkeypatch.setattr(mk, "market_status", lambda now=None: {"state": "open", "label": "Market open", "now_ist": "x", "hours": "x"})
    return TestClient(server.app)


def test_api_signal_and_scan(client):
    r = client.get("/api/signal/nifty?rows=5").json()
    assert r["signal"]["primary"]["kind"] == "BUY_CE" and len(r["chain"]["rows"]) == 11
    assert r["chain"]["expiries"] and r["market"]["state"] == "open"
    scan = {x["symbol"]: x for x in client.get("/api/scan?symbols=NIFTY,bank nifty,BAD,nifty").json()}
    assert set(scan) == {"NIFTY", "BANKNIFTY", "BAD"} and scan["BANKNIFTY"]["kind"] == "BUY_PE" and "error" in scan["BAD"]
    assert client.get("/api/signal/BAD").status_code == 502


def test_api_static_and_config(client):
    assert "Strike &amp; Hedge" in client.get("/").text
    assert client.get("/app.js").status_code == 200 and client.get("/styles.css").status_code == 200
    cfg = client.get("/api/config").json()
    assert cfg["default_scan"][:2] == ["NIFTY", "BANKNIFTY"] and "chain" in cfg["analysts"]
    u = client.get("/api/underlyings").json()
    assert {i["symbol"] for i in u["indices"]} >= {"NIFTY", "BANKNIFTY", "SENSEX"}
    assert client.post("/api/runs", json={"symbol": "x;y"}).status_code == 400
    assert client.get("/api/runs/nope").status_code == 404


def test_scan_reports_a_missing_chain_as_outage_not_as_a_call(client, monkeypatch):
    """A dead feed must not render as an AVOID trading decision — that reads as a call the desk never made."""
    import server

    def nodata(sym, expiry=None):
        return {"symbol": sym, "exchange": "BSE", "futures": None,
                "chain": {"spot": 73000.0, "rows": [], "expiries": [], "source": "fallback"},
                "_quote_fallback": {"price": 73000.0, "change_pct": 0.77},
                "signal": {"bias": "AVOID", "confidence": 0, "metrics": {}, "setups": [],
                           "error": "Option chain unavailable from exchange"}}

    monkeypatch.setattr(server, "_instant", nodata)
    row = client.get("/api/scan?symbols=SENSEX").json()[0]
    assert row["error"] == "Option chain unavailable from exchange" and "bias" not in row
