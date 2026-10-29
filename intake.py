"""Приём справок ФИНАМ: распознать шапку, разложить по счетам, перечислить счета и покрытые дни.

Общий для окна программы (app.py) и автозагрузки через API (finam_fetch.py). Только стандартная библиотека
и brep.htables (разбор HTML-таблиц) — без Dash/Flask, чтобы фоновая загрузка собиралась маленьким exe.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import re
import zipfile

from brep.htables import tables

REPORT_EXT = (".xls", ".html", ".htm")
PERIOD = re.compile(r"за период [cс] (\d\d)\.(\d\d)\.(\d{4}) по (\d\d)\.(\d\d)\.(\d{4})")
FILE = re.compile(r"_(\d{4}-\d\d-\d\d)\.\.(\d{4}-\d\d-\d\d)\.xls$")


def peek(raw: bytes):
    """(номер счёта, с, по) из шапки справки или ValueError. Читается только начало файла."""
    text = raw[:600_000].decode("utf-8-sig", errors="replace")
    tabs = tables(text)
    if not tabs:
        raise ValueError("в файле нет таблиц — это не справка ФИНАМ в формате HTML/.xls")
    m = PERIOD.search(" ".join(h for h, _ in tabs[:2]))
    if not m:
        raise ValueError("в шапке нет «за период с … по …» — это не «Справка о состоянии обязательств»")
    acc = next((row[1] for row in tabs[0][1] if len(row) > 1 and row[0].startswith("По счету")), "")
    d = m.groups()
    return acc, f"{d[2]}-{d[1]}-{d[0]}", f"{d[5]}-{d[4]}-{d[3]}"


def slug(acc: str) -> str:
    """Адрес счёта в программе: цифры и латиница номера («КлФ-1234567» → «1234567» = account_id в API)."""
    s = re.sub(r"[^0-9A-Za-z]+", "", acc)
    return s or "account"


def walk_upload(name: str, raw: bytes, depth: int = 0):
    """Файл загрузки → [(имя, байты)] справок; zip (в т.ч. вложенные, как в архиве кабинета) распаковывается."""
    low = name.lower()
    if low.endswith(".zip") or raw[:4] == b"PK\x03\x04":
        if depth > 3:
            return [(name, None)]
        out = []
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for i in z.infolist():
                if i.is_dir() or i.filename.lower().endswith(".sga"):     # .sga — электронная подпись, не справка
                    continue
                out += walk_upload(f"{name}/{i.filename}", z.read(i), depth + 1)
        return out
    if low.endswith(REPORT_EXT):
        return [(name, raw)]
    return [(name, None)]


def ru(d: str) -> str:
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}"


def save(reports: str, raw: bytes):
    """Сохранить справку как <счёт>/<счёт>_<с>..<по>.xls. → (адрес счёта, номер, с, по, заменила ли прежнюю)."""
    acc, d0, d1 = peek(raw)
    s = slug(acc)
    os.makedirs(os.path.join(reports, s), exist_ok=True)
    dst = os.path.join(reports, s, f"{s}_{d0}..{d1}.xls")
    existed = os.path.exists(dst)
    if acc:
        with open(os.path.join(reports, s, "account.txt"), "w", encoding="utf-8") as f:
            f.write(acc)
    tmp = f"{dst}.{os.getpid()}.part"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, dst)
    return s, acc, d0, d1, existed


def accept(reports: str, name: str, raw: bytes | None):
    """Загрузка человеком: (ok, текст для человека)."""
    if raw is None:
        return False, f"{name}: пропущен — не .xls/.html/.zip"
    try:
        s, acc, d0, d1, existed = save(reports, raw)
    except ValueError as e:
        return False, f"{name}: {e}"
    return True, f"{name}: счёт {acc or s}, {ru(d0)}–{ru(d1)}" + (" (заменила прежнюю)" if existed else "")


def periods(reports: str, s: str):
    p = os.path.join(reports, s)
    if not os.path.isdir(p):
        return []
    return sorted(m.groups() for m in (FILE.search(n) for n in os.listdir(p)) if m)


def covered_days(reports: str, s: str) -> set[dt.date]:
    out = set()
    for a, b in periods(reports, s):
        d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
        out |= {d0 + dt.timedelta(days=i) for i in range((d1 - d0).days + 1)}
    return out


def accounts(reports: str):
    """[(адрес, номер, справок, с, по)] по загруженным справкам."""
    out = []
    if not os.path.isdir(reports):
        return out
    for s in sorted(os.listdir(reports)):
        per = periods(reports, s)
        if per:
            nf = os.path.join(reports, s, "account.txt")
            name = open(nf, encoding="utf-8").read().strip() if os.path.exists(nf) else s
            out.append((s, name, len(per), per[0][0], max(b for _, b in per)))
    return out
