"""Сброс paper-торговли: очищает позиции, решения и банкролл.
Данные наблюдений (прогнозы, цены, стаканы) не трогает.
"""
import db

with db.conn() as c, c.cursor() as cur:
    for t in ("positions", "decisions", "bankroll"):
        cur.execute(f"DELETE FROM {t} WHERE mode = 'paper'")
        print(f"  {t}: удалено {cur.rowcount}")
print("\nPaper-торговля сброшена. Наблюдения сохранены.")