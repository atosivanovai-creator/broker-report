"""Быстрый разбор таблиц справки Финама (HTML): [(заголовок перед таблицей, строки[ячейки])]."""
import html, re

_TAG = re.compile(r"<(/?)(table|tr|td|th)\b[^>]*>", re.I)
_ANY = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>", re.I)


def _txt(s: str) -> str:
    return " ".join(html.unescape(_ANY.sub(" ", _BR.sub(" ", s))).split())


def tables(s: str):
    out, depth, last_end, row, cell_start, cur = [], 0, 0, None, None, None
    for m in _TAG.finditer(s):
        close, tag = m.group(1) == "/", m.group(2).lower()
        if tag == "table":
            if not close:
                depth += 1
                if depth == 1:
                    cur = (_txt(s[last_end:m.start()])[-160:], [])
                    out.append(cur)
            else:
                depth -= 1
                if depth == 0:
                    last_end = m.end()
        elif depth == 1 or (depth >= 1 and cur is not None):
            if tag == "tr":
                if not close:
                    row = []
                elif row is not None:
                    if any(row):
                        cur[1].append(row)
                    row = None
            elif tag in ("td", "th"):
                if not close:
                    cell_start = m.end()
                elif cell_start is not None and row is not None:
                    row.append(_txt(s[cell_start:m.start()]))
                    cell_start = None
    return out
