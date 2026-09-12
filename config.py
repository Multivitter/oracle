"""Конфиг оракула. Города = там, где у Kalshi ежедневные рынки высокой температуры."""
import os
from dotenv import load_dotenv

load_dotenv()

DB_DSN = os.environ["DATABASE_URL"]
DB_SCHEMA = os.getenv("ORACLE_SCHEMA", "oracle")

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLY_GAMMA = "https://gamma-api.polymarket.com"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
NWS_BASE = "https://api.weather.gov"
NWS_UA = os.getenv("NWS_USER_AGENT", "oracle-logger (contact: you@example.com)")

OM_MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_seamless"]

CITIES = {
    "NYC":  dict(lat=40.7789, lon=-73.9692, tz="America/New_York",    nws="KNYC", kalshi="KXHIGHNY",   poly="NYC"),
    "CHI":  dict(lat=41.7868, lon=-87.7522, tz="America/Chicago",     nws="KMDW", kalshi="KXHIGHCHI",  poly="Chicago"),
    "MIA":  dict(lat=25.7959, lon=-80.2870, tz="America/New_York",    nws="KMIA", kalshi="KXHIGHMIA",  poly="Miami"),
    "AUS":  dict(lat=30.3208, lon=-97.7604, tz="America/Chicago",     nws="KATT", kalshi="KXHIGHAUS",  poly="Austin"),
    "DEN":  dict(lat=39.8561, lon=-104.6737, tz="America/Denver",     nws="KDEN", kalshi="KXHIGHDEN",  poly="Denver"),
    "LAX":  dict(lat=33.9425, lon=-118.4081, tz="America/Los_Angeles", nws="KLAX", kalshi="KXHIGHLAX",  poly="Los Angeles"),
    "PHL":  dict(lat=39.8729, lon=-75.2437, tz="America/New_York",    nws="KPHL", kalshi="KXHIGHPHIL", poly="Philadelphia"),
}

SNAPSHOT_HOURS_UTC = [11, 17, 23]
OUTCOME_HOUR_UTC = 12