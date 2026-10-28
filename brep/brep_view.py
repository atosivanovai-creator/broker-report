"""Окно «Отчёт брокера» — Dash-приложение на общем Flask-сервере (только показ).

Сводка по календарным годам, по инструментам, по дням. Один счёт — одно окно. Справки читаются из
каталога <reports_dir>/<счёт>/<счёт>_*.xls; кэш разбора — отдельно (cache_dir).

mount(server, url_base, account, reports_dir, cache_dir) — поднимает окно на адресе url_base.
"""
from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict

import dash_ag_grid as dag
from dash import Dash, Input, Output, dcc, html, no_update

from .brep_model import Store, ru

HERE = os.path.dirname(os.path.abspath(__file__))

# тема таблиц — из переменных окна (их копирует assets/brep-theme.js из родительского окна); запасные — «Графит»
THEME = ("themeQuartz.withParams({accentColor: 'var(--acc)', backgroundColor: 'var(--card)', "
         "foregroundColor: 'var(--txt)', headerBackgroundColor: 'var(--hdr)', chromeBackgroundColor: 'var(--hdr)', "
         "oddRowBackgroundColor: 'var(--odd)', borderColor: 'var(--line)', rowHoverColor: 'var(--hover)', "
         "inputBackgroundColor: 'var(--bg)', fontSize: 12.5, headerFontWeight: 650, wrapperBorderRadius: 10, "
         "spacing: 5})")
GRID = {"theme": {"function": THEME}, "rowHeight": 28, "headerHeight": 32, "animateRows": False,
        "tooltipShowDelay": 200, "enableCellTextSelection": True,
        "rowClassRules": {"tot": "params.node.rowPinned === 'bottom'"}}
DEF = {"sortable": True, "resizable": True, "filter": True, "floatingFilter": True, "suppressHeaderMenuButton": True,
       "wrapHeaderText": True, "autoHeaderHeight": True}
SIGN = {"neg": "params.value < 0", "pos": "params.value > 0"}
LINKS = []   # внешние ссылки в шапке окна: (подпись, адрес, подсказка)
MON = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def money(field, name, dec=0, sign=False, w=None, tip=None, **kw):
    c = {"field": field, "headerName": name, "type": "rightAligned", "filter": "agNumberColumnFilter",
         "valueFormatter": {"function": f"rub(params.value, {dec})"}}
    if sign:
        c["cellClassRules"] = SIGN
    if w:
        c["width"] = w
        c["minWidth"] = w          # при растяжке на ширину экрана колонка не уже своего числа
    if tip:
        c["headerTooltip"] = tip
    c.update(kw)
    return c


def pct(field, name, sign=False, tip=None, dec=1):
    c = money(field, name, dec, sign, tip=tip)
    c["valueFormatter"] = {"function": f"pct(params.value, {dec})"}
    return c


# ── сводка: строки — показатели разделами, колонки — годы и «Итого» ──
SUM_ROWS = [
    ("ДЕНЬГИ", "Стоимость на начало", "eq0", "rub", False, "стоимость счёта по справке на начало периода"),
    ("ДЕНЬГИ", "Ввод", "dep", "rub", False, "ввод денег на счёт, в т.ч. переводы со своих счетов (как в итогах брокера)"),
    ("ДЕНЬГИ", "Вывод", "wd", "rub", True, "вывод денег со счёта, в т.ч. переводы на свои счета (как в итогах брокера)"),
    ("ДЕНЬГИ", "Стоимость на конец", "eq1", "rub", False, "стоимость счёта по справке на конец периода"),
    ("ДОХОД", "Прибыль", "profit", "rub", True, "изменение стоимости счёта без ввода и вывода"),
    ("ДОХОД", "% за период", "pct", "pct", True, "год: по отрезкам между вводом/выводом — стоимость перед движением к стоимости после прошлого, отрезки перемножаются (без движений = (конец − начало) / начало); несколько лет («Итого»): произведение годовых (1 + %) − 1"),
    ("ДОХОД", "% годовых", "pct_y", "pct", True, "по стоимости на начало и конец в пересчёте на год (сложный процент); полный календарный год = «% за период»; от 30 дней данных"),
    ("ДОХОД", "в т.ч. срочный рынок (ВМ)", "vm", "rub", True,
     "вариационная маржа, включая начисленную и ещё не оплаченную на конец периода"),
    ("ДОХОД", "в т.ч. бумаги, купоны и прочее", "rest", "rub", True, "остаток прибыли: купоны, проценты, переоценка бумаг"),
    ("ЗАТРАТЫ", "Брокер", "broker", "rub", True, "комиссия брокера и депозитарий"),
    ("ЗАТРАТЫ", "Биржа", "exch", "rub", True, "биржевой сбор"),
    ("ЗАТРАТЫ", "Налоги", "tax", "rub", True, "НДФЛ: уплачено и удержано, минус возвраты"),
    ("ЗАТРАТЫ", "Затраты от прибыли до затрат", "cost_share", "pct", False, "(брокер + биржа) / прибыль до затрат и налогов"),
    ("РИСК", "ГО макс", "go_max", "rub", False, "наибольшее ГО на конец дня"),
    ("РИСК", "ГО / стоимость счёта макс", "go_pct_max", "pct", False, "наибольшая доля ГО от стоимости счёта на конец дня"),
    ("РИСК", "Худший день", "worst", "rub", True, "результат дня = ВМ − комиссии + доходы"),
    ("РИСК", "дата худшего дня", "worst_d_ru", "text", False, ""),
    ("РИСК", "Макс. просадка", "dd", "rub", True, "наибольшее падение кривой результата (без ввода/вывода) от пика"),
    ("РИСК", "Макс. просадка, %", "dd_pct", "pct", True,
     "наибольшее падение индекса доходности от пика за период (без влияния ввода/вывода) — как в окне «Графики»"),
    ("ОБОРОТ", "Лоты", "lots", "rub", False, "сумма лотов по сделкам"),
    ("ОБОРОТ", "Сделки", "deals", "rub", False, "число сделок (исполнений)"),
    ("ОБОРОТ", "Затраты на лот", "per_lot", "rub3", False, "(брокер + биржа) / лоты"),
]


def summary_table(cols_data):
    cols = [{"field": "name", "headerName": "Показатель", "pinned": "left", "width": 212, "tooltipField": "tip",
             "cellClassRules": {"sec": "params.data.sec === true"}}]
    for k, (lab, _) in enumerate(cols_data):
        # колонки лет делят ширину экрана поровну; не уже 96 px (число до 11 знаков) — дальше горизонтальная прокрутка
        # colId по подписи (год / «Итого»), а не по номеру: ширина, заданная человеком, держится за свой год
        # при смене периода (assets/brep-cols.js)
        cols.append({"field": f"c{k}", "colId": f"y-{lab}", "headerName": lab, "type": "rightAligned", "flex": 1,
                     "minWidth": 96,
                     "valueFormatter": {"function": "fmtv(params)"},
                     "cellClassRules": {"neg": "params.data.sign && params.value < 0",
                                        "pos": "params.data.sign && params.value > 0",
                                        "totc": f"'{lab}' === 'Итого'"}})
    rows, sec = [], None
    for section, name, field, fmt, sign, tip in SUM_ROWS:
        if section != sec:
            rows.append({"name": section, "sec": True})
            sec = section
        r = {"name": name, "fmt": fmt, "sign": sign, "tip": tip}
        for k, (_, d) in enumerate(cols_data):
            r[f"c{k}"] = d.get(field)
        rows.append(r)
    return cols, rows


DAY_COLS = [
    {"field": "date", "headerName": "Дата", "pinned": "left", "width": 124, "minWidth": 124, "sort": "desc",
     "valueFormatter": {"function": "params.data && params.data.lab ? params.data.lab : ruDate(params.value)"}},
    money("equity", "Стоимость счёта", w=130, tooltipField="eq_tip",
          cellClassRules={"est": "params.data && params.data.exact === false"},
          tip="точно — на день справки; курсивом — оценка по деньгам дня"),
    money("result", "Результат", sign=True, w=112, tip="ВМ − комиссии + доходы (без налогов, ввода/вывода)"),
    money("vm", "ВМ", sign=True, w=110), money("broker", "Брокер", sign=True, w=96), money("exch", "Биржа", sign=True, w=96),
    money("income", "Доходы", sign=True, w=92), money("tax", "Налоги", sign=True, w=92),
    money("flow", "Ввод/вывод", sign=True, w=110), money("lots", "Лоты", w=92), money("deals", "Сделки", w=84),
    money("go", "ГО", w=118, tip="на конец дня (неделя/месяц — максимум)"),
    pct("go_pct", "ГО/счёт", tip="на конец дня (неделя/месяц — максимум)"),
]

INS_COLS = [
    {"field": "instr", "headerName": "Инструмент", "pinned": "left", "width": 210, "minWidth": 180},
    money("vm", "ВМ", sign=True, w=130), money("exch", "Биржевой сбор", sign=True, w=124),
    money("broker", "Брокер", sign=True, w=118, tip="комиссия брокера за день, разделённая по лотам этого дня"),
    money("net", "Итог после затрат", sign=True, w=140),
    money("lots", "Лоты", w=110), money("deals", "Сделки", w=96),
    money("per_lot", "Затраты на лот", 3, w=116), pct("cost_share", "Затраты / |ВМ|"),
]


def _wkey(d: str, mode: str) -> str:
    if mode == "week":
        x = dt.date.fromisoformat(d)
        return (x - dt.timedelta(days=x.weekday())).isoformat()
    return d[:7] + "-01"


def days_rows(acc, a: str, b: str, mode: str):
    rows = [r for r in acc.days if a <= r["date"] <= b]
    out = []
    if mode == "day":
        for r in rows:
            out.append(dict(r, flow=round(r["dep"] + r["wd"] + r["tr"], 2),
                            eq_tip="точно, по справке" if r["exact"] else "оценка: последняя справка + деньги дня"))
    else:
        g = defaultdict(list)
        for r in rows:
            g[_wkey(r["date"], mode)].append(r)
        for k, rs in sorted(g.items()):
            last = rs[-1]
            lab = f"нед. {k[8:10]}.{k[5:7]}" if mode == "week" else f"{MON[int(k[5:7]) - 1]} {k[:4]}"
            s = lambda f: round(sum(x[f] for x in rs), 2)  # noqa: E731
            gp = [x["go_pct"] for x in rs if x["go_pct"] is not None]
            out.append({"date": k, "lab": lab, "equity": last["equity"], "exact": last["exact"],
                        "eq_tip": "на последний день периода" + ("" if last["exact"] else " (оценка)"),
                        "result": s("result"), "vm": s("vm"), "broker": s("broker"), "exch": s("exch"),
                        "income": s("income"), "tax": s("tax"), "flow": round(s("dep") + s("wd") + s("tr"), 2),
                        "lots": s("lots"), "deals": s("deals"), "go": max(x["go"] for x in rs),
                        "go_pct": max(gp) if gp else None})
    tot = {"date": None, "lab": "Итого"}
    for f in ("result", "vm", "broker", "exch", "income", "tax", "flow", "lots", "deals"):
        tot[f] = round(sum(r[f] for r in out), 2)
    return out, [tot]


def _block(title, note, *children):
    return html.Section([html.Div([html.H2(title), html.Span(note, className="hint")], className="bh"), *children],
                        className="card")


def mount(server, url_base: str, account: str, reports_dir: str, cache_dir: str, stand_nav: list | None = None):
    """Поднять окно на общем Flask-сервере. stand_nav — ссылки в шапке (например, «← Счета»)."""
    store = Store(account, reports_dir, cache_dir)
    app = Dash(__name__, server=server, url_base_pathname=url_base, assets_folder=os.path.join(HERE, "assets"),
               title="Отчёт брокера", update_title=None, suppress_callback_exceptions=True)

    # инфографика — отдельное окно по ссылке из отчёта (brep_charts, адрес …/broker-charts/)
    charts_url = url_base.rstrip("/").rsplit("/", 1)[0] + "/broker-charts/"
    try:
        from brep import brep_charts
        brep_charts.mount(server, charts_url, account, store, url_base)
        charts_link = [html.A("Графики ↗", href=charts_url, target="_blank", rel="noopener", className="lnk",
                              title="Отдельное окно: доходность (индекс), просадка и стоимость счёта по справкам")]
    except Exception as e:                                      # noqa: BLE001 — отчёт не падает из-за графиков
        print(f"⚠ окно «Графики» не поднялось: {type(e).__name__}: {e}", flush=True)
        charts_link = []
    links = html.Span(charts_link + [html.A(t,
                                            href=u, target="_blank", rel="noopener noreferrer", title=tip, className="lnk")
                                     for t, u, tip in LINKS], className="links")
    app.layout = html.Div([
        html.Header([html.H1(id="title"), links, html.Nav(stand_nav or []), html.Span(id="meta", className="meta")]),
        html.Div([dcc.RadioItems(id="preset", inline=True, className="seg"),
                  html.Span("с", className="meta"), dcc.Input(id="d-from", type="date", className="inp date"),
                  html.Span("по", className="meta"), dcc.Input(id="d-to", type="date", className="inp date"),
                  html.Span(id="checks", className="checks")], className="bar"),
        _block("Сводка", "по календарным годам и за выбранный период; наведите на показатель — пояснение",
               dag.AgGrid(id="g-sum", defaultColDef={"sortable": False, "resizable": True, "suppressMovable": True},
                          dashGridOptions=dict(GRID, domLayout="autoHeight"), className="g", style={"height": "auto"}),
               html.Div(id="sum-notes", className="notes")),
        _block("По инструментам", "срочный рынок; комиссия брокера за день делится по лотам дня",
               html.Div([dcc.RadioItems(id="i-mode", options=[{"label": "базовый актив", "value": "base"},
                                                              {"label": "контракт", "value": "code"}],
                                        value="base", inline=True, className="seg")], className="tools"),
               # ширина колонок — assets/brep-cols.js: сохранённая человеком, иначе «по ширине экрана», как прежде
               # columnSize="responsiveSizeToFit" (он же затирал ширины, заданные вручную)
               dag.AgGrid(id="g-i", columnDefs=INS_COLS, defaultColDef=DEF,
                          dashGridOptions=dict(GRID, domLayout="autoHeight"), className="g", style={"height": "auto"})),
        _block("По дням", "стоимость счёта точна в дни справок, между ними (курсив) — оценка",
               html.Div([dcc.RadioItems(id="d-mode", options=[{"label": "день", "value": "day"},
                                                              {"label": "неделя", "value": "week"},
                                                              {"label": "месяц", "value": "month"}],
                                        value="day", inline=True, className="seg"),
                         html.Button("CSV", id="d-csv", className="btn"),
                         # сброс запомненных колонок (обработчик — assets/brep-cols.js)
                         html.Button("Сбросить колонки", id="cols-reset", className="btn",
                                     title="Вернуть ширину и порядок колонок всех трёх таблиц отчёта к виду по "
                                           "умолчанию (для окон всех счетов в этом браузере)")],
                        className="tools"),
               dag.AgGrid(id="g-d", columnDefs=DAY_COLS, defaultColDef=DEF,               # ширины — brep-cols.js
                          dashGridOptions=GRID, className="g", style={"height": 430},
                          csvExportParams={"fileName": f"{account}-po-dnyam.csv", "columnSeparator": ";"})),
        html.Footer(id="foot"),
    ], className="page")

    def acc_or_none():
        return store.get()

    def rng(acc, a, b):
        return (a or acc.first)[:10], (b or acc.last)[:10]

    @app.callback(Output("title", "children"), Output("meta", "children"), Output("preset", "options"),
                  Output("preset", "value"), Output("checks", "children"), Output("foot", "children"),
                  Input("title", "id"))
    def _head(_):
        acc = acc_or_none()
        if acc is None or not acc.reports:
            msg = store.error or f"справок счёта {account} пока нет"
            return "Отчёт брокера", msg, [], None, "", ""
        years = sorted({r["date"][:4] for r in acc.days})
        opts = [{"label": "всё", "value": "all"}] + [{"label": y, "value": y} for y in years] + \
               [{"label": "свой период", "value": "custom"}]
        ok = not acc.recon_bad and not acc.go_bad
        checks = [html.Span("✓ " if ok else "⚠ ", className="ok" if ok else "bad"),
                  f"сверка с итогами брокера: {len(acc.reports) - len(acc.recon_bad)} из {len(acc.reports)} справок, "
                  f"9 видов денег"]
        meta = f"справок {len(acc.reports)} · {ru(acc.first)} – {ru(acc.last)}"
        foot = [html.P("Деньги — оплаченные рублёвые строки справок; по каждой справке их сумма по видам сверена с "
                       "итогами брокера «Виды движений денежных средств». Прибыль = изменение стоимости счёта без "
                       "ввода и вывода между ближайшими точными точками (начало/конец справок).")] + \
               [html.P("Замечания: " + "; ".join(acc.notes))] * bool(acc.notes) + \
               [html.P("⚠ Не сошлось со справкой: " + str(acc.recon_bad + acc.go_bad), className="bad")] * (not ok) + \
               [html.P("⚠ " + store.error, className="bad")] * bool(store.error)
        return f"Отчёт брокера · {acc.account_no}", meta, opts, "all", checks, foot

    @app.callback(Output("d-from", "value"), Output("d-to", "value"), Input("preset", "value"))
    def _preset(v):
        acc = acc_or_none()
        if acc is None or not acc.reports:
            return no_update, no_update
        if v == "all":
            return acc.first, acc.last
        if v and v.isdigit():
            return max(acc.first, f"{v}-01-01"), min(acc.last, f"{v}-12-31")
        return no_update, no_update

    @app.callback(Output("g-sum", "columnDefs"), Output("g-sum", "rowData"), Output("sum-notes", "children"),
                  Input("d-from", "value"), Input("d-to", "value"))
    def _sum(a, b):
        acc = acc_or_none()
        if acc is None or not acc.reports:
            return [], [], ""
        a, b = rng(acc, a, b)
        ys = acc.years(a, b)
        tot = acc.summary(a, b, "Итого")
        notes = [f"{s['label']}: {s['note']}" for s in ys + ([tot] if tot else []) if s["note"]]
        cols, rows = summary_table([(s["label"], s) for s in ys] + ([("Итого", tot)] if tot and len(ys) != 1 else []))
        return cols, rows, ("Границы по справкам — " + "; ".join(notes) + ": стоимость счёта известна только на "
                            "концах справок, справка за период внутри себя по стоимости не делится.") if notes else ""

    @app.callback(Output("g-d", "rowData"), Output("g-d", "dashGridOptions"),
                  Input("d-from", "value"), Input("d-to", "value"), Input("d-mode", "value"))
    def _days(a, b, mode):
        acc = acc_or_none()
        if acc is None or not acc.reports:
            return [], GRID
        a, b = rng(acc, a, b)
        data, pin = days_rows(acc, a, b, mode)
        return data, dict(GRID, pinnedBottomRowData=pin)

    @app.callback(Output("g-i", "rowData"), Output("g-i", "dashGridOptions"),
                  Input("d-from", "value"), Input("d-to", "value"), Input("i-mode", "value"))
    def _ins(a, b, mode):
        acc = acc_or_none()
        if acc is None or not acc.reports:
            return [], dict(GRID, domLayout="autoHeight")
        a, b = rng(acc, a, b)
        rows = acc.instruments(a, b, mode == "base")
        tot = {"instr": "Итого"}
        for f in ("vm", "exch", "broker", "net", "lots", "deals"):
            tot[f] = round(sum(r[f] for r in rows), 2)
        return rows, dict(GRID, domLayout="autoHeight", pinnedBottomRowData=[tot])

    @app.callback(Output("g-d", "exportDataAsCsv"), Input("d-csv", "n_clicks"), prevent_initial_call=True)
    def _csv(_):
        return True

    return app, store
