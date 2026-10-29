"""Сквозная проверка BrokerReport: загрузка синтетических справок, страницы, Dash-колбэки. Реальных данных нет.

python tests/e2e_check.py http://127.0.0.1:18765
"""
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_brep_old_format import _report  # noqa: E402
from test_brep_securities import _report as _sec_report  # noqa: E402

U = sys.argv[1].rstrip("/")
s = requests.Session()
tok = re.search(r"name=t value=([\w-]+)", s.get(U + "/").text).group(1)

r1 = _report().encode("utf-8")
r2 = (_report().replace("КлФ-ИИС1", "КлФ-ДЕМО2")
      .replace("c 01.10.2021 по 31.12.2021", "с 01.10.2021 по 31.12.2021")).encode("utf-8")   # кириллическая «с»
inner = io.BytesIO()
with zipfile.ZipFile(inner, "w") as z:
    z.writestr("spravka.html", r2)
outer = io.BytesIO()
with zipfile.ZipFile(outer, "w") as z:
    z.writestr("doc1.zip", inner.getvalue())
    z.writestr("doc1.sga", b"signature")
files = [("files", ("q4-2021.xls", r1)), ("files", ("archive.zip", outer.getvalue())),
         ("files", ("notes.txt", b"hello")), ("files", ("bonds-2025.xls", _sec_report().encode("utf-8")))]
up = s.post(U + "/upload", data={"t": tok}, files=files).text
print("upload:", re.findall(r"<li class=(\w+)>(.*?)</li>", up))
assert s.post(U + "/upload", data={"t": "bad"}, files=files).status_code == 403
home = s.get(U + "/").text
accs = re.findall(r"href='/a/(\w+)/broker-report/'", home)
print("accounts:", accs)
assert accs[:2] == ["1", "2"] and len(accs) == 3, accs
sec_acc = accs[2]
assert "КлФ-ИИС1" in home and "КлФ-ДЕМО2" in home and "КлФ-ТЕСТ" in home

for a in accs:
    for p in ("broker-report", "broker-charts"):
        b = f"{U}/a/{a}/{p}/"
        assert s.get(b).status_code == 200, b
        deps = s.get(b + "_dash-dependencies").json()
        lay = s.get(b + "_dash-layout").json()
        print(a, p, "callbacks:", len(deps), "layout ok:", bool(lay))
    # колбэк шапки отчёта: заголовок, период, сверка
    b = f"{U}/a/{a}/broker-report/"
    dep = next(d for d in s.get(b + "_dash-dependencies").json() if "title.children" in d["output"])
    body = {"output": dep["output"], "outputs": [{"id": o.split(".")[0], "property": o.split(".")[1]}
                                                for o in dep["output"].strip(".").split("...")],
            "inputs": [{"id": "title", "property": "id", "value": "title"}], "changedPropIds": []}
    out = s.post(b + "_dash-update-component", json=body).json()["response"]
    print(a, "head:", json.dumps({k: v.get("children") for k, v in out.items() if k in ("title", "meta")},
                                ensure_ascii=False), "| checks:", str(out["checks"]["children"])[:120])
# блок «Ценные бумаги» (счёт с облигациями): строки по ISIN, количество сходится, займ ЦБ — не продажа
b = f"{U}/a/{sec_acc}/broker-report/"
dep = next(d for d in s.get(b + "_dash-dependencies").json() if "g-sec.rowData" in d["output"])
body = {"output": dep["output"], "outputs": [{"id": o.split(".")[0], "property": o.split(".")[1]}
                                            for o in dep["output"].strip(".").split("...")],
        "inputs": [{"id": "d-from", "property": "value", "value": None}, {"id": "d-to", "property": "value", "value": None}],
        "changedPropIds": []}
sec = s.post(b + "_dash-update-component", json=body).json()["response"]
rows = sec["g-sec"]["rowData"]
print("3 securities:", [(r["name"], r.get("q1"), r.get("chk"), round(r.get("result") or 0)) for r in rows])
assert len(rows) == 2 and all(r.get("chk") == "✓" for r in rows) and sec["sec-wrap"]["style"] == {}, rows
assert s.post(U + "/delete/2", data={"t": tok}).status_code in (200, 302)
assert s.post(U + f"/delete/{sec_acc}", data={"t": tok}).status_code in (200, 302)
assert re.findall(r"href='/a/(\w+)/broker-report/'", s.get(U + "/").text) == ["1"]
assert s.get(U + "/a/2/broker-report/", allow_redirects=False).status_code == 302

# ── автозагрузка через API (только если окно запущено с BR_FINAM_API на подставной сервер tests/fake_finam.py) ──
if len(sys.argv) > 2 and sys.argv[2] == "--api":
    import datetime as dt
    import time
    from fake_finam import ACC, RO, TRADE, spravka
    yday = dt.date.today() - dt.timedelta(days=1)
    hist = spravka(f"{yday.year}-01-01", str(yday - dt.timedelta(days=1)))   # история «из кабинета» — по позавчера
    s.post(U + "/upload", data={"t": tok}, files=[("files", ("hist.xls", hist))])
    page = s.post(U + "/api/key", data={"t": tok, "secret": TRADE}).text
    assert "Ключ не подключён" in page and "ТОРГОВЫЙ" in page, "торговый ключ должен отклоняться"
    page = s.post(U + "/api/key", data={"t": tok, "secret": RO, "label": "проверка"}).text
    assert "Ключ подключён" in page and RO not in page, page[:300]
    for _ in range(60):
        if not s.get(U + "/api/status").json()["running"]:
            break
        time.sleep(0.5)
    home = s.get(U + "/").text
    assert "получено" in home and "API ✓" in home, "автозагрузка не отметилась"
    files = re.findall(r"справок: (\d+)", home)
    print("api: ключ подключён, справка за вчера получена; справок у счёта:", files)
    page = s.post(U + "/api/schedule", data={"t": tok, "on": "1"}).text
    assert "Расписание включено" in page, page[:300]
    assert "по расписанию" in s.get(U + "/").text
    page = s.post(U + "/api/schedule", data={"t": tok, "on": "0"}).text
    assert "Расписание выключено" in page
    kid = re.search(r"/api/key/(\w+)/delete", s.get(U + "/").text).group(1)
    s.post(U + f"/api/key/{kid}/delete", data={"t": tok})
    s.post(U + f"/delete/{ACC}", data={"t": tok})
    assert "не подключено" in s.get(U + "/").text
    print("api: расписание включено и выключено, ключ и счёт удалены")
print("E2E OK")
