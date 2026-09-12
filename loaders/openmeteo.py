"""Ансамбль моделей Open-Meteo: дневной максимум по каждой модели отдельно. Без ключа."""
import requests
from config import OPEN_METEO, OM_MODELS, CITIES
import db


def fetch_city(key, c, days=3):
    r = requests.get(OPEN_METEO, params=dict(
        latitude=c["lat"], longitude=c["lon"], daily="temperature_2m_max",
        temperature_unit="fahrenheit", timezone=c["tz"], forecast_days=days,
        models=",".join(OM_MODELS)), timeout=30)
    r.raise_for_status()
    d = r.json()["daily"]
    rows = []
    for i, day in enumerate(d["time"]):
        for m in OM_MODELS:
            col = f"temperature_2m_max_{m}"
            if col not in d:
                col = "temperature_2m_max"
            v = d[col][i]
            if v is None:
                continue
            rows.append(dict(city=key, target_date=day, source=m, tmax_f=v, raw=None))
    return rows


def run():
    total = 0
    for key, c in CITIES.items():
        try:
            total += db.insert("model_forecasts", fetch_city(key, c))
        except Exception as e:
            db.log("openmeteo", False, f"{key}: {e}")
    db.log("openmeteo", True, f"rows={total}")
    return total