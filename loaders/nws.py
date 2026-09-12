"""NWS: официальный прогноз (то, на что смотрит рынок) + наблюдения для исходов."""
import requests
import datetime as dt
from zoneinfo import ZoneInfo
from config import NWS_BASE, NWS_UA, CITIES
import db

H = {"User-Agent": NWS_UA, "Accept": "application/geo+json"}
_grid_cache = {}


def _forecast_url(c):
    k = (c["lat"], c["lon"])
    if k not in _grid_cache:
        p = requests.get(f"{NWS_BASE}/points/{c['lat']},{c['lon']}", headers=H, timeout=30).json()
        _grid_cache[k] = p["properties"]["forecast"]
    return _grid_cache[k]


def fetch_forecast(key, c):
    f = requests.get(_forecast_url(c), headers=H, timeout=30).json()
    rows = []
    for per in f["properties"]["periods"]:
        if not per.get("isDaytime"):
            continue
        day = per["startTime"][:10]
        t = per["temperature"] if per.get("temperatureUnit") == "F" else per["temperature"] * 9 / 5 + 32
        rows.append(dict(city=key, target_date=day, source="nws", tmax_f=t, raw=per))
    return rows


def run_forecast():
    total = 0
    for key, c in CITIES.items():
        try:
            total += db.insert("model_forecasts", fetch_forecast(key, c))
        except Exception as e:
            db.log("nws_forecast", False, f"{key}: {e}")
    db.log("nws_forecast", True, f"rows={total}")
    return total


def fetch_outcome(key, c, day):
    """Максимум по часовым наблюдениям станции за локальные сутки."""
    tz = ZoneInfo(c["tz"])
    start = dt.datetime.combine(day, dt.time(0), tz)
    end = start + dt.timedelta(days=1)
    r = requests.get(f"{NWS_BASE}/stations/{c['nws']}/observations",
                     params={"start": start.isoformat(), "end": end.isoformat(), "limit": 500},
                     headers=H, timeout=30)
    r.raise_for_status()
    temps = []
    for ob in r.json()["features"]:
        t = ob["properties"].get("temperature", {}).get("value")
        if t is not None:
            temps.append(t * 9 / 5 + 32)
    if not temps:
        return None
    return dict(city=key, target_date=day, tmax_f=round(max(temps), 1),
                source=f"nws_obs_{c['nws']}",
                raw={"n_obs": len(temps), "max_c": max(temps)})


def run_outcomes(day=None):
    day = day or (dt.date.today() - dt.timedelta(days=1))
    n = 0
    for key, c in CITIES.items():
        try:
            row = fetch_outcome(key, c, day)
            if row:
                db.upsert_outcome(row)
                n += 1
        except Exception as e:
            db.log("nws_outcome", False, f"{key}: {e}")
    db.log("nws_outcome", True, f"day={day} rows={n}")
    return n