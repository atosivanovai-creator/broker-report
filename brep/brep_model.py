"""Модель отчёта брокера по одному счёту (окно «Отчёт брокера»).

Справки → сжатые итоги на справку (кэш JSON по mtime/размеру) → сводка за интервал, годы, дни, инструменты.
В памяти окна держим только итоги по дням (не строки и не сделки): справка за период с частой торговлей — до 100 МБ.

Правила учёта (проверяемые):
  * деньги — только оплаченные рублёвые датированные строки справок; по каждой справке их сумма по видам сверяется
    с итогами брокера «Виды движений денежных средств» (recon);
  * стоимость счёта точна только на границах справок; внутри — оценка: последняя точная + деньги дня (ВМ, комиссии,
    налоги, доходы, ввод/вывод), без покупок/продаж/погашений бумаг;
  * прибыль за интервал = Δ стоимости − (ввод − вывод ± переводы) между ближайшими точными точками;
    ВМ = оплаченная + Δ «Оценки требований и обязательств» (начислено, но не оплачено);
  * ГО по дням = ГО начала справки + изменения ГО по дням; сверяется с ГО конца справки;
  * лоты/сделки: оплаченные сделки — из справки дня оплаты (по дате сделки), неоплаченные — только из последней справки.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import threading
from collections import defaultdict

from . import brep_parse as bp

CACHE_V = 2          # 2: lots_late — сделки, оплаченные позже даты сделки (склейка справок)
INCOME = (bp.KIND_COUPON, bp.KIND_INTEREST)
FLOWS = (bp.KIND_IN, bp.KIND_OUT, bp.KIND_TR)


def compact(r: dict) -> dict:
    """Разобранная справка → итоги по дням (то, что нужно отчёту)."""
    day_kind = defaultdict(lambda: defaultdict(float))
    day_ins = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))       # дата → код → [ВМ, сбор]
    broker_pfi = defaultdict(float)
    for i in r["items"]:
        day_kind[i["date"]][i["kind"]] += i["amount"]
        if i["kind"] == bp.KIND_VM and i["instr"]:
            day_ins[i["date"]][i["instr"]][0] += i["amount"]
        elif i["kind"] == bp.KIND_EXCH and i["instr"]:
            day_ins[i["date"]][i["instr"]][1] += i["amount"]
        elif i["kind"] == bp.KIND_BROKER and i["src"] == "ПФИ":
            broker_pfi[i["date"]] += i["amount"]
    lots_paid = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))       # дата сделки → код → [лоты, сделки]
    lots_open = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    lots_late = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0.0, 0])))   # оплата → дата сделки → код
    for t in r["trades"].values():
        if not t["date"]:
            continue
        g = (lots_paid if t["paid"] else lots_open)[t["date"]][t["code"]]
        g[0] += t["qty"]
        g[1] += 1
        if t["paid"] and t["paid"] > t["date"]:
            x = lots_late[t["paid"]][t["date"]][t["code"]]
            x[0] += t["qty"]
            x[1] += 1
    go = defaultdict(float)
    for d, v in r["go_changes"]:
        go[d] += v
    rnd = lambda x: round(x, 2)  # noqa: E731
    return {
        "file": r["file"], "from": r["from"], "to": r["to"], "account": r["account"],
        "equity": r["equity"], "go": r["go"], "req": r["req"], "recon": r["recon"],
        "day_kind": {d: {k: rnd(v) for k, v in kv.items()} for d, kv in day_kind.items()},
        "day_ins": {d: {c: [rnd(a), rnd(b)] for c, (a, b) in cv.items()} for d, cv in day_ins.items()},
        "broker_pfi": {d: rnd(v) for d, v in broker_pfi.items()},
        "lots_paid": {d: {c: v for c, v in cv.items()} for d, cv in lots_paid.items()},
        "lots_open": {d: {c: v for c, v in cv.items()} for d, cv in lots_open.items()},
        "lots_late": {p: {d: {c: v for c, v in cv.items()} for d, cv in dv.items()} for p, dv in lots_late.items()},
        "go_ch": {d: rnd(v) for d, v in go.items()},
    }


def _load(path: str, cache_dir: str) -> dict:
    st = os.stat(path)
    key = os.path.join(cache_dir, os.path.basename(path) + ".json")
    sig = [CACHE_V, int(st.st_mtime), st.st_size]
    try:
        with open(key, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("sig") == sig:
            return c["r"]
    except (OSError, ValueError):
        pass
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        r = bp.parse_report(f.read(), os.path.basename(path))
    r["recon"] = bp.reconcile(r)
    c = compact(r)
    os.makedirs(cache_dir, mode=0o700, exist_ok=True)
    tmp = f"{key}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"sig": sig, "r": c}, f, ensure_ascii=False)
    os.replace(tmp, key)
    return c


def _days(a: str, b: str) -> list[str]:
    d, e, out = dt.date.fromisoformat(a), dt.date.fromisoformat(b), []
    while d <= e:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def ru(d) -> str:
    s = d if isinstance(d, str) else d.isoformat()
    return f"{s[8:10]}.{s[5:7]}.{s[:4]}"


def years_between(d0: str, d1: str) -> float:
    """Длина интервала (d0, d1] в годах по календарю: полный календарный год = 1, остаток — доля своего года."""
    a, b = dt.date.fromisoformat(d0), dt.date.fromisoformat(d1)

    def shift(n: int) -> dt.date:
        try:
            return a.replace(year=a.year + n)
        except ValueError:                       # 29.02
            return a.replace(year=a.year + n, day=28)
    n = 0
    while shift(n + 1) <= b:
        n += 1
    lo, hi = shift(n), shift(n + 1)
    return n + (b - lo).days / (hi - lo).days


def dir_signature(data_dir: str, acc: str) -> tuple:
    out = []
    for p in sorted(glob.glob(os.path.join(data_dir, acc, f"{acc}_*.xls"))):
        try:
            st = os.stat(p)
            out.append((os.path.basename(p), int(st.st_mtime), st.st_size))
        except OSError:
            pass
    return tuple(out)


OVERLAP_MAX_DAYS = 3


def join_overlap(a: dict, b: dict) -> dict | None:
    """Справка b заходит на a на 1–3 дня (кабинет даёт период «по 31.12», автоматика — «с 31.12»): если деньги дней
    пересечения в обеих одинаковы и стоимость на стыке сходится, дни пересечения берутся из a, b продолжается со дня после
    a. Иначе None — прежнее правило «вторая не учтена». Сверка с брокером b — по самой справке, как прежде."""
    if b["to"] <= a["to"]:
        return None
    over = _days(b["from"], a["to"])
    if not over or len(over) > OVERLAP_MAX_DAYS:
        return None
    for d in over:
        if b["day_kind"].get(d, {}) != a["day_kind"].get(d, {}) or b["broker_pfi"].get(d, 0) != a["broker_pfi"].get(d, 0):
            return None
    money = sum(v for d in over for v in b["day_kind"].get(d, {}).values())
    if abs(b["equity"][0] + money - a["equity"][1]) > 1.0:
        return None
    cut = lambda m: {d: v for d, v in m.items() if d > a["to"]}  # noqa: E731
    # сделки второй справки, оплаченные ПОСЛЕ стыка, в первой отсутствуют (кабинет «по 31.12» не показывает сделку
    # вечера 30.12 с оплатой 05.01) — их лоты остаются, хотя дата сделки до стыка; оплаченные в дни стыка есть в первой
    lots = {d: {c: list(v) for c, v in cv.items()} for d, cv in cut(b["lots_paid"]).items()}
    for p, dv in b.get("lots_late", {}).items():
        if p > a["to"]:
            for d, cv in dv.items():
                if d <= a["to"]:
                    for c, (q, k) in cv.items():
                        x = lots.setdefault(d, {}).setdefault(c, [0.0, 0])
                        x[0] += q
                        x[1] += k
    return dict(b, **{"from": (dt.date.fromisoformat(a["to"]) + dt.timedelta(days=1)).isoformat(),
                      "equity": (a["equity"][1], b["equity"][1]), "go": (a["go"][1], b["go"][1]),
                      "req": (a["req"][1], b["req"][1]), "day_kind": cut(b["day_kind"]), "day_ins": cut(b["day_ins"]),
                      "broker_pfi": cut(b["broker_pfi"]), "lots_paid": lots,
                      "lots_open": cut(b["lots_open"]), "go_ch": cut(b["go_ch"]), "joined": len(over)})


class Account:
    def __init__(self, acc: str, data_dir: str, cache_dir: str):
        self.acc = acc
        self.sig = dir_signature(data_dir, acc)
        reps = [_load(os.path.join(data_dir, acc, n), os.path.join(cache_dir, acc)) for n, _, _ in self.sig]
        reps.sort(key=lambda r: (r["from"], r["to"]))
        self.notes: list[str] = []
        kept = []
        for r in reps:
            if kept and r["from"] <= kept[-1]["to"]:
                j = join_overlap(kept[-1], r)
                if j is None:
                    self.notes.append(f"справки пересекаются: {kept[-1]['file']} и {r['file']} — вторая не учтена")
                    continue
                r = j
            kept.append(r)
        self.reports = kept
        self.account_no = next((r["account"] for r in kept if r["account"]), acc)
        self.recon_bad = [(r["file"], {k: v for k, v in r["recon"].items() if abs(v[2]) > 0.01})
                          for r in kept if any(abs(v[2]) > 0.01 for v in r["recon"].values())]
        for a, b in zip(kept, kept[1:]):
            miss = [d for d in _days(a["to"], b["from"])[1:-1] if dt.date.fromisoformat(d).weekday() < 5]
            if miss:
                self.notes.append(f"нет справок за {len(miss)} будн. дн.: {ru(miss[0])}–{ru(miss[-1])}")
        pts, req = {}, {}
        for r in kept:
            pts.setdefault(bp.day_before(r["from"]), r["equity"][0])
            req.setdefault(bp.day_before(r["from"]), r["req"][0])
            pts[r["to"]], req[r["to"]] = r["equity"][1], r["req"][1]
        self.points, self.req = sorted(pts.items()), req
        # итоги по дням по всем справкам
        self.kind = defaultdict(lambda: defaultdict(float))
        self.ins = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        self.broker_pfi = defaultdict(float)
        self.lots = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
        for n, r in enumerate(kept):
            for d, kv in r["day_kind"].items():
                for k, v in kv.items():
                    self.kind[d][k] += v
            for d, cv in r["day_ins"].items():
                for c, (vm, ex) in cv.items():
                    self.ins[d][c][0] += vm
                    self.ins[d][c][1] += ex
            for d, v in r["broker_pfi"].items():
                self.broker_pfi[d] += v
            srcs = [r["lots_paid"]] + ([r["lots_open"]] if n == len(kept) - 1 else [])
            for src in srcs:
                for d, cv in src.items():
                    for c, (q, k) in cv.items():
                        self.lots[d][c][0] += q
                        self.lots[d][c][1] += k
        self.first = kept[0]["from"] if kept else None
        self.last = kept[-1]["to"] if kept else None
        self._daily()

    # ── по дням ───────────────────────────────────────────────────────────────
    def _daily(self):
        rows, self.go_check, self.eq_check = [], [], []
        for r in self.reports:
            eq, go = r["equity"][0], r["go"][0]
            for d in _days(r["from"], r["to"]):
                k = self.kind.get(d, {})
                lt = self.lots.get(d, {})
                if not k and not lt and d not in r["go_ch"] and d != r["to"]:
                    continue
                vm = k.get(bp.KIND_VM, 0.0)
                broker = k.get(bp.KIND_BROKER, 0.0) + k.get(bp.KIND_DEPO, 0.0)
                exch = k.get(bp.KIND_EXCH, 0.0)
                inc = sum(k.get(x, 0.0) for x in INCOME)
                tax = k.get(bp.KIND_TAX, 0.0)
                other = k.get(bp.KIND_OTHER, 0.0)
                flow = sum(k.get(x, 0.0) for x in FLOWS)
                result = vm + broker + exch + inc + other
                eq += result + tax + flow
                go += r["go_ch"].get(d, 0.0)
                exact = d == r["to"]
                if exact:
                    self.go_check.append((r["file"], round(go - r["go"][1], 2)))
                    self.eq_check.append((r["file"], round(eq - r["equity"][1], 2)))
                    eq, go = r["equity"][1], r["go"][1]
                go_v = round(go, 2) if abs(go) > 0.5 else 0.0
                rows.append({"date": d, "equity": round(eq, 2), "exact": exact, "result": round(result, 2),
                             "vm": round(vm, 2), "broker": round(broker, 2), "exch": round(exch, 2),
                             "income": round(inc, 2), "tax": round(tax, 2), "other": round(other, 2),
                             "dep": round(k.get(bp.KIND_IN, 0.0), 2), "wd": round(k.get(bp.KIND_OUT, 0.0), 2),
                             "tr": round(k.get(bp.KIND_TR, 0.0), 2),
                             "lots": sum(v[0] for v in lt.values()), "deals": sum(v[1] for v in lt.values()),
                             "go": go_v, "go_pct": round(go_v / eq * 100, 1) if eq > 0 else None})
        self.days = rows
        self.go_bad = [g for g in self.go_check if abs(g[1]) > 1]

    # ── сводка за интервал ────────────────────────────────────────────────────
    def point_at(self, d: str):
        best = None
        for p in self.points:
            if p[0] <= d:
                best = p
        return best or (self.points[0] if self.points else None)

    def _year_factor(self, d0: str, d1: str) -> float | None:
        """Множитель (1 + доходность) отрезка (d0, d1] внутри одного календарного года: год
        режется по дням ввода/вывода/переводов; доходность куска = стоимость в день движения без самого движения /
        стоимость после прошлого движения; куски перемножаются. Концы — точные стоимости справок; стоимость в день движения
        внутри справки за период — оценка (без переоценки бумаг). Без движений = конец / начало."""
        p0, p1 = self.point_at(d0), self.point_at(d1)
        if p1[0] <= p0[0]:
            return 1.0
        eq_by_day = {r["date"]: r["equity"] for r in self.days}
        days_sorted = sorted(eq_by_day)
        k, prev = 1.0, p0[1]
        for d in sorted(d for d in self.kind if p0[0] < d <= p1[0]):
            f = sum(self.kind[d].get(x, 0.0) for x in FLOWS)
            if not f:
                continue
            if d in eq_by_day:
                after = eq_by_day[d]
            else:                                   # дня нет в строках по дням — последняя оценка до него + движение
                before_days = [x for x in days_sorted if x < d]
                after = (eq_by_day[before_days[-1]] if before_days else prev) + f
            before = after - f
            if prev <= 0 or before <= 0:
                return None
            k *= before / prev
            prev = after
        return k * p1[1] / prev if prev > 0 else None

    def index_series(self, a: str, b: str, with_base: bool = False):
        """Индекс доходности по дням [a..b], 100 = день перед a:
        день = (стоимость − ввод/вывод/переводы дня) / стоимость прошлого дня — тот же способ «б», что % года сводки
        (произведение за год = множитель года). Цепочка — от точной точки перед a, база — последний день перед a.
        → [(дата, индекс, движение дня)]; with_base → ((дата базы, стоимость базы), список)."""
        p0 = self.point_at(bp.day_before(a))
        out, k, prev, base = [], 100.0, p0[1], (p0[0], p0[1], 100.0)
        for r in self.days:
            d = r["date"]
            if d <= p0[0] or d > b:
                continue
            f = (r.get("dep") or 0.0) + (r.get("wd") or 0.0) + (r.get("tr") or 0.0)
            if prev > 0 and r["equity"] - f > 0:
                k *= (r["equity"] - f) / prev
            prev = r["equity"]
            if d < a:
                base = (d, r["equity"], k)
                continue
            out.append((d, k, f))
        out = [(d, v * 100.0 / base[2], f) for d, v, f in out]
        return ((base[0], base[1]), out) if with_base else out

    def summary(self, a: str, b: str, label: str) -> dict | None:
        if not self.points:
            return None
        pS, pE = self.point_at(bp.day_before(a)), self.point_at(b)
        if pE[0] <= pS[0]:
            return None
        s = defaultdict(float)
        for d, kv in self.kind.items():
            if pS[0] < d <= pE[0]:
                for k, v in kv.items():
                    s[k] += v
        dep, wd, trn = s[bp.KIND_IN], s[bp.KIND_OUT], s[bp.KIND_TR]
        profit = pE[1] - pS[1] - dep - wd - trn
        # «Перевод ДС» со своего/на свой счёт брокер в итогах считает вводом/выводом —
        # в строки «Ввод»/«Вывод» сводки; знак — по дню. Прибыль выше считается от исходных сумм (перевод в ней не дважды).
        tr_in = sum(kv.get(bp.KIND_TR, 0.0) for d, kv in self.kind.items() if pS[0] < d <= pE[0] and kv.get(bp.KIND_TR, 0.0) > 0)
        dep, wd = dep + tr_in, wd + (trn - tr_in)
        d_req = self.req.get(pE[0], 0.0) - self.req.get(pS[0], 0.0)
        vm = s[bp.KIND_VM] + d_req
        broker = s[bp.KIND_BROKER] + s[bp.KIND_DEPO]
        exch, tax = s[bp.KIND_EXCH], s[bp.KIND_TAX]
        inc = s[bp.KIND_COUPON] + s[bp.KIND_INTEREST]
        rest = profit - vm - broker - exch - tax
        days = [r for r in self.days if pS[0] < r["date"] <= pE[0]]
        span = (dt.date.fromisoformat(pE[0]) - dt.date.fromisoformat(pS[0])).days
        # Доходность: каждый календарный год — только по своей стоимости на начало и конец
        # и своим вводу/выводу/переводам (в базе с долей года, когда деньги были на счёте); соседние годы на него не
        # влияют. Интервал через несколько лет («Итого») = произведение годовых множителей (1 + % года) − 1.
        pct, k = None, 1.0
        for y in range(int(pS[0][:4]) + (pS[0][5:] == "12-31"), int(pE[0][:4]) + 1):
            k1 = self._year_factor(pS[0] if pS[0] > f"{y - 1}-12-31" else f"{y - 1}-12-31", min(pE[0], f"{y}-12-31"))
            if k1 is None:
                k = None
                break
            k *= k1
        if k is not None:
            pct = (k - 1) * 100
        yrs = years_between(pS[0], pE[0])
        pct_y = ((1 + pct / 100) ** (1 / yrs) - 1) * 100 if pct is not None and pct > -100 and span >= 30 else None
        # Просадка: рубли — наибольшее падение кривой результата от пика; % — наибольшее падение ИНДЕКСА доходности
        # от пика за период (вывод денег в просадке её не прячет;
        # то же число, что на графике окна «Графики» за тот же период)
        cum, peak, dd, worst = 0.0, 0.0, 0.0, None
        for r in days:
            cum += r["result"] + r["tax"]
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
            if worst is None or r["result"] < worst[1]:
                worst = (r["date"], r["result"])
        ipk, dd_pct = 100.0, 0.0
        for _, v, _ in self.index_series(bp.day_after(pS[0]), pE[0]):
            ipk = max(ipk, v)
            dd_pct = max(dd_pct, (1 - v / ipk) * 100)
        go_max = max(days, key=lambda r: r["go"], default=None)
        gop = max((r for r in days if r["go_pct"] is not None), key=lambda r: r["go_pct"], default=None)
        lots = sum(v[0] for d, cv in self.lots.items() if pS[0] < d <= pE[0] for v in cv.values())
        deals = sum(v[1] for d, cv in self.lots.items() if pS[0] < d <= pE[0] for v in cv.values())
        costs = broker + exch
        before = profit - costs - tax
        note = ""
        if pS[0] != bp.day_before(a) or pE[0] != b:
            note = f"по справкам: {ru(dt.date.fromisoformat(pS[0]) + dt.timedelta(days=1))}–{ru(pE[0])}"
        return {
            "label": label, "from": pS[0], "to": pE[0], "note": note,
            "eq0": pS[1], "dep": dep, "wd": wd, "tr": trn, "eq1": pE[1],   # tr — справочно, уже в dep/wd
            "profit": profit, "pct": pct, "pct_y": pct_y,
            "vm": vm, "rest": rest, "income": inc, "broker": broker, "exch": exch, "tax": tax, "costs": costs,
            "cost_share": -costs / before * 100 if before > 0 else None,
            "go_max": go_max["go"] if go_max else None, "go_pct_max": gop["go_pct"] if gop else None,
            "worst": worst[1] if worst else None, "worst_d_ru": ru(worst[0]) if worst else "",
            "dd": -dd if dd else 0.0, "dd_pct": -dd_pct if dd_pct else None,
            "lots": lots, "deals": deals, "per_lot": -costs / lots if lots else None,
        }

    def years(self, a: str, b: str):
        out = []
        for y in range(int(a[:4]), int(b[:4]) + 1):
            s = self.summary(max(a, f"{y}-01-01"), min(b, f"{y}-12-31"), str(y))
            if s:
                out.append(s)
        return out

    # ── инструменты ───────────────────────────────────────────────────────────
    def ins_days(self, a: str, b: str, base: bool) -> dict:
        """{дата: {инструмент: [ВМ, биржа, брокер]}} за [a, b] — по дням то же, что instruments() за период (комиссия
        брокера дня делится по лотам дня; день без сделок — «без сделок в день списания»)."""
        key = (lambda c: c.split("-")[0]) if base else (lambda c: c)
        out = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
        lots_day = defaultdict(lambda: defaultdict(float))
        for d, cv in self.lots.items():
            if a <= d <= b:
                for c, (q, _) in cv.items():
                    lots_day[d][key(c)] += q
        for d, cv in self.ins.items():
            if a <= d <= b:
                for c, (vm, ex) in cv.items():
                    out[d][key(c)][0] += vm
                    out[d][key(c)][1] += ex
        for d, v in self.broker_pfi.items():
            if not a <= d <= b:
                continue
            tot = sum(lots_day[d].values())
            if not tot:
                out[d]["без сделок в день списания"][2] += v
                continue
            for k, q in lots_day[d].items():
                out[d][k][2] += v * q / tot
        return out

    def instruments(self, a: str, b: str, base: bool):
        key = (lambda c: c.split("-")[0]) if base else (lambda c: c)
        agg = defaultdict(lambda: {"vm": 0.0, "exch": 0.0, "broker": 0.0, "lots": 0.0, "deals": 0})
        lots_day = defaultdict(lambda: defaultdict(float))
        for d, cv in self.lots.items():
            if a <= d <= b:
                for c, (q, n) in cv.items():
                    agg[key(c)]["lots"] += q
                    agg[key(c)]["deals"] += n
                    lots_day[d][key(c)] += q
        for d, cv in self.ins.items():
            if a <= d <= b:
                for c, (vm, ex) in cv.items():
                    agg[key(c)]["vm"] += vm
                    agg[key(c)]["exch"] += ex
        unalloc = 0.0
        for d, v in self.broker_pfi.items():
            if not a <= d <= b:
                continue
            tot = sum(lots_day[d].values())
            if not tot:
                unalloc += v
                continue
            for k, q in lots_day[d].items():
                agg[k]["broker"] += v * q / tot
        rows = []
        for k, g in agg.items():
            net = g["vm"] + g["exch"] + g["broker"]
            cost = g["exch"] + g["broker"]
            rows.append({"instr": k, "vm": round(g["vm"], 2), "exch": round(g["exch"], 2),
                         "broker": round(g["broker"], 2), "net": round(net, 2), "lots": g["lots"], "deals": g["deals"],
                         "per_lot": round(-cost / g["lots"], 3) if g["lots"] else None,
                         "cost_share": round(-cost / abs(g["vm"]) * 100, 1) if g["vm"] else None})
        if abs(unalloc) > 0.005:
            rows.append({"instr": "без сделок в день списания", "vm": 0.0, "exch": 0.0, "broker": round(unalloc, 2),
                         "net": round(unalloc, 2), "lots": 0, "deals": 0, "per_lot": None, "cost_share": None})
        rows.sort(key=lambda r: r["net"])
        return rows


class Store:
    """Счёт окна: пересобирается, когда в каталоге справок что-то изменилось (новая справка, замена)."""

    def __init__(self, acc: str, data_dir: str, cache_dir: str):
        self.acc, self.data_dir, self.cache_dir = acc, data_dir, cache_dir
        self._lock = threading.Lock()
        self._a: Account | None = None
        self.error = ""

    def get(self) -> Account | None:
        sig = dir_signature(self.data_dir, self.acc)
        if self._a is not None and self._a.sig == sig:
            return self._a
        with self._lock:
            if self._a is None or self._a.sig != sig:
                try:
                    self._a = Account(self.acc, self.data_dir, self.cache_dir)
                    self.error = ""
                except Exception as e:  # noqa: BLE001 — окно не должно падать из-за справки
                    self.error = f"{type(e).__name__}: {e}"
        return self._a
