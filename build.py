"""Сборка BrokerReport для Windows: exe (PyInstaller, onedir) + лицензии зависимостей + zip.

    .venv\\Scripts\\python build.py
Результат: dist\\BrokerReport-<версия>-win64.zip (внутри папка BrokerReport с BrokerReport.exe).
"""
import importlib.metadata as md
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from app import APP, VERSION  # noqa: E402

RUNTIME = ["dash", "dash-ag-grid", "plotly", "Flask", "Werkzeug", "waitress"]


def closure(names):
    seen, todo = {}, list(names)
    while todo:
        n = re.split(r"[\s;<>=!~\[(]", todo.pop())[0]
        key = n.lower().replace("_", "-")
        if not n or key in seen:
            continue
        try:
            d = md.distribution(n)
        except md.PackageNotFoundError:
            continue
        seen[key] = d
        for r in d.requires or []:
            if "extra ==" in r:
                continue
            todo.append(r)
    return seen


def notices(path):
    out = [f"{APP} {VERSION} включает следующие сторонние компоненты.\n"
           "Тексты их лицензий приведены ниже без изменений.\n"]
    dists = closure(RUNTIME)
    for key in sorted(dists):
        d = dists[key]
        lic = d.metadata.get("License-Expression") or d.metadata.get("License") or ""
        lic = lic.splitlines()[0][:80] if lic else "см. текст ниже"
        out.append("=" * 78 + f"\n{d.metadata['Name']} {d.version} — {lic}\n" + "=" * 78)
        files = [f for f in (d.files or []) if re.search(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", f.name, re.I)]
        texts = []
        for f in files:
            try:
                texts.append(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        out.append("\n\n".join(texts) if texts else "(текст лицензии в пакете не найден; лицензия: " + lic + ")")
    py = os.path.join(sys.base_prefix, "LICENSE.txt")
    if os.path.exists(py):
        out.append("=" * 78 + f"\nPython {sys.version.split()[0]} — PSF License\n" + "=" * 78)
        out.append(open(py, encoding="utf-8", errors="replace").read())
    out.append("=" * 78 + "\nAG Grid Community (внутри dash-ag-grid) — MIT, https://github.com/ag-grid/ag-grid\n"
               "Plotly.js (внутри plotly) — MIT, https://github.com/plotly/plotly.js\n"
               "Исполняемый файл собран PyInstaller (GPL с исключением для загрузчика: распространение\n"
               "собранных программ под любой лицензией разрешено).\n" + "=" * 78)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(out))
    return sorted(f"{dists[k].metadata['Name']}=={dists[k].version}" for k in dists)


def main():
    dist = os.path.join(ROOT, "dist")
    shutil.rmtree(os.path.join(dist, APP), ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", APP, "--onedir",
                    "--console", "--add-data", f"{os.path.join(ROOT, 'brep', 'assets')}{os.pathsep}brep/assets",
                    "--collect-all", "dash", "--collect-all", "dash_ag_grid", "--collect-all", "plotly",
                    "--hidden-import", "brep.brep_charts", "--distpath", dist,
                    "--workpath", os.path.join(ROOT, "build"), "--specpath", os.path.join(ROOT, "build"),
                    os.path.join(ROOT, "app.py")], check=True, cwd=ROOT)
    out = os.path.join(dist, APP)
    shutil.copy(os.path.join(ROOT, "LICENSE"), os.path.join(out, "LICENSE.txt"))
    shutil.copy(os.path.join(ROOT, "README.md"), os.path.join(out, "README.txt"))
    pk = notices(os.path.join(out, "THIRD-PARTY-NOTICES.txt"))
    z = os.path.join(dist, f"{APP}-{VERSION}-win64.zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _, files in os.walk(out):
            for f in files:
                p = os.path.join(base, f)
                zf.write(p, os.path.relpath(p, dist))
    print("компоненты:", ", ".join(pk))
    print("готово:", z, f"{os.path.getsize(z) / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()
