"""Синтез: ансамбль моделей → калиброванная вероятность по каждому диапазону рынка.
v1: нормальное распределение вокруг взвешенного среднего.
SD намеренно консервативный: bias моделей по городам пока неизвестен,
считаем его после 2-3 недель данных и только тогда сужаем."""
import math
import datetime as dt
import db

WEIGHTS = {"ecmwf_ifs025": 0.35, "gfs_seamless": 0.25, "icon_seamless": 0.15,
           "gem_seamless": 0.10, "nws": 0.15}
SD_FLOOR = 3.0      # °F — минимальная неопределённость (день вперёд)
SD_PER_DAY = 1.0    # добавка за каждый день горизонта
SD_HARD_MIN = 3.0   # ниже не опускаемся ни при каком согласии моделей


def _cdf(x, mu, sd):
    return 0.5 * (1 + math.erf((x - mu) / (sd * math.sqrt(2))))


def prob_bucket(lo, hi, mu, sd):
    """Границы уже приведены к включительным целым: [lo..hi] → [lo-0.5, hi+0.5)"""
    a = -1e9 if lo is None else float(lo) - 0.5
    b = 1e9 if hi is None else float(hi) + 0.5
    return max(0.0, min(1.0, _cdf(b, mu, sd) - _cdf(a, mu, sd)))


def latest_forecasts(city, target_date):
    return db.query("""
      SELECT DISTINCT ON (source) source, tmax_f FROM model_forecasts
      WHERE city=%s AND target_date=%s AND tmax_f IS NOT NULL
      ORDER BY source, fetched_at DESC""", (city, target_date))


def latest_markets(city, target_date, venue="kalshi"):
    return db.query("""
      SELECT DISTINCT ON (market_id) market_id, bucket_lo, bucket_hi,
             yes_bid, yes_ask, last_price
      FROM market_snapshots WHERE venue=%s AND city=%s AND target_date=%s
      ORDER BY market_id, fetched_at DESC""", (venue, city, target_date))


def run(days_ahead=(0, 1, 2)):
    n = 0
    today = dt.date.today()
    for row in db.query("SELECT DISTINCT city FROM model_forecasts"):
        city = row["city"]
        for d in days_ahead:
            td = today + dt.timedelta(days=d)
            fc = latest_forecasts(city, td)
            if len(fc) < 2:
                continue
            w = [(float(f["tmax_f"]), WEIGHTS.get(f["source"], 0.1)) for f in fc]
            mu = sum(v * k for v, k in w) / sum(k for _, k in w)
            spread = math.sqrt(sum(k * (v - mu) ** 2 for v, k in w) / sum(k for _, k in w))
            sd = max(SD_FLOOR + SD_PER_DAY * d, spread * 1.2, SD_HARD_MIN)
            rows = []
            for m in latest_markets(city, td):
                p = prob_bucket(m["bucket_lo"], m["bucket_hi"], mu, sd)
                mid = None
                if m["yes_bid"] is not None and m["yes_ask"] is not None:
                    mid = (float(m["yes_bid"]) + float(m["yes_ask"])) / 2
                elif m["last_price"] is not None:
                    mid = float(m["last_price"])
                rows.append(dict(
                    city=city, target_date=td, market_id=m["market_id"],
                    bucket_lo=m["bucket_lo"], bucket_hi=m["bucket_hi"],
                    ens_mean=round(mu, 2), ens_sd=round(sd, 2), n_models=len(fc),
                    my_prob=round(p, 4), market_mid=mid,
                    edge=(round(p - mid, 4) if mid is not None else None),
                    method="normal_v2"))
            n += db.insert("my_probs", rows)
    db.log("synth", True, f"rows={n}")
    return n