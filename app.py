"""BrokerReport — локальный разбор справок брокера ФИНАМ в браузере.

Запуск: BrokerReport.exe (или python app.py). Сервер слушает только 127.0.0.1, справки никуда не отправляются.
Справки и кэш разбора лежат в %LOCALAPPDATA%\\BrokerReport (кнопка «Удалить все данные» на странице «Счета»).

Ключи: --port N (по умолчанию 8765), --no-browser, --data DIR (свой каталог данных).
"""
from __future__ import annotations

import argparse
import html as H
import io
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import urllib.request
import webbrowser
import zipfile

from flask import Flask, Response, abort, redirect, request

APP = "BrokerReport"
VERSION = "1.0.1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from brep import brep_view  # noqa: E402
from brep.htables import tables  # noqa: E402

TOKEN = secrets.token_urlsafe(16)          # защита форм от чужих страниц (CSRF на localhost)
MAX_UPLOAD = 2 * 1024 ** 3                  # справка за период с частой торговлей — до сотен МБ
REPORT_EXT = (".xls", ".html", ".htm")
PERIOD = re.compile(r"за период [cс] (\d\d)\.(\d\d)\.(\d{4}) по (\d\d)\.(\d\d)\.(\d{4})")


def data_dirs(base: str):
    return os.path.join(base, "reports"), os.path.join(base, "cache")


# ───────────────────────────── приём справок ─────────────────────────────

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


def accept(reports: str, name: str, raw: bytes | None):
    """Сохранить справку как <счёт>/<счёт>_<с>..<по>.xls. → (ok, текст для человека)."""
    if raw is None:
        return False, f"{name}: пропущен — не .xls/.html/.zip"
    try:
        acc, d0, d1 = peek(raw)
    except ValueError as e:
        return False, f"{name}: {e}"
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
    return True, f"{name}: счёт {acc or s}, {ru(d0)}–{ru(d1)}" + (" (заменила прежнюю)" if existed else "")


def ru(d: str) -> str:
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}"


def accounts(reports: str):
    out = []
    if not os.path.isdir(reports):
        return out
    for s in sorted(os.listdir(reports)):
        p = os.path.join(reports, s)
        if not os.path.isdir(p):
            continue
        per = sorted(re.findall(r"_(\d{4}-\d\d-\d\d)\.\.(\d{4}-\d\d-\d\d)\.xls$", n)[0]
                     for n in os.listdir(p) if re.search(r"_\d{4}-\d\d-\d\d\.\.\d{4}-\d\d-\d\d\.xls$", n))
        if per:
            nf = os.path.join(p, "account.txt")
            name = open(nf, encoding="utf-8").read().strip() if os.path.exists(nf) else s
            out.append((s, name, len(per), per[0][0], max(b for _, b in per)))
    return out


# ───────────────────────────── страница «Счета» ─────────────────────────────

CSS = """
:root{--bg:#0f1218;--card:#161b24;--line:#262e3b;--txt:#e6eaf0;--mut:#8b96a8;--acc:#4f8cff;--bad:#ff6b6b;--ok:#3ecf8e}
[data-skin=warm]{--bg:#14100c;--card:#1c1712;--line:#30261c;--txt:#efe6da;--mut:#a69683;--acc:#eda04a}
[data-skin=light]{--bg:#f2f4f7;--card:#fff;--line:#dfe4ec;--txt:#1d2433;--mut:#5f6b7f;--acc:#2563eb;--bad:#c0262d;--ok:#18794e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui,Segoe UI,sans-serif}
.page{max-width:980px;margin:0 auto;padding:22px 18px}h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:14px 0}
.mut{color:var(--mut)}a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
table{width:100%;border-collapse:collapse}td,th{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--mut);font-weight:600}.btn{background:var(--acc);color:#fff;border:0;border-radius:7px;padding:7px 14px;
cursor:pointer;font:inherit}.btn.ghost{background:transparent;color:var(--bad);border:1px solid var(--line)}
.ok{color:var(--ok)}.bad{color:var(--bad)}ul{margin:6px 0;padding-left:20px}
select{font:inherit;background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:2px 6px}
.drop{border:2px dashed var(--line);border-radius:10px;padding:18px;text-align:center}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}form{display:inline}
"""
THEME_JS = """<script>
(function(){var k='graphite';try{k=localStorage.getItem('panel-skin')||'graphite'}catch(e){}
document.documentElement.setAttribute('data-skin',k);
window.setSkin=function(v){try{localStorage.setItem('panel-skin',v)}catch(e){};document.documentElement.setAttribute('data-skin',v)};
document.addEventListener('DOMContentLoaded',function(){var s=document.getElementById('skin');if(s)s.value=k});})();
</script>"""


def page(body: str) -> str:
    return (f"<!doctype html><html lang=ru><head><meta charset=utf-8><title>{APP} — счета</title>"
            f"<style>{CSS}</style>{THEME_JS}</head><body><div class=page>{body}</div></body></html>")


def home_html(reports: str, base: str, result: list | None = None) -> str:
    rows = "".join(
        f"<tr><td><b>{H.escape(nm)}</b></td><td>{n}</td><td>{ru(a)} – {ru(b)}</td>"
        f"<td><a href='/a/{s}/broker-report/'>Отчёт</a> · <a href='/a/{s}/broker-charts/' target=_blank>Графики ↗</a></td>"
        f"<td><form method=post action='/delete/{s}' onsubmit=\"return confirm('Удалить справки счёта {H.escape(nm)}?')\">"
        f"<input type=hidden name=t value={TOKEN}><button class='btn ghost'>удалить</button></form></td></tr>"
        for s, nm, n, a, b in accounts(reports))
    acc = (f"<table><tr><th>Счёт</th><th>Справок</th><th>Период</th><th></th><th></th></tr>{rows}</table>"
           if rows else "<p class=mut>Справок пока нет — загрузите их ниже.</p>")
    res = ""
    if result:
        res = "<div class=card><h2>Загрузка</h2><ul>" + "".join(
            f"<li class={'ok' if ok else 'bad'}>{'✓' if ok else '✗'} {H.escape(t)}</li>" for ok, t in result) + "</ul></div>"
    return page(f"""
<div class=top><div><h1>Отчёт брокера по справкам</h1>
<div class=mut>Справки ФИНАМ «о состоянии обязательств» → сводка по годам, инструменты, дни, графики.
Всё считается на этом компьютере, в интернет ничего не отправляется.</div></div>
<div class=mut>тема <select id=skin onchange="setSkin(this.value)"><option value=graphite>Графит</option>
<option value=warm>Тёплая</option><option value=light>Светлая</option></select></div></div>
{res}
<div class=card><h2>Счета</h2>{acc}</div>
<div class=card><h2>Загрузить справки</h2>
<form method=post action=/upload enctype=multipart/form-data><div class=drop>
<input type=hidden name=t value={TOKEN}>
<input type=file name=files multiple accept=".xls,.html,.htm,.zip"> <button class=btn>Загрузить</button>
<p class=mut>Файлы .xls / .html или zip-архив из личного кабинета (вложенные архивы распаковываются, подписи .sga
пропускаются). Счёт и период берутся из шапки справки. Лучше по календарным годам — справка за несколько лет
по годам не делится.</p></div></form></div>
<div class=card><h2>Данные</h2><p class=mut>Справки и кэш: {H.escape(base)}</p>
<form method=post action=/wipe onsubmit="return confirm('Удалить все справки и кэш?')">
<input type=hidden name=t value={TOKEN}><button class='btn ghost'>Удалить все данные</button></form></div>
<p class=mut>{APP} {VERSION} · неофициальная программа, не связана с АО «ФИНАМ» · MIT</p>""")


# ───────────────────────────── сервер ─────────────────────────────

class Dispatch:
    """/a/<счёт>/… — своё Dash-окно счёта (создаётся при первом обращении), остальное — страница «Счета»."""

    def __init__(self, base: str):
        self.base = base
        self.reports, self.cache = data_dirs(base)
        self.apps, self.lock = {}, threading.Lock()
        self.home = self._home()

    def _home(self):
        srv = Flask(APP)
        srv.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD

        def check():
            if request.form.get("t") != TOKEN:
                abort(403)

        @srv.get("/")
        def index():
            return home_html(self.reports, self.base)

        @srv.get("/health")
        def health():
            return APP

        @srv.post("/upload")
        def upload():
            check()
            res = []
            for f in request.files.getlist("files"):
                if not f.filename:
                    continue
                for name, raw in walk_upload(f.filename, f.read()):
                    res.append(accept(self.reports, name, raw))
            return home_html(self.reports, self.base, res or [(False, "файлы не выбраны")])

        @srv.post("/delete/<s>")
        def delete(s):
            check()
            if re.fullmatch(r"[0-9A-Za-z]+", s):
                with self.lock:
                    self.apps.pop(s, None)
                for d in (self.reports, self.cache):
                    shutil.rmtree(os.path.join(d, s), ignore_errors=True)
            return redirect("/")

        @srv.post("/wipe")
        def wipe():
            check()
            with self.lock:
                self.apps.clear()
            for d in (self.reports, self.cache):
                shutil.rmtree(d, ignore_errors=True)
            return redirect("/")

        return srv

    def account_app(self, s: str):
        with self.lock:
            a = self.apps.get(s)
            if a is None:
                from dash import html
                a = Flask(f"{APP}-{s}")
                brep_view.mount(a, f"/a/{s}/broker-report/", s, self.reports, self.cache,
                                stand_nav=[html.A("← Счета", href="/", className="lnk")])
                self.apps[s] = a
            return a

    def __call__(self, environ, start_response):
        m = re.match(r"^/a/([0-9A-Za-z]+)/", environ.get("PATH_INFO", ""))
        if m and os.path.isdir(os.path.join(self.reports, m.group(1))):
            return self.account_app(m.group(1))(environ, start_response)
        if m:
            return redirect("/")(environ, start_response)
        return self.home(environ, start_response)


def running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
            return r.read().decode() == APP
    except Exception:  # noqa: BLE001
        return False


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main(argv=None):
    for st in (sys.stdout, sys.stderr):         # вывод в файл/канал на Windows — cp1252, кириллица роняла запуск
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(prog=APP)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--data", default=os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP))
    a = ap.parse_args(argv)
    url = f"http://127.0.0.1:{a.port}/"
    if running(a.port):                         # уже запущена — просто открыть окно
        print(f"{APP} уже работает: {url}")
        if not a.no_browser:
            webbrowser.open(url)
        return
    if not port_free(a.port):
        sys.exit(f"Порт {a.port} занят другой программой. Запустите с другим: {APP}.exe --port 8766")
    os.makedirs(a.data, exist_ok=True)
    app = Dispatch(a.data)
    print(f"{APP} {VERSION}: {url}\nДанные: {a.data}\nЗакройте это окно, чтобы остановить программу.", flush=True)
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    from waitress import serve
    serve(app, host="127.0.0.1", port=a.port, threads=8, max_request_body_size=MAX_UPLOAD, ident=APP)


if __name__ == "__main__":
    main()
