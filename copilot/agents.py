"""F&O desk prompts: chain / vol / flow / news -> bull/bear -> manager -> trader -> risk -> PM."""
import re

RATINGS = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
SCALE = """**Rating Scale** (use exactly one):
- **Buy**: High-conviction debit (BUY CE or BUY PE) or futures in the direction of the tape
- **Overweight**: Constructive; smaller size or slightly OTM
- **Hold**: Range / no trade / wait for OI confirmation
- **Underweight**: Mildly bearish or fade: defined-risk credit spread, smaller size
- **Sell**: High-conviction bearish: BUY PE or futures short"""
DECIDE = ("The debate always contains conflict; deciding which side is stronger is the job. "
          "Commit to Buy CE, Buy PE, Sell CE Spread, Sell PE Spread, Fut Long, Fut Short, or Avoid. "
          "Naked option selling is not allowed: premium selling must be a hedged credit spread. "
          "Choose Hold/Avoid only when PCR, OI walls and VIX truly disagree. Never invent chain numbers.")
TABLE = " Append a Markdown table of the key strikes / levels."


def lang(cfg):
    l = (cfg.get("output_language") or "English").strip()
    if l.lower() == "english":
        return ""
    return (f" Write your entire response in {l}, except labelled lines the format asks for "
            f"(e.g. \"**Rating**:\", \"**Action**:\", \"**Instrument**:\"), which keep English labels and values.")


def absent(text, label):
    return (text or "").strip() or f"(No {label} pack available.)"


def _cap(text, limit):
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n")
    if nl > limit * 0.6:
        cut = cut[:nl]
    return cut.rstrip() + "\n…[truncated]"


def _tail(text, limit):
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[-limit:]
    nl = cut.find("\n")
    if 0 <= nl < limit * 0.4:
        cut = cut[nl + 1:]
    return "…[earlier turns trimmed]\n" + cut


def _budget(cfg, key, default):
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _opp(text, who):
    return (text or "").strip() or f"(The {who} has not spoken yet.)"


def parse_rating(text, labels="Rating|Recommendation"):
    m = re.search(rf"\*\*(?:{labels})\*\*\s*:?\s*\**\s*(Buy|Overweight|Hold|Underweight|Sell)\b", text, re.I)
    if not m:
        m = re.search(rf"(?:{labels})\s*:\s*\**\s*(Buy|Overweight|Hold|Underweight|Sell)\b", text, re.I)
    if not m:
        m = re.search(r"\b(Overweight|Underweight|Buy|Sell|Hold)\b", text)
    return m.group(1).capitalize() if m else "Hold"


def parse_action(text):
    m = re.search(r"\*\*Action\*\*\s*:?\s*\**\s*([^\n]+)", text, re.I)
    if m:
        raw = re.sub(r"[*]", "", m.group(1)).strip()
        return raw.split(".")[0][:40]
    m = re.search(r"FINAL TRANSACTION PROPOSAL:\s*\**\s*([^*\n]+)", text, re.I)
    return m.group(1).strip() if m else "Avoid"


def parse_price(text, label):
    m = re.search(rf"\*\*{label}\*\*\s*:?\s*([^\n]*)", text, re.I)
    if not m:
        return None
    s = m.group(1)
    if re.search(r"\d\s*(?:-|–|to)\s*(?:₹|rs\.?)?\s*\d", s, re.I):
        return None
    for num, pct in re.findall(r"([0-9][0-9,]*\.?[0-9]*)\s*(%?)", s):
        if not pct:
            try:
                return float(num.replace(",", ""))
            except ValueError:
                continue
    return None


def parse_field(text, label):
    m = re.search(rf"\*\*{label}\*\*\s*:?\s*([^\n]+)", text, re.I)
    return re.sub(r"[*]", "", m.group(1)).strip() if m else None


def _analyst(llm, role, task, data, bundle, cfg):
    system = ("You are an F&O research analyst on an Indian derivatives desk. "
              f"Report what the verified pack supports; another agent decides the trade. "
              f"Analysis date: {bundle['trade_date']}. {bundle['context']}\n{task}{lang(cfg)}")
    return llm.invoke(system, f"{role} data pack (verified by code; the only facts you may cite):\n\n{data}")


def chain_analyst(llm, bundle, cfg):
    task = ("You are the Option Chain Analyst. Using ONLY the pack: interpret PCR (OI and volume), max pain vs spot, "
            "put/call OI walls as support/resistance, ATM CE vs PE, writing vs covering, and which strikes the market "
            "is defending. Do not invent OI. End with a one-line directional lean: bullish / bearish / range." + TABLE)
    return _analyst(llm, "Chain", task, absent(bundle.get("chain_pack"), "chain"), bundle, cfg)


def volatility_analyst(llm, bundle, cfg):
    task = ("You are the Volatility Analyst. Using ONLY the pack: India VIX, ATM IV, straddle expected move, DTE, "
            "and whether premium is rich or cheap relative to VIX. Say whether the desk should buy premium, sell "
            "premium, or stand aside. Flag event risk if headlines in other packs mention RBI/budget/results/FOMC. "
            "Do not invent IV rank." + TABLE)
    return _analyst(llm, "Volatility", task, absent(bundle.get("volatility_pack"), "volatility"), bundle, cfg)


def flow_analyst(llm, bundle, cfg):
    task = ("You are the Futures & Flow Analyst. Using ONLY the pack: futures premium/discount, OI buildup "
            "(long buildup / short buildup / short covering / long unwinding), RSI/MACD/ATR on the underlying, "
            "and the rule-based setups already computed (entry/stop/target). Agree or disagree with those setups "
            "using the same numbers — do not invent new prices." + TABLE)
    return _analyst(llm, "Flow", task, absent(bundle.get("flow_pack"), "flow"), bundle, cfg)


def news_analyst(llm, bundle, cfg):
    task = ("You are the News Analyst for an F&O desk. List dated headlines that can move Nifty/BankNifty/Sensex "
            "or this stock's options: RBI, inflation, FII/DII, geopolitics, results, ban-period / GSM if mentioned. "
            "If the pack is thin, say so. Do not invent headlines." + TABLE)
    return _analyst(llm, "News", task, absent(bundle.get("news_pack"), "news"), bundle, cfg)


ANALYST_FN = {"chain": chain_analyst, "volatility": volatility_analyst, "flow": flow_analyst, "news": news_analyst}
ANALYST_LABEL = {"chain": "Chain Analyst", "volatility": "Volatility Analyst", "flow": "Flow Analyst", "news": "News Analyst"}


def _reports(r, budget=2600):
    return (f"Chain report: {_cap(absent(r.get('chain'), 'chain'), budget)}\n"
            f"Volatility report: {_cap(absent(r.get('volatility'), 'volatility'), budget)}\n"
            f"Flow report: {_cap(absent(r.get('flow'), 'flow'), budget)}\n"
            f"News report: {_cap(absent(r.get('news'), 'news'), budget)}")


def bull(llm, ctx, reports, history, last_bear, cfg):
    hist = _tail(history, _budget(cfg, "debate_history_chars", 6000))
    opp = _cap(_opp(last_bear, "bear"), _budget(cfg, "debate_history_chars", 6000))
    p = (f"You are the Bull F&O Analyst. Argue for longs: BUY CE, futures long, or put writing only if defined-risk "
         f"is already in the pack. Cite PCR, put walls, futures buildup.\n\n{ctx}\n{reports}\n"
         f"Debate history: {hist}\nLast bear: {opp}\nCounter the bear with numbers from the pack.")
    return "Bull Analyst: " + llm.invoke("You are the Bull Researcher on an Indian F&O desk." + lang(cfg), p, max_tokens=1600)


def bear(llm, ctx, reports, history, last_bull, cfg):
    hist = _tail(history, _budget(cfg, "debate_history_chars", 6000))
    opp = _cap(_opp(last_bull, "bull"), _budget(cfg, "debate_history_chars", 6000))
    p = (f"You are the Bear F&O Analyst. Argue for shorts: BUY PE, futures short, or call writing only if in the pack. "
         f"Cite call walls, VIX, weak PCR, short buildup.\n\n{ctx}\n{reports}\n"
         f"Debate history: {hist}\nLast bull: {opp}\nCounter the bull with numbers from the pack.")
    return "Bear Analyst: " + llm.invoke("You are the Bear Researcher on an Indian F&O desk." + lang(cfg), p, max_tokens=1600)


def research_manager(llm, ctx, history, cfg):
    hist = _tail(history, _budget(cfg, "debate_history_chars", 6000))
    p = (f"As Research Manager, pick a side and write the F&O plan for the trader.\n\n{ctx}\n\n{SCALE}\n\n{DECIDE}\n\n"
         f"**Debate:**\n{hist}\n\n## Output\n"
         "- **Recommendation**: Buy / Overweight / Hold / Underweight / Sell\n"
         "- **Rationale**\n- **Preferred structure**: BUY CE / BUY PE / SELL CE SPREAD / SELL PE SPREAD / FUT LONG / FUT SHORT / AVOID\n"
         "- **Key levels**: put wall, call wall, max pain")
    return llm.invoke("You are the Research Manager on an F&O desk." + lang(cfg), p)


def trader(llm, ctx, plan, flow_report, live_note, rule_signal, cfg):
    sysm = ("You are the F&O trader. Propose ONE primary structure with rupee prices. "
            "Ground entry/stop/target in the verified chain: option LTP or futures LTP, OI walls, ATR. "
            f"Risk about {cfg.get('risk_per_trade_pct', 1)}% of a ₹1,00,000 notional at the stop. "
            "A rule-based engine already computed candidate setups — prefer those numbers; do not invent LTPs. "
            "Absolute rupees, not percentages." + lang(cfg))
    u = (f"Research plan:\n{_cap(plan, _budget(cfg, 'debate_history_chars', 6000))}\n\n{ctx}\n{live_note}\n\n"
         f"Flow/technical report:\n{_cap(absent(flow_report, 'flow'), 2 * _budget(cfg, 'report_char_budget', 2600))}\n\n"
         f"RULE ENGINE (code, not optional colour):\n{_cap(str(rule_signal)[:3500], 3500)}\n\n"
         "## Output\n"
         "- **Action**: BUY CE / BUY PE / SELL CE SPREAD / SELL PE SPREAD / FUT LONG / FUT SHORT / AVOID\n"
         "- **Instrument**: e.g. NIFTY 25000 CE 09-Oct-2025\n"
         "- **Entry**, **Stop Loss**, **Target**, **Exit** (when to book / trail)\n"
         "- **Lots** and **Max loss ₹**\n- **Reasoning**\n"
         "End with: FINAL TRANSACTION PROPOSAL: **BUY CE/BUY PE/SELL CE SPREAD/SELL PE SPREAD/FUT LONG/FUT SHORT/AVOID**")
    return llm.invoke(sysm, u)


RISK_ROLES = {
    "aggressive": ("Aggressive Risk Analyst", "push for size when PCR, walls and futures agree; still name a hard stop."),
    "conservative": ("Conservative Risk Analyst", "cut size, prefer defined-risk, warn about theta, gap, ban period, VIX spikes."),
    "neutral": ("Neutral Risk Analyst", "balance theta vs gamma, event risk, and whether the rule engine's stop is tradable."),
}


def risk(llm, who, ctx, reports, trader_plan, history, last, cfg):
    title, mission = RISK_ROLES[who]
    b = _budget(cfg, "debate_history_chars", 6000)
    others = "\n".join(f"Last {k}: {_cap(_opp(last.get(k), k), b)}" for k in RISK_ROLES if k != who)
    hist = _tail(history, b)
    p = (f"As the {title}, {mission}\n\nTrader:\n{_cap(trader_plan, b)}\n\n{ctx}\n{reports}\nHistory: {hist}\n{others}\n"
         "Debate the others. Cite only pack facts. Reject any naked short option outright; credit spreads need a hedge leg.")
    return f"{title.split()[0]} Analyst: " + llm.invoke(f"You are the {title}." + lang(cfg), p, max_tokens=1600)


def portfolio_manager(llm, ctx, research_plan, trader_plan, history, lessons, cfg):
    les = f"- Lessons:\n{lessons}\n" if lessons else ""
    b = _budget(cfg, "debate_history_chars", 6000)
    p = (f"As Portfolio Manager, approve or reject the F&O proposal.\n\n{ctx}\n\n{SCALE}\n\n"
         f"Research: {_cap(research_plan, b)}\nTrader: {_cap(trader_plan, b)}\n{les}\nRisk debate:\n{_tail(history, b)}\n\n"
         f"{DECIDE}\n\n## Output\n- **Rating**: Buy / Overweight / Hold / Underweight / Sell\n"
         "- **Action** and **Instrument**\n- **Entry**, **Stop Loss**, **Target**, **Exit**\n"
         "- **Executive Summary**\n- **What would kill the thesis**")
    return llm.invoke("You are the Portfolio Manager on an Indian F&O desk." + lang(cfg), p)


def reflect(llm, decision, outcome, cfg):
    p = (f"Past F&O call:\n{decision}\n\nOutcome: {outcome}\n\nOne paragraph ≤90 words: what the chain read got right or wrong.")
    return llm.invoke("You review past F&O decisions." + lang(cfg), p, max_tokens=400)
