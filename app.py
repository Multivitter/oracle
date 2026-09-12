"""Oracle dashboard.
Запуск: streamlit run app.py --server.port 8502
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
  .block-container {padding-top: 2rem; max-width: 1500px;}
  h1 {font-size: 1.8rem !important; font-weight: 600; letter-spacing: -0.02em;}
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

t1, t2, t3, t4 = st.tabs(["Сигналы", "Калибровка", "Точность моделей", "Здоровье"])

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
        sig["диапазон"] = sig.apply(
            lambda r: f"{r.bucket_lo:.0f}–{r.bucket_hi:.0f}°"
            if pd.notna(r.bucket_lo) and pd.notna(r.bucket_hi)
            else (f"≥{r.bucket_lo:.0f}°" if pd.notna(r.bucket_lo) else f"≤{r.bucket_hi:.0f}°"), axis=1)
        sig["сторона"] = sig.edge.apply(lambda e: "YES" if e > 0 else "NO")
        sig["прогноз"] = sig.apply(lambda r: f"{r.ens_mean:.1f} ± {r.ens_sd:.1f}", axis=1)

        m = st.columns(4)
        m[0].metric("Сигналов", len(sig))
        m[1].metric("Городов", sig.city.nunique())
        m[2].metric("Макс. edge", f"{sig.edge.abs().max():.0%}")
        m[3].metric("Медиана", f"{sig.edge.abs().median():.0%}")

        view = sig[["city", "target_date", "диапазон", "прогноз", "my_prob",
                    "market_mid", "edge", "сторона"]]
        view.columns = ["Город", "Дата", "Диапазон", "Прогноз °F", "Моя p",
                        "Рынок", "Edge", "Сторона"]
        st.dataframe(view, use_container_width=True, hide_index=True)
        st.caption("Большой edge чаще означает ошибку модели, а не рынка.")
    else:
        st.info("Нет сигналов выше порога.")

with t2:
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
            f2.update_layout(title=f"Бумажный P&L · {len(bets)} ставок · "
                                   f"ROI {bets.pnl.mean() * 100:+.1f}%/ставка",
                             height=380, xaxis_title="ставка", yaxis_title="$ на контракт", **PLOT)
            right.plotly_chart(f2, use_container_width=True)
        else:
            right.info("Нет ставок выше порога 10%")

with t3:
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

with t4:
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

    e = q("SELECT ts, job, left(msg, 200) msg FROM run_log WHERE NOT ok ORDER BY ts DESC LIMIT 20")
    if len(e):
        st.dataframe(e, use_container_width=True, hide_index=True)
    else:
        st.success("Ошибок нет")