"""Polymarket Gamma API — погодные рынки по городам (для арбитража Kalshi↔Poly). Без ключа."""
import requests
import json
import re
from config import POLY_GAMMA, CITIES
import db


def fetch(query):
    r = requests.get(f"{POLY_GAMMA}/markets",
                     params={"active": "true", "closed": "false", "limit": 100, "tag": "weather"},
                     timeout=30)
    r.raise_for_status()
    q = query.lower()
    return [m for m in r.json()
            if q in (m.get("question") or "").lower()
            and "temperature" in (m.get("question") or "").lower()]


def _bucket(q):
    a = re.findall(r"(-?\d+)\s*°?F?", q)
    ql = q.lower()
    if ("or higher" in ql or "above" in ql) and a:
        return float(a[-1]), None
    if ("or lower" in ql or "below" in ql) and a:
        return None, float(a[-1])
    if len(a) >= 2:
        return float(a[-2]), float(a[-1])
    return None, None


def run():
    total = 0
    for key, c in CITIES.items():
        try:
            rows = []
            for m in fetch(c["poly"]):
                prices = m.get("outcomePrices")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                yes = float(prices[0]) if prices else None
                lo, hi = _bucket(m.get("question", ""))
                rows.append(dict(
                    venue="polymarket", city=key,
                    target_date=m.get("endDate", "")[:10] or None,
                    market_id=m.get("conditionId") or str(m.get("id")),
                    title=m.get("question"), bucket_lo=lo, bucket_hi=hi,
                    yes_bid=m.get("bestBid"), yes_ask=m.get("bestAsk"), last_price=yes,
                    volume=m.get("volumeNum") or m.get("volume"),
                    open_interest=m.get("liquidityNum"), status="active", raw=m))
            total += db.insert("market_snapshots", rows)
        except Exception as e:
            db.log("polymarket", False, f"{key}: {e}")
    db.log("polymarket", True, f"rows={total}")
    return total