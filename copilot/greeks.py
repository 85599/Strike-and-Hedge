"""Black–Scholes Greeks for NSE/BSE index & stock options (European). Pure Python."""
import math

N = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
n = lambda x: math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(s, k, t, r, q, sigma):
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return None, None
    v = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / v
    return d1, d1 - v


def bs_price(s, k, t, r, sigma, kind="CE", q=0.0):
    d1, d2 = _d1_d2(s, k, t, r, q, sigma)
    if d1 is None:
        return max(s - k, 0.0) if kind == "CE" else max(k - s, 0.0)
    if kind == "CE":
        return s * math.exp(-q * t) * N(d1) - k * math.exp(-r * t) * N(d2)
    return k * math.exp(-r * t) * N(-d2) - s * math.exp(-q * t) * N(-d1)


def greeks(s, k, t, r, sigma, kind="CE", q=0.0):
    """Delta, gamma, vega (per 1 vol point), theta (per day), rho (per 1% rate)."""
    d1, d2 = _d1_d2(s, k, t, r, q, sigma)
    if d1 is None:
        intrinsic = max(s - k, 0.0) if kind == "CE" else max(k - s, 0.0)
        return {"delta": 1.0 if kind == "CE" and s > k else (-1.0 if kind == "PE" and s < k else 0.0),
                "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0, "price": intrinsic}
    disc_q, disc_r = math.exp(-q * t), math.exp(-r * t)
    gamma = disc_q * n(d1) / (s * sigma * math.sqrt(t))
    vega = s * disc_q * n(d1) * math.sqrt(t) / 100.0
    if kind == "CE":
        delta = disc_q * N(d1)
        theta = (-s * disc_q * n(d1) * sigma / (2 * math.sqrt(t))
                 - r * k * disc_r * N(d2) + q * s * disc_q * N(d1)) / 365.0
        rho = k * t * disc_r * N(d2) / 100.0
    else:
        delta = -disc_q * N(-d1)
        theta = (-s * disc_q * n(d1) * sigma / (2 * math.sqrt(t))
                 + r * k * disc_r * N(-d2) - q * s * disc_q * N(-d1)) / 365.0
        rho = -k * t * disc_r * N(-d2) / 100.0
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho,
            "price": bs_price(s, k, t, r, sigma, kind, q)}


def implied_vol(price, s, k, t, r=0.065, kind="CE", q=0.0, lo=1e-4, hi=5.0):
    """Bisection IV. Returns None if the quote is off the BS range."""
    if price is None or price <= 0 or t <= 0 or s <= 0 or k <= 0:
        return None
    intrinsic = max(s * math.exp(-q * t) - k * math.exp(-r * t), 0.0) if kind == "CE" else \
        max(k * math.exp(-r * t) - s * math.exp(-q * t), 0.0)
    if price < intrinsic * 0.98:
        return None
    a, b = lo, hi
    fa = bs_price(s, k, t, r, a, kind, q) - price
    fb = bs_price(s, k, t, r, b, kind, q) - price
    # Outside the bracket the quote carries no solvable volatility (deep-ITM bids trade below
    # the lowest-vol model price). Returning the boundary would surface as a fake 500% IV.
    if fa > 0 or fb < 0:
        return None
    for _ in range(60):
        m = 0.5 * (a + b)
        fm = bs_price(s, k, t, r, m, kind, q) - price
        if abs(fm) < 1e-4:
            return m
        if fa * fm <= 0:
            b = m
        else:
            a, fa = m, fm
    return 0.5 * (a + b)


def year_fraction(expiry_date, now, calendar="365"):
    """expiry_date/now: datetime. Date-only uses IST close (15:30) as expiry."""
    seconds = (expiry_date - now).total_seconds()
    if seconds <= 0:
        return 1 / (365 * 24 * 60 * 60)
    return seconds / (365.0 * 24 * 3600) if calendar == "365" else seconds / (365.25 * 24 * 3600)
