"""VLSFO bunker prices from Ship & Bunker's free overview page.

Bunker fuel is the single biggest voyage cost, so this is the number that turns
a freight rate into an owner's actual earnings. Ship & Bunker publishes major
port prices publicly.

Each port renders as:
    <a ... class="unit sg-sin down">
      <span class="name">Singapore</span>
      <span class="price">932.50<span class="change">...1.50</span></span>
    </a>
The change figure is unsigned; its direction comes from the up/down class.

The page repeats the same port list three times, once per fuel grade, in the
order given by the VLSFO/MGO/IFO380 header row. Collecting anchors blindly into
a dict silently files IFO380 prices under VLSFO, so the anchors are clustered
into their three groups and matched to that header order. As a backstop the
result is checked against the relationship that always holds between these
grades: MGO (a distillate) costs more than VLSFO, which costs more than IFO380
(cheap high-sulphur residual).
"""
import datetime as dt
import re

from http_util import FetchError, fetch

URL = "https://shipandbunker.com/prices"
GRADES = ("VLSFO", "MGO", "IFO380")

PORT_RE = re.compile(
    r'<a[^>]*class="unit\s+([a-z\-]+)\s*(up|down)?"[^>]*>\s*'
    r'<span class="name">([^<]+)</span>\s*'
    r'<span class="price">\s*([\d,.]+)'
    r'(?:\s*<span class="change">.*?([\d,.]+)\s*</span>)?',
    re.S,
)
FUEL_RE = re.compile(r"\b(VLSFO|MGO|IFO380)\b")
GROUP_GAP = 5000  # chars; real gaps between grade blocks are ~18,000


def _grade_order(html):
    """The grade order as the page's header row lists it."""
    seen = []
    for m in FUEL_RE.finditer(html):
        g = m.group(1)
        if g not in seen:
            seen.append(g)
        if len(seen) == len(GRADES):
            break
    return seen or list(GRADES)


def _groups(html):
    """Cluster port anchors into one list per grade block, in page order."""
    out, current, last = [], [], None
    for m in PORT_RE.finditer(html):
        if last is not None and m.start() - last > GROUP_GAP:
            out.append(current)
            current = []
        current.append(m)
        last = m.start()
    if current:
        out.append(current)
    return out


def _parse(matches, log):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ports = {}
    for m in matches:
        _slug, direction, name, price, change = m.groups()
        name = name.strip()
        try:
            value = float(price.replace(",", ""))
        except ValueError:
            continue
        # Marine fuel trades in the hundreds; outside that is a parse slip.
        if not 100 <= value <= 5000:
            log(f"  bunkers: ignoring implausible {name} = {value}")
            continue
        delta = None
        if change:
            try:
                delta = float(change.replace(",", ""))
                if direction == "down":
                    delta = -delta
            except ValueError:
                delta = None
        ports[name] = {"value": value, "change": delta, "as_of": now}
    return ports


def fetch_all_grades(log=print):
    """Return {grade: {port: {...}}} for every grade the page publishes."""
    html = fetch(URL)
    groups = _groups(html)
    order = _grade_order(html)
    if len(groups) < len(order):
        log(f"  bunkers: found {len(groups)} price blocks for {len(order)} grades")

    by_grade = {}
    for grade, matches in zip(order, groups):
        parsed = _parse(matches, log)
        if parsed:
            by_grade[grade] = parsed
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
