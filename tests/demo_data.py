"""Вымышленный демо-счёт «КлФ-ДЕМО1» для скриншотов README: годовые справки со сделками по фьючерсам Si и CR,
ежедневной вариационной маржой, сборами, комиссией брокера, вводом и выводом. Реальных данных нет.

python tests/demo_data.py <папка>   — записать справки demo-<год>.xls
"""
import datetime as dt
import random
import sys
from pathlib import Path

ACC = "КлФ-ДЕМО1"


def _ru(x: float) -> str:
    s = f"{abs(x):,.2f}".replace(",", " ").replace(".", ",")
    return ("-" if x < 0 else "") + s


def _t(title: str, rows: list) -> str:
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<p>{title}</p><table>{body}</table>"


def _days(d0: dt.date, d1: dt.date):
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def reports(today: dt.date | None = None, seed: int = 7) -> list[tuple[str, str]]:
    """[(имя файла, html)] — справки по календарным годам с 2024 по вчера."""
    rnd = random.Random(seed)
    end = (today or dt.date.today()) - dt.timedelta(days=1)
    eq, out, no = 0.0, [], 1000
    for year in range(2024, end.year + 1):
        d0, d1 = dt.date(year, 1, 1), min(dt.date(year, 12, 31), end)
        eq0 = eq
        pfi, nontrade = [], []
        tot = dict(dep=0.0, wd=0.0, vm=0.0, exch=0.0, broker=0.0)
        if year == 2024:
            nontrade.append([d0.replace(day=9).strftime("%d.%m.%Y"), "Ввод ДС", "", "Рубль", _ru(1_500_000), "",
                             _ru(1_500_000), ""])
            tot["dep"] += 1_500_000
        if year == 2025:
            nontrade.append(["15.04.2025", "Ввод ДС", "", "Рубль", _ru(500_000), "", _ru(500_000), ""])
            tot["dep"] += 500_000
            nontrade.append(["10.12.2025", "Вывод ДС", "", "Рубль", "", _ru(300_000), _ru(-300_000), ""])
            tot["wd"] += 300_000
        for d in _days(d0, d1):
            ds = d.strftime("%d.%m.%Y")
            if d < dt.date(2024, 1, 10):
                continue
            # ВМ дня по двум контрактам: случайное блуждание с небольшим положительным сносом
            for code, scale, drift in (("SiZ6", 9000, 420), ("CRZ6", 4000, 120)):
                vm = round(rnd.gauss(drift, scale), 2)
                pfi.append([ds, "", "Фьючерсы, Расчетный", code, "Вариационная маржа", "", "", "", "", "",
                            _ru(vm), "", "Рубль", ds, "", "", "", ""])
                tot["vm"] += vm
            # сделки: 0–3 в день, сбор биржи со сделки
            fees = 0.0
            for _ in range(rnd.choice((0, 1, 1, 2, 3))):
                code = rnd.choice(("SiZ6", "SiZ6", "CRZ6"))
                qty = rnd.choice((1, 2, 3, 5))
                price = 90_000 + rnd.randint(-5000, 5000) if code.startswith("Si") else 12_000 + rnd.randint(-800, 800)
                fee = round(qty * (2.03 if code.startswith("Si") else 1.14), 2)
                no += 1
                pfi.append([ds, f"{rnd.randint(10, 23)}:{rnd.randint(0, 59):02d}:00", "Фьючерсы, Расчетный", code,
                            rnd.choice(("Покупка", "Продажа")), str(qty), _ru(price), "Пункты", "", _ru(fee), "0,00",
                            "", "Рубль", ds, "СР", "", str(no), ""])
                tot["exch"] -= fee
                fees += fee
            if fees:
                br = round(fees * 0.45, 2)
                pfi.append([ds, "", "", "", "Комиссия брокерская", "", "", "", "", _ru(br), "", "", "Рубль", "", "", "",
                            "", "Комиссия брокера по сделкам ПФИ"])
                tot["broker"] -= br
        tot = {k: round(v, 2) for k, v in tot.items()}
        eq = round(eq0 + tot["dep"] - tot["wd"] + tot["vm"] + tot["exch"] + tot["broker"], 2)
        flows = ["Рубль", _ru(tot["dep"]), _ru(tot["wd"]), "0,00", "0,00", "0,00", "0,00", "0,00", "0,00",
                 _ru(tot["vm"]), "0,00", _ru(tot["exch"]), "0,00", _ru(tot["broker"]), "0,00", "0,00", "0,00", "0,00"]
        html = ("<html>" + _t(f"Справка о состоянии обязательств за период c {d0:%d.%m.%Y} по {d1:%d.%m.%Y}",
                              [["По счету:", ACC]])
                + _t("1. Оценка состояния счета Клиента", [["ИТОГО оценка", _ru(eq0), _ru(eq)]])
                + _t("Виды движений денежных средств", [flows])
                + _t("2. Движения по счету Клиента Неторговые операции с денежными средствами",
                     [["Дата", "Тип операции", "Инструмент", "Валюта", "Зачислено", "Списано", "Оценка", "Комментарии"]]
                     + nontrade)
                + _t("Торговые движения ПФИ, в т.ч. Комиссии", pfi) + "</html>")
        out.append((f"demo-{year}.xls", html))
    return out


if __name__ == "__main__":
    p = Path(sys.argv[1])
    p.mkdir(parents=True, exist_ok=True)
    for name, html in reports():
        (p / name).write_text(html, encoding="utf-8")
        print(p / name)
