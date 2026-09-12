"""Слой доступа к базе. Все таблицы — в схеме oracle (search_path)."""
import json
import psycopg2
import psycopg2.extras
from config import DB_DSN, DB_SCHEMA


def conn():
    c = psycopg2.connect(DB_DSN, sslmode="require")
    with c.cursor() as cur:
        cur.execute(f"SET search_path TO {DB_SCHEMA}, public")
    c.commit()
    return c


def init():
    """Таблицы уже созданы через Setup oracle schema.py — здесь ничего не делаем."""
    pass


def insert(table, rows):
    """rows: list[dict]; dict/list-значения сериализуются в jsonb."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    vals = [
        [json.dumps(r[k]) if isinstance(r[k], (dict, list)) else r[k] for k in cols]
        for r in rows
    ]
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES %s"
    with conn() as c, c.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, vals)
    return len(rows)


def upsert_outcome(row):
    with conn() as c, c.cursor() as cur:
        cur.execute(
            """INSERT INTO outcomes (city, target_date, tmax_f, source, raw)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (city, target_date) DO UPDATE
                 SET tmax_f = EXCLUDED.tmax_f,
                     source = EXCLUDED.source,
                     raw = EXCLUDED.raw,
                     fetched_at = now()""",
            (row["city"], row["target_date"], row["tmax_f"], row["source"],
             json.dumps(row.get("raw"))),
        )


def log(job, ok, msg=""):
    with conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO run_log (job, ok, msg) VALUES (%s, %s, %s)",
                    (job, ok, str(msg)[:2000]))


def query(sql, params=None):
    with conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()