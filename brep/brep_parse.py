"""Разбор «Справки о состоянии обязательств» ФИНАМ (HTML, .xls) — собственный разбор.

Из справки берём:
  * итоги брокера: стоимость счёта, рублёвая позиция, ГО, требования/обязательства на начало и конец;
  * «Виды движений денежных средств» — итоги брокера по видам (контрольные суммы для сверки);
  * датированные денежные строки: неторговые операции, «Торговые движения ПФИ» (ВМ, биржевой сбор,
    комиссия брокера по дням, изменения ГО), сделки с бумагами и комиссии по ним;
  * сделки ПФИ (номер, дата, контракт, сторона, лоты) — для оборота.
Деньги учитываются только рублёвые и только оплаченные (у сделки есть «Дата оплаты факт»).
"""
from __future__ import annotations

import datetime as dt
import re

from .htables import tables

DATE = re.compile(r"^(\d\d)\.(\d\d)\.(\d{4})$")
ISO = re.compile(r"^\d{4}-\d\d-\d\d$")


def num(s: str) -> float:
    s = (s or "").replace("\u00a0", "").replace(" ", "").replace(",", ".").replace("\u2212", "-")
    try:
        return float(s)
    except ValueError:
        return 0.0


def iso(s: str) -> str | None:
    s = (s or "").strip()
    if ISO.match(s):
        return s
    m = DATE.match(s)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _find(tabs, *keys):
    for h, rows in tabs:
        if all(k in h for k in keys):
            return rows
    return []


KIND_IN, KIND_OUT, KIND_TR = "Ввод", "Вывод", "Перевод между счетами"
KIND_VM, KIND_EXCH, KIND_BROKER = "Вариационная маржа", "Биржевой сбор", "Комиссия брокера"
KIND_DEPO, KIND_TAX = "Депозитарий", "Налог (НДФЛ)"
KIND_COUPON, KIND_INTEREST = "Доход: купоны, дивиденды", "Доход: проценты"
KIND_SEC_BUY, KIND_SEC_SELL, KIND_REDEEM, KIND_OTHER = "Бумаги: покупка", "Бумаги: продажа", "Бумаги: погашение", "Прочее"
KIND_FX_BUY, KIND_FX_SELL = "Валюта: покупка", "Валюта: продажа"


def _nontrade_kind(typ: str, comment: str, amount: float) -> str:
    t, c = typ.lower(), comment.lower()
    if "перевод" in t:
        return KIND_TR
    if "ввод дс" in t:
        return KIND_IN
    if "вывод дс" in t:
        return KIND_OUT
    if "ндфл" in t or "налог" in t:
        return KIND_TAX
    if "депозитар" in t:
        return KIND_DEPO
    if "комиссия брокер" in t:
        return KIND_BROKER
    if "услуги сторонних" in t and re.search(r"сбор|клиринг", c):
        return KIND_EXCH              # «Коррекция сбора / комиссии за клиринг» (справки 2022) — брокер: биржевой сбор
    if "погашен" in t or "погашен" in c:
        return KIND_REDEEM
    if "нкд" in t or "купон" in t or "дивиденд" in t or re.search(r"купонн|дивиденд", c):
        return KIND_COUPON
    if re.search(r"процент", c) or re.search(r"вознаграждени\w* по пункту 14\.4", c):
        return KIND_INTEREST          # п. 14.4 Регламента — проценты брокера на остаток денег (брокер: «иное»)
    if "зачисление дс" in t:
        return KIND_IN
    return KIND_OTHER


def parse_report(text: str, name: str = "") -> dict:
    tabs = tables(text)
    if not tabs:
        raise ValueError("нет таблиц")
    head = " ".join(h for h, _ in tabs[:2])
    m = re.search(r"за период [cс] (\d\d\.\d\d\.\d{4}) по (\d\d\.\d\d\.\d{4})", head)
    if not m:
        raise ValueError("не справка: нет периода")
    r = {"file": name, "from": iso(m.group(1)), "to": iso(m.group(2)), "account": "",
         "items": [], "trades": {}, "go_changes": [], "unknown": {}}
    for row in tabs[0][1]:
        if row and row[0].startswith("По счету"):
            r["account"] = row[1]
    # 1. Оценка состояния счёта
    st = {}
    for row in _find(tabs, "1. Оценка состояния"):
        if len(row) >= 3:
            st[row[0]] = (num(row[1]), num(row[2]))
    pick = lambda pref: next((v for k, v in st.items() if k.startswith(pref)), (0.0, 0.0))
    r["equity"] = pick("ИТОГО оценка")
    r["rub"] = pick("Оценка рублевой")
    r["go"] = pick("Оценка позиции по ПФИ")
    r["sec"] = pick("Оценка позиции по ценным")
    r["req"] = pick("Оценка требований")
    if "ИТОГО оценка" not in " ".join(st):
        raise ValueError("не справка: нет итога оценки счёта")
    # Виды движений: итоги брокера (рубль)
    f = {}
    for row in _find(tabs, "Виды движений"):
        if row and row[0] == "Рубль" and len(row) in (18, 20):
            v = [num(x) for x in row]
            n = 4 if len(row) == 20 else 3      # 18 ячеек — старая раскладка (до 2022): без колонок драгметаллов
            if n == 3:
                v = v[:6] + [0.0] + v[6:9] + [0.0] + v[9:]
            f = {"dep": v[1], "wd": v[2], "sec_in": sum(v[3:7]), "sec_out": sum(v[7:11]), "vm": v[11],
                 "go_chg": v[12], "exch": v[13], "income": v[14], "broker": v[15], "advisor": v[16],
                 "tax": v[17] + v[18], "tax_paid": v[17], "tax_withheld": v[18], "other": v[19]}
    r["flows"] = f
    items = r["items"]

    def add(date, kind, amount, instr="", comment="", src=""):
        if date and abs(amount) > 1e-9:
            items.append({"date": date, "kind": kind, "instr": instr, "amount": round(amount, 2),
                          "comment": comment, "src": src})

    # Неторговые операции с деньгами
    for row in _find(tabs, "Неторговые операции с денежными"):
        if len(row) < 8 or not DATE.match(row[0]) or "(итого)" in row[1]:
            continue
        if row[3] and row[3] != "Рубль":
            r["unknown"]["валюта " + row[3]] = r["unknown"].get("валюта " + row[3], 0) + 1
            continue
        amount = num(row[4]) - num(row[5])
        comment = row[-1]
        kind = _nontrade_kind(row[1], comment, amount)
        wt = re.search(r"удержан налог\s*([\d\s.,]+)", comment, re.I)
        if kind == KIND_COUPON and wt and num(wt.group(1)):
            add(iso(row[0]), kind, amount + num(wt.group(1)), row[2], comment, "неторговые")
            add(iso(row[0]), KIND_TAX, -num(wt.group(1)), row[2], "удержан при выплате дохода", "неторговые")
            continue
        add(iso(row[0]), kind, amount, row[2], comment or row[1], "неторговые")
    # Торговые движения ПФИ
    vm_agg, ex_agg = {}, {}
    for row in _find(tabs, "Торговые движения ПФИ"):
        if len(row) not in (18, 25) or not DATE.match(row[0]):
            continue
        new = len(row) == 25             # формат с 16.10.2026: колонки задолженностей и дат оплаты
        c_go, c_paid, c_no, c_cm = (13, 17, 23, 24) if new else (11, 13, 16, 17)
        op = row[4]
        if op in ("Покупка", "Продажа"):
            no = row[c_no] or f"{row[0]}{row[1]}{row[3]}{row[5]}{row[6]}"
            paid = iso(row[c_paid])
            r["trades"][no] = {"date": iso(row[0]), "time": row[1], "code": row[3], "side": op,
                               "qty": num(row[5]), "price": num(row[6]), "paid": paid}
            if paid:
                vm_agg[(paid, row[3])] = vm_agg.get((paid, row[3]), 0) + num(row[10])
                ex_agg[(paid, row[3])] = ex_agg.get((paid, row[3]), 0) + num(row[9])
        elif op == "Вариационная маржа":
            d = (iso(row[17]) or iso(row[16]) if new else None) or iso(row[0])
            vm_agg[(d, row[3])] = vm_agg.get((d, row[3]), 0) + num(row[10])
        elif op == "Комиссия брокерская":
            add(iso(row[0]), KIND_BROKER, -num(row[9]), "", row[c_cm], "ПФИ")
        elif op == "Гарантийное обеспечение":
            r["go_changes"].append((iso(row[0]), num(row[c_go])))
        elif op == "Комиссия биржевая":             # клиринг при исполнении контракта (экспирация) — биржевой сбор
            add(iso(row[0]), KIND_EXCH, -num(row[9]), "", row[c_cm], "ПФИ")
        elif op == "Списание" and "огашение контракта" in row[c_cm]:
            pass                                    # исполнение контракта на экспирации: денег в строке нет (ВМ 0)
        else:
            r["unknown"]["ПФИ: " + op] = r["unknown"].get("ПФИ: " + op, 0) + 1
    for (d, code), v in sorted(vm_agg.items()):
        add(d, KIND_VM, v, code, "", "ПФИ")
    for (d, code), v in sorted(ex_agg.items()):
        add(d, KIND_EXCH, -v, code, "", "ПФИ")
    # Сделки с бумагами и комиссии по ним
    for row in _find(tabs, "в качестве ценных бумаг (включая незавершенные"):
        if len(row) < 20 or not DATE.match(row[0]):
            continue
        op = row[4]
        if op in ("Покупка", "Продажа"):
            paid = next((iso(row[i]) for i in (19, 18) if iso(row[i])), None)
            add(paid, KIND_SEC_BUY if op == "Покупка" else KIND_SEC_SELL, num(row[13]),
                row[2] or row[3], f"{num(row[5]):g} шт. по {row[6]}", "бумаги")
        elif op == "Комиссия брокерская":
            add(iso(row[0]), KIND_BROKER, -num(row[14]), "", row[-1], "бумаги")
        else:
            r["unknown"]["бумаги: " + op] = r["unknown"].get("бумаги: " + op, 0) + 1
    # Сделки с валютой (справки 2022: USDRUB/EURRUB): объём — деньги на дату исполнения; 50 ₽ за сделку брокер относит
    # к биржевому сбору, строки «Вознаграждение Компании» — к вознаграждению брокера (сверено с «Видами движений»)
    for row in _find(tabs, "Торговые движения валют"):
        if len(row) != 16 or not DATE.match(row[0]):
            continue
        op, paid = row[3], iso(row[11]) or iso(row[10]) or iso(row[0])
        if row[9] and row[9] != "Рубль":
            r["unknown"]["валюта: комиссия в " + row[9]] = r["unknown"].get("валюта: комиссия в " + row[9], 0) + 1
            continue
        if op in ("Покупка", "Продажа"):
            add(paid, KIND_FX_BUY if op == "Покупка" else KIND_FX_SELL,
                -num(row[7]) if op == "Покупка" else num(row[7]), row[2], f"{row[4]} по {row[5]}", "валюта")
            add(paid, KIND_EXCH, -num(row[8]), row[2], "комиссия сделки с валютой", "валюта")
        elif op.startswith("Вознаграждение"):
            add(paid, KIND_BROKER, -num(row[8]), "", row[-1] or op, "валюта")
        else:
            r["unknown"]["валюта: " + op] = r["unknown"].get("валюта: " + op, 0) + 1
    # налог, удержанный при выплате дохода, которого нет в датированных строках, — итогом справки на её последний день
    rest = f.get("tax_withheld", 0.0) - sum(i["amount"] for i in items if i["kind"] == KIND_TAX
                                            and i["comment"] == "удержан при выплате дохода")
    if abs(rest) > 0.005:
        add(r["to"], KIND_TAX, rest, "", "удержан при выплате дохода; в справке только итогом за период, без даты", "итог справки")
    return r


# какие виды строк соответствуют колонкам итогов брокера
FLOW_MAP = {"dep": [KIND_IN], "wd": [KIND_OUT], "vm": [KIND_VM], "exch": [KIND_EXCH],
            "broker": [KIND_BROKER, KIND_DEPO], "tax": [KIND_TAX], "income": [KIND_COUPON],
            "other": [KIND_INTEREST, KIND_REDEEM, KIND_OTHER], "sec_in": [KIND_SEC_SELL, KIND_FX_SELL],
            "sec_out": [KIND_SEC_BUY, KIND_FX_BUY]}


def reconcile(r: dict) -> dict:
    """Сумма датированных строк по видам против итогов брокера. Ключ → (наше, брокер, разница)."""
    out = {}
    for k, kinds in FLOW_MAP.items():
        ours = sum(i["amount"] for i in r["items"] if i["kind"] in kinds)
        if k in ("dep", "wd"):        # «Перевод ДС» между своими счетами брокер в итогах считает вводом / выводом
            ours += sum(i["amount"] for i in r["items"] if i["kind"] == KIND_TR and (i["amount"] > 0) == (k == "dep"))
        theirs = r["flows"].get(k, 0.0)
        if k == "wd":
            theirs = -abs(theirs)
        out[k] = (round(ours, 2), round(theirs, 2), round(ours - theirs, 2))
    return out


def day_after(d: str) -> str:
    return (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()


def day_before(d: str) -> str:
    return (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()
