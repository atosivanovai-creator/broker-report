"""«Отчёт брокера»: справка из кабинета «по 31.12» и справка автоматики «с 31.12» пересекаются на день (main, 28.10.2026)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brep import brep_model as bm  # noqa: E402


def _r(f, t, eq, kinds, lots=None):
    return {"file": f"{f}..{t}", "from": f, "to": t, "equity": eq, "go": (0.0, 0.0), "req": (0.0, 0.0), "recon": {},
            "day_kind": kinds, "day_ins": {}, "broker_pfi": {}, "lots_paid": lots or {}, "lots_open": {}, "go_ch": {}}


def test_same_overlap_day_is_joined():
    a = _r("2021-10-01", "2025-12-31", (100.0, 205.67), {"2025-12-30": {"ВМ": -50.0}, "2025-12-31": {"%": 7.94, "к": -2.27}})
    b = _r("2025-12-31", "2026-10-15", (200.0, 300.0), {"2025-12-31": {"%": 7.94, "к": -2.27}, "2026-01-05": {"ВМ": 94.33}},
           lots={"2025-12-30": {"CR": [5.0, 1]}, "2026-01-05": {"CR": [3.0, 1]}})
    j = bm.join_overlap(a, b)
    assert j["from"] == "2026-01-01" and j["equity"] == (205.67, 300.0) and j["joined"] == 1
    assert list(j["day_kind"]) == ["2026-01-05"] and list(j["lots_paid"]) == ["2026-01-05"]


def test_different_overlap_or_contained_is_rejected():
    a = _r("2021-10-01", "2025-12-31", (100.0, 205.67), {"2025-12-31": {"%": 7.94}})
    b = _r("2025-12-31", "2026-10-15", (200.0, 300.0), {"2025-12-31": {"%": 8.00}})
    assert bm.join_overlap(a, b) is None                                       # дни различаются
    c = _r("2025-12-30", "2025-12-30", (190.0, 200.0), {})
    assert bm.join_overlap(a, c) is None                                       # целиком внутри — прежнее правило
    d = _r("2025-12-01", "2026-03-31", (150.0, 300.0), {})
    assert bm.join_overlap(a, d) is None                                       # пересечение больше 3 дней
