"""«Отчёт брокера» → окно «Графики», адрес окна …/broker-charts/, ссылка «Графики ↗» в шапке отчёта.

Одна фигура, общая ось дат:
  1) ГЛАВНЫЙ — доходность за выбранный период, %: индекс доходности (Account.index_series, способ «б» сводки), в начале
     любого периода 0 %; ввод/вывод на него не влияют;
  2) просадка индекса от пика, % (то же число, что «Макс. просадка, %» сводки за тот же период);
  3) стоимость счёта, ₽ — дополнительно: по дням (между справками — оценка; на концах справок честный рывок к точной
     стоимости, не сглаживается), точки — точная стоимость, ▲ ввод / ▼ вывод.
Шкала «обычная / лог» — полосы 1 и 3.
"""
from __future__ import annotations

import datetime as dt

import plotly.graph_objects as go
from plotly.subplots import make_subplots

GRAPHITE = {"bg": "#111722", "line": "#1f2938", "txt": "#e6ebf3", "mut": "#8e9ab0", "acc": "#22c3ee",
            "ok": "#5ee0a0", "bad": "#ff6b7a", "idx": "#f5b94a"}
LIGHT = {"bg": "#ffffff", "line": "#dfe4ec", "txt": "#1d2433", "mut": "#5f6b7f", "acc": "#2563eb",
         "ok": "#18794e", "bad": "#c0262d", "idx": "#b45309"}
WARM = {"bg": "#1c1712", "line": "#30261c", "txt": "#efe6da", "mut": "#a69683", "acc": "#eda04a",
        "ok": "#5ee0a0", "bad": "#ff6b7a", "idx": "#7cc4ff"}
THEMES = {"graphite": GRAPHITE, "light": LIGHT, "warm": WARM}
FLOW_NAMES = (("dep", "ввод"), ("wd", "вывод"), ("tr", "перевод"))
LABEL_SHARE = 0.10            # подпись суммы у маркера — только у движений ≥ 10 % стоимости, остальные — в подсказке
PCT_TICKS = (-90, -75, -50, -25, 0, 25, 50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000, 20000, 50000)
T_RET, T_DD, T_EQ = "Доходность за период, % — без влияния ввода и вывода", "Просадка от пика, %", "Стоимость счёта, ₽"


def rub(x: float) -> str:
    """1234567.8 → «1 234 568» (неразрывный пробел, минус «−»)."""
    s = f"{abs(x):,.0f}".replace(",", "\u00a0")
    return ("−" if round(x) < 0 else "") + s


def short(x: float) -> str:
    a = abs(x)
    s = f"{a / 1e6:.1f}".replace(".", ",") + " млн" if a >= 1e6 else f"{a / 1e3:.0f} тыс."
    return ("+" if x > 0 else "−") + s


def pct_txt(v: float | None, plus: bool = True) -> str:
    if v is None:
        return "—"
    s = f"{v:+,.1f}" if plus else f"{v:,.1f}"
    return s.replace(",", "\u00a0").replace(".", ",").replace("-", "−") + "%"


def ru_date(d: str) -> str:
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}"


def series(acc, d0: str | None = None, d1: str | None = None) -> dict:
    """Ряды за [d0, d1] (по умолчанию — вся история). acc — brep_model.Account."""
    d0 = max(d0 or acc.first, acc.first)[:10]
    d1 = min(d1 or acc.last, acc.last)[:10]
    base, ix = acc.index_series(d0, d1, with_base=True)
    if not ix:
        return {"dates": []}
    rows = {r["date"]: r for r in acc.days}
    pts = {d for d, _ in acc.points}
    dates, eq, exact, ret, dd, flows = [], [], [], [], [], []
    peak = 100.0
    for d, k, _ in ix:
        r = rows[d]
        peak = max(peak, k)
        dates.append(d)
        eq.append(r["equity"])
        exact.append(bool(r.get("exact")) or d in pts)
        ret.append(k - 100.0)
        dd.append((k / peak - 1) * 100)
        for x, name in FLOW_NAMES:
            v = r.get(x, 0.0) or 0.0
            if abs(v) >= 0.5:
                flows.append({"date": d, "amount": v, "name": name, "equity": r["equity"]})
    return {"dates": dates, "equity": eq, "exact": exact, "ret": ret, "dd": dd, "flows": flows, "base": base,
            "episodes": episodes(dates, [100 + v for v in ret])}


def episodes(dates: list, idx: list, top: int = 3) -> list:
    """Эпизоды просадки индекса: пик → дно → восстановление (None — ещё не восстановились). Крупнейшие top."""
    out, pk_i, tr_i, in_dd = [], 0, 0, False
    for i, v in enumerate(idx):
        if v >= idx[pk_i]:
            if in_dd:
                out.append((pk_i, tr_i, i))
                in_dd = False
            pk_i = tr_i = i
        else:
            in_dd = True
            if v < idx[tr_i]:
                tr_i = i
    if in_dd:
        out.append((pk_i, tr_i, None))
    res = [{"peak": dates[p], "trough": dates[t], "recovered": dates[r] if r is not None else None,
            "depth": (idx[t] / idx[p] - 1) * 100,
            "days": (dt.date.fromisoformat(dates[r] if r is not None else dates[-1]) - dt.date.fromisoformat(dates[p])).days}
           for p, t, r in out]
    return sorted(res, key=lambda e: e["depth"])[:top]


def log_ticks(lo: float, hi: float) -> list:
    """Деления лог-шкалы 1-2-5 в пределах [lo, hi]."""
    out, m = [], 10 ** int(f"{max(lo, 1e-9):e}".split("e")[1])
    while m <= hi * 10:
        out += [v for v in (m, 2 * m, 5 * m) if lo * 0.8 <= v <= hi * 1.25]
        m *= 10
    return out


def figure(s: dict, theme: str = "graphite", height: int = 860, log: bool = False) -> go.Figure:
    c = THEMES.get(theme, GRAPHITE)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.045, row_heights=[0.52, 0.18, 0.30],
                        subplot_titles=(T_RET, T_DD, T_EQ))
    if not s["dates"]:
        return fig
    # 1) доходность: обычная шкала — % напрямую; лог — множитель (1 + %/100), подписи делений в %
    y_ret = [1 + v / 100 for v in s["ret"]] if log else s["ret"]
    fig.add_trace(go.Scatter(x=s["dates"], y=y_ret, mode="lines", name="доходность", line=dict(color=c["idx"], width=2.2),
                             fill=None if log else "tozeroy", fillcolor=_alpha(c["idx"], 0.10), customdata=s["ret"],
                             hovertemplate="доходность %{customdata:+,.1f}%<extra></extra>", showlegend=False), 1, 1)
    fig.add_hline(y=1 if log else 0, line=dict(color=c["mut"], width=1, dash="dot"), row=1, col=1)
    # 2) просадка индекса
    fig.add_trace(go.Scatter(x=s["dates"], y=s["dd"], mode="lines", name="просадка", fill="tozeroy",
                             line=dict(color=c["bad"], width=1), fillcolor=_alpha(c["bad"], 0.28),
                             hovertemplate="просадка %{y:.1f}%<extra></extra>", showlegend=False), 2, 1)
    for e in s["episodes"]:
        fig.add_vrect(x0=e["peak"], x1=e["recovered"] or s["dates"][-1], fillcolor=_alpha(c["bad"], 0.07), line_width=0, row=2, col=1)
        rec = f"восстановление {ru_date(e['recovered'])}" if e["recovered"] else "не восстановлена"
        fig.add_annotation(x=e["trough"], y=e["depth"], row=2, col=1, text=f"{pct_txt(e['depth'], False)} · {e['days']} дн.",
                           hovertext=f"пик {ru_date(e['peak'])} → дно {ru_date(e['trough'])}, {rec}",
                           showarrow=True, arrowhead=0, arrowcolor=c["mut"], ax=0, ay=14, yanchor="top",
                           font=dict(color=c["txt"], size=11), bgcolor=_alpha(c["bg"], 0.8))
    # 3) стоимость счёта
    est = ["точно — конец справки" if e else "оценка между справками" for e in s["exact"]]
    fig.add_trace(go.Scatter(x=s["dates"], y=s["equity"], mode="lines", name="стоимость", line=dict(color=c["acc"], width=1.5),
                             customdata=est, hovertemplate="%{y:,.0f} ₽ · %{customdata}<extra></extra>", showlegend=False), 3, 1)
    xe = [d for d, e in zip(s["dates"], s["exact"]) if e]
    ye = [v for v, e in zip(s["equity"], s["exact"]) if e]
    fig.add_trace(go.Scatter(x=xe, y=ye, mode="markers", name="● точная стоимость (конец справки)",
                             marker=dict(color=c["acc"], size=6, line=dict(color=c["bg"], width=1)), hoverinfo="skip"), 3, 1)
    for sign, sym, col, nm in ((1, "triangle-up", c["ok"], "ввод / перевод на счёт"), (-1, "triangle-down", c["bad"], "вывод / перевод со счёта")):
        fl = [f for f in s["flows"] if (f["amount"] > 0) == (sign > 0)]
        if not fl:
            continue
        big = [abs(f["amount"]) >= LABEL_SHARE * max(f["equity"] - min(f["amount"], 0), 1) for f in fl]
        fig.add_trace(go.Scatter(
            x=[f["date"] for f in fl], y=[f["equity"] for f in fl], mode="markers+text", name=nm,
            marker=dict(symbol=sym, size=11, color=col, line=dict(color=c["bg"], width=1)),
            text=[short(f["amount"]) if b else "" for f, b in zip(fl, big)], textposition="top center" if sign > 0 else "bottom center",
            textfont=dict(color=col, size=11), customdata=[f"{f['name']} {rub(f['amount'])} ₽" for f in fl],
            hovertemplate="%{customdata}<extra></extra>"), 3, 1)
    # оси
    y0, y1 = int(s["dates"][0][:4]), int(s["dates"][-1][:4])
    for y in range(y0 + 1, y1 + 1):
        fig.add_vline(x=f"{y}-01-01", line=dict(color=c["line"], width=1, dash="dot"))
    ax = dict(gridcolor=c["line"], zerolinecolor=c["line"], linecolor=c["line"], tickfont=dict(color=c["mut"]),
              showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1, spikecolor=c["mut"], spikedash="dot")
    span = (dt.date.fromisoformat(s["dates"][-1]) - dt.date.fromisoformat(s["dates"][0])).days
    fig.update_xaxes(**ax, hoverformat="%d.%m.%Y",
                     **(dict(dtick="M12", tickformat="%Y", ticklabelmode="period") if span > 730 else
                        dict(dtick="M3" if span > 300 else "M1", tickformat="%m.%Y")))
    fig.update_yaxes(**ax)
    if log:
        lo, hi = min(y_ret), max(y_ret)
        ps = [p for p in PCT_TICKS if lo * 0.85 <= 1 + p / 100 <= hi * 1.15]
        fig.update_yaxes(type="log", tickvals=[1 + p / 100 for p in ps],
                         ticktext=[(f"{p:+,}".replace(",", "\u00a0").replace("-", "−") if p else "0") + "%" for p in ps], row=1, col=1)
        tv = log_ticks(min(s["equity"]), max(s["equity"]))
        fig.update_yaxes(type="log", tickvals=tv, ticktext=[rub(v) for v in tv], row=3, col=1)
    else:
        fig.update_yaxes(ticksuffix="%", tickformat=",.0f", row=1, col=1)
        fig.update_yaxes(tickformat=",.0f", row=3, col=1)
    fig.update_yaxes(ticksuffix="%", tickformat=".0f", row=2, col=1)
    for t in (T_RET, T_DD, T_EQ):
        fig.update_annotations(selector=dict(text=t), font=dict(color=c["txt"], size=14 if t == T_RET else 13), x=0, xanchor="left")
    fig.update_layout(
        height=height, margin=dict(l=76, r=20, t=34, b=30), paper_bgcolor=c["bg"], plot_bgcolor=c["bg"],
        font=dict(family="IBM Plex Sans, Segoe UI, Roboto, sans-serif", color=c["txt"], size=12), separators=", ",
        hovermode="x unified", hoverlabel=dict(bgcolor=c["bg"], bordercolor=c["line"], font=dict(color=c["txt"])),
        legend=dict(orientation="h", x=1, xanchor="right", y=0.276, yanchor="bottom",   # в зазоре над «Стоимостью» font=dict(color=c["mut"], size=11),
                    bgcolor="rgba(0,0,0,0)"))
    return fig


def _alpha(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


# ───────────────────────── пакет 2: месяцы, затраты, инструменты ─────────────────────────
MON_RU = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
PALETTE = ["#22c3ee", "#f5b94a", "#5ee0a0", "#c084fc", "#fb923c", "#f472b6", "#94a3b8"]
OTHER_INS = "прочие"


def _layout(fig: go.Figure, c: dict, height: int, legend: bool = False) -> go.Figure:
    ax = dict(gridcolor=c["line"], zerolinecolor=c["line"], linecolor=c["line"], tickfont=dict(color=c["mut"]))
    fig.update_xaxes(**ax)
    fig.update_yaxes(**ax)
    fig.update_annotations(font=dict(color=c["txt"], size=13))
    fig.update_layout(height=height, margin=dict(l=76, r=20, t=34, b=30), paper_bgcolor=c["bg"], plot_bgcolor=c["bg"],
                      font=dict(family="IBM Plex Sans, Segoe UI, Roboto, sans-serif", color=c["txt"], size=12), separators=", ",
                      hoverlabel=dict(bgcolor=c["bg"], bordercolor=c["line"], font=dict(color=c["txt"])), showlegend=legend,
                      legend=dict(orientation="h", x=0, y=1.0, yanchor="bottom", font=dict(color=c["mut"], size=11),
                                  bgcolor="rgba(0,0,0,0)"))
    return fig


def _num(v: float | None, d: int = 1) -> str:
    return "" if v is None else f"{v:+,.{d}f}".replace(",", "\u00a0").replace(".", ",").replace("-", "−")


def month_returns(s: dict) -> dict:
    """{«ГГГГ-ММ»: % месяца} по индексу периода: конец месяца к концу прошлого (первый — к базе периода)."""
    out, prev, cur, last = {}, 100.0, None, None
    for d, r in zip(s["dates"], s["ret"]):
        if cur is not None and d[:7] != cur:
            out[cur] = (last / prev - 1) * 100
            prev = last
        cur, last = d[:7], 100.0 + r
    if cur is not None:
        out[cur] = (last / prev - 1) * 100
    return out


def year_returns(mr: dict) -> dict:
    k = {}
    for m, v in mr.items():
        k[m[:4]] = k.get(m[:4], 1.0) * (1 + v / 100)
    return {y: (x - 1) * 100 for y, x in k.items()}


def fig_months(s: dict, theme: str = "graphite") -> go.Figure:
    c = THEMES.get(theme, GRAPHITE)
    mr = month_returns(s) if s["dates"] else {}
    if not mr:
        return _layout(go.Figure(), c, 120)
    years = sorted({m[:4] for m in mr})
    z = [[mr.get(f"{y}-{i:02d}") for i in range(1, 13)] for y in years]
    yr = year_returns(mr)
    m_abs = max(abs(v) for row in z for v in row if v is not None) or 1.0
    y_abs = max(abs(v) for v in yr.values()) or 1.0
    scale = [[0, c["bad"]], [0.5, c["line"]], [1, c["ok"]]]
    fig = make_subplots(rows=1, cols=2, column_widths=[0.9, 0.1], horizontal_spacing=0.01)
    fig.add_trace(go.Heatmap(z=z, x=MON_RU, y=years, zmin=-m_abs, zmax=m_abs, colorscale=scale, showscale=False, xgap=3, ygap=3,
                             text=[[_num(v) for v in row] for row in z], texttemplate="%{text}", textfont=dict(color=c["txt"], size=12),
                             hovertemplate="%{x} %{y}: %{text}%<extra></extra>", hoverongaps=False), 1, 1)
    fig.add_trace(go.Heatmap(z=[[yr[y]] for y in years], x=["год"], y=years, zmin=-y_abs, zmax=y_abs, colorscale=scale,
                             showscale=False, xgap=3, ygap=3, text=[[_num(yr[y])] for y in years], texttemplate="<b>%{text}</b>",
                             textfont=dict(color=c["txt"], size=12), hovertemplate="%{y}: %{text}%<extra></extra>"), 1, 2)
    fig.update_yaxes(autorange="reversed", type="category")
    fig.update_yaxes(showticklabels=False, row=1, col=2)
    fig.update_xaxes(side="top", showgrid=False)
    fig.update_yaxes(showgrid=False)
    _layout(fig, c, 70 + 36 * len(years))
    fig.update_layout(margin=dict(l=56, r=20, t=30, b=10))
    return fig


def waterfall_values(acc, s: dict) -> tuple[list, float]:
    """Разложение прибыли периода. Период совпал с концами справок — ровно строки сводки; иначе — суммы дней, а в
    «прочее» уходит остаток до прибыли плашки (переоценка бумаг, начисленная ВМ на концах)."""
    a, b = s["dates"][0], s["dates"][-1]
    profit = kpi_values(s)["profit"]
    sm = acc.summary(a, b, "w")
    if sm and sm["from"] == s["base"][0] and sm["to"] == b:
        vm, inc, br, ex, tax, profit = sm["vm"], sm["income"], sm["broker"], sm["exch"], sm["tax"], sm["profit"]
    else:
        rows = [r for r in acc.days if a <= r["date"] <= b]
        vm, inc, br, ex, tax = (sum(r[f] for r in rows) for f in ("vm", "income", "broker", "exch", "tax"))
    parts = [("ВМ", vm), ("доходы", inc), ("бумаги и пр.", profit - vm - inc - br - ex - tax),
             ("брокер", br), ("биржа", ex), ("налоги", tax)]
    return parts, profit


def fig_costs(acc, s: dict, theme: str = "graphite") -> go.Figure:
    c = THEMES.get(theme, GRAPHITE)
    if not s["dates"]:
        return _layout(go.Figure(), c, 120)
    parts, profit = waterfall_values(acc, s)
    fig = make_subplots(rows=1, cols=2, column_widths=[0.42, 0.58], horizontal_spacing=0.09,
                        specs=[[{}, {"secondary_y": True}]],
                        subplot_titles=("Из чего прибыль за период, ₽", "Затраты по месяцам, ₽ (столбцы) и на лот, ₽ (линия)"))
    tips = {"ВМ": "вариационная маржа (оплаченная + начисленная на концах справок)", "доходы": "проценты на остаток, купоны, дивиденды",
            "бумаги и пр.": "переоценка бумаг и валюты, прочее — остаток до прибыли", "брокер": "комиссия брокера и депозитарий",
            "биржа": "биржевой и клиринговый сбор", "налоги": "НДФЛ уплачено и удержано минус возвраты",
            "прибыль": "изменение стоимости без ввода и вывода"}
    fig.add_trace(go.Waterfall(
        x=[p[0] for p in parts] + ["прибыль"], y=[p[1] for p in parts] + [profit], showlegend=False,
        customdata=[tips[p[0]] for p in parts] + [tips["прибыль"]],
        measure=["relative"] * len(parts) + ["total"], text=[short(p[1]) if abs(p[1]) >= 500 else "" for p in parts] + [short(profit)],
        textposition="outside", textfont=dict(color=c["txt"], size=11), connector=dict(line=dict(color=c["line"], width=1)),
        increasing=dict(marker=dict(color=c["ok"])), decreasing=dict(marker=dict(color=c["bad"])),
        totals=dict(marker=dict(color=c["acc"])), cliponaxis=False,
        hovertemplate="%{x}: %{y:,.0f} ₽<br>%{customdata}<extra></extra>"), 1, 1)
    g = {}
    for r in acc.days:
        if s["dates"][0] <= r["date"] <= s["dates"][-1]:
            m = g.setdefault(r["date"][:7] + "-01", [0.0, 0.0, 0.0, 0.0])
            m[0] -= r["broker"]
            m[1] -= r["exch"]
            m[2] += r["lots"]
            m[3] += r["vm"]
    ms = sorted(g)
    for i, (nm, col) in enumerate((("брокер", c["acc"]), ("биржа", c["idx"]))):
        fig.add_trace(go.Bar(x=ms, y=[g[m][i] for m in ms], name=nm, marker_color=col,
                             hovertemplate=nm + " %{y:,.0f} ₽<extra></extra>"), 1, 2)
    per_lot = [(g[m][0] + g[m][1]) / g[m][2] if g[m][2] else None for m in ms]
    share = [(g[m][0] + g[m][1]) / abs(g[m][3]) * 100 if g[m][3] else None for m in ms]
    fig.add_trace(go.Scatter(x=ms, y=per_lot, name="на лот", mode="lines+markers", line=dict(color=c["txt"], width=1.4),
                             marker=dict(size=5), customdata=share, connectgaps=False,
                             hovertemplate="на лот %{y:,.2f} ₽ · затраты / |ВМ| %{customdata:,.0f}%<extra></extra>"), 1, 2, secondary_y=True)
    fig.update_layout(barmode="stack", hovermode="closest")
    _layout(fig, c, 400, legend=True)
    fig.update_yaxes(tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(tickformat=",.0f", row=1, col=2, secondary_y=False)
    fig.update_yaxes(tickformat=",.1f", showgrid=False, rangemode="tozero", tickmode="auto", nticks=6, row=1, col=2,
                     secondary_y=True)
    fig.update_xaxes(tickangle=0, row=1, col=1)
    fig.update_xaxes(tickformat="%m.%Y", row=1, col=2)
    fig.update_layout(legend=dict(x=1, xanchor="right", y=1.08), margin=dict(t=48))
    return fig


def ins_series(acc, s: dict, top: int = 6) -> dict:
    """Накопленный результат базовых активов после затрат (ВМ + биржа + брокер) по дням периода; крупнейшие top,
    остальные — «прочие». Корзины столбцов — годы (период > 1 года) или месяцы."""
    a, b = s["dates"][0], s["dates"][-1]
    daily = acc.ins_days(a, b, True)
    tot = {}
    for cv in daily.values():
        for k, v in cv.items():
            tot[k] = tot.get(k, 0.0) + sum(v)
    keys = sorted(tot, key=lambda k: -abs(tot[k]))
    show = [k for k in keys[:top] if k != "без сделок в день списания"]
    name = lambda k: k if k in show else OTHER_INS  # noqa: E731
    names = show + ([OTHER_INS] if any(name(k) == OTHER_INS for k in keys) else [])
    by_year = len({d[:4] for d in s["dates"]}) > 1
    cum = {n: [] for n in names}
    run = {n: 0.0 for n in names}
    buckets = {}
    for d in s["dates"]:
        for k, v in daily.get(d, {}).items():
            run[name(k)] += sum(v)
            bk = d[:4] if by_year else f"{MON_RU[int(d[5:7]) - 1]} {d[:4]}"
            buckets.setdefault(bk, {n: 0.0 for n in names})[name(k)] += sum(v)
        for n in names:
            cum[n].append(run[n])
    return {"names": names, "cum": cum, "buckets": buckets, "by_year": by_year, "total": tot}


def fig_ins(acc, s: dict, theme: str = "graphite") -> go.Figure:
    c = THEMES.get(theme, GRAPHITE)
    if not s["dates"]:
        return _layout(go.Figure(), c, 120)
    x = ins_series(acc, s)
    if not x["names"]:
        return _layout(go.Figure(), c, 120)
    fig = make_subplots(rows=2, cols=1, row_heights=[0.6, 0.4], vertical_spacing=0.13,
                        subplot_titles=("Накопленный результат после затрат (ВМ + биржа + брокер), ₽",
                                        "По годам, ₽" if x["by_year"] else "По месяцам, ₽"))
    bk = list(x["buckets"])
    for i, n in enumerate(x["names"]):
        col = c["mut"] if n == OTHER_INS else PALETTE[i % len(PALETTE)]
        fig.add_trace(go.Scatter(x=s["dates"], y=x["cum"][n], name=n, legendgroup=n, mode="lines", line=dict(color=col, width=1.8),
                                 hovertemplate=n + " %{y:,.0f} ₽<extra></extra>"), 1, 1)
        fig.add_trace(go.Bar(x=bk, y=[x["buckets"][b][n] for b in bk], name=n, legendgroup=n, showlegend=False, marker_color=col,
                             hovertemplate=n + " %{x}: %{y:,.0f} ₽<extra></extra>"), 2, 1)
    tots = [sum(x["buckets"][b].values()) for b in bk]
    fig.add_trace(go.Scatter(x=bk, y=tots, mode="markers+text", name="итого", showlegend=False,
                             marker=dict(symbol="line-ew", size=26, line=dict(color=c["txt"], width=2)),
                             text=[short(v) if abs(v) >= 1000 else "" for v in tots], textposition="top center",
                             textfont=dict(color=c["txt"], size=11), hovertemplate="итого %{x}: %{y:,.0f} ₽<extra></extra>"), 2, 1)
    fig.add_hline(y=0, line=dict(color=c["mut"], width=1, dash="dot"), row=1, col=1)
    pos = [sum(max(v, 0) for v in x["buckets"][b].values()) for b in bk]
    neg = [sum(min(v, 0) for v in x["buckets"][b].values()) for b in bk]
    hi, lo = max(pos + tots + [0]), min(neg + tots + [0])
    fig.update_yaxes(range=[lo - 0.12 * (hi - lo), hi + 0.2 * (hi - lo)], row=2, col=1)
    fig.update_layout(barmode="relative", hovermode="x unified")
    _layout(fig, c, 660, legend=True)
    fig.update_yaxes(tickformat=",.0f")
    fig.update_xaxes(tickformat="%m.%Y", row=1, col=1)
    fig.update_xaxes(type="category", row=2, col=1)
    fig.update_layout(legend=dict(x=1, xanchor="right", y=1.04), margin=dict(t=48))
    return fig


# ───────────────────────────── окно (Dash на общем Flask-сервере) ─────────────────────────────

def kpi_values(s: dict) -> dict:
    """Итоги периода из тех же рядов, что график: доходность = последняя точка индекса; прибыль = конец − база − движения."""
    from brep.brep_model import years_between
    ret = s["ret"][-1]
    yrs = years_between(s["base"][0], s["dates"][-1])
    span = (dt.date.fromisoformat(s["dates"][-1]) - dt.date.fromisoformat(s["base"][0])).days
    fl = [f["amount"] for f in s["flows"]]
    return {"ret": ret, "ret_y": ((1 + ret / 100) ** (1 / yrs) - 1) * 100 if span >= 30 and ret > -100 else None,
            "dd": min(s["dd"]), "profit": s["equity"][-1] - s["base"][1] - sum(fl),
            "dep": sum(x for x in fl if x > 0), "wd": sum(x for x in fl if x < 0), "eq": s["equity"][-1]}


def kpi(s: dict) -> list:
    from dash import html
    if not s["dates"]:
        return []
    k = kpi_values(s)
    cls = lambda v: "pos" if (v or 0) > 0 else "neg" if (v or 0) < 0 else ""  # noqa: E731
    items = [("доходность за период", pct_txt(k["ret"]), cls(k["ret"]), True),
             ("годовых", pct_txt(k["ret_y"]), cls(k["ret_y"]), False),
             ("макс. просадка", pct_txt(k["dd"], False) if k["dd"] < 0 else "0%", "neg" if k["dd"] < 0 else "", False),
             ("прибыль", rub(k["profit"]) + " ₽", cls(k["profit"]), False),
             (f"стоимость на {ru_date(s['dates'][-1])}", rub(k["eq"]) + " ₽", "", False),
             ("ввод · вывод", f"{rub(k['dep'])} · {rub(k['wd'])} ₽", "", False)]
    return [html.Div([html.I(n), html.B(v, className=c)], className="k main" if m else "k") for n, v, c, m in items]


CSS = """.kpi{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 8px}.k{border:1px solid var(--line);border-radius:8px;padding:6px 12px}
.k b{display:block;font-size:15px}.k.main b{font-size:20px}.k i{font-style:normal;color:var(--mut);font-size:11.5px}
.k .pos{color:var(--okt)}.k .neg{color:var(--badt)}.chart{border:1px solid var(--line);border-radius:10px;padding:4px 6px}"""


def mount(server, url_base: str, account: str, store, report_url: str):
    """Окно «Графики» на общем Flask-сервере окна (тот же вход before_request). store — brep_view.Store."""
    import os
    from dash import Dash, Input, Output, dcc, html, no_update
    here = os.path.dirname(os.path.abspath(__file__))
    app = Dash(__name__ + "_charts", server=server, url_base_pathname=url_base, assets_folder=os.path.join(here, "assets"),
               title="Графики · отчёт брокера", update_title=None, suppress_callback_exceptions=True)
    app.index_string = app.index_string.replace("</head>", f"<style>{CSS}</style></head>")
    app.layout = html.Div([
        html.Header([html.H1(id="c-title"), html.A("← Отчёт брокера", href=report_url, className="lnk"),
                     html.Span(id="c-meta", className="meta")]),
        html.Div([dcc.RadioItems(id="c-preset", inline=True, className="seg"),
                  html.Span("с", className="meta"), dcc.Input(id="c-from", type="date", className="inp date"),
                  html.Span("по", className="meta"), dcc.Input(id="c-to", type="date", className="inp date"),
                  html.Span("шкала", className="meta"),
                  dcc.RadioItems(id="c-scale", options=[{"label": "обычная", "value": "lin"}, {"label": "лог", "value": "log"}],
                                 value="lin", inline=True, className="seg")], className="bar"),
        html.Div(id="c-kpi", className="kpi"),
        html.Div(dcc.Graph(id="c-fig", config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]},
                           style={"height": "860px"}), className="chart"),
        *[html.Section([html.Div([html.H2(h), html.Span(n, className="hint")], className="bh"),
                        dcc.Graph(id=i, config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]})],
                       className="card") for h, n, i in (
            ("Доходность по месяцам, %", "индекс доходности: конец месяца к концу прошлого; «год» — произведение месяцев периода",
             "c-months"),
            ("Затраты", "из чего сложилась прибыль периода; затраты брокера и биржи по месяцам и на один лот", "c-costs"),
            ("Инструменты", "базовый актив срочного рынка; комиссия брокера за день делится по лотам дня, как во вкладке "
                            "«По инструментам»", "c-ins"))],
        html.P("Доходность — индекс без влияния ввода и вывода (тот же расчёт, что «% за период» сводки отчёта): в начале "
               "выбранного периода 0 %. Стоимость счёта точна на концах справок (точки), между ними — оценка; на концах "
               "справок линия выравнивается к точной стоимости (переоценка бумаг/валюты).", className="hint"),
        dcc.Store(id="c-skin"),
    ], className="page")

    app.clientside_callback("function(_){try{return localStorage.getItem('panel-skin')||'graphite'}catch(e){return 'graphite'}}",
                            Output("c-skin", "data"), Input("c-title", "id"))

    @app.callback(Output("c-title", "children"), Output("c-meta", "children"), Output("c-preset", "options"),
                  Output("c-preset", "value"), Input("c-title", "id"))
    def _head(_):
        acc = store.get()
        if acc is None or not acc.reports:
            return "Графики", store.error or f"справок счёта {account} пока нет", [], None
        years = sorted({r["date"][:4] for r in acc.days})
        opts = [{"label": "всё", "value": "all"}] + [{"label": y, "value": y} for y in years] + \
               [{"label": "свой период", "value": "custom"}]
        return (f"Графики · {acc.account_no}", f"справок {len(acc.reports)} · {ru_date(acc.first)} – {ru_date(acc.last)}",
                opts, "all")

    @app.callback(Output("c-from", "value"), Output("c-to", "value"), Input("c-preset", "value"))
    def _preset(v):
        acc = store.get()
        if acc is None or not acc.reports or v in (None, "custom"):
            return no_update, no_update
        if v == "all":
            return acc.first[:10], acc.last[:10]
        return max(acc.first[:10], f"{v}-01-01"), min(acc.last[:10], f"{v}-12-31")

    @app.callback(Output("c-fig", "figure"), Output("c-kpi", "children"), Output("c-months", "figure"),
                  Output("c-costs", "figure"), Output("c-ins", "figure"),
                  Input("c-from", "value"), Input("c-to", "value"), Input("c-scale", "value"), Input("c-skin", "data"))
    def _draw(a, b, scale, skin):
        acc = store.get()
        if acc is None or not acc.reports or not a or not b:
            return go.Figure(), [], go.Figure(), go.Figure(), go.Figure()
        a, b = min(a, b)[:10], max(a, b)[:10]
        s = series(acc, a, b)
        th = skin or "graphite"
        return (figure(s, theme=th, log=scale == "log"), kpi(s), fig_months(s, th), fig_costs(acc, s, th),
                fig_ins(acc, s, th))

    return app
