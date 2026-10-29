"""Автозагрузка справок ФИНАМ через Trade API (api.finam.ru) — при запуске программы и по расписанию Windows.

Правило запроса: у брокера заказывается справка, только если нужной справки ещё нет И сегодня она ещё не была
получена. Удачная загрузка ставит отметку «за сегодня получено» — следующие запуски в этот день к брокеру не
обращаются вовсе. Неудача (нет связи, квота, справка не собралась, не тот файл) отметки не ставит — следующий
запуск по расписанию пробует снова.

Квота брокера — 3 заказа справок за скользящие сутки на клиента. Поэтому:
  * одна справка на весь пробел «после последней имеющейся — по вчера» (до 92 дней — предел API), а не по дню;
  * номер заказа пишется сразу; справку, которая ещё собирается, следующий запуск дочитывает по номеру,
    без нового заказа;
  * перед заказом — GET /v1/usage; остаток 0 — не заказываем.

Ключ (secret key) хранится зашифрованным средствами Windows (DPAPI: расшифровать может только этот пользователь
на этом компьютере), в окне и журнале не показывается. Ключи с правом торговли не принимаются (поле readonly
в /v1/sessions/details), если человек явно не разрешил. Заявок модуль не выставляет и таких методов не содержит:
только вход, сведения о ключе, квота, заказ и чтение справки.

Только стандартная библиотека Python. Запуск без окна: BrokerReportFetch.exe --data <каталог данных>.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intake  # noqa: E402

API = os.environ.get("BR_FINAM_API", "https://api.finam.ru")
QUOTA = "ReportsService.createAccountReport"
MSK = dt.timezone(dt.timedelta(hours=3))
MAX_DAYS = 92                                   # предел периода справки в API
POLL_S = float(os.environ.get("BR_POLL_S", "4"))
POLL_N = int(os.environ.get("BR_POLL_N", "75"))  # ~5 минут ожидания готовности за запуск
TASK = "BrokerReport - справки ФИНАМ"
TIMES = ("07:30", "13:30", "19:30")
APPNAME = "BrokerReport"


# ───────────────────────────── хранение ключа ─────────────────────────────

def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt, kernel = ctypes.WinDLL("crypt32", use_last_error=True), ctypes.WinDLL("kernel32")
    fn = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]

    def blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    src, _keep = blob(data)
    ent, _keep2 = blob(APPNAME.encode())
    out = Blob()
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out)):  # UI_FORBIDDEN
        raise OSError(f"шифрование Windows (DPAPI) не сработало: код {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel.LocalFree(out.pbData)


def protect(secret: str) -> str:
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_dpapi(secret.encode(), True)).decode()
    if os.environ.get("BR_INSECURE_KEYSTORE") == "1":          # только для проверки вне Windows
        return "plain:" + base64.b64encode(secret.encode()).decode()
    raise RuntimeError("хранение ключа поддерживается только в Windows")


def unprotect(s: str) -> str:
    kind, _, b = s.partition(":")
    raw = base64.b64decode(b)
    if kind == "dpapi":
        return _dpapi(raw, False).decode()
    if kind == "plain" and os.environ.get("BR_INSECURE_KEYSTORE") == "1":
        return raw.decode()
    raise RuntimeError("ключ сохранён в неизвестном виде — подключите его заново")


# ───────────────────────────── API брокера ─────────────────────────────

class ApiError(Exception):
    pass


HUMAN = {401: "ключ недействителен, истёк или отозван", 403: "у ключа нет доступа к этому счёту",
         404: "счёт или справка не найдены у брокера", 429: "брокер просит обращаться реже",
         500: "ошибка на стороне брокера", 503: "сервис брокера недоступен", 504: "сервис брокера не ответил вовремя"}


class Api:
    def __init__(self, secret: str, base: str | None = None):
        self.sec, self.base, self.jwt, self.jwt_at = secret, (base or API).rstrip("/"), "", 0.0
        self.calls = 0

    def _call(self, method, path, body=None, auth=True, params=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json"})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", "Bearer " + self.token())
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {"_error": e.read()[:300].decode("utf-8", "replace")}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ApiError(f"нет связи с {urllib.parse.urlsplit(self.base).hostname}: {getattr(e, 'reason', e)}")

    def _need(self, st, j, what):
        if st != 200:
            raise ApiError(f"{what}: {HUMAN.get(st, 'ответ брокера')} (http {st})")
        return j

    def token(self) -> str:
        if not self.jwt or time.time() - self.jwt_at > 600:      # JWT живёт ~15 минут
            st, j = self._call("POST", "/v1/sessions", {"secret": self.sec}, auth=False)
            self.jwt = self._need(st, j, "вход по ключу").get("token") or ""
            if not self.jwt:
                raise ApiError("вход по ключу: брокер не выдал токен")
            self.jwt_at = time.time()
        return self.jwt

    def details(self) -> dict:
        st, j = self._call("POST", "/v1/sessions/details", {"token": self.token()}, auth=False)
        return self._need(st, j, "сведения о ключе")

    def quota(self):
        """(осталось, когда восстановится) заказов справок; (None, "") — брокер не сообщил."""
        st, j = self._call("GET", "/v1/usage")
        for q in j.get("quotas", []) if st == 200 else []:
            if q.get("name") == QUOTA:
                return int(q.get("remaining", 0)), str(q.get("reset_time") or "")
        return None, ""

    def order(self, acc: str, d0: dt.date, d1: dt.date) -> str:
        body = {"account_id": acc, "report_form": "REPORT_FORM_LONG",
                "date_range": {"date_begin": f"{d0}T00:00:00Z", "date_end": f"{d1}T12:00:00Z"}}
        st, j = self._call("POST", "/v1/report", body)
        rid = self._need(st, j, "заказ справки").get("report_id")
        if not rid:
            raise ApiError("заказ справки: брокер не вернул номер заказа")
        return str(rid)

    def info(self, rid: str):
        st, j = self._call("GET", f"/v1/report/{urllib.parse.quote(rid)}/info")
        return st, (j.get("info") or {}) if st == 200 else {}

    def download(self, url: str) -> bytes:
        host, api_host = urllib.parse.urlsplit(url).hostname or "", urllib.parse.urlsplit(self.base).hostname
        req = urllib.request.Request(url)
        if host == api_host or host.endswith(".finam.ru") or host == "finam.ru":   # ключ — только брокеру
            req.add_header("Authorization", "Bearer " + self.token())
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise ApiError(f"скачивание справки: http {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ApiError(f"скачивание справки: нет связи ({getattr(e, 'reason', e)})")


# ───────────────────────────── настройки и состояние ─────────────────────────────

def _paths(base: str):
    return {"cfg": os.path.join(base, "api.json"), "state": os.path.join(base, "api-state.json"),
            "log": os.path.join(base, "api.log"), "lock": os.path.join(base, "api.lock"),
            "reports": os.path.join(base, "reports")}


def _load(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _dump(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.{os.getpid()}.part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def now_msk() -> dt.datetime:
    fake = os.environ.get("BR_NOW")                       # только для проверок: «сейчас» = это время МСК
    return dt.datetime.fromisoformat(fake).replace(tzinfo=MSK) if fake else dt.datetime.now(MSK)


def log(base: str, msg: str) -> None:
    os.makedirs(base, exist_ok=True)
    with open(_paths(base)["log"], "a", encoding="utf-8") as f:
        f.write(f"{now_msk():%Y-%m-%d %H:%M:%S} МСК  {msg}\n")


def load_cfg(base):
    return _load(_paths(base)["cfg"], {"keys": []})


def load_state(base):
    return _load(_paths(base)["state"], {})


def add_key(base: str, secret: str, label: str = "", allow_trading: bool = False, api_base: str | None = None) -> dict:
    secret = secret.strip()
    if len(secret) < 20:
        raise ApiError("это не похоже на ключ Trade API — скопируйте его целиком")
    api = Api(secret, api_base)
    d = api.details()
    ro, ids = bool(d.get("readonly")), [str(x) for x in d.get("account_ids") or []]
    if not ids:
        raise ApiError("у ключа нет ни одного счёта — при создании ключа отметьте нужные счета")
    if not ro and not allow_trading:
        raise ApiError("ключ ТОРГОВЫЙ (может выставлять заявки). Для отчёта достаточно ключа «только просмотр» — "
                       "создайте такой на портале Trade API; торговый ключ программа не принимает")
    cfg = load_cfg(base)
    fp = hashlib.sha256(secret.encode()).hexdigest()[:16]
    cfg["keys"] = [k for k in cfg["keys"] if k.get("fp") != fp]
    k = {"id": fp, "fp": fp, "label": label.strip()[:60], "enc": protect(secret), "readonly": ro, "accounts": ids,
         "added": f"{now_msk():%Y-%m-%d %H:%M}"}
    cfg["keys"].append(k)
    _dump(_paths(base)["cfg"], cfg)
    log(base, f"подключён ключ {k['label'] or fp[:6]}: счетов {len(ids)}, " + ("только просмотр" if ro else "ТОРГОВЫЙ"))
    return k


def remove_key(base: str, kid: str) -> None:
    cfg = load_cfg(base)
    gone = [k for k in cfg["keys"] if k["id"] == kid]
    cfg["keys"] = [k for k in cfg["keys"] if k["id"] != kid]
    _dump(_paths(base)["cfg"], cfg)
    st = load_state(base)
    for k in gone:
        for a in k.get("accounts", []):
            st.pop(a, None)
    _dump(_paths(base)["state"], st)
    if gone:
        log(base, f"ключ {gone[0].get('label') or kid[:6]} удалён")


# ───────────────────────────── блокировка (окно программы + задача по расписанию) ─────────────────────────────

class Lock:
    def __init__(self, path):
        self.path, self.f = path, None

    def __enter__(self):
        self.f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            self.f = None
        return self.f is not None

    def __exit__(self, *a):
        if self.f:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self.f.close()


# ───────────────────────────── загрузка ─────────────────────────────

def gap(reports: str, acc: str, today: dt.date):
    """Первый недостающий отрезок (с, по) после последней имеющейся справки — или None, если справки есть по вчера.
    Нет ни одной справки — с 1 января текущего года (историю удобнее загрузить файлами из личного кабинета)."""
    yday = today - dt.timedelta(days=1)
    cov = intake.covered_days(reports, acc)
    d0 = max(cov) + dt.timedelta(days=1) if cov else dt.date(today.year, 1, 1)
    if d0 > yday:
        return None
    return d0, min(yday, d0 + dt.timedelta(days=MAX_DAYS - 1))


def _take(api: Api, base: str, acc: str, rid: str, d0: dt.date, d1: dt.date):
    """Дождаться справки по номеру заказа и сохранить. → (готово, текст). Не готова — (False, …), номер остаётся."""
    info, st = {}, 200
    for i in range(POLL_N):
        st, info = api.info(rid)
        if st != 200 or info.get("status") in ("SUCCESS", "ERROR"):
            break
        time.sleep(POLL_S)
    if st == 404:
        return None, "брокер не знает этот заказ — закажу заново"
    if st != 200:
        return False, f"проверка готовности: {HUMAN.get(st, 'ответ брокера')} (http {st})"
    if info.get("status") == "ERROR":
        return None, "брокер не смог собрать справку — закажу заново"
    if info.get("status") != "SUCCESS" or not info.get("url"):
        return False, "справка ещё собирается у брокера — заберу при следующем запуске"
    blob = api.download(info["url"])
    try:
        s, num, a, b, _ = intake.save(_paths(base)["reports"], blob)
    except ValueError as e:
        return None, f"брокер прислал не справку ({e}) — закажу заново"
    if (a, b) != (str(d0), str(d1)) or s != acc:
        log(base, f"{acc}: пришла справка {s} {a}..{b} вместо {d0}..{d1} — сохранена как есть")
    return True, f"получена справка {intake.ru(a)}–{intake.ru(b)} ({len(blob) // 1024} КБ)"


def fetch_account(api: Api, base: str, acc: str, st: dict, today: dt.date) -> None:
    s = st.setdefault(acc, {})
    s["last_try"] = f"{now_msk():%Y-%m-%d %H:%M}"
    reports = _paths(base)["reports"]
    got = []
    for _ in range(13):                                    # пробел больше 92 дней — до 6 справок (заказ + получение)
        p = s.get("pending")
        if p:
            ok, msg = _take(api, base, acc, p["rid"], dt.date.fromisoformat(p["d0"]), dt.date.fromisoformat(p["d1"]))
            if ok is False:
                s.update(result="fail", msg=msg)
                return
            s.pop("pending", None)
            if ok is None:
                log(base, f"{acc}: {msg}")
            else:
                got.append(msg)
                log(base, f"{acc}: {msg}")
            continue
        g = gap(reports, acc, today)
        if g is None:
            s.update(result="ok", done_on=str(today), last_ok=s["last_try"],
                     msg=("; ".join(got) if got else "справки по вчерашний день уже есть"))
            return
        left, reset = api.quota()
        if left == 0:
            s.update(result="fail", msg=f"квота брокера на заказ справок исчерпана (3 в сутки на клиента)"
                                        + (f", восстановится {reset[:16].replace('T', ' ')} UTC" if reset else ""))
            return
        rid = api.order(acc, *g)
        s["pending"] = {"rid": rid, "d0": str(g[0]), "d1": str(g[1]), "at": s["last_try"]}
        _dump(_paths(base)["state"], st)                   # номер заказа — сразу, чтобы сбой не стоил квоты
        log(base, f"{acc}: заказана справка {g[0]}..{g[1]}")
    s.update(result="fail", msg="слишком много справок за один запуск — продолжу при следующем")


def run_once(base: str, reason: str = "schedule") -> dict:
    """Один проход по всем подключённым ключам и счетам. → состояние."""
    P = _paths(base)
    os.makedirs(base, exist_ok=True)
    with Lock(P["lock"]) as got:
        if not got:
            return {"_busy": True}
        cfg, st = load_cfg(base), load_state(base)
        today = now_msk().date()
        for k in cfg.get("keys", []):
            accs = k.get("accounts") or []
            for a in accs:                                 # справки по вчера уже есть (в т.ч. загружены вручную)
                if st.get(a, {}).get("done_on") != str(today) and not st.get(a, {}).get("pending") \
                        and gap(P["reports"], a, today) is None:
                    st.setdefault(a, {}).update(result="ok", done_on=str(today), msg="справки по вчерашний день уже есть",
                                                last_try=f"{now_msk():%Y-%m-%d %H:%M}")
            if accs and all(st.get(a, {}).get("done_on") == str(today) for a in accs):
                continue                                   # за сегодня всё получено — к брокеру не обращаемся
            try:
                api = Api(unprotect(k["enc"]))
                d = api.details()
                accs = [str(x) for x in d.get("account_ids") or []]
                if accs != k.get("accounts"):
                    k["accounts"] = accs
                    _dump(P["cfg"], cfg)
            except (ApiError, RuntimeError, OSError) as e:
                for a in k.get("accounts") or []:
                    st.setdefault(a, {}).update(result="fail", msg=str(e), last_try=f"{now_msk():%Y-%m-%d %H:%M}")
                log(base, f"ключ {k.get('label') or k['id'][:6]}: {e}")
                continue
            for a in accs:
                if st.get(a, {}).get("done_on") == str(today):
                    continue
                try:
                    fetch_account(api, base, a, st, today)
                except ApiError as e:
                    st.setdefault(a, {}).update(result="fail", msg=str(e))
                log(base, f"{a}: {st[a].get('result')} — {st[a].get('msg')} ({reason})")
                _dump(P["state"], st)
        _dump(P["state"], st)
        return st


# ───────────────────────────── Планировщик Windows ─────────────────────────────

def fetch_command(base: str):
    """(программа, аргументы) запуска фоновой загрузки: собранный BrokerReportFetch.exe рядом с BrokerReport.exe,
    при запуске из исходников — pythonw.exe этого модуля (без окна консоли)."""
    if getattr(sys, "frozen", False):
        d = os.path.dirname(sys.executable)
        exe = os.path.join(d, "BrokerReportFetch.exe")
        if os.path.exists(exe):
            return exe, f'--data "{base}"'
        return sys.executable, f'--fetch --data "{base}"'
    py = sys.executable
    w = os.path.join(os.path.dirname(py), "pythonw.exe")
    return (w if os.path.exists(w) else py), f'"{os.path.abspath(__file__)}" --data "{base}"'


def task_xml(cmd: str, args: str, times=TIMES) -> str:
    from xml.sax.saxutils import escape
    start = dt.date.today().isoformat()
    trig = "".join(f"<CalendarTrigger><StartBoundary>{start}T{t}:00</StartBoundary><Enabled>true</Enabled>"
                   "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>" for t in times)
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
            "<RegistrationInfo><Description>BrokerReport: справка брокера ФИНАМ за вчерашний день через Trade API. "
            "Если справка уже получена — к брокеру не обращается.</Description></RegistrationInfo>"
            f"<Triggers>{trig}</Triggers>"
            '<Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType>'
            "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>"
            "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
            "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
            "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable>"
            "<RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>"
            "<ExecutionTimeLimit>PT30M</ExecutionTimeLimit><Enabled>true</Enabled><Hidden>false</Hidden></Settings>"
            f'<Actions Context="Author"><Exec><Command>{escape(cmd)}</Command>'
            f"<Arguments>{escape(args)}</Arguments></Exec></Actions></Task>")


def _schtasks(*a):
    flags = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW
    r = subprocess.run(["schtasks", *a], capture_output=True, creationflags=flags)
    out = (r.stdout or b"") + (r.stderr or b"")
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return r.returncode, out.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.returncode, out.decode("utf-8", "replace")


def schedule_on(base: str, task: str = TASK, times=TIMES) -> None:
    cmd, args = fetch_command(base)
    fd, p = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        with open(p, "w", encoding="utf-16") as f:
            f.write(task_xml(cmd, args, times))
        rc, out = _schtasks("/Create", "/TN", task, "/XML", p, "/F")
        if rc != 0:
            raise RuntimeError(f"Планировщик Windows не принял задачу: {out.strip()[:200]}")
    finally:
        os.remove(p)
    log(base, f"включено расписание ({', '.join(times)})")


def schedule_off(base: str, task: str = TASK) -> None:
    rc, out = _schtasks("/Delete", "/TN", task, "/F")
    if rc != 0 and schedule_state(task)["on"]:
        raise RuntimeError(f"Планировщик Windows не удалил задачу: {out.strip()[:200]}")
    log(base, "расписание выключено")


def schedule_state(task: str = TASK) -> dict:
    """{"on": есть ли задача, "cmd": что запускает, "times": [ЧЧ:ММ]} — по XML задачи (не зависит от языка Windows)."""
    if os.name != "nt":
        return {"on": False, "cmd": "", "times": []}
    rc, out = _schtasks("/Query", "/TN", task, "/XML")
    if rc != 0:
        return {"on": False, "cmd": "", "times": []}
    return {"on": "<Enabled>false</Enabled>" not in out.split("<Settings>")[-1],
            "cmd": (re.search(r"<Command>(.*?)</Command>", out, re.S) or [None, ""])[1],
            "times": re.findall(r"<StartBoundary>[^<]*T(\d\d:\d\d)", out)}


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(prog="BrokerReportFetch")
    ap.add_argument("--fetch", action="store_true", help="(совместимость: то же, что запуск без ключа)")
    ap.add_argument("--data", default=os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APPNAME))
    a = ap.parse_args(argv)
    st = run_once(a.data, "schedule")
    if st.get("_busy"):
        return 0
    bad = [k for k, v in st.items() if isinstance(v, dict) and v.get("result") == "fail"]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
