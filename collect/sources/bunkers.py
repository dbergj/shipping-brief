"""VLSFO bunker prices from Ship & Bunker's free overview page.

Bunker fuel is the single biggest voyage cost, so this is the number that turns
a freight rate into an owner's actual earnings.

The page is a table: a header row naming the fuel grades, then one row per port
with a price and a signed change per grade.

    <tr data-href="/prices/apac/sea/sg-sin-singapore">
      <td><a ...>Singapore</a></td>
      <td class="price-cell">
        <span class="price-val">$980.00</span>
        <span class="price-chg chg-up">+17.00</span>
      </td>
      ... one cell per grade ...

Grades are resolved from the header, never from column position. The site has
already reordered these columns once — they were VLSFO, MGO, IFO380 and are now
VLSFO, IFO380, MGO — and a positional parser would have silently filed the
wrong fuel under each name rather than failing. The MGO > VLSFO > IFO380 check
at the end is the backstop for exactly that.
"""
import datetime as dt
import html as html_mod
import re

from http_util import FetchError, fetch

URL = "https://shipandbunker.com/prices"
GRADES = ("VLSFO", "IFO380", "MGO")

ROW_RE = re.compile(r"<tr[^>]*data-href[^>]*>(.*?)</tr>", re.S)
NAME_RE = re.compile(r"<a[^>]*>([^<]+)</a>", re.S)
VAL_RE = re.compile(r'class="price-val"[^>]*>\s*\$?([\d,.]+)\s*<', re.S)
CHG_RE = re.compile(r'class="price-chg\s+chg-(up|dn)"[^>]*>\s*([+\-]?[\d,.]+)\s*<', re.S)
HEAD_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.S)


def _clean(s):
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _grade_columns(table):
    """Grade order as the header declares it, normalised to our names."""
    cols = []
    for raw in HEAD_RE.findall(table):
        text = _clean(raw).upper()
        for g in GRADES:
            if g.replace("IFO380", "IFO380") in text.replace(" ", "") or g in text:
                cols.append(g)
                break
        else:
            if "PORT" in text or "AVERAGE" in text:
                continue
    return cols


def _num(s):
    try:
        return float(s.replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def fetch_all_grades(log=print):
    """Return {grade: {port: {"value", "change", "as_of"}}}."""
    html = fetch(URL)
    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.S)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    by_grade = {}
    for table in tables:
        cols = _grade_columns(table)
        if not cols:
            continue
        for row in ROW_RE.findall(table):
            name = NAME_RE.search(row)
            if not name:
                continue
            port = _clean(name.group(1))
            values = [_num(v) for v in VAL_RE.findall(row)]
            changes = CHG_RE.findall(row)
            for i, grade in enumerate(cols):
                if i >= len(values) or values[i] is None:
                    continue
                value = values[i]
                # Marine fuel trades in the hundreds; outside that is a parse slip.
                if not 100 <= value <= 5000:
                    log(f"  bunkers: ignoring implausible {port} {grade} = {value}")
                    continue
                delta = None
                if i < len(changes):
                    direction, raw = changes[i]
                    delta = _num(raw)
                    if delta is not None:
                        delta = -abs(delta) if direction == "dn" else abs(delta)
                by_grade.setdefault(grade, {})[port] = {
                    "value": value, "change": delta, "as_of": now,
                }
        if by_grade:
            break

    if not by_grade:
        raise FetchError("Ship & Bunker page parsed to zero ports (layout may have changed)")

    _sanity_check(by_grade, log)
    return by_grade


def _sanity_check(by_grade, log):
    """MGO > VLSFO > IFO380 at the same port. Catches a grade mix-up."""
    for port in by_grade.get("VLSFO", {}):
        v = by_grade.get("VLSFO", {}).get(port, {}).get("value")
        m = by_grade.get("MGO", {}).get(port, {}).get("value")
        i = by_grade.get("IFO380", {}).get(port, {}).get("value")
        if v and m and m < v:
            log(f"  bunkers: WARNING {port} MGO {m} < VLSFO {v} — grades may be mislabelled")
        if v and i and i > v:
            log(f"  bunkers: WARNING {port} IFO380 {i} > VLSFO {v} — grades may be mislabelled")
        break  # one port is enough to detect a systematic swap


def fetch_bunkers(fuel="VLSFO", log=print):
    """Return {port: {"value", "change", "as_of"}} for one grade."""
    grades = fetch_all_grades(log=log)
    if fuel not in grades:
        raise FetchError(f"grade {fuel} not found (got {list(grades)})")
    return grades[fuel]
