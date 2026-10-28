"""Перевод ДС между своими счетами = ввод (входящий) / вывод (исходящий) во всех местах отчёта.

Сквозная проверка: файл справки → разбор → сверка с итогами брокера → сводка, таблица по дням, графики.
Прибыль и доходность от перевода не меняются (перевод — движение денег, а не доход).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from brep import brep_charts as bc  # noqa: E402
from brep.brep_model import Store  # noqa: E402
from brep.brep_view import days_rows  # noqa: E402
from test_brep_old_format import _report  # noqa: E402

IN_ROW = '["03.11.2021", "Перевод ДС", "", "Рубль", "1 000,00", "", "1 000,00", "Счет списания: КлФ-000000"]'


def _variant(direction: str) -> str:
    """Справка из теста старого формата: перевод +1000 (как есть), −1000 (исходящий) или обычный ввод."""
    r = _report()
    if direction == "in":
        return r
    if direction == "deposit":       # тот же ввод, но «Ввод ДС» — эталон для сравнения прибыли и %
        return r.replace("<td>Перевод ДС</td>", "<td>Ввод ДС</td>", 1)
    # исходящий: «Списано» 1000, оценка −1000; итоги брокера: ввод 0, вывод 1000; стоимость на конец −2000
    r = r.replace("<td>Перевод ДС</td><td></td><td>Рубль</td><td>1 000,00</td><td></td><td>1 000,00</td>"
                  "<td>Счет списания: КлФ-000000</td>",
                  "<td>Перевод ДС</td><td></td><td>Рубль</td><td></td><td>1 000,00</td><td>-1 000,00</td>"
                  "<td>Счет зачисления: КлФ-000000</td>", 1)
    r = r.replace("<td>Рубль</td><td>1 000,00</td><td>0,00</td>", "<td>Рубль</td><td>0,00</td><td>1 000,00</td>", 1)
    r = r.replace("<td>2 086,01</td>", "<td>86,01</td>", 1)
    assert "-1 000,00" in r and "<td>86,01</td>" in r
    return r


def _acc(tmp_path, direction):
    d = tmp_path / direction
    (d / "reports" / "x").mkdir(parents=True)
    (d / "reports" / "x" / "x_2021-10-01..2021-12-31.xls").write_text(_variant(direction), encoding="utf-8")
    st = Store("x", str(d / "reports"), str(d / "cache"))
    a = st.get()
    assert a is not None and a.reports, st.error
    return a


@pytest.mark.parametrize("direction,dep,wd", [("in", 1000, 0), ("out", 0, -1000), ("deposit", 1000, 0)])
def test_transfer_is_deposit_or_withdrawal_everywhere(tmp_path, direction, dep, wd):
    a = _acc(tmp_path, direction)
    assert not a.recon_bad and not a.go_bad, (a.recon_bad, a.go_bad)          # сверка с итогами брокера: 1 из 1
    s = a.summary(a.first, a.last, "Итого")
    assert (round(s["dep"]), round(s["wd"])) == (dep, wd)                       # сводка: строки «Ввод» / «Вывод»
    assert round(s["profit"], 2) == 86.01                                       # прибыль без перевода
    rows, tot = days_rows(a, a.first, a.last, "day")                            # «По дням»: колонка «Ввод/вывод»
    assert round(tot[0]["flow"]) == dep + wd
    assert [round(r["flow"]) for r in rows if r["date"] == "2021-11-03"] == [dep + wd]
    sr = bc.series(a)                                                           # «Графики»: маркер и плашка
    assert [round(f["amount"]) for f in sr["flows"] if f["date"] == "2021-11-03"] == [dep + wd]
    k = bc.kpi_values(sr)
    assert (round(k["dep"]), round(k["wd"])) == (dep, wd) and round(k["profit"], 2) == 86.01


def test_transfer_return_equals_plain_deposit(tmp_path):
    """Доходность при переводе = доходности при обычном вводе той же суммы в тот же день."""
    t, d = _acc(tmp_path, "in"), _acc(tmp_path, "deposit")
    st, sd = t.summary(t.first, t.last, "x"), d.summary(d.first, d.last, "x")
    assert st["pct"] == pytest.approx(sd["pct"]) and st["dd_pct"] == pytest.approx(sd["dd_pct"])
    assert bc.series(t)["ret"] == pytest.approx(bc.series(d)["ret"])
