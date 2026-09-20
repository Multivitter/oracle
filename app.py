"""Oracle dashboard.
Запуск: python -m streamlit run app.py --server.port 8502
"""
import datetime as dt
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import db

st.set_page_config(page_title="Oracle", page_icon="◆", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
  .block-container {padding-top: 2rem; max-width: 1600px;}
  h1 {font-size: 1.8rem !important; font-weight: 600; letter-spacing: -0.02em;}
  h2 {font-size: 1.2rem !important; font-weight: 600;}
  h3 {font-size: 1.05rem !important; font-weight: 600; color: #9CA3AF;}
  [data-testid="stMetricValue"] {font-size: 1.6rem; font-weight: 600;}
  [data-testid="stMetricLabel"] {color: #6B7280; font-size: 0.78rem;
      text-transform: uppercase; letter-spacing: 0.05em;}
  [data-testid="stMetric"] {background: rgba(255,255,255,0.03);
      border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; padding: 14px 18px;}
  hr {margin: 0.6rem 0 1.2rem 0; border-color: rgba(255,255,255,0.08);}
</style>
""", unsafe_allow_html=True)

PLOT = dict(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(255,255,255,0.02)",
            font=dict(family="Inter, Segoe UI, sans-serif", size=12),
            margin=dict(l=50, r=20, t=50, b=40))


@st.cache_data(ttl=180)
def q(sql, params=None):
    return pd.DataFrame(db.query(sql, params))


def hits(df):
    lo = df.bucket_lo.astype(float).fillna(-1e9) - 0.5
    hi = df.bucket_hi.astype(float).fillna(1e9) + 0.5
    t = df.tmax_f.astype(float)
    return ((t >= lo) & (t < hi)).astype(int)


def bucket_label(r):
    if pd.notna(r.bucket_lo) and pd.notna(r.bucket_hi):
        return f"{float(r.bucket_lo):.0f}–{float(r.bucket_hi):.0f}°"
    if pd.notna(r.bucket_lo):
        return f"≥{float(r.bucket_lo):.0f}°"
    return f"≤{float(r.bucket_hi):.0f}°"


@st.cache_data(ttl=180)
def closed():
    df = q("""WITH last AS (
                SELECT DISTINCT ON (city, target_date, market_id) *
                FROM my_probs WHERE computed_at::date = target_date - 1
                ORDER BY city, target_date, market_id, computed_at DESC)
              SELECT p.city, p.target_date, p.market_id, p.bucket_lo, p.bucket_hi,
                     p.my_prob, p.market_mid, p.ens_mean, o.tmax_f
              FROM last p JOIN outcomes o USING (city, target_date)
              WHERE p.market_mid IS NOT NULL""")
    if len(df):
        df["my_prob"] = df.my_prob.astype(float)
        df["market_mid"] = df.market_mid.astype(float)
        df["y"] = hits(df)
    return df


st.title("◆ Oracle")
st.caption(f"Прогнозы против рынка · обновлено {dt.datetime.now():%d.%m %H:%M}")

t1, t2, t3, t4, t5, t6, t7, t8 = st.tabs(
    ["Сигналы", "Оценка", "Торговля", "История", "Калибровка",
     "Точность моделей", "Здоровье", "ИИ-разбор"])

# ─────────────────────────────── СИГНАЛЫ ───────────────────────────────
with t1:
    c = st.columns([1, 3])
    th = c[0].slider("Порог |edge|", 0.0, 0.40, 0.10, 0.01)

    sig = q("""WITH last AS (
                 SELECT DISTINCT ON (city, target_date, market_id) *
                 FROM my_probs WHERE target_date >= current_date
                 ORDER BY city, target_date, market_id, computed_at DESC)
               SELECT city, target_date, market_id, bucket_lo, bucket_hi,
                      ens_mean, ens_sd, my_prob, market_mid, edge
               FROM last WHERE market_mid IS NOT NULL AND abs(edge) >= %s
               ORDER BY abs(edge) DESC""", (th,))

    if len(sig):
        sig["диапазон"] = sig.apply(bucket_label, axis=1)
        sig["сторона"] = sig.edge.apply(lambda e: "YES" if e > 0 else "NO")
        sig["прогноз"] = sig.apply(lambda r: f"{float(r.ens_mean):.1f} ± {float(r.ens_sd):.1f}", axis=1)
        sig["моя p"] = sig.my_prob.astype(float).round(3)
        sig["рынок"] = sig.market_mid.astype(float).round(3)
        sig["edge_r"] = sig.edge.astype(float).round(3)

        m = st.columns(4)
        m[0].metric("Сигналов", len(sig))
        m[1].metric("Городов", sig.city.nunique())
        m[2].metric("Макс. edge", f"{sig.edge.abs().max():.0%}")
        m[3].metric("Медиана", f"{sig.edge.abs().median():.0%}")

        view = sig[["city", "target_date", "диапазон", "прогноз", "моя p",
                    "рынок", "edge_r", "сторона"]]
        view.columns = ["Город", "Дата", "Диапазон", "Прогноз °F", "Моя p",
                        "Рынок", "Edge", "Сторона"]
        st.dataframe(view, use_container_width=True, hide_index=True)
        st.caption("Большой edge чаще означает ошибку модели, а не рынка. "
                   "Одинаковый ±SD у всех городов значит, что поправки на bias ещё не введены.")
    else:
        st.info("Нет сигналов выше порога.")


# ─────────────────────────────── ОЦЕНКА ───────────────────────────────
with t2:
    st.caption("Только прогнозы, сделанные заранее. Сегодняшние рынки исключены: "
               "к полудню рынок видит фактические замеры, и наш edge там — шум.")

    h = st.radio("Горизонт прогноза", [1, 2], horizontal=True,
                 format_func=lambda x: f"за {x} дн. до события")

    ev = q("""
        WITH last AS (
          SELECT DISTINCT ON (city, target_date, market_id) *
          FROM my_probs WHERE computed_at::date <= target_date - %s
          ORDER BY city, target_date, market_id, computed_at DESC)
        SELECT p.method, p.city, p.target_date, p.my_prob, p.market_mid, p.edge,
               p.ens_mean, p.ens_sd, p.bucket_lo, p.bucket_hi, o.tmax_f
        FROM last p JOIN outcomes o USING (city, target_date)
        WHERE p.market_mid IS NOT NULL""", (int(h),))

    if not len(ev):
        st.info("Закрытых пар с таким горизонтом ещё нет. "
                "Первые появляются на следующий день после прогноза.")
    else:
        ev["my_prob"] = ev.my_prob.astype(float)
        ev["market_mid"] = ev.market_mid.astype(float)
        ev["edge"] = ev.edge.astype(float)
        ev["y"] = hits(ev)

        # ── Сводка по версиям ──
        rows = []
        for m, g in ev.groupby("method"):
            bm = ((g.my_prob - g.y) ** 2).mean()
            bk = ((g.market_mid - g.y) ** 2).mean()
            bets = g[(g.edge.abs() >= 0.05) & (g.edge.abs() <= 0.25)]
            if len(bets):
                pnl = bets.apply(
                    lambda r: (r.y - r.market_mid) - 0.01 if r.edge > 0
                    else ((1 - r.y) - (1 - r.market_mid)) - 0.01, axis=1)
                roi, total, nb, wins = (pnl.mean() * 100, pnl.sum(),
                                        len(bets), int((pnl > 0).sum()))
            else:
                roi = total = nb = wins = 0
            rows.append(dict(версия=m, n=len(g), brier_мой=round(bm, 4),
                             brier_рынка=round(bk, 4), разница=round(bk - bm, 4),
                             ставок=nb, выигр=wins,
                             roi=round(roi, 1), pnl=round(total, 2)))
        res = pd.DataFrame(rows).sort_values("n", ascending=False)

        best = res.iloc[res.разница.argmax()]
        m = st.columns(4)
        m[0].metric("Лучшая версия", best.версия)
        m[1].metric("Brier мой", f"{best.brier_мой:.4f}")
        m[2].metric("Brier рынка", f"{best.brier_рынка:.4f}")
        m[3].metric("Разница", f"{best.разница:+.4f}",
                    "бьём рынок" if best.разница > 0 else "рынок впереди",
                    delta_color="normal" if best.разница > 0 else "inverse")

        view = res.copy()
        view.columns = ["Версия", "Пар", "Brier мой", "Brier рынка", "Разница",
                        "Ставок", "Выигр.", "ROI %", "P&L $"]
        st.dataframe(view, use_container_width=True, hide_index=True)
        st.caption("Разница положительная — мы точнее рынка. ROI считается по "
                   "сигналам в коридоре 5–25% с комиссией 1¢.")

        st.markdown("---")
        left, right = st.columns(2)

        # ── По городам ──
        cr = []
        for c, g in ev.groupby("city"):
            cr.append(dict(city=c, n=len(g),
                           разница=((g.market_mid - g.y) ** 2).mean()
                                   - ((g.my_prob - g.y) ** 2).mean()))
        cdf = pd.DataFrame(cr).sort_values("разница")
        f = px.bar(cdf, x="разница", y="city", orientation="h",
                   color="разница", color_continuous_scale="RdYlGn",
                   range_color=[-0.08, 0.08],
                   labels={"разница": "Brier рынка − Brier наш", "city": ""})
        f.add_vline(x=0, line=dict(color="#6B7280", dash="dot"))
        f.update_layout(title="Где мы ближе к истине, чем рынок", height=340,
                        showlegend=False, **PLOT)
        left.plotly_chart(f, use_container_width=True)

        # ── Накопительный P&L по версиям ──
        fig = go.Figure()
        for m_, g in ev.groupby("method"):
            bets = g[(g.edge.abs() >= 0.05) & (g.edge.abs() <= 0.25)].sort_values("target_date")
            if len(bets) < 3:
                continue
            pnl = bets.apply(
                lambda r: (r.y - r.market_mid) - 0.01 if r.edge > 0
                else ((1 - r.y) - (1 - r.market_mid)) - 0.01, axis=1)
            fig.add_scatter(y=pnl.cumsum().values, mode="lines", name=m_,
                            line=dict(width=2))
        fig.add_hline(y=0, line=dict(color="#6B7280", dash="dot"))
        fig.update_layout(title="Бумажный P&L по версиям", height=340,
                          xaxis_title="ставка", yaxis_title="$ на контракт", **PLOT)
        right.plotly_chart(fig, use_container_width=True)

        # ── Точность центра прогноза ──
        st.markdown("---")
        cen = ev.drop_duplicates(subset=["city", "target_date", "method"]).copy()
        cen["промах"] = cen.ens_mean.astype(float) - cen.tmax_f.astype(float)
        agg = (cen.groupby("method")
               .agg(n=("промах", "size"), bias=("промах", "mean"),
                    mae=("промах", lambda s: s.abs().mean()))
               .round(2).reset_index())
        agg.columns = ["Версия", "Дней", "Смещение °F", "Средняя ошибка °F"]
        st.subheader("Точность центра прогноза")
        st.caption("Смещение около нуля значит, что поправки по городам работают.")
        st.dataframe(agg, use_container_width=True, hide_index=True)

# ─────────────────────────────── ТОРГОВЛЯ ───────────────────────────────
with t3:
    mode = st.radio("Режим", ["paper", "live"], horizontal=True, label_visibility="collapsed")

    bank = q("""SELECT ts, balance, exposure, realized_pnl, n_open, note
                FROM bankroll WHERE mode=%s ORDER BY ts""", (mode,))

    if not len(bank):
        st.info("Торговля ещё не запускалась. Запусти `python trader.py`.")
    else:
        cur = bank.iloc[-1]
        start = float(bank.iloc[0]["balance"])
        m = st.columns(5)
        m[0].metric("Баланс", f"${float(cur.balance):.2f}",
                    f"{(float(cur.balance) / start - 1) * 100:+.1f}%")
        m[1].metric("Реализовано", f"${float(cur.realized_pnl):+.2f}")
        m[2].metric("В позициях", f"${float(cur.exposure):.2f}")
        m[3].metric("Открыто сделок", int(cur.n_open))
        peak = float(bank.balance.astype(float).max())
        m[4].metric("Просадка", f"{(1 - float(cur.balance) / peak) * 100:.1f}%",
                    delta_color="inverse")

        st.markdown("---")

        if len(bank) > 1:
            b = bank.copy()
            b["balance"] = b.balance.astype(float)
            f = go.Figure()
            f.add_scatter(x=b.ts, y=b.balance, mode="lines+markers",
                          line=dict(color="#34D399", width=2), name="баланс")
            f.add_hline(y=start, line=dict(color="#6B7280", dash="dot"))
            f.update_layout(title="Банкролл", height=340,
                            xaxis_title="", yaxis_title="$", **PLOT)
            st.plotly_chart(f, use_container_width=True)

        done = q("""SELECT settled_at, city, target_date, bucket_lo, bucket_hi, side,
                           qty, entry_price, outcome, pnl, edge, ens_mean, model_spread
                    FROM positions WHERE mode=%s AND status='settled'
                    ORDER BY settled_at DESC""", (mode,))
        if len(done):
            done["pnl"] = done.pnl.astype(float)
            n, wins = len(done), int((done.pnl > 0).sum())
            total = done.pnl.sum()
            staked = (done.qty.astype(float) * done.entry_price.astype(float)).sum()
            k = st.columns(4)
            k[0].metric("Закрыто", n)
            k[1].metric("Выигрышных", f"{wins} ({wins / n * 100:.0f}%)")
            k[2].metric("Итого P&L", f"${total:+.2f}")
            k[3].metric("ROI на оборот", f"{total / staked * 100:+.1f}%" if staked else "—")

            d = done.copy()
            d["диапазон"] = d.apply(bucket_label, axis=1)
            d["исход"] = d.outcome.map({1: "попал", 0: "мимо"})
            view = d[["settled_at", "city", "target_date", "диапазон", "side",
                      "qty", "entry_price", "edge", "исход", "pnl"]]
            view.columns = ["Закрыта", "Город", "Дата", "Диапазон", "Сторона",
                            "Шт", "Цена", "Edge", "Исход", "P&L $"]
            st.dataframe(view, use_container_width=True, hide_index=True, height=300)

            fig = px.scatter(done, x=done.edge.astype(float), y="pnl",
                             color=done.outcome.map({1: "попал", 0: "мимо"}),
                             size=done.qty.astype(float),
                             color_discrete_map={"попал": "#34D399", "мимо": "#F87171"},
                             labels={"x": "edge на входе", "pnl": "P&L, $", "color": ""})
            fig.add_hline(y=0, line=dict(color="#6B7280", dash="dot"))
            fig.add_vline(x=0, line=dict(color="#6B7280", dash="dot"))
            fig.update_layout(title="Оправдался ли edge", height=360, **PLOT)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Закрытых сделок пока нет — появятся после исходов.")

        op = q("""SELECT opened_at, city, target_date, bucket_lo, bucket_hi, side,
                         qty, entry_price, edge, my_prob, market_mid, ens_mean, reason
                  FROM positions WHERE mode=%s AND status='open'
                  ORDER BY opened_at DESC""", (mode,))
        if len(op):
            st.markdown("---")
            st.subheader("Открытые позиции")
            o = op.copy()
            o["диапазон"] = o.apply(bucket_label, axis=1)
            o["вложено"] = (o.qty.astype(float) * o.entry_price.astype(float)).round(2)
            view = o[["city", "target_date", "диапазон", "side", "qty",
                      "entry_price", "вложено", "my_prob", "market_mid", "edge"]]
            view.columns = ["Город", "Дата", "Диапазон", "Сторона", "Шт",
                            "Цена", "Вложено $", "Моя p", "Рынок", "Edge"]
            st.dataframe(view, use_container_width=True, hide_index=True)

        sk = q("""SELECT reason, count(*) n FROM decisions
                  WHERE mode=%s AND action='skip' AND ts > now() - interval '48 hours'
                  GROUP BY 1 ORDER BY 2 DESC LIMIT 12""", (mode,))
        if len(sk):
            st.markdown("---")
            st.subheader("Почему пропускали (48ч)")
            st.caption("Если один фильтр режет почти всё — возможно, он слишком строгий.")
            f = px.bar(sk.sort_values("n"), x="n", y="reason", orientation="h",
                       labels={"n": "случаев", "reason": ""})
            f.update_layout(height=max(260, 26 * len(sk)), **PLOT)
            st.plotly_chart(f, use_container_width=True)

# ─────────────────────────────── ИСТОРИЯ ───────────────────────────────
with t4:
    c = st.columns([1, 1, 2])
    days_back = c[0].number_input("Дней назад", 1, 60, 7)
    only_closed = c[1].toggle("Только с исходом", value=True)

    hist = q("""
        WITH last AS (
          SELECT DISTINCT ON (city, target_date, market_id) *
          FROM my_probs WHERE target_date >= current_date - %s
          ORDER BY city, target_date, market_id, computed_at DESC)
        SELECT p.target_date, p.city, p.market_id, p.bucket_lo, p.bucket_hi,
               p.ens_mean, p.ens_sd, p.my_prob, p.market_mid, p.edge,
               o.tmax_f, p.computed_at
        FROM last p LEFT JOIN outcomes o USING (city, target_date)
        WHERE p.market_mid IS NOT NULL
        ORDER BY p.target_date DESC, p.city, abs(p.edge) DESC""", (int(days_back),))

    if not len(hist):
        st.info("Нет данных за период.")
    else:
        if only_closed:
            hist = hist[hist.tmax_f.notna()]
        if not len(hist):
            st.info("Закрытых рынков за период нет — исходы появляются на следующий день.")
        else:
            hist["попал"] = hits(hist).map({1: "✓", 0: "—"})
            hist["диапазон"] = hist.apply(bucket_label, axis=1)
            hist["моя p"] = hist.my_prob.astype(float).round(3)
            hist["рынок"] = hist.market_mid.astype(float).round(3)
            hist["edge_r"] = hist.edge.astype(float).round(3)
            hist["прогноз"] = hist.ens_mean.astype(float).round(1)
            hist["факт"] = hist.tmax_f.astype(float)
            hist["промах"] = (hist["прогноз"] - hist["факт"]).round(1)

            m = st.columns(4)
            closed_n = int(hist.tmax_f.notna().sum())
            m[0].metric("Записей", len(hist))
            m[1].metric("Закрыто", closed_n)
            if closed_n:
                y = hits(hist)
                bm = ((hist.my_prob.astype(float) - y) ** 2).mean()
                bk = ((hist.market_mid.astype(float) - y) ** 2).mean()
                m[2].metric("Brier мой", f"{bm:.4f}")
                m[3].metric("Brier рынка", f"{bk:.4f}", f"{bk - bm:+.4f}",
                            delta_color="normal" if bm < bk else "inverse")

            view = hist[["target_date", "city", "диапазон", "прогноз", "факт",
                         "промах", "моя p", "рынок", "edge_r", "попал"]]
            view.columns = ["Дата", "Город", "Диапазон", "Прогноз", "Факт",
                            "Промах °F", "Моя p", "Рынок", "Edge", "Попал"]
            st.dataframe(view, use_container_width=True, hide_index=True, height=520)

            st.markdown("---")
            by_day = (hist.dropna(subset=["факт"])
                      .groupby(["target_date", "city"], as_index=False)
                      .agg(промах=("промах", "mean")))
            if len(by_day):
                f = px.line(by_day, x="target_date", y="промах", color="city", markers=True,
                            labels={"target_date": "", "промах": "°F (+ = завысили)"})
                f.add_hline(y=0, line=dict(color="#6B7280", dash="dot"))
                f.update_layout(title="Промах прогноза по дням", height=340, **PLOT)
                st.plotly_chart(f, use_container_width=True)

# ─────────────────────────────── КАЛИБРОВКА ───────────────────────────────
with t5:
    cal = closed()
    if len(cal) < 20:
        st.info(f"Нужно 20+ закрытых рынков. Есть {len(cal)}. Копим.")
    else:
        bm = ((cal.my_prob - cal.y) ** 2).mean()
        bk = ((cal.market_mid - cal.y) ** 2).mean()
        m = st.columns(4)
        m[0].metric("Закрытых рынков", len(cal))
        m[1].metric("Brier мой", f"{bm:.4f}")
        m[2].metric("Brier рынка", f"{bk:.4f}")
        m[3].metric("Разница", f"{bk - bm:+.4f}",
                    "лучше рынка" if bm < bk else "хуже рынка",
                    delta_color="normal" if bm < bk else "inverse")

        st.markdown("---")
        left, right = st.columns(2)

        bins = pd.cut(cal.my_prob, [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0])
        g = (cal.groupby(bins, observed=True)
             .agg(pred=("my_prob", "mean"), real=("y", "mean"), n=("y", "size"))
             .reset_index(drop=True).dropna())
        fig = go.Figure()
        fig.add_scatter(x=[0, 1], y=[0, 1], mode="lines", name="идеал",
                        line=dict(dash="dot", color="#4B5563"))
        fig.add_scatter(x=g.pred, y=g.real, mode="markers+lines", name="я",
                        marker=dict(size=(g.n ** 0.5 * 4).clip(8, 34), color="#60A5FA"),
                        line=dict(color="#60A5FA", width=2))
        fig.update_layout(title="Калибровка: сказал → сбылось", height=380,
                          xaxis_title="моя вероятность", yaxis_title="доля сбывшихся", **PLOT)
        left.plotly_chart(fig, use_container_width=True)

        fee = 0.01
        cal = cal.assign(e=cal.my_prob - cal.market_mid)
        bets = cal[cal.e.abs() >= 0.10].copy()
        if len(bets):
            bets["pnl"] = bets.apply(
                lambda r: (r.y - r.market_mid) - fee if r.e > 0
                else ((1 - r.y) - (1 - r.market_mid)) - fee, axis=1)
            bets = bets.sort_values("target_date")
            f2 = go.Figure()
            f2.add_scatter(y=bets.pnl.cumsum(), mode="lines", line=dict(color="#34D399", width=2))
            f2.add_hline(y=0, line=dict(color="#6B7280", dash="dot"))
            f2.update_layout(title=f"Бумажный P&L (все сигналы) · {len(bets)} ставок · "
                                   f"ROI {bets.pnl.mean() * 100:+.1f}%/ставка",
                             height=380, xaxis_title="ставка", yaxis_title="$ на контракт", **PLOT)
            right.plotly_chart(f2, use_container_width=True)
            right.caption("Гипотетический P&L без фильтров риска. "
                          "Реальные сделки — во вкладке «Торговля».")
        else:
            right.info("Нет ставок выше порога 10%")

# ─────────────────────────── ТОЧНОСТЬ МОДЕЛЕЙ ───────────────────────────
with t6:
    err = q("""WITH last AS (
                 SELECT DISTINCT ON (city, target_date, source)
                        city, target_date, source, tmax_f
                 FROM model_forecasts WHERE fetched_at::date < target_date
                 ORDER BY city, target_date, source, fetched_at DESC)
               SELECT f.city, f.source, f.target_date,
                      f.tmax_f AS pred, o.tmax_f AS fact,
                      f.tmax_f - o.tmax_f AS err
               FROM last f JOIN outcomes o USING (city, target_date)""")
    if len(err) < 10:
        st.info(f"Нужно больше закрытых дней. Есть {len(err)} наблюдений.")
    else:
        err["err"] = err.err.astype(float)
        left, right = st.columns(2)

        by_src = err.groupby("source").agg(
            bias=("err", "mean"), mae=("err", lambda s: s.abs().mean()),
            n=("err", "size")).reset_index()
        f1 = px.bar(by_src.sort_values("mae"), x="mae", y="source", orientation="h",
                    color="bias", color_continuous_scale="RdBu", range_color=[-4, 4],
                    labels={"mae": "средняя ошибка, °F", "source": "", "bias": "смещение"})
        f1.update_layout(title="Точность моделей (меньше — лучше)", height=340, **PLOT)
        left.plotly_chart(f1, use_container_width=True)

        by_city = err.groupby("city").agg(
            bias=("err", "mean"), mae=("err", lambda s: s.abs().mean()),
            n=("err", "size")).reset_index()
        f2 = px.bar(by_city.sort_values("bias"), x="bias", y="city", orientation="h",
                    color="bias", color_continuous_scale="RdBu", range_color=[-4, 4],
                    labels={"bias": "систематическое смещение, °F", "city": ""})
        f2.update_layout(title="Смещение по городам (+ = завышаем)", height=340, **PLOT)
        right.plotly_chart(f2, use_container_width=True)

        st.caption("Основа для v3 синтеза: вычесть смещение по городу, сузить SD где модели точны.")
        st.dataframe(by_city.round(2), use_container_width=True, hide_index=True)

# ─────────────────────────────── ЗДОРОВЬЕ ───────────────────────────────
with t7:
    m = st.columns(4)
    days = q("SELECT count(DISTINCT target_date) n FROM outcomes")
    snaps = q("SELECT count(*) n FROM market_snapshots")
    last = q("SELECT max(ts) t FROM run_log WHERE ok")
    errs = q("SELECT count(*) n FROM run_log WHERE NOT ok AND ts > now()-interval '24 hours'")
    m[0].metric("Дней с исходами", int(days.n[0]) if len(days) else 0)
    m[1].metric("Снимков рынка", f"{int(snaps.n[0]):,}" if len(snaps) else 0)
    m[2].metric("Последний прогон", str(last.t[0])[11:16] if len(last) and last.t[0] else "—")
    m[3].metric("Ошибок за 24ч", int(errs.n[0]) if len(errs) else 0, delta_color="inverse")

    daily = q("""SELECT date(fetched_at) d, venue, count(*) n FROM market_snapshots
                 WHERE fetched_at > now()-interval '30 days' GROUP BY 1,2 ORDER BY 1""")
    if len(daily):
        f = px.bar(daily, x="d", y="n", color="venue",
                   color_discrete_map={"kalshi": "#60A5FA", "polymarket": "#A78BFA"},
                   labels={"d": "", "n": "строк", "venue": ""})
        f.update_layout(title="Сбор по дням — дыры означают пропуски", height=320, **PLOT)
        st.plotly_chart(f, use_container_width=True)

    liq = q("""SELECT DISTINCT ON (market_id) market_id, city, target_date,
                      spread, depth_yes_2c, notional_yes_2c, notional_no_2c
               FROM liquidity WHERE target_date >= current_date
               ORDER BY market_id, fetched_at DESC""")
    if len(liq):
        st.markdown("---")
        st.subheader("Ликвидность")
        st.caption("Сколько денег стоит в пределах 2¢ от лучшей цены — это потолок размера позиции.")
        tot = liq.notional_yes_2c.astype(float).sum()
        st.metric("Всего доступно", f"${tot:,.0f}",
                  f"максимум ${liq.notional_yes_2c.astype(float).max():,.0f} на рынке")
        st.dataframe(liq.sort_values("notional_yes_2c", ascending=False).head(15),
                     use_container_width=True, hide_index=True)

    e = q("SELECT ts, job, left(msg, 200) msg FROM run_log WHERE NOT ok ORDER BY ts DESC LIMIT 20")
    if len(e):
        st.markdown("---")
        st.dataframe(e, use_container_width=True, hide_index=True)
    else:
        st.success("Ошибок нет")

# ─────────────────────────────── ИИ-РАЗБОР ───────────────────────────────
with t8:
    st.caption("Модель читает текущие данные и объясняет, что в них видно. "
               "Быстрый разбор — Sonnet, глубокий — Opus (дороже, для еженедельного анализа).")

    c = st.columns([1, 1, 3])
    deep = c[1].toggle("Глубокий (Opus)",
                       help="Медленнее и дороже, но лучше видит связи в накопленных данных")

    if c[0].button("Проанализировать", type="primary"):
        with st.spinner("Читаю данные…"):
            try:
                import analyst
                st.session_state["ai"] = analyst.analyze(
                    "claude-opus-5" if deep else "claude-sonnet-4-6")
                st.session_state["ai_at"] = dt.datetime.now()
                st.session_state["ai_model"] = "Opus" if deep else "Sonnet"
            except Exception as ex:
                st.error(f"Ошибка: {ex}")

    if st.session_state.get("ai"):
        st.caption(f"{st.session_state.get('ai_model', '')} · "
                   f"{st.session_state['ai_at']:%d.%m %H:%M}")
        st.markdown(st.session_state["ai"])
