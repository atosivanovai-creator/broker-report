"""Подставной Finam Trade API для проверок BrokerReport: вход, сведения о ключе, квота, заказ, готовность, скачивание.

Из теста: srv, fk = start()  (fk.quota / fk.mode / fk.orders / fk.calls).  Отдельно: python tests/fake_finam.py PORT
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_brep_old_format import _report  # noqa: E402

RO, TRADE, ACC = "ro-secret-0123456789abcdef", "trade-secret-0123456789abcdef", "7654321"
QUOTA = "ReportsService.createAccountReport"


def spravka(d0: str, d1: str) -> bytes:
    ru = lambda d: f"{d[8:10]}.{d[5:7]}.{d[:4]}"  # noqa: E731
    return (_report().replace("КлФ-ИИС1", f"КлФ-{ACC}")
            .replace("c 01.10.2021 по 31.12.2021", f"c {ru(d0)} по {ru(d1)}")).encode("utf-8")


class Fake:
    def __init__(self):
        self.quota, self.mode, self.orders, self.calls, self.reports = 3, "SUCCESS", [], [], {}
        self.deny_readonly = False          # брокер не выдаёт справки по ключу «только просмотр» (http 403)


def start(port: int = 0):
    fk = Fake()

    class Hd(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _j(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _auth(self):
            return self.headers.get("Authorization", "").startswith("Bearer jwt-")

        def do_POST(self):
            fk.calls.append(("POST", self.path))
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/v1/sessions":
                return self._j(200, {"token": "jwt-" + body["secret"]}) if body.get("secret") in (RO, TRADE) else \
                    self._j(401, {"message": "bad"})
            if self.path == "/v1/sessions/details":
                return self._j(200, {"account_ids": [ACC], "readonly": body.get("token") == "jwt-" + RO})
            if self.path == "/v1/report" and self._auth():
                if fk.deny_readonly and self.headers.get("Authorization") == "Bearer jwt-" + RO:
                    return self._j(403, {"code": 7, "message": "permission denied: readonly token"})
                if fk.quota <= 0:
                    return self._j(429, {"message": "quota"})
                fk.quota -= 1
                rid = f"r{len(fk.orders) + 1}"
                dr = body["date_range"]
                fk.orders.append((body["account_id"], dr["date_begin"][:10], dr["date_end"][:10], body["report_form"]))
                fk.reports[rid] = (dr["date_begin"][:10], dr["date_end"][:10])
                return self._j(200, {"report_id": rid})
            self._j(404, {})

        def do_GET(self):
            fk.calls.append(("GET", self.path))
            if not self._auth():
                return self._j(401, {})
            if self.path == "/v1/usage":
                return self._j(200, {"quotas": [{"name": QUOTA, "limit": "3", "remaining": str(fk.quota),
                                                 "reset_time": "2026-10-30T09:00:00Z"}]})
            m = re.fullmatch(r"/v1/report/(\w+)/info", self.path)
            if m:
                if m.group(1) not in fk.reports:
                    return self._j(404, {})
                info = {"status": fk.mode}
                if fk.mode == "SUCCESS":
                    info["url"] = f"http://127.0.0.1:{srv.server_port}/dl/{m.group(1)}"
                return self._j(200, {"info": info})
            m = re.fullmatch(r"/dl/(\w+)", self.path)
            if m:
                b = spravka(*fk.reports[m.group(1)])
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                return self.wfile.write(b)
            self._j(404, {})

    srv = ThreadingHTTPServer(("127.0.0.1", port), Hd)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, fk


if __name__ == "__main__":
    s, _ = start(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(f"fake Finam API: http://127.0.0.1:{s.server_port}", flush=True)
    threading.Event().wait()
