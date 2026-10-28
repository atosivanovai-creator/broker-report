"""«Отчёт брокера», сводка: год — по отрезкам между вводом/выводом («б»), без движений —
(конец − начало) / начало; «Итого» — произведение годовых множителей; годовых — по итогу на длину интервала;
просадка % — от пика; переводы — в строках «Ввод»/«Вывод»."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brep import brep_model as bm, brep_parse as bp  # noqa: E402


def _acc(points, kind, days):
    a = bm.Account.__new__(bm.Account)
    a.points, a.kind, a.req, a.lots = points, kind, {}, {}
    a.days = [dict(go=0.0, go_pct=None, tax=0.0, **d) for d in days]
    return a


def test_full_year_start_end():
    a = _acc([("2023-12-31", 100.0), ("2024-12-31", 150.0)], {}, [{"date": "2024-12-31", "equity": 150.0, "result": 50.0}])
    s = a.summary("2024-01-01", "2024-12-31", "2024")
    assert s["pct"] == pytest.approx(50.0) and s["pct_y"] == pytest.approx(50.0)   # полный год: годовых = за период


def test_year_split_by_flows():
    # 2022: 100 → 150 к 01.07, вывод −120 (остаётся 30), к концу 60: куски 150/100 и 60/30 → ×3 = +200 %
    a = _acc([("2021-12-31", 100.0), ("2022-12-31", 60.0)], {"2022-07-01": {bp.KIND_OUT: -120.0}},
             [{"date": "2022-07-01", "equity": 30.0, "result": 50.0}, {"date": "2022-12-31", "equity": 60.0, "result": 30.0}])
    s = a.summary("2022-01-01", "2022-12-31", "2022")
    assert s["pct"] == pytest.approx(200.0)
    assert (s["dep"], s["wd"], s["profit"]) == (0.0, -120.0, pytest.approx(80.0))


def test_total_is_product_of_years():
    a = _acc([("2021-12-31", 100.0), ("2022-12-31", 300.0), ("2023-12-31", 450.0)], {"2022-07-01": {bp.KIND_TR: 100.0}},
             [{"date": "2022-07-01", "equity": 250.0, "result": 50.0}, {"date": "2022-12-31", "equity": 300.0, "result": 50.0},
              {"date": "2023-12-31", "equity": 450.0, "result": 150.0}])
    y22 = a.summary("2022-01-01", "2022-12-31", "2022")
    assert y22["pct"] == pytest.approx((150 / 100 * 300 / 250 - 1) * 100)       # перевод +100 в день, когда было 150
    y23 = a.summary("2023-01-01", "2023-12-31", "2023")
    assert y23["pct"] == pytest.approx(50.0)                                     # перевод 2022 на 2023 не влияет
    tot = a.summary("2022-01-01", "2023-12-31", "Итого")
    k = 1.8 * 1.5
    assert tot["pct"] == pytest.approx((k - 1) * 100) and tot["pct_y"] == pytest.approx((k ** 0.5 - 1) * 100)


def test_drawdown_from_peak():
    a = _acc([("2023-12-31", 100.0), ("2024-12-31", 300.0)], {},
             [{"date": "2024-03-01", "equity": 200.0, "result": 100.0},
              {"date": "2024-05-01", "equity": 150.0, "result": -50.0},
              {"date": "2024-12-31", "equity": 300.0, "result": 150.0}])
    assert a.summary("2024-01-01", "2024-12-31", "2024")["dd_pct"] == pytest.approx(-25.0)   # 50 от пика 200


def test_transfer_shown_as_deposit_profit_unchanged():
    """Перевод со своего счёта в строке «Ввод», прибыль без него."""
    a = _acc([("2021-12-31", 100.0), ("2022-12-31", 300.0)], {"2022-01-03": {bp.KIND_TR: 100.0}, "2022-06-01": {bp.KIND_OUT: -20.0}},
             [{"date": "2022-01-03", "equity": 200.0, "result": 0.0}, {"date": "2022-06-01", "equity": 250.0, "result": 70.0},
              {"date": "2022-12-31", "equity": 300.0, "result": 50.0}])
    s = a.summary("2022-01-01", "2022-12-31", "2022")
    assert (s["dep"], s["wd"]) == (100.0, -20.0)
    assert s["profit"] == pytest.approx(120.0)                                   # 300 − 100 − 100 + 20


def test_years_between():
    assert bm.years_between("2023-12-31", "2024-12-31") == 1
    assert bm.years_between("2021-09-30", "2021-12-31") == pytest.approx(92 / 365)
    assert bm.years_between("2021-09-30", "2026-10-26") == pytest.approx(5 + 26 / 365)
