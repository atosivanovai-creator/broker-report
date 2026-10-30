"""BrokerReport — локальный разбор справок брокера ФИНАМ в браузере.

Запуск: BrokerReport.exe (или python app.py). Сервер слушает только 127.0.0.1. Справки разбираются на этом
компьютере; в интернет программа обращается только к api.finam.ru — и только если подключён ключ Trade API
(автозагрузка справок, finam_fetch.py). Данные — %LOCALAPPDATA%\\BrokerReport.

Ключи: --port N (по умолчанию 8765), --no-browser, --data DIR (свой каталог данных), --no-fetch (не запрашивать
справки при запуске), --fetch (одна автозагрузка без окна и выход — так её запускает Планировщик Windows, если
рядом нет BrokerReportFetch.exe).
"""
from __future__ import annotations

import argparse
import html as H
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import urllib.request
import webbrowser

from flask import Flask, abort, jsonify, redirect, request

APP = "BrokerReport"
VERSION = "1.0.6"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import finam_fetch as ff  # noqa: E402
from brep import brep_view  # noqa: E402
from intake import accept, accounts, peek, ru, slug, walk_upload  # noqa: E402,F401

TOKEN = secrets.token_urlsafe(16)          # защита форм от чужих страниц (CSRF на localhost)
MAX_UPLOAD = 2 * 1024 ** 3                  # справка за период с частой торговлей — до сотен МБ
PORTAL = "https://api.finam.ru/"


def data_dirs(base: str):
    return os.path.join(base, "reports"), os.path.join(base, "cache")


# ───────────────────────────── страница «Счета» ─────────────────────────────

CSS = """
:root{--bg:#0a0e15;--card:#111722;--line:#1f2938;--txt:#e6ebf3;--mut:#8e9ab0;--acc:#22c3ee;
--acc2:color-mix(in srgb,var(--acc) 16%,transparent);--bad:#ff6b7a;--ok:#5ee0a0;--warn:#f5c04a;--sh:0 1px 2px rgba(0,0,0,.25)}
[data-skin=warm]{--bg:#14100c;--card:#1c1712;--line:#30261c;--txt:#efe6da;--mut:#a69683;--acc:#eda04a}
[data-skin=light]{--bg:#f2f4f7;--card:#fff;--line:#dfe4ec;--txt:#1d2433;--mut:#5f6b7f;--acc:#2563eb;
--bad:#c0262d;--ok:#18794e;--warn:#a36a00;--sh:0 1px 3px rgba(20,30,50,.08)}
*{box-sizing:border-box}html{color-scheme:dark}html[data-skin=light]{color-scheme:light}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 "Segoe UI",system-ui,-apple-system,Roboto,sans-serif}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}code{font-size:12.5px}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px 40px}
.bar{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(6px);
border-bottom:1px solid var(--line)}
.bar .wrap{display:flex;align-items:center;gap:14px;height:58px;padding-bottom:0}
.brand{display:flex;align-items:center;gap:10px;font-weight:650;font-size:16px;letter-spacing:.01em}
.brand svg{flex:none}.ver{font-weight:500;color:var(--mut);font-size:12px;border:1px solid var(--line);border-radius:20px;padding:1px 8px}
.sp{flex:1}.pill{display:inline-flex;align-items:center;gap:6px;color:var(--mut);font-size:12.5px}
.pill i{width:7px;height:7px;border-radius:50%;background:var(--ok);display:inline-block}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{background:transparent;border:0;color:var(--mut);font:inherit;font-size:12.5px;padding:4px 10px;cursor:pointer}
.seg button.on{background:var(--acc2);color:var(--txt)}
h1{font-size:26px;line-height:1.25;margin:34px 0 8px;letter-spacing:-.01em}
.lead{color:var(--mut);font-size:15px;max-width:760px;margin:0}
h2{font-size:16px;margin:0}.sec{margin-top:28px}.sec-h{display:flex;align-items:baseline;gap:10px;margin-bottom:12px}
.mut{color:var(--mut)}.small{font-size:12.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;box-shadow:var(--sh)}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:22px}
.step{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.step b{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.num{width:24px;height:24px;border-radius:50%;background:var(--acc2);color:var(--acc);display:inline-grid;place-items:center;
font-size:12.5px;font-weight:700;flex:none}
.step p{margin:0;color:var(--mut);font-size:13px}
.drop{display:block;margin-top:16px;border:2px dashed color-mix(in srgb,var(--line),var(--acc) 30%);border-radius:14px;
background:var(--card);padding:34px 20px;text-align:center;cursor:pointer;transition:border-color .15s,background .15s}
.drop:hover,.drop.over{border-color:var(--acc);background:color-mix(in srgb,var(--card),var(--acc) 6%)}
.drop.small{padding:16px 20px;text-align:left;display:flex;align-items:center;gap:14px}
.drop .big{font-size:16px;font-weight:600;margin:8px 0 4px}.drop input{display:none}
.drop .ico{color:var(--acc);flex:none}.drop.busy{opacity:.6;pointer-events:none}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px}
.acc{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;display:flex;flex-direction:column;
gap:6px;box-shadow:var(--sh)}
.acc-h{display:flex;align-items:center;gap:8px}.acc-n{font-weight:650;font-size:15.5px}
.acc-p{font-variant-numeric:tabular-nums}.acc-m{color:var(--mut);font-size:12.5px}
.acc-a{display:flex;gap:8px;margin-top:8px;align-items:center}
.btn{display:inline-flex;align-items:center;gap:6px;background:var(--acc);color:#041018;border:0;border-radius:8px;
padding:7px 14px;font:inherit;font-weight:600;cursor:pointer;text-decoration:none}
[data-skin=light] .btn{color:#fff}.btn:hover{text-decoration:none;filter:brightness(1.08)}
.btn2{display:inline-flex;align-items:center;gap:6px;background:transparent;color:var(--txt);border:1px solid var(--line);
border-radius:8px;padding:6px 12px;font:inherit;cursor:pointer;text-decoration:none}
.btn2:hover{border-color:var(--acc);text-decoration:none}.btn2[disabled]{opacity:.5;cursor:default}
.x{background:transparent;border:0;color:var(--mut);cursor:pointer;font:inherit;font-size:12.5px;padding:2px 6px;
border-radius:6px}.x:hover{color:var(--bad);background:color-mix(in srgb,var(--bad) 10%,transparent)}
.msgs{margin-top:18px;display:grid;gap:6px}
.msg{border:1px solid var(--line);border-left:3px solid var(--ok);background:var(--card);border-radius:8px;padding:8px 12px;
font-size:13px}.msg.bad{border-left-color:var(--bad)}.msg.warn{border-left-color:var(--warn)}
.tag{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;border-radius:20px;padding:1px 8px;
border:1px solid var(--line);color:var(--mut);white-space:nowrap}
.tag.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,var(--line))}
.tag.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,var(--line))}
.tag.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,var(--line))}
.api-h{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.cols{display:grid;grid-template-columns:1.1fr 1fr;gap:22px;margin-top:12px}
ol.how{margin:6px 0 0;padding-left:18px;color:var(--mut);font-size:13px}ol.how li{margin:3px 0}
.f{display:flex;flex-direction:column;gap:8px}
.inp{background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit;width:100%}
.inp:focus{outline:none;border-color:var(--acc)}
.chk{display:flex;gap:8px;align-items:flex-start;font-size:12.5px;color:var(--mut)}
table.st{width:100%;border-collapse:collapse;margin-top:12px;font-size:13px}
table.st th{color:var(--mut);font-weight:600;text-align:left;font-size:12px;padding:6px 8px;border-bottom:1px solid var(--line)}
table.st td{padding:8px;border-bottom:1px solid var(--line);vertical-align:top}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:14px}
details.data{margin-top:28px}details.data summary{cursor:pointer;color:var(--mut);font-size:13px}
details.data .card{margin-top:10px}.foot{margin-top:26px;color:var(--mut);font-size:12px}
form{display:inline;margin:0}.spin{display:inline-block;width:11px;height:11px;border:2px solid var(--line);
border-top-color:var(--warn);border-radius:50%;animation:r 1s linear infinite}@keyframes r{to{transform:rotate(360deg)}}
@media (max-width:820px){.steps,.cols{grid-template-columns:1fr}.pill{display:none}}
"""

JS = """<script>
(function(){var k='graphite';try{k=localStorage.getItem('panel-skin')||'graphite'}catch(e){}
document.documentElement.setAttribute('data-skin',k);
window.setSkin=function(v){try{localStorage.setItem('panel-skin',v)}catch(e){};document.documentElement.setAttribute('data-skin',v);
document.querySelectorAll('.seg button').forEach(function(b){b.classList.toggle('on',b.dataset.v===v)})};
document.addEventListener('DOMContentLoaded',function(){
 document.querySelectorAll('.seg button').forEach(function(b){b.classList.toggle('on',b.dataset.v===k)});
 document.querySelectorAll('.drop').forEach(function(z){var f=z.querySelector('input[type=file]'),fm=z.closest('form');
  function go(){if(!f.files.length)return;z.classList.add('busy');var t=z.querySelector('.big');
   if(t)t.textContent='Загружаю '+f.files.length+' файл(ов)…';fm.submit()}
  f.addEventListener('change',go);
  ['dragenter','dragover'].forEach(function(e){z.addEventListener(e,function(ev){ev.preventDefault();z.classList.add('over')})});
  ['dragleave','drop'].forEach(function(e){z.addEventListener(e,function(ev){ev.preventDefault();z.classList.remove('over')})});
  z.addEventListener('drop',function(ev){f.files=ev.dataTransfer.files;go()});});
 if(document.getElementById('api-live')){var t=setInterval(function(){fetch('/api/status').then(function(r){return r.json()})
  .then(function(j){if(!j.running){clearInterval(t);location.replace('/')}}).catch(function(){})},3000)}
});})();
</script>"""

LOGO = ("<svg width=26 height=26 viewBox='0 0 26 26' aria-hidden=true><rect x=1 y=1 width=24 height=24 rx=7 "
        "fill='var(--acc2)' stroke='var(--acc)' stroke-opacity='.5'/><path d='M7 17.5v-4M11 17.5v-7M15 17.5v-5M19 17.5V8'"
        " stroke='var(--acc)' stroke-width='2.2' stroke-linecap='round'/></svg>")
UP = ("<svg class=ico width=34 height=34 viewBox='0 0 24 24' fill=none stroke=currentColor stroke-width=1.6 "
      "stroke-linecap=round stroke-linejoin=round aria-hidden=true><path d='M12 16V4M7 9l5-5 5 5'/>"
      "<path d='M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3'/></svg>")


def page(body: str) -> str:
    return (f"<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{APP} — отчёт по справкам брокера</title><style>{CSS}</style>{JS}</head><body>"
            f"<header class=bar><div class=wrap><div class=brand>{LOGO}{APP}<span class=ver>{VERSION}</span></div>"
            "<div class=sp></div><span class=pill title='Программа работает только на этом компьютере'><i></i>"
            "локально · 127.0.0.1</span><div class=seg role=group aria-label='Тема'>"
            "<button data-v=graphite onclick=\"setSkin('graphite')\">Графит</button>"
            "<button data-v=warm onclick=\"setSkin('warm')\">Тёплая</button>"
            "<button data-v=light onclick=\"setSkin('light')\">Светлая</button></div></div></header>"
            f"<main class=wrap>{body}</main></body></html>")


def _hid():
    return f"<input type=hidden name=t value={TOKEN}>"


def _ru_dt(s: str) -> str:
    """«2026-10-29 07:31» → «29.10 07:31»."""
    return f"{s[8:10]}.{s[5:7]}{s[10:]}" if len(s) >= 10 else s


def _api_tag(s: str, st: dict):
    """Метка автозагрузки для карточки счёта."""
    a = st.get(s)
    if not a:
        return ""
    if a.get("result") == "ok":
        return f"<span class='tag ok' title='{H.escape(a.get('msg', ''))}'>API ✓ {H.escape(_ru_dt(a.get('last_try', '')))}</span>"
    return f"<span class='tag bad' title='{H.escape(a.get('msg', ''))}'>API: не получено</span>"


def accounts_html(reports: str, st: dict):
    cards = []
    for s, nm, n, a, b in accounts(reports):
        cards.append(
            f"<div class=acc><div class=acc-h><span class=acc-n>{H.escape(nm)}</span>{_api_tag(s, st)}"
            f"<form method=post action='/delete/{s}' style='margin-left:auto' onsubmit=\"return confirm('Удалить справки "
            f"счёта {H.escape(nm)} с этого компьютера?')\">{_hid()}<button class=x title='Удалить справки этого счёта'>"
            "удалить</button></form></div>"
            f"<div class=acc-p>{ru(a)} — {ru(b)}</div>"
            f"<div class=acc-m>справок: {n} · последняя по {ru(b)}</div>"
            f"<div class=acc-a><a class=btn href='/a/{s}/broker-report/'>Открыть отчёт</a>"
            f"<a class=btn2 href='/a/{s}/broker-charts/' target=_blank>Графики ↗</a></div></div>")
    return cards


def drop_html(small: bool) -> str:
    if small:
        inner = (f"{UP}<div><div class=big style='margin:0'>Добавить справки</div><div class='mut small'>Перетащите файлы "
                 "сюда или нажмите, чтобы выбрать: .xls, .html или zip-архив из личного кабинета.</div></div>")
    else:
        inner = (f"{UP}<div class=big>Перетащите справки сюда</div><div class=mut>или нажмите, чтобы выбрать файлы — "
                 ".xls, .html или zip-архив из личного кабинета, можно сразу несколько</div>")
    return (f"<form method=post action=/upload enctype=multipart/form-data>{_hid()}<label class='drop{' small' if small else ''}'>"
            f"<input type=file name=files multiple accept='.xls,.html,.htm,.zip'>{inner}</label></form>")


def api_html(base: str, busy: bool) -> str:
    cfg, st = ff.load_cfg(base), ff.load_state(base)
    keys = cfg.get("keys", [])
    sch = ff.schedule_state()
    names = {a: nm for a, nm, *_ in accounts(os.path.join(base, "reports"))}
    if busy:
        status = "<span class='tag warn' id=api-live><span class=spin></span>запрашиваю справки у брокера…</span>"
    elif not keys:
        status = "<span class=tag>не подключено</span>"
    elif sch["on"]:
        status = f"<span class='tag ok'>по расписанию: {', '.join(sch['times'] or ff.TIMES)}</span>"
    else:
        status = "<span class=tag>только при запуске программы</span>"
    head = (f"<div class=api-h><h2>Автозагрузка справок через API ФИНАМ</h2>{status}</div>"
            "<p class='mut small' style='margin:6px 0 0'>Программа сама заказывает у брокера справку за прошедшие дни — "
            "при запуске и, если включить, по расписанию Windows 3 раза в день. Если справка за сегодня уже получена, "
            "к брокеру не обращается: повторяет только неудачную попытку.</p>")
    form = (f"<form method=post action=/api/key class=f autocomplete=off>{_hid()}"
            "<input class=inp type=password name=secret placeholder='Ключ Trade API (secret key)' required "
            "autocomplete=new-password spellcheck=false>"
            "<input class=inp name=label placeholder='Название ключа — необязательно' maxlength=60>"
            "<label class=chk><input type=checkbox name=trading value=1> Разрешить торговый ключ — только если брокер "
            "не выдаёт справки по ключу «только просмотр» (программа заявок не выставляет)</label>"
            "<div><button class=btn>Подключить ключ</button></div></form>")
    how = (f"<ol class=how><li>Откройте портал <a href='{PORTAL}' target=_blank rel=noopener>Finam Trade API</a> → "
           "«Токены» → создайте ключ.</li><li>Доступ — <b>только просмотр</b> (рекомендуется), отметьте нужные счета. "
           "Если брокер по нему откажет, программа покажет ошибку — тогда создайте торговый ключ.</li>"
           "<li>Скопируйте ключ и вставьте сюда.</li></ol><p class='mut small'>Ключ хранится на этом компьютере "
           "зашифрованным средствами Windows и больше нигде не показывается. Брокер даёт 3 заказа справок в сутки "
           "на клиента; программа тратит не больше одного в день на счёт.</p>")
    if not keys:
        return f"<section class=sec><div class=card>{head}<div class=cols><div>{how}</div><div>{form}</div></div></div></section>"
    rows = []
    for k in keys:
        mode = "<span class='tag ok'>только просмотр</span>" if k.get("readonly") else "<span class='tag warn'>торговый</span>"
        for a in k.get("accounts") or ["—"]:
            s = st.get(a, {})
            res = ("<span class='tag ok'>получено</span>" if s.get("result") == "ok" else
                   "<span class='tag bad'>не получено</span>" if s.get("result") == "fail" else "<span class=tag>ещё не было</span>")
            pend = " <span class='tag warn'>заказ ждёт</span>" if s.get("pending") else ""
            rows.append(f"<tr><td><b>{H.escape(names.get(a, a))}</b></td><td>{H.escape(k.get('label') or 'ключ ' + k['id'][:6])} {mode}</td>"
                        f"<td>{H.escape(_ru_dt(s.get('last_try', '—')))}</td><td>{res}{pend}<div class='mut small'>"
                        f"{H.escape(s.get('msg', ''))}</div></td></tr>")
    dels = "".join(f"<form method=post action='/api/key/{k['id']}/delete' onsubmit=\"return confirm('Удалить ключ "
                   f"{H.escape(k.get('label') or k['id'][:6])} с этого компьютера?')\">{_hid()}<button class=btn2>"
                   f"Удалить ключ {H.escape(k.get('label') or k['id'][:6])}</button></form>" for k in keys)
    sch_btn = (f"<form method=post action=/api/schedule>{_hid()}<input type=hidden name=on value={'0' if sch['on'] else '1'}>"
               f"<button class={'btn2' if sch['on'] else 'btn'}>"
               f"{'Выключить расписание' if sch['on'] else 'Включить расписание (3 раза в день)'}</button></form>")
    run_btn = (f"<form method=post action=/api/run>{_hid()}<button class=btn2{' disabled' if busy else ''}>Проверить сейчас"
               "</button></form>")
    more = (f"<details style='margin-top:14px'><summary class='mut small' style='cursor:pointer'>Подключить ещё ключ</summary>"
            f"<div class=cols><div>{how}</div><div>{form}</div></div></details>")
    return (f"<section class=sec><div class=card>{head}<table class=st><tr><th>Счёт</th><th>Ключ</th>"
            f"<th>Последняя попытка, МСК</th><th>Результат</th></tr>{''.join(rows)}</table>"
            f"<div class=row>{sch_btn}{run_btn}{dels}</div>{more}</div></section>")


def _plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "счёт"
    return "счёта" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "счетов"


def home_html(reports: str, base: str, msgs: list | None = None, busy: bool = False) -> str:
    st = ff.load_state(base)
    cards = accounts_html(reports, st)
    m = ""
    if msgs:
        m = "<div class=msgs>" + "".join(
            f"<div class='msg {'' if ok is True else 'warn' if ok == 'warn' else 'bad'}'>"
            f"{'✓' if ok is True else '!' if ok == 'warn' else '✗'} {H.escape(t)}</div>" for ok, t in msgs) + "</div>"
    if cards:
        top = (f"{m}<section class=sec><div class=sec-h><h2>Счета</h2><span class='mut small'>{len(cards)} "
               f"{_plural(len(cards))}</span></div><div class=grid>{''.join(cards)}</div>{drop_html(True)}</section>")
    else:
        top = (f"<h1>Отчёт по справкам брокера ФИНАМ</h1><p class=lead>Загрузите «Справки о состоянии обязательств» — "
               "программа посчитает доходность по годам, затраты, риск, результат по инструментам, бумагам и дням и "
               f"построит графики.</p>{m}"
               "<div class=steps><div class=step><b><span class=num>1</span>Скачайте справки</b><p>Личный кабинет ФИНАМ → "
               "«Отчёты» → «Справка о состоянии обязательств», лучше по календарным годам. Можно архивом.</p></div>"
               "<div class=step><b><span class=num>2</span>Загрузите сюда</b><p>Перетащите файлы в поле ниже. Счёт и "
               "период программа возьмёт из шапки справки, имена файлов не важны.</p></div>"
               "<div class=step><b><span class=num>3</span>Откройте отчёт</b><p>Появится карточка счёта — по кнопке "
               "«Открыть отчёт» сводка по годам, инструменты, бумаги, дни и графики.</p></div></div>"
               f"{drop_html(False)}")
    data = (f"<details class=data><summary>Где хранятся данные · удаление</summary><div class=card><p class='mut small' "
            f"style='margin-top:0'>Справки, кэш разбора, зашифрованный ключ API и журнал автозагрузки (api.log): "
            f"<code>{H.escape(base)}</code></p><form method=post action=/wipe onsubmit=\"return confirm('Удалить все "
            f"справки, кэш и ключи API с этого компьютера?')\">{_hid()}<button class=btn2 style='color:var(--bad)'>"
            "Удалить все данные</button></form></div></details>")
    foot = (f"<p class=foot>{APP} {VERSION} · неофициальная программа, не связана с АО «ФИНАМ» и не является "
            "инвестиционной рекомендацией · лицензия MIT</p>")
    return page(top + api_html(base, busy) + data + foot)


# ───────────────────────────── сервер ─────────────────────────────

class Dispatch:
    """/a/<счёт>/… — своё Dash-окно счёта (создаётся при первом обращении), остальное — страница «Счета»."""

    def __init__(self, base: str):
        self.base = base
        self.reports, self.cache = data_dirs(base)
        self.apps, self.lock = {}, threading.Lock()
        self.fetching = threading.Event()
        self.home = self._home()

    def fetch_bg(self, reason: str) -> bool:
        """Автозагрузка в фоне (при запуске и по кнопке); одновременно — одна."""
        if self.fetching.is_set() or not ff.load_cfg(self.base).get("keys"):
            return False
        self.fetching.set()

        def work():
            try:
                ff.run_once(self.base, reason)
            except Exception as e:  # noqa: BLE001 — окно не должно падать из-за сети
                ff.log(self.base, f"сбой автозагрузки: {type(e).__name__}: {e}")
            finally:
                self.fetching.clear()

        threading.Thread(target=work, daemon=True).start()
        return True

    def _home(self):
        srv = Flask(APP)
        srv.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD

        def check():
            if request.form.get("t") != TOKEN:
                abort(403)

        def show(msgs=None):
            return home_html(self.reports, self.base, msgs, self.fetching.is_set())

        @srv.get("/")
        def index():
            return show()

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
            return show(res or [(False, "файлы не выбраны")])

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
            for p in ("api.json", "api-state.json"):
                try:
                    os.remove(os.path.join(self.base, p))
                except OSError:
                    pass
            if ff.schedule_state()["on"]:
                try:
                    ff.schedule_off(self.base)
                except RuntimeError:
                    pass
            return redirect("/")

        @srv.post("/api/key")
        def api_key():
            check()
            try:
                k = ff.add_key(self.base, request.form.get("secret", ""), request.form.get("label", ""),
                               allow_trading=request.form.get("trading") == "1")
            except (ff.ApiError, RuntimeError, OSError) as e:
                return show([(False, f"Ключ не подключён: {e}")])
            self.fetch_bg("key")
            msgs = [(True, f"Ключ подключён: счетов {len(k['accounts'])}, "
                           f"{'только просмотр' if k['readonly'] else 'ТОРГОВЫЙ'}. Запрашиваю справки…")]
            if not ff.schedule_state()["on"]:
                msgs.append(("warn", "Чтобы справки приходили и без открытой программы — включите расписание ниже."))
            return show(msgs)

        @srv.post("/api/key/<kid>/delete")
        def api_key_del(kid):
            check()
            ff.remove_key(self.base, kid)
            if not ff.load_cfg(self.base).get("keys") and ff.schedule_state()["on"]:
                try:
                    ff.schedule_off(self.base)
                except RuntimeError:
                    pass
            return redirect("/")

        @srv.post("/api/schedule")
        def api_schedule():
            check()
            try:
                if request.form.get("on") == "1":
                    ff.schedule_on(self.base)
                    return show([(True, f"Расписание включено: каждый день в {', '.join(ff.TIMES)} — задача "
                                        f"«{ff.TASK}» в Планировщике Windows. Если компьютер был выключен, она "
                                        "запустится, как только он включится.")])
                ff.schedule_off(self.base)
                return show([(True, "Расписание выключено — справки запрашиваются только при запуске программы.")])
            except RuntimeError as e:
                return show([(False, str(e))])

        @srv.post("/api/run")
        def api_run():
            check()
            self.fetch_bg("manual")
            return redirect("/")

        @srv.get("/api/status")
        def api_status():
            return jsonify(running=self.fetching.is_set())

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
    ap.add_argument("--fetch", action="store_true", help="одна автозагрузка через API без окна и выход")
    ap.add_argument("--no-fetch", action="store_true", help="не запрашивать справки при запуске")
    ap.add_argument("--data", default=os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP))
    a = ap.parse_args(argv)
    if a.fetch:
        sys.exit(ff.main(["--data", a.data]))
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
    if not a.no_fetch and app.fetch_bg("startup"):
        print("Автозагрузка справок через API: запрос в фоне (журнал — api.log в папке данных).", flush=True)
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    from waitress import serve
    serve(app, host="127.0.0.1", port=a.port, threads=8, max_request_body_size=MAX_UPLOAD, ident=APP)


if __name__ == "__main__":
    main()
