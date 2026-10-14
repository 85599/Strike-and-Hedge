"""F&O universe: index underlyings + liquid stock options. Lot sizes are fallbacks;
live lot is taken from the exchange payload when present (SEBI revises lots)."""

INDEX_FNO = {
    "NIFTY": {
        "kind": "index", "exchange": "NSE", "yahoo": "^NSEI", "nse_name": "NIFTY 50",
        "oc": "indices", "lot": 65, "step": 50,
    },
    "BANKNIFTY": {
        "kind": "index", "exchange": "NSE", "yahoo": "^NSEBANK", "nse_name": "NIFTY BANK",
        "oc": "indices", "lot": 15, "step": 100,
    },
    "FINNIFTY": {
        "kind": "index", "exchange": "NSE", "yahoo": "^CNXFINANCE", "nse_name": "NIFTY FINANCIAL SERVICES",
        # Yahoo delisted ^CNXFINANCE, so daily history comes from NSE's index site instead.
        "nifty_index": "NIFTY FIN SERVICE",
        "oc": "indices", "lot": 25, "step": 50,
    },
    "MIDCPNIFTY": {
        "kind": "index", "exchange": "NSE", "yahoo": "^NSEMDCP50", "nse_name": "NIFTY MIDCAP SELECT",
        "oc": "indices", "lot": 50, "step": 25,
    },
    "SENSEX": {
        "kind": "index", "exchange": "BSE", "yahoo": "^BSESN", "nse_name": None,
        "oc": "bse", "lot": 20, "step": 100, "bse_scrip": 1,
    },
    "BANKEX": {
        "kind": "index", "exchange": "BSE", "yahoo": "^BSEBANK", "nse_name": None,
        # No bse_scrip: BSE discontinued Bankex index options, so there is no chain to load.
        "oc": "bse", "lot": 15, "step": 100,
    },
}

# Liquid NSE stock F&O (not exhaustive; any NSE equity can still be requested).
STOCK_FNO = {
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "ITC", "LT", "BHARTIARTL",
    "AXISBANK", "KOTAKBANK", "HINDUNILVR", "BAJFINANCE", "MARUTI", "TATAMOTORS", "TATASTEEL",
    "HCLTECH", "SUNPHARMA", "WIPRO", "ADANIENT", "ADANIPORTS", "ASIANPAINT", "BAJAJFINSV",
    "TITAN", "ULTRACEMCO", "NESTLEIND", "POWERGRID", "NTPC", "ONGC", "COALINDIA", "JSWSTEEL",
    "HINDALCO", "TECHM", "M&M", "TATACONSUM", "CIPLA", "DRREDDY", "EICHERMOT", "GRASIM",
    "INDUSINDBK", "BPCL", "HEROMOTOCO", "BRITANNIA", "DIVISLAB", "APOLLOHOSP", "SHRIRAMFIN",
    "TRENT", "BEL", "HAL", "DLF", "LODHA", "IRFC", "PFC", "RECLTD", "BANKBARODA", "PNB",
    "CANBK", "FEDERALBNK", "IDFCFIRSTB", "AUROPHARMA", "LUPIN", "BIOCON", "ZYDUSLIFE",
    "VEDL", "HINDZINC", "NMDC", "SAIL", "JINDALSTEL", "TATAPOWER", "ADANIGREEN", "ADANIPOWER",
}

ALIASES = {
    "NIFTY50": "NIFTY", "NIFTY 50": "NIFTY", "CNXNIFTY": "NIFTY",
    "BANK NIFTY": "BANKNIFTY", "NIFTY BANK": "BANKNIFTY", "NIFTYBANK": "BANKNIFTY",
    "FIN NIFTY": "FINNIFTY", "NIFTY FIN": "FINNIFTY", "FINNIFTY": "FINNIFTY",
    "MIDCAP NIFTY": "MIDCPNIFTY", "NIFTY MIDCAP": "MIDCPNIFTY",
    "BSE SENSEX": "SENSEX", "S&P BSE SENSEX": "SENSEX",
    "BSE BANKEX": "BANKEX",
}

# Approximate index members (Yahoo tickers without .NS) for the Market Overview
# screener and breadth panels. Reviewed lists, not exchange-official weights.
YAHOO_INDEX_MEMBERS = {
    "NIFTY 50": [
        "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "BHARTIARTL", "ITC",
        "LT", "HINDUNILVR", "BAJFINANCE", "AXISBANK", "KOTAKBANK", "ASIANPAINT", "MARUTI",
        "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND", "WIPRO", "HCLTECH", "NTPC",
        "POWERGRID", "ONGC", "COALINDIA", "M&M", "TATAMOTORS", "TATASTEEL", "JSWSTEEL",
        "ADANIPORTS", "ADANIENT", "BAJAJFINSV", "GRASIM", "TECHM", "BRITANNIA",
        "APOLLOHOSP", "EICHERMOT", "DRREDDY", "CIPLA", "DIVISLAB", "HEROMOTOCO",
        "HINDALCO", "VEDL", "SBILIFE", "HDFCLIFE", "TATACONSUM", "INDUSINDBK",
        "SHRIRAMFIN", "BPCL", "TRENT"],
    "NIFTY BANK": [
        "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "INDUSINDBK",
        "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB", "PNB", "BANKBARODA", "AUBANK"],
    "NIFTY IT": [
        "TCS", "INFY", "HCLTECH", "WIPRO", "TECHM", "LTIM", "PERSISTENT", "COFORGE",
        "MPHASIS", "OFSS"],
    "NIFTY AUTO": [
        "MARUTI", "TATAMOTORS", "M&M", "EICHERMOT", "HEROMOTOCO", "BAJAJ-AUTO",
        "TVSMOTOR", "ASHOKLEY", "BOSCHLTD", "MOTHERSON", "BHARATFORG", "EXIDEIND",
        "MRF", "BALKRISIND", "SONACOMS"],
    "NIFTY PHARMA": [
        "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "AUROPHARMA", "LUPIN",
        "TORNTPHARM", "ALKEM", "IPCALAB", "GLENMARK"],
    "NIFTY FMCG": [
        "HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "GODREJCP", "MARICO",
        "COLPAL", "TATACONSUM", "EMAMILTD"],
    "NIFTY METAL": [
        "TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "COALINDIA", "JINDALSTEL",
        "SAIL", "NMDC", "NATIONALUM", "APLAPOLLO", "RATNAMANI", "WELCORP", "MOIL",
        "GRAPHITE", "HINDZINC"],
    "NIFTY FIN SERVICE": [
        "BAJFINANCE", "BAJAJFINSV", "SHRIRAMFIN", "CHOLAFIN", "MUTHOOTFIN", "SBICARD",
        "MANAPPURAM", "LTF", "PFC", "RECLTD"],
}

# Liquid F&O names polled for the Futures Activity (buildup) panel.
FUT_ACTIVE_POOL = [
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK",
    "SBIN", "INFY", "TCS", "ITC", "LT", "AXISBANK", "BAJFINANCE", "TATAMOTORS",
    "TATASTEEL", "JSWSTEEL", "ADANIPORTS", "ADANIENT", "M&M", "MARUTI", "SUNPHARMA",
    "HCLTECH", "TECHM", "VEDL", "HINDALCO", "BPCL", "ONGC", "NTPC", "TRENT",
]


def canonical(sym: str) -> str:
    s = (sym or "").strip().upper().replace(".NS", "").replace(".BO", "")
    s = s.replace(" ", "")
    return ALIASES.get(s) or ALIASES.get((sym or "").strip().upper()) or s


def meta(sym: str) -> dict:
    s = canonical(sym)
    if s in INDEX_FNO:
        return {"symbol": s, **INDEX_FNO[s]}
    return {
        "symbol": s, "kind": "stock", "exchange": "NSE", "yahoo": f"{s}.NS",
        "nse_name": None, "oc": "equities", "lot": 1, "step": None,
    }


def is_index(sym: str) -> bool:
    return canonical(sym) in INDEX_FNO
