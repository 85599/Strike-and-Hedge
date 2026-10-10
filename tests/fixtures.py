"""Synthetic option chains + fake LLM for offline tests and the UI harness."""
import datetime as dt
import math

from copilot import chain as chain_mod
from copilot.greeks import bs_price
from copilot.nse import IST


def expiry_str(days=5):
    return (dt.datetime.now(IST) + dt.timedelta(days=days)).strftime("%d-%b-%Y")


def make_chain(spot=25000.0, step=50, n=20, lean="bull", iv=0.14, lot=75, name="NIFTY", days=5):
    """lean='bull': heavy put OI + put writing below spot; 'bear': mirror; 'range': balanced."""
    exp = expiry_str(days)
    t = max(days, 0.5) / 365.0
    atm = round(spot / step) * step
    rows = []
    for i in range(-n, n + 1):
        k = atm + i * step
        ce_p = max(bs_price(spot, k, t, 0.065, iv, "CE"), 0.05)
        pe_p = max(bs_price(spot, k, t, 0.065, iv, "PE"), 0.05)
        base = 100000 * math.exp(-abs(i) / 7)
        coi, poi = base, base
        cch, pch = base * 0.02, base * 0.02
        if lean == "bull":
            poi *= 1.0 + (3.0 if i == -3 else 0.6 if i < 0 else 0)
            coi *= 0.6 if i < 0 else 1.0
            pch = base * 0.25 if -4 <= i <= 0 else pch
            cch = -base * 0.05 if i > 0 else cch
        elif lean == "bear":
            coi *= 1.0 + (3.0 if i == 3 else 0.6 if i > 0 else 0)
            poi *= 0.6 if i > 0 else 1.0
            cch = base * 0.25 if 0 <= i <= 4 else cch
            pch = -base * 0.05 if i < 0 else pch
        rows.append({"strike": float(k),
                     "ce": {"ltp": round(ce_p, 2), "iv": iv * 100, "oi": coi, "oi_chg": cch, "volume": coi * 0.5,
                            "change": {"bull": 2.0, "bear": -2.0}.get(lean, -2.0)},
                     "pe": {"ltp": round(pe_p, 2), "iv": iv * 100, "oi": poi, "oi_chg": pch, "volume": poi * 0.5,
                            "change": {"bull": -2.0, "bear": 2.0}.get(lean, -2.0)}})
    chain = {"underlying": name, "spot": spot, "expiry": exp, "expiries": [exp, expiry_str(days + 7)], "rows": rows,
             "lot_size": lot, "source": "TEST", "as_of": "now", "kind": "indices"}
    return chain_mod._attach_greeks(chain)


def make_fut(spot=25000.0, buildup="long buildup", basis_pct=0.12):
    ltp = spot * (1 + basis_pct / 100)
    return {"ltp": ltp, "change": 40.0, "oi": 1e6, "oi_chg": 5e4, "lot_size": 75, "expiry": expiry_str(25),
            "buildup": buildup, "spot": spot, "basis": ltp - spot, "basis_pct": basis_pct}


TECH_BULL = {"rsi": 64.0, "macdh": 12.0, "atr": 180.0, "close_10_ema": 24900.0, "close_50_sma": 24500.0}
TECH_BEAR = {"rsi": 35.0, "macdh": -12.0, "atr": 180.0, "close_10_ema": 25100.0, "close_50_sma": 25500.0}
VIX_OK = {"price": 13.5, "change_pct": -1.0, "source": "TEST", "as_of_label": "now", "stale": False}
VIX_HIGH = {**VIX_OK, "price": 24.0}


class FakeLLM:
    calls = []

    def __init__(self, provider, model, cfg, usage=None):
        self.usage, self.model = usage, model

    def invoke(self, system, user, max_tokens=None):
        FakeLLM.calls.append(system[:40])
        if self.usage:
            self.usage.add(100, 50)
        if "Research Manager" in system:
            return "**Recommendation**: Buy\n\n**Rationale**: puts defended.\n\n**Preferred structure**: BUY CE"
        if "F&O trader" in system:
            return ("**Action**: BUY CE\n\n**Instrument**: NIFTY 25100 CE\n\n**Entry**: 25100\n\n**Stop Loss**: 22\n\n"
                    "**Target**: 90\n\n**Exit**: book half at target\n\nFINAL TRANSACTION PROPOSAL: **BUY CE**")
        if "Portfolio Manager" in system:
            return "**Rating**: Buy\n\n**Action**: BUY CE\n\n**Executive Summary**: act."
        if "review past" in system:
            return "Lesson: size smaller."
        return f"[{system[:30]}] argument\n| a | b |"
