"""Окно «Графики» отчёта брокера: индекс доходности — главный график, от 0 % в начале любого
периода; просадка % сводки — по индексу («считай по индексу»); ступеньки стоимости не сглаживаются."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brep import brep_charts as bc, brep_model as bm, brep_parse as bp  # noqa: E402


def _acc(points, days, kind=None):
    a = bm.Account.__new__(bm.Account)
    a.points, a.kind, a.req, a.lots = points, kind or {}, {}, {}
    a.days = [dict(dict(go=0.0, go_pct=None, tax=0.0, dep=0.0, wd=0.0, tr=0.0, exact=False, result=0.0), **d) for d in days]
    a.first, a.last = a.days[0]["date"], a.days[-1]["date"]
    return a


# 100 → 200 (пик) → вывод 100 в день, когда стало 150 (осталось 50) → 75: индекс 100 → 200 → 150 → 225
A = dict(points=[("2023-12-31", 100.0), ("2024-12-31", 75.0)],
         days=[{"date": "2024-02-01", "equity": 200.0}, {"date": "2024-03-01", "equity": 50.0, "wd": -100.0},
               {"date": "2024-12-31", "equity": 75.0, "exact": True}],
         kind={"2024-03-01": {bp.KIND_OUT: -100.0}})


def test_index_series_ignores_flows():
    a = _acc(**A)
    assert [round(k, 6) for _, k, _ in a.index_series("2024-01-01", "2024-12-31")] == [200.0, 150.0, 225.0]
    assert a.summary("2024-01-01", "2024-12-31", "2024")["pct"] == pytest.approx(125.0)   # тот же «б»


def test_summary_drawdown_by_index_not_rubles():
    a = _acc(**A)
    s = a.summary("2024-01-01", "2024-12-31", "2024")
    assert s["dd_pct"] == pytest.approx(-25.0)                    # 150 от пика 200; вывод просадку не прячет и не создаёт


def test_series_starts_from_zero_in_any_period():
    a = _acc(**A)
    s = bc.series(a, "2024-03-01", "2024-12-31")                  # период с 01.03: база — стоимость 01.02 (200)
    assert s["ret"] == pytest.approx([-25.0, 12.5])
    assert s["dd"] == pytest.approx([-25.0, 0.0])
    assert [f["name"] for f in s["flows"]] == ["вывод"]
    full = bc.series(a)
    assert full["ret"][0] == pytest.approx(100.0) and full["ret"][-1] == pytest.approx(125.0)


def test_equity_steps_are_not_smoothed():
    a = _acc(**A)
    assert bc.series(a)["equity"] == [200.0, 50.0, 75.0]           # стоимость — как в днях модели, без разнесения


def test_figure_order_and_log():
    s = bc.series(_acc(**A))
    for log in (False, True):
        f = bc.figure(s, log=log)
        assert f.layout.annotations[0].text.startswith("Доходность за период")   # главный — первым
        assert f.data[0].name == "доходность"
    f = bc.figure(s, log=True)
    assert f.layout.yaxis.type == "log" and list(f.layout.yaxis.ticktext) == ["+50%", "+100%"]


def test_kpi_from_same_series():
    k = bc.kpi_values(bc.series(_acc(**A)))
    assert k["ret"] == pytest.approx(125.0) and k["dd"] == pytest.approx(-25.0)
    assert (k["profit"], k["dep"], k["wd"]) == (pytest.approx(75.0), 0, -100.0)        # 75 − 100 + 100


def test_episodes():
    d = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    e = bc.episodes(d, [100, 80, 120, 110])
    assert [(x["peak"], x["trough"], x["recovered"], round(x["depth"], 2)) for x in e] == \
        [(d[0], d[1], d[2], -20.0), (d[2], d[3], None, -8.33)]


# ── пакет 2: месяцы, затраты, инструменты ──

def test_months_multiply_to_period_return():
    s = bc.series(_acc(**A))
    mr = bc.month_returns(s)
    assert list(mr) == ["2024-02", "2024-03", "2024-12"]
    assert mr["2024-02"] == pytest.approx(100.0) and mr["2024-03"] == pytest.approx(-25.0)
    assert bc.year_returns(mr)["2024"] == pytest.approx(125.0)                      # = % года сводки


def test_waterfall_sums_to_profit():
    a = _acc(**A)
    for r in a.days:
        r.update(vm=10.0, income=1.0, broker=-2.0, exch=-1.0)
    s = bc.series(a, "2024-03-01", "2024-12-31")                                     # не по концам справок — суммы дней
    parts, profit = bc.waterfall_values(a, s)
    assert sum(v for _, v in parts) == pytest.approx(profit)
    assert dict(parts)["ВМ"] == pytest.approx(20.0) and dict(parts)["брокер"] == pytest.approx(-4.0)


def test_ins_days_equal_instruments():
    a = _acc(**A)
    a.lots = {"2024-02-01": {"Si-3.24": [3.0, 1], "CNY-3.24": [1.0, 1]}}
    a.ins = {"2024-02-01": {"Si-3.24": [100.0, -3.0], "CNY-3.24": [-40.0, -1.0]}, "2024-03-01": {"Si-3.24": [10.0, 0.0]}}
    a.broker_pfi = {"2024-02-01": -8.0, "2024-03-01": -1.0}
    days = a.ins_days("2024-01-01", "2024-12-31", True)
    tot = {}
    for cv in days.values():
        for k, v in cv.items():
            tot[k] = tot.get(k, 0.0) + sum(v)
    assert tot == pytest.approx({r["instr"]: r["net"] for r in a.instruments("2024-01-01", "2024-12-31", True)})
    x = bc.ins_series(a, bc.series(a))
    assert x["cum"]["Si"][-1] == pytest.approx(100 - 3 - 6 + 10) and x["by_year"] is False

