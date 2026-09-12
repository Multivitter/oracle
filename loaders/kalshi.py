"""Kalshi public market data (без ключа). Поля *_dollars уже в долларах."""
import requests
import re
import datetime as dt
from config import KALSHI_BASE, CITIES
import db


def _bucket(m):
    """Возвращает (lo, hi) в градусах — включительные границы целых значений.
    strike_type: greater (>N → lo=N+1), less (<N → hi=N-1),
                 between/иное (floor..cap включительно)."""
    st = (m.get("strike_type") or "").lower()
    lo, hi = m.get("floor_strike"), m.get("cap_strike")
    if st == "greater" and lo is not None:
        return float(lo) + 1, None
    if st == "greater_or_equal" and lo is not None:
        return float(lo), None
    if st == "less" and hi is not None:
        return None, float(hi) - 1
    if st == "less_or_equal" and hi is not None:
        return None, float(hi)
    if lo is not None or hi is not None:
        return (float(lo) if lo is not None else None,
                float(hi) if hi is not None else None)
    s = (m.get("yes_sub_title") or m.get("subtitle") or "")
    a = re.findall(r"(-?\d+)", s)
    sl = s.lower()
    if ("above" in sl or "higher" in sl) and a:
        return float(a[0]), None
    if ("below" in sl or "lower" in sl) and a:
        return None, float(a[0])
    if len(a) >= 2:
        return float(a[0]), float(a[1])
    return None, None


def _date_from_ticker(t):
    """KXHIGHPHIL-26SEP13-T87 → 2026-09-13"""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})-", t)
    if not m:
        return None
    try:
        return dt.datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}", "%y%b%d").date()
    except ValueError:
        return None


def _num(m, *keys):
    """Первое непустое значение из перечисленных ключей, как float."""
    for k in keys:
        v = m.get(k)
        if v is not None and v != "":
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def fetch_series(series):
    out, cursor = [], None
    while True:
        p = {"series_ticker": series, "status": "open", "limit": 200}
        if cursor:
            p["cursor"] = cursor
        r = requests.get(f"{KALSHI_BASE}/markets", params=p, timeout=30)
        r.raise_for_status()
        j = r.json()
        out += j.get("markets", [])
        cursor = j.get("cursor")
        if not cursor:
            break
    return out


def run():
    total = 0
    for key, c in CITIES.items():
        try:
            rows = []
            for m in fetch_series(c["kalshi"]):
                lo, hi = _bucket(m)
                rows.append(dict(
                    venue="kalshi", city=key, target_date=_date_from_ticker(m["ticker"]),
                    market_id=m["ticker"], title=m.get("title"),
                    bucket_lo=lo, bucket_hi=hi,
                    yes_bid=_num(m, "yes_bid_dollars"),
                    yes_ask=_num(m, "yes_ask_dollars"),
                    last_price=_num(m, "last_price_dollars"),
                    volume=_num(m, "volume_fp", "volume_24h_fp"),
                    open_interest=_num(m, "open_interest_fp"),
                    status=m.get("status"), raw=m))
            total += db.insert("market_snapshots", rows)
        except Exception as e:
            db.log("kalshi", False, f"{key}: {e}")
    db.log("kalshi", True, f"rows={total}")
    return total