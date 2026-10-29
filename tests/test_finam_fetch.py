"""Автозагрузка справок через API: подставной сервер Trade API (вход, сведения о ключе, квота, заказ, готовность,
скачивание) и правило «запрашивать, только если прошлая попытка не удалась или справки ещё нет»."""
import json
import os
import sys
import xml.dom.minidom
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finam_fetch as ff  # noqa: E402
import intake  # noqa: E402

from fake_finam import ACC, RO, TRADE, spravka, start  # noqa: E402


@pytest.fixture()
def api(monkeypatch, tmp_path):
    srv, fk = start()
    monkeypatch.setattr(ff, "API", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(ff, "POLL_S", 0.01)
    monkeypatch.setattr(ff, "POLL_N", 3)
    monkeypatch.setenv("BR_NOW", "2026-10-29T10:00:00")
    if os.name != "nt":
        monkeypatch.setenv("BR_INSECURE_KEYSTORE", "1")
    fk.base = str(tmp_path / "data")
    yield fk
    srv.shutdown()


def _files(fk):
    return sorted(p.name for p in Path(fk.base, "reports", ACC).glob("*.xls"))


def test_trading_key_refused_readonly_accepted(api):
    with pytest.raises(ff.ApiError, match="ТОРГОВЫЙ"):
        ff.add_key(api.base, TRADE)
    with pytest.raises(ff.ApiError, match="недействителен"):
        ff.add_key(api.base, "wrong-secret-0123456789abcdef")
    k = ff.add_key(api.base, RO, "мой")
    assert k["readonly"] and k["accounts"] == [ACC]
    raw = Path(api.base, "api.json").read_text(encoding="utf-8")
    assert RO not in raw                                          # ключ на диске только зашифрованным
    assert ff.unprotect(json.loads(raw)["keys"][0]["enc"]) == RO
    assert ff.add_key(api.base, TRADE, allow_trading=True)["readonly"] is False


def test_request_only_while_previous_failed(api, monkeypatch):
    ff.add_key(api.base, RO)
    # 1-й запуск: справок нет — с 1 января по вчера кусками ≤92 дней, квота 3 кончается на 4-м куске → неудача
    st = ff.run_once(api.base)
    assert st[ACC]["result"] == "fail" and "квота" in st[ACC]["msg"] and "done_on" not in st[ACC]
    assert [o[1:3] for o in api.orders] == [("2026-01-01", "2026-04-02"), ("2026-04-03", "2026-07-03"),
                                           ("2026-07-04", "2026-10-03")]
    assert {o[3] for o in api.orders} == {"REPORT_FORM_LONG"} and len(_files(api)) == 3
    # 2-й запуск (квота восстановилась): прошлый неудачен — запрашивает остаток и ставит «за сегодня получено»
    api.quota = 3
    st = ff.run_once(api.base)
    assert st[ACC]["result"] == "ok" and st[ACC]["done_on"] == "2026-10-29"
    assert api.orders[-1][1:3] == ("2026-10-04", "2026-10-28") and len(api.orders) == 4
    # 3-й запуск в тот же день: прошлый удачен — к брокеру не обращается ВООБЩЕ
    api.calls.clear()
    ff.run_once(api.base)
    assert api.calls == []
    # на следующий день — одна справка за вчера
    monkeypatch.setenv("BR_NOW", "2026-10-30T07:30:00")
    st = ff.run_once(api.base)
    assert api.orders[-1][1:3] == ("2026-10-29", "2026-10-29") and len(api.orders) == 5 and st[ACC]["result"] == "ok"
    assert intake.covered_days(str(Path(api.base, "reports")), ACC) >= {ff.dt.date(2026, 1, 1), ff.dt.date(2026, 10, 29)}


def test_not_ready_report_is_taken_later_without_new_order(api, monkeypatch):
    ff.add_key(api.base, RO)
    rep = Path(api.base, "reports")
    intake.save(str(rep), spravka("2026-01-01", "2026-10-27"))     # история уже загружена вручную
    api.mode = "PROCESSING"
    st = ff.run_once(api.base)
    assert st[ACC]["result"] == "fail" and "собирается" in st[ACC]["msg"] and st[ACC]["pending"]["rid"] == "r1"
    assert [o[1:3] for o in api.orders] == [("2026-10-28", "2026-10-28")]
    api.mode = "SUCCESS"
    st = ff.run_once(api.base)                                     # дочитывает тот же заказ
    assert len(api.orders) == 1 and st[ACC]["result"] == "ok" and "pending" not in st[ACC]
    assert f"{ACC}_2026-10-28..2026-10-28.xls" in _files(api)


def test_broker_error_reorders_and_manual_history_needs_no_network(api):
    ff.add_key(api.base, RO)
    rep = str(Path(api.base, "reports"))
    intake.save(rep, spravka("2026-01-01", "2026-10-28"))           # справки по вчера уже есть
    api.calls.clear()
    st = ff.run_once(api.base)
    assert st[ACC]["result"] == "ok" and api.calls == []            # к брокеру не обращался
    os.remove(os.path.join(rep, ACC, f"{ACC}_2026-01-01..2026-10-28.xls"))
    intake.save(rep, spravka("2026-01-01", "2026-10-27"))
    st = ff.load_state(api.base)
    st[ACC].pop("done_on")
    ff._dump(ff._paths(api.base)["state"], st)
    api.mode = "ERROR"
    st = ff.run_once(api.base)
    assert st[ACC]["result"] == "fail" and "done_on" not in st[ACC]
    assert len(api.orders) >= 2                                     # ошибка брокера → заказ заново
    api.mode, api.quota = "SUCCESS", 3
    st = ff.run_once(api.base)
    assert st[ACC]["result"] == "ok"


def test_network_down_is_failure_and_retried(api, monkeypatch):
    ff.add_key(api.base, RO)
    monkeypatch.setattr(ff, "API", "http://127.0.0.1:9")            # никто не слушает
    st = ff.run_once(api.base)
    assert st[ACC]["result"] == "fail" and "нет связи" in st[ACC]["msg"] and "done_on" not in st[ACC]


def test_task_xml_is_valid():
    x = ff.task_xml(r"C:\Program Files\BrokerReport\BrokerReportFetch.exe", '--data "C:\\Users\\a b\\BrokerReport"')
    dom = xml.dom.minidom.parseString(x.replace('encoding="UTF-16"', 'encoding="UTF-8"').encode("utf-8"))
    trig = dom.getElementsByTagName("CalendarTrigger")
    assert len(trig) == 3 and dom.getElementsByTagName("StartWhenAvailable")[0].firstChild.data == "true"
    assert dom.getElementsByTagName("LogonType")[0].firstChild.data == "InteractiveToken"


@pytest.mark.skipif(os.name != "nt", reason="Планировщик Windows")
def test_schedule_on_off_real(tmp_path):
    name = "BrokerReport - проверка тестом"
    try:
        ff.schedule_on(str(tmp_path), task=name)
        s = ff.schedule_state(name)
        assert s["on"] and s["times"] == list(ff.TIMES) and s["cmd"]
    finally:
        ff.schedule_off(str(tmp_path), task=name)
    assert ff.schedule_state(name)["on"] is False
