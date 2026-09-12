"""Проверка собранных данных."""
import db

print("=== СЧЁТЧИКИ ===")
for t in ("model_forecasts", "market_snapshots", "my_probs", "outcomes"):
    n = db.query(f"SELECT count(*) c FROM {t}")[0]["c"]
    print(f"  {t:20} {n:>6}")

print("\n=== ПРОГНОЗЫ ПО ИСТОЧНИКАМ ===")
for r in db.query("""SELECT source, count(*) c, round(avg(tmax_f),1) avg_f
                     FROM model_forecasts GROUP BY 1 ORDER BY 1"""):
    print(f"  {r['source']:16} {r['c']:>4}  сред {r['avg_f']}°F")

print("\n=== РЫНКИ KALSHI (парсинг диапазонов) ===")
for r in db.query("""SELECT city, target_date, market_id, bucket_lo, bucket_hi,
                            yes_bid, yes_ask, volume
                     FROM market_snapshots WHERE venue='kalshi'
                     ORDER BY city, market_id LIMIT 12"""):
    print(f"  {r['city']} {r['target_date']} {r['market_id']:24} "
          f"[{r['bucket_lo']}..{r['bucket_hi']}]  bid={r['yes_bid']} ask={r['yes_ask']} vol={r['volume']}")

print("\n=== ИСХОДЫ (вчера) ===")
for r in db.query("SELECT city, target_date, tmax_f, source FROM outcomes ORDER BY city"):
    print(f"  {r['city']} {r['target_date']}  {r['tmax_f']}°F  ({r['source']})")

print("\n=== ТОП РАСХОЖДЕНИЙ С РЫНКОМ ===")
rows = db.query("""SELECT city, target_date, market_id, bucket_lo, bucket_hi,
                          ens_mean, ens_sd, my_prob, market_mid, edge
                   FROM my_probs WHERE market_mid IS NOT NULL
                   ORDER BY abs(edge) DESC LIMIT 15""")
if not rows:
    print("  нет рынков с ценой (bid/ask пустые — рынок неликвиден)")
for r in rows:
    print(f"  {r['city']} {r['target_date']} [{r['bucket_lo']}..{r['bucket_hi']}] "
          f"прогноз {r['ens_mean']}±{r['ens_sd']}  я={r['my_prob']} рынок={r['market_mid']} edge={r['edge']}")