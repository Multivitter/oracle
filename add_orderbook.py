"""Добавляет таблицу под стаканы Kalshi. Запуск один раз."""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
SCHEMA = os.getenv("ORACLE_SCHEMA", "oracle")

DDL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.orderbooks (
  id bigserial PRIMARY KEY,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  venue text NOT NULL,
  city text,
  target_date date,
  market_id text NOT NULL,
  side text NOT NULL,              -- yes / no
  price numeric NOT NULL,          -- в долларах 0..1
  size numeric NOT NULL,           -- контрактов на уровне
  level int NOT NULL               -- 1 = лучшая цена
);
CREATE INDEX IF NOT EXISTS ix_ob ON {SCHEMA}.orderbooks(market_id, fetched_at, side, level);

-- сводка по ликвидности: сколько можно купить, не сдвинув цену дальше порога
CREATE TABLE IF NOT EXISTS {SCHEMA}.liquidity (
  id bigserial PRIMARY KEY,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  market_id text NOT NULL,
  city text, target_date date,
  best_yes_bid numeric, best_yes_ask numeric, spread numeric,
  depth_yes_2c numeric,            -- контрактов в пределах 2¢ от лучшей цены
  depth_no_2c numeric,
  notional_yes_2c numeric,         -- в долларах
  notional_no_2c numeric
);
CREATE INDEX IF NOT EXISTS ix_liq ON {SCHEMA}.liquidity(market_id, fetched_at);
"""

conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")
conn.autocommit = True
cur = conn.cursor()
cur.execute(DDL)
print("Таблицы orderbooks и liquidity созданы.")
cur.execute(f"SELECT table_name FROM information_schema.tables WHERE table_schema='{SCHEMA}' ORDER BY 1")
for (t,) in cur.fetchall():
    print(" ", t)
cur.close()
conn.close()