"""ИИ-аналитик: читает данные из базы и даёт разбор словами."""
import os
import json
import datetime as dt
from anthropic import Anthropic
import db

_client = None


def client():
    global _client
    if _client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        try:
            import streamlit as st
            key = st.secrets.get("ANTHROPIC_API_KEY", key)
        except Exception:
            pass
        if not key:
            raise RuntimeError("Нет ANTHROPIC_API_KEY в .env или secrets")
        _client = Anthropic(api_key=key)
    return _client


def gather():
    """Собираем компактную сводку для модели — только цифры, без сырья."""
    signals = db.query("""
        WITH last AS (
          SELECT DISTINCT ON (city, target_date, market_id) *
          FROM my_probs WHERE target_date >= current_date
          ORDER BY city, target_date, market_id, computed_at DESC)
        SELECT city, target_date::text, bucket_lo, bucket_hi, ens_mean, ens_sd,
               my_prob, market_mid, edge
        FROM last WHERE market_mid IS NOT NULL
        ORDER BY abs(edge) DESC LIMIT 25""")

    spread = db.query("""
        WITH last AS (
          SELECT DISTINCT ON (city, target_date, source) city, target_date, source, tmax_f
          FROM model_forecasts WHERE target_date >= current_date
          ORDER BY city, target_date, source, fetched_at DESC)
        SELECT city, target_date::text,
               round(min(tmax_f), 1) lo, round(max(tmax_f), 1) hi,
               round(max(tmax_f) - min(tmax_f), 1) spread, count(*) n
        FROM last GROUP BY 1, 2 HAVING count(*) >= 3 ORDER BY 5 DESC LIMIT 12""")

    bias = db.query("""
        WITH last AS (
          SELECT DISTINCT ON (city, target_date, source) city, target_date, source, tmax_f
          FROM model_forecasts WHERE fetched_at::date < target_date
          ORDER BY city, target_date, source, fetched_at DESC)
        SELECT f.city, f.source,
               round(avg(f.tmax_f - o.tmax_f)::numeric, 2) bias,
               round(avg(abs(f.tmax_f - o.tmax_f))::numeric, 2) mae,
               count(*) n
        FROM last f JOIN outcomes o USING (city, target_date)
        GROUP BY 1, 2 HAVING count(*) >= 2 ORDER BY 3""")

    health = db.query("""
        SELECT (SELECT count(*) FROM outcomes) AS days,
               (SELECT count(*) FROM market_snapshots) AS snaps,
               (SELECT count(*) FROM run_log WHERE NOT ok
                 AND ts > now() - interval '24 hours') AS errors_24h""")

    return dict(signals=signals, model_spread=spread, bias=bias, health=health[0] if health else {})


PROMPT = """Ты аналитик прогнозного движка. Он сравнивает ансамбль погодных моделей
(GFS, ECMWF, ICON, GEM, NWS) с ценами рынков Kalshi на дневной максимум температуры.
Цель — найти, где рынок оценивает вероятность неверно.

Данные на {date}:
{data}

Поля: my_prob — наша вероятность, market_mid — цена рынка (она же его вероятность),
edge = my_prob - market_mid. ens_mean/ens_sd — среднее и разброс ансамбля в °F.
bias — систематическая ошибка модели (+ значит завышаем), mae — средняя ошибка по модулю.

Дай короткий разбор на русском, 4 блока, без воды:

1. ЧТО ИНТЕРЕСНОГО — 2-3 самых осмысленных расхождения. Осмысленное значит:
   edge умеренный (5-15%), модели между собой согласны (маленький spread),
   и по этому городу у нас нет большого bias. Огромный edge при большом spread —
   это наша неуверенность, а не ошибка рынка.
2. ГДЕ МЫ ВРЁМ — города/модели с системным смещением. Если bias по городу больше 2°F,
   скажи прямо, что там нашим прогнозам верить нельзя, пока не введена поправка.
3. РАСХОЖДЕНИЕ МОДЕЛЕЙ — где ансамбль разъехался больше чем на 4°F. Это места,
   где рынок обычно тормозит, значит потенциальное окно.
4. ЧТО СДЕЛАТЬ — 1-2 конкретных действия (какую поправку ввести, за чем следить).

Будь скептичен. Наша модель пока сырая — если данных мало, так и скажи.
Не выдумывай причин, которых не видно в цифрах. Без вступлений и заключений."""


def analyze(model="claude-sonnet-4-6"):
    data = gather()
    msg = client().messages.create(
        model=model, max_tokens=1200,
        messages=[{"role": "user", "content": PROMPT.format(
            date=dt.date.today().isoformat(),
            data=json.dumps(data, ensure_ascii=False, default=str, indent=1))}])
    return "".join(b.text for b in msg.content if b.type == "text")