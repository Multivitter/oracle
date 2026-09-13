"""Стаканы Kalshi: глубина по уровням + сводка ликвидности.

Формат ответа: {"orderbook_fp": {"yes_dollars": [["0.0100","4266.87"], ...],
                                 "no_dollars":  [["0.8600","1495.00"], ...]}}
Цены строками, в долларах. Обе стороны — это БИДЫ: yes_dollars — заявки на покупку YES,
no_dollars — заявки на покупку NO. Ask по YES = 1 - лучший бид по NO.
"""
import requests
from config import KALSHI_BASE
import db

DEPTH_BAND = 0.02   # 2¢ от лучшей цены — в этой полосе считаем глубину
MAX_MARKETS = 40    # предохранитель от лавины запросов


def targets():
    """Рынки, которые стоит смотреть: есть цена, приоритет по edge и объёму."""
    return db.query("""
        WITH last_snap AS (
          SELECT DISTINCT ON (market_id) market_id, city, target_date, volume, last_price
          FROM market_snapshots
          WHERE venue='kalshi' AND target_date >= current_date
          ORDER BY market_id, fetched_at DESC),
        last_prob AS (
          SELECT DISTINCT ON (market_id) market_id, abs(edge) AS e
          FROM my_probs WHERE target_date >= current_date
          ORDER BY market_id, computed_at DESC)
        SELECT s.market_id, s.city, s.target_date
        FROM last_snap s LEFT JOIN last_prob p USING (market_id)
        WHERE s.last_price IS NOT NULL
        ORDER BY COALESCE(p.e, 0) DESC, COALESCE(s.volume, 0) DESC
        LIMIT %s""", (MAX_MARKETS,))


def fetch(ticker):
    r = requests.get(f"{KALSHI_BASE}/markets/{ticker}/orderbook",
                     params={"depth": 10}, timeout=30)
    r.raise_for_status()
    j = r.json()
    return j.get("orderbook_fp") or j.get("orderbook") or {}


def _levels(raw, key):
    """Возвращает [(цена, размер), ...] отсортированные по убыванию цены."""
    lv = raw.get(f"{key}_dollars") or raw.get(key) or []
    out = []
    for item in lv:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            price, size = float(item[0]), float(item[1])
        except (TypeError, ValueError):
            continue
        if price > 1.5:          # если вдруг пришло в центах
            price /= 100.0
        out.append((price, size))
    return sorted(out, key=lambda x: -x[0])   # лучший бид первым


def run():
    rows, liq, n_err = [], [], 0

    for t in targets():
        try:
            ob = fetch(t["market_id"])
        except Exception as e:
            n_err += 1
            db.log("orderbook", False, f"{t['market_id']}: {e}")
            continue

        yes = _levels(ob, "yes")
        no = _levels(ob, "no")

        for side, lv in (("yes", yes), ("no", no)):
            for lvl, (price, size) in enumerate(lv, 1):
                rows.append(dict(venue="kalshi", city=t["city"], target_date=t["target_date"],
                                 market_id=t["market_id"], side=side,
                                 price=price, size=size, level=lvl))

        best_yes_bid = yes[0][0] if yes else None
        best_no_bid = no[0][0] if no else None
        best_yes_ask = round(1 - best_no_bid, 4) if best_no_bid is not None else None
        spread = (round(best_yes_ask - best_yes_bid, 4)
                  if best_yes_bid is not None and best_yes_ask is not None else None)

        d_yes = sum(s for p, s in yes
                    if best_yes_bid is not None and p >= best_yes_bid - DEPTH_BAND)
        d_no = sum(s for p, s in no
                   if best_no_bid is not None and p >= best_no_bid - DEPTH_BAND)
        n_yes = sum(p * s for p, s in yes
                    if best_yes_bid is not None and p >= best_yes_bid - DEPTH_BAND)
        n_no = sum(p * s for p, s in no
                   if best_no_bid is not None and p >= best_no_bid - DEPTH_BAND)

        liq.append(dict(market_id=t["market_id"], city=t["city"], target_date=t["target_date"],
                        best_yes_bid=best_yes_bid, best_yes_ask=best_yes_ask, spread=spread,
                        depth_yes_2c=d_yes, depth_no_2c=d_no,
                        notional_yes_2c=round(n_yes, 2), notional_no_2c=round(n_no, 2)))

    n1 = db.insert("orderbooks", rows)
    n2 = db.insert("liquidity", liq)
    db.log("orderbook", True, f"levels={n1} markets={n2} errors={n_err}")
    return n2