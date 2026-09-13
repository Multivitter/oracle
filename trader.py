"""Движок исполнения. По умолчанию paper — реальных ордеров не ставит.

Логика одного прогона:
  1. взять свежие сигналы (прогноз + цена рынка + edge)
  2. прогнать через фильтры риска — каждое решение записать в decisions
  3. на прошедших фильтры открыть позиции по дробному Келли
  4. закрыть вчерашние позиции по факту исхода, посчитать P&L

Запуск:
  python trader.py            — один прогон (открыть новые + закрыть старые)
  python trader.py settle     — только закрыть по исходам
  python trader.py report     — сводка по банкроллу и сделкам
"""
import sys
import datetime as dt
import db

# ─────────────────────────── НАСТРОЙКИ РИСКА ───────────────────────────
MODE = "paper"              # paper / live — live включаем только после вердикта по edge
START_BANKROLL = 500.0      # стартовый банк, $

MIN_EDGE = 0.05             # ниже этого не входим вообще
MAX_EDGE = 0.25             # выше — почти наверняка наша ошибка, а не рынка
MAX_MODEL_SPREAD = 4.0      # °F: если модели разъехались сильнее, мы не знаем ответа
MIN_LIQUIDITY = 20.0        # $ в 2¢-полосе: меньше — не влезем без проскальзывания
MAX_SPREAD = 0.04           # шире 4¢ — исполнение съест edge
MAX_BOOK_SHARE = 0.25       # берём не более четверти контрактов, стоящих в 2¢-полосе
MIN_PRICE = 0.08            # дешевле 8¢ не входим: хвосты, где наша модель врёт сильнее
MAX_PRICE = 0.92            # зеркально

KELLY_FRACTION = 0.25       # четверть Келли: медленнее, но переживает просадки
MAX_BET_PCT = 0.05          # не больше 5% банка в одну ставку
MAX_EXPOSURE_PCT = 0.40     # не больше 40% банка в открытых позициях одновременно
MAX_BETS_PER_RUN = 6
MAX_PER_CITY_PER_DAY = 2    # не ставить весь банк на один город

FEE = 0.01                  # комиссия за контракт, $
DRAWDOWN_STOP = 0.20        # просадка 20% от пика — останов

# Рынки на дату открываются примерно за сутки. Входим только в ранние часы UTC,
# пока день события ещё не начался в западных городах (UTC-4..UTC-7).
MAX_HOUR_UTC = 17           # после 17:00 UTC на сегодняшнюю дату не входим


# ─────────────────────────────── БАНКРОЛЛ ───────────────────────────────
def bankroll():
    r = db.query("""SELECT balance, realized_pnl FROM bankroll
                    WHERE mode=%s ORDER BY ts DESC LIMIT 1""", (MODE,))
    if r:
        return float(r[0]["balance"]), float(r[0]["realized_pnl"])
    db.insert("bankroll", [dict(mode=MODE, balance=START_BANKROLL, exposure=0,
                                realized_pnl=0, n_open=0, note="старт")])
    return START_BANKROLL, 0.0


def exposure():
    r = db.query("""SELECT COALESCE(sum(qty*entry_price),0) e, count(*) n
                    FROM positions WHERE mode=%s AND status='open'""", (MODE,))
    return float(r[0]["e"]), int(r[0]["n"])


def peak_balance():
    r = db.query("SELECT max(balance) m FROM bankroll WHERE mode=%s", (MODE,))
    return float(r[0]["m"]) if r and r[0]["m"] is not None else START_BANKROLL


def save_bankroll(balance, realized, note=""):
    exp, n = exposure()
    db.insert("bankroll", [dict(mode=MODE, balance=round(balance, 2),
                                exposure=round(exp, 2), realized_pnl=round(realized, 2),
                                n_open=n, note=note)])


# ─────────────────────────────── СИГНАЛЫ ───────────────────────────────
def signals():
    """Свежие вероятности + ликвидность + разброс моделей.

    Рынки Kalshi на дату открываются примерно за сутки, поэтому сигналы на
    послезавтра физически отсутствуют. Берём сегодня и дальше, но входим только
    рано (см. MAX_HOUR_UTC): после полудня по местному времени день уже наполовину
    состоялся и рынок знает больше нас."""
    return db.query("""
        WITH last_prob AS (
          SELECT DISTINCT ON (market_id) *
          FROM my_probs WHERE target_date >= current_date
          ORDER BY market_id, computed_at DESC),
        last_liq AS (
          SELECT DISTINCT ON (market_id) market_id, best_yes_bid, best_yes_ask, spread,
                 notional_yes_2c, notional_no_2c, depth_yes_2c, depth_no_2c
          FROM liquidity ORDER BY market_id, fetched_at DESC),
        spread_models AS (
          SELECT city, target_date, max(tmax_f) - min(tmax_f) AS ms
          FROM (SELECT DISTINCT ON (city, target_date, source) city, target_date, source, tmax_f
                FROM model_forecasts WHERE target_date >= current_date
                ORDER BY city, target_date, source, fetched_at DESC) x
          GROUP BY 1, 2)
        SELECT p.market_id, p.city, p.target_date, p.bucket_lo, p.bucket_hi,
               p.my_prob, p.market_mid, p.edge, p.ens_mean, p.ens_sd,
               l.best_yes_bid, l.best_yes_ask, l.spread AS book_spread,
               l.notional_yes_2c, l.notional_no_2c,
               l.depth_yes_2c, l.depth_no_2c, s.ms AS model_spread
        FROM last_prob p
        LEFT JOIN last_liq l USING (market_id)
        LEFT JOIN spread_models s ON s.city = p.city AND s.target_date = p.target_date
        WHERE p.market_mid IS NOT NULL
        ORDER BY abs(p.edge) DESC""")


def already_open(market_id):
    r = db.query("""SELECT count(*) n FROM positions
                    WHERE mode=%s AND market_id=%s AND status='open'""", (MODE, market_id))
    return r[0]["n"] > 0


def city_count(city, target_date):
    r = db.query("""SELECT count(*) n FROM positions
                    WHERE mode=%s AND status='open' AND city=%s AND target_date=%s""",
                 (MODE, city, target_date))
    return r[0]["n"]


# ─────────────────────────────── РЕШЕНИЕ ───────────────────────────────
def kelly(p, price):
    """Дробный Келли для бинарного контракта.
    Покупаем за price, получаем 1 при исходе. b = (1-price)/price."""
    if price <= 0 or price >= 1:
        return 0.0
    b = (1 - price) / price
    f = (p * b - (1 - p)) / b
    return max(0.0, f * KELLY_FRACTION)


def decide(s, balance, exp, n_bets):
    """Возвращает (action, reason, qty, side, price)."""
    e = float(s["edge"])
    p = float(s["my_prob"])
    mid = float(s["market_mid"])
    side = "yes" if e > 0 else "no"

    # цена входа: покупаем по ask (то есть хуже, чем mid) — консервативно
    if side == "yes":
        price = float(s["best_yes_ask"]) if s["best_yes_ask"] else mid + 0.01
        p_win = p
        liq = float(s["notional_no_2c"] or 0)     # против нас стоят биды по NO
        depth = float(s["depth_no_2c"] or 0)      # столько контрактов там реально есть
    else:
        price = round(1 - float(s["best_yes_bid"]), 4) if s["best_yes_bid"] else (1 - mid) + 0.01
        p_win = 1 - p
        liq = float(s["notional_yes_2c"] or 0)
        depth = float(s["depth_yes_2c"] or 0)

    ms = float(s["model_spread"]) if s["model_spread"] is not None else 99.0
    bs = float(s["book_spread"]) if s["book_spread"] is not None else 99.0

    now_utc = dt.datetime.now(dt.timezone.utc)
    if s["target_date"] == now_utc.date() and now_utc.hour >= MAX_HOUR_UTC:
        return "skip", f"поздно для сегодняшней даты ({now_utc.hour}:00 UTC)", 0, side, price

    if abs(e) < MIN_EDGE:
        return "skip", f"edge {e:+.3f} ниже порога {MIN_EDGE}", 0, side, price
    if abs(e) > MAX_EDGE:
        return "skip", f"edge {e:+.3f} слишком велик — вероятна наша ошибка", 0, side, price
    if price < MIN_PRICE or price > MAX_PRICE:
        return "skip", f"цена {price:.2f} в хвосте распределения", 0, side, price
    if ms > MAX_MODEL_SPREAD:
        return "skip", f"модели разъехались на {ms:.1f}°F", 0, side, price
    if bs > MAX_SPREAD:
        return "skip", f"спред в стакане {bs:.3f} слишком широк", 0, side, price
    if liq < MIN_LIQUIDITY:
        return "skip", f"ликвидность ${liq:.0f} мала", 0, side, price
    if already_open(s["market_id"]):
        return "skip", "позиция по этому рынку уже открыта", 0, side, price
    if city_count(s["city"], s["target_date"]) >= MAX_PER_CITY_PER_DAY:
        return "skip", f"уже {MAX_PER_CITY_PER_DAY} позиции по {s['city']}", 0, side, price
    if n_bets >= MAX_BETS_PER_RUN:
        return "skip", "лимит ставок за прогон", 0, side, price
    if exp >= balance * MAX_EXPOSURE_PCT:
        return "skip", f"экспозиция ${exp:.0f} на пределе", 0, side, price

    f = kelly(p_win, price)
    stake = min(balance * f, balance * MAX_BET_PCT, balance * MAX_EXPOSURE_PCT - exp)
    qty = int(stake / price) if price > 0 else 0

    # физический потолок: нельзя купить больше, чем стоит в стакане
    qty_cap = int(depth * MAX_BOOK_SHARE)
    if qty > qty_cap:
        qty = qty_cap

    if qty < 1:
        return ("skip",
                f"размер ${stake:.2f} / глубина {depth:.0f} контрактов — меньше лота",
                0, side, price)

    return ("bet",
            f"edge {e:+.3f}, келли {f:.3f}, спред моделей {ms:.1f}°F, "
            f"цена {price:.2f}, глубина {depth:.0f} шт",
            qty, side, price)


# ─────────────────────────────── ОТКРЫТИЕ ───────────────────────────────
def open_positions():
    balance, realized = bankroll()
    peak = peak_balance()
    if balance < peak * (1 - DRAWDOWN_STOP):
        print(f"СТОП: просадка {(1 - balance / peak):.0%} от пика ${peak:.0f}. Торговля остановлена.")
        db.log("trader", True, f"drawdown stop at {balance:.2f} (peak {peak:.2f})")
        return 0

    exp, _ = exposure()
    n_bets = 0
    decisions, positions = [], []

    for s in signals():
        action, reason, qty, side, price = decide(s, balance, exp, n_bets)

        decisions.append(dict(
            mode=MODE, market_id=s["market_id"], action=action, reason=reason,
            my_prob=s["my_prob"], market_mid=s["market_mid"], edge=s["edge"],
            liquidity=s["notional_yes_2c"], model_spread=s["model_spread"],
            kelly=None, qty=qty))

        if action != "bet":
            continue

        cost = qty * price + qty * FEE
        positions.append(dict(
            mode=MODE, venue="kalshi", market_id=s["market_id"],
            city=s["city"], target_date=s["target_date"],
            bucket_lo=s["bucket_lo"], bucket_hi=s["bucket_hi"],
            side=side, qty=qty, entry_price=price, fee=round(qty * FEE, 4),
            my_prob=s["my_prob"], market_mid=s["market_mid"], edge=s["edge"],
            ens_mean=s["ens_mean"], ens_sd=s["ens_sd"], model_spread=s["model_spread"],
            reason=reason, status="open"))
        exp += cost
        n_bets += 1
        print(f"  СТАВКА {s['city']} {s['target_date']} {side.upper()} "
              f"{qty} шт по {price:.2f} = ${qty * price:.2f}  ({reason})")

    db.insert("decisions", decisions)
    db.insert("positions", positions)
    if positions:
        save_bankroll(balance, realized, f"открыто {len(positions)}")
    db.log("trader", True, f"open={len(positions)} skip={len(decisions) - len(positions)}")
    return len(positions)


# ─────────────────────────────── ЗАКРЫТИЕ ───────────────────────────────
def settle():
    rows = db.query("""
        SELECT p.id, p.market_id, p.city, p.target_date, p.bucket_lo, p.bucket_hi,
               p.side, p.qty, p.entry_price, p.fee, o.tmax_f
        FROM positions p JOIN outcomes o USING (city, target_date)
        WHERE p.mode=%s AND p.status='open'""", (MODE,))
    if not rows:
        return 0

    balance, realized = bankroll()
    n = 0
    for r in rows:
        t = float(r["tmax_f"])
        lo = float(r["bucket_lo"]) - 0.5 if r["bucket_lo"] is not None else -1e9
        hi = float(r["bucket_hi"]) + 0.5 if r["bucket_hi"] is not None else 1e9
        hit = 1 if (lo <= t < hi) else 0
        won = hit if r["side"] == "yes" else (1 - hit)

        qty = float(r["qty"])
        cost = qty * float(r["entry_price"]) + float(r["fee"])
        payout = qty * 1.0 if won else 0.0
        pnl = round(payout - cost, 2)

        with db.conn() as c, c.cursor() as cur:
            cur.execute("""UPDATE positions SET status='settled', settled_at=now(),
                           outcome=%s, pnl=%s WHERE id=%s""", (hit, pnl, r["id"]))

        balance += pnl
        realized += pnl
        n += 1
        mark = "+" if pnl > 0 else "-"
        print(f"  {mark} {r['city']} {r['target_date']} {r['side'].upper()} "
              f"факт {t}°F → {'выиграл' if won else 'проиграл'}  P&L ${pnl:+.2f}")

    save_bankroll(balance, realized, f"закрыто {n}")
    db.log("trader", True, f"settled={n} balance={balance:.2f}")
    return n


# ─────────────────────────────── ОТЧЁТ ───────────────────────────────
def report():
    balance, realized = bankroll()
    exp, n_open = exposure()
    peak = peak_balance()
    print(f"\nРЕЖИМ: {MODE}")
    print(f"Баланс:      ${balance:.2f}   (старт ${START_BANKROLL:.0f}, "
          f"{(balance / START_BANKROLL - 1) * 100:+.1f}%)")
    print(f"Реализовано: ${realized:+.2f}")
    print(f"В позициях:  ${exp:.2f} в {n_open} сделках")
    print(f"Просадка:    {(1 - balance / peak) * 100:.1f}% от пика ${peak:.2f}")

    s = db.query("""SELECT count(*) n, count(*) FILTER (WHERE pnl > 0) wins,
                           round(sum(pnl)::numeric, 2) total,
                           round(avg(pnl)::numeric, 3) avg_pnl,
                           round(avg(edge)::numeric, 3) avg_edge
                    FROM positions WHERE mode=%s AND status='settled'""", (MODE,))[0]
    if s["n"]:
        wr = s["wins"] / s["n"] * 100
        print(f"\nЗакрыто сделок: {s['n']}, выигрышных {s['wins']} ({wr:.0f}%)")
        print(f"Итого P&L: ${s['total']:+.2f}, в среднем ${s['avg_pnl']:+.3f} на сделку")
        print(f"Средний edge на входе: {s['avg_edge']:+.3f}")
    else:
        print("\nЗакрытых сделок пока нет.")

    d = db.query("""SELECT action, count(*) n FROM decisions
                    WHERE mode=%s AND ts > now() - interval '24 hours'
                    GROUP BY 1""", (MODE,))
    if d:
        print("\nРешения за 24ч: " + ", ".join(f"{r['action']} {r['n']}" for r in d))
    top = db.query("""SELECT reason, count(*) n FROM decisions
                      WHERE mode=%s AND action='skip' AND ts > now() - interval '24 hours'
                      GROUP BY 1 ORDER BY 2 DESC LIMIT 5""", (MODE,))
    if top:
        print("Почему пропускали:")
        for r in top:
            print(f"   {r['n']:>3}×  {r['reason'][:70]}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "settle":
        print("Закрываю позиции по исходам…")
        settle()
    elif cmd == "report":
        report()
    else:
        print("Закрываю вчерашнее…")
        settle()
        print("Ищу новые входы…")
        open_positions()
        report()