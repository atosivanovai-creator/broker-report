"""Демо-счёт для скриншотов README разбирается без нераспознанного и сходится с итогами брокера."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from brep import brep_parse as bp  # noqa: E402
from demo_data import ACC, reports  # noqa: E402


def test_demo_reports_reconcile_and_chain():
    rs = [bp.parse_report(h, n) for n, h in reports(dt.date(2026, 10, 29))]
    assert [r["from"] for r in rs] == ["2024-01-01", "2025-01-01", "2026-01-01"] and rs[-1]["to"] == "2026-10-28"
    for r in rs:
        assert r["unknown"] == {}
        assert {k: v for k, v in bp.reconcile(r).items() if abs(v[2]) > 0.005} == {}
    for a, b in zip(rs, rs[1:]):
        assert a["equity"][1] == b["equity"][0]          # конец справки = начало следующей
    assert any(ch.isdigit() for ch in ACC)               # адрес счёта в программе — по цифрам номера
