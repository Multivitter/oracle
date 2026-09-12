"""Планировщик.
  python run.py           — вечный цикл (3 снимка/день + исходы утром)
  python run.py once      — один полный прогон сейчас (проверка)
  python run.py outcomes  — только закрыть вчерашние исходы
"""
import sys
import time
import datetime as dt
import traceback

import db
from loaders import openmeteo, nws, kalshi, polymarket
import synth

UTC = dt.timezone.utc


def snapshot():
    for job in (openmeteo.run, nws.run_forecast, kalshi.run, polymarket.run, synth.run):
        try:
            stamp = dt.datetime.now(UTC).strftime("%H:%M")
            print(stamp, job.__module__, job.__name__, "→", job())
        except Exception:
            db.log(job.__module__, False, traceback.format_exc())
            print(traceback.format_exc())


def outcomes():
    try:
        print("outcomes →", nws.run_outcomes())
    except Exception:
        db.log("outcomes", False, traceback.format_exc())
        print(traceback.format_exc())


if __name__ == "__main__":
    db.init()
    mode = sys.argv[1] if len(sys.argv) > 1 else "loop"

    if mode == "once":
        snapshot()
        outcomes()
        sys.exit()

    if mode == "outcomes":
        outcomes()
        sys.exit()

    from config import SNAPSHOT_HOURS_UTC, OUTCOME_HOUR_UTC

    done = set()
    print("loop started; snapshots at UTC", SNAPSHOT_HOURS_UTC, "outcomes at", OUTCOME_HOUR_UTC)
    while True:
        now = dt.datetime.now(UTC)
        key = (now.date(), now.hour)
        if now.hour in SNAPSHOT_HOURS_UTC and (key, "s") not in done:
            snapshot()
            done.add((key, "s"))
        if now.hour == OUTCOME_HOUR_UTC and (key, "o") not in done:
            outcomes()
            done.add((key, "o"))
        if len(done) > 50:
            done = {d for d in done if d[0][0] >= now.date() - dt.timedelta(days=1)}
        time.sleep(300)