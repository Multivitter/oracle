"""Таблицы под исполнение (paper и live). Запуск один раз.
Идемпотентно — можно гонять повторно, ничего не сломает.
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]
SCHEMA = os.getenv("ORACLE_SCHEMA", "oracle")

DDL = f"""
-- Позиции: одна строка на сделку
CREATE TABLE IF NOT EXISTS {SCHEMA}.positions (
  id bigserial PRIMARY KEY,
  opened_at timestamptz NOT NULL DEFAULT now(),
  mode text NOT NULL,                    -- paper / live
  venue text NOT NULL DEFAULT 'kalshi',
  market_id text NOT NULL,
  city text, target_date date,
  bucket_lo numeric, bucket_hi numeric,
  side text NOT NULL,                    -- yes / no
  qty numeric NOT NULL,                  -- контрактов
  entry_price numeric NOT NULL,          -- цена входа, доли
  fee numeric NOT NULL DEFAULT 0,
  -- что думали в момент входа (для последующего разбора)
  my_prob numeric, market_mid numeric, edge numeric,
  ens_mean numeric, ens_sd numeric, model_spread numeric,
  reason text,
  -- закрытие
  status text NOT NULL DEFAULT 'open',   -- open / settled / cancelled
  settled_at timestamptz,
  outcome int,                           -- 1 = сбылось, 0 = нет
  pnl numeric,                           -- итог в долларах
  order_id text                          -- id ордера на бирже (live)
);
CREATE INDEX IF NOT EXISTS ix_pos ON {SCHEMA}.positions(mode, status, target_date, market_id);

-- Состояние банкролла
CREATE TABLE IF NOT EXISTS {SCHEMA}.bankroll (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  mode text NOT NULL,
  balance numeric NOT NULL,
  exposure numeric NOT NULL DEFAULT 0,   -- сколько в открытых позициях
  realized_pnl numeric NOT NULL DEFAULT 0,
  n_open int NOT NULL DEFAULT 0,
  note text
);
CREATE INDEX IF NOT EXISTS ix_bank ON {SCHEMA}.bankroll(mode, ts);

-- Журнал решений: почему поставили или почему пропустили
CREATE TABLE IF NOT EXISTS {SCHEMA}.decisions (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  mode text NOT NULL,
  market_id text NOT NULL,
  action text NOT NULL,                  -- bet / skip
  reason text NOT NULL,
  my_prob numeric, market_mid numeric, edge numeric,
  liquidity numeric, model_spread numeric, kelly numeric, qty numeric
);
CREATE INDEX IF NOT EXISTS ix_dec ON {SCHEMA}.decisions(mode, ts, market_id);
"""

conn = psycopg2.connect(DATABASE_URL, sslmode="require")
conn.autocommit = True
cur = conn.cursor()

cur.execute(DDL)
print("Таблицы positions, bankroll, decisions созданы.\n")

cur.execute(f"""SELECT table_name FROM information_schema.tables
                WHERE table_schema='{SCHEMA}' ORDER BY 1""")
for (t,) in cur.fetchall():
    cur.execute(f"SELECT count(*) FROM {SCHEMA}.{t}")
    print(f"  {t:20} {cur.fetchone()[0]:>8,} строк")

cur.close()
conn.close()