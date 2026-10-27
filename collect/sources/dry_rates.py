"""Dry bulk freight rates, read out of the daily Reuters market report.

The Baltic Exchange licenses its indices for GBP 3,420/yr, which this project
does not pay. But Reuters writes the numbers into a daily news story, Hellenic
republishes it, and a published sentence is not a licensed data feed. So the
dry bulk rows that would otherwise sit greyed out are filled from prose:

    "The capesize index decreased 65 points, or 1.71%, to 3,726 points.
     Average daily earnings for capesize vessels ... dropped by $585 to $30,292."

That yields the sub-indices AND the average daily earnings per vessel class —
the TCE figures a broker actually quotes — one day in arrears, free.

Wording varies between reports ("fell ... to 2,628 points", "dropped roughly 2%
... at 2,109 points"), so each figure is matched on several phrasings and every
result is bounds-checked. A sentence that does not parse yields nothing rather
than a wrong number.
"""
import datetime as dt
import html as html_mod
import re

from http_util import FetchError, fetch

SEARCH_FEED = "https://www.hellenicshippingnews.com/?s=baltic+exchange+capesize&feed=rss2"

# Plausibility bands. A Baltic sub-index has never been near these edges, and a
# daily TCE outside them means the sentence was misread.
BANDS = {
    "bdi": (200, 20000), "bci": (200, 30000), "bpi": (200, 20000), "bsi": (200, 20000),
    "cape_5tc": (1000, 300000), "panamax_4tc": (1000, 150000),
    "supramax_10tc": (1000, 150000),
}

SEGMENTS = (
    ("capesize", "bci", "cape_5tc"),
    ("panamax", "bpi", "panamax_4tc"),
    ("supramax", "bsi", "supramax_10tc"),
)

NUM = r"([\d][\d,]*(?:\.\d+)?)"


def _article_text(url):
    html = fetch(url)
    m = re.search(r'(?is)<div class="entry">(.*?)</div>\s*<(?:div|footer|section)', html)
    body = m.group(1) if m else html
    body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
    text = html_mod.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))).strip()
    return text.split("Source:")[0].replace("’", "'")


def _to_num(raw):
    try:
        return float(raw.replace(",", ""))
    except (AttributeError, ValueError):
        return None


def _bounded(key, value):
    lo, hi = BANDS.get(key, (0, float("inf")))
    return value if value is not None and lo <= value <= hi else None


def _index_level(text, label):
    """Find '<label> index ... to|at N points', tolerating the wording between.

    The gap is matched with a bounded any-character run rather than "not a full
    stop": the sentence contains percentages like "or 1.71%", and excluding the
    period stopped the match before it reached the figure.
    """
    for pat in (
        rf"{label}\s+index\b.{{0,150}}?\b(?:to|at)\s+{NUM}\s*points",
        rf"{label}\s+index\b.{{0,150}}?\bstood at\s+{NUM}",
        rf"{label}.{{0,60}}?\bindex\b.{{0,150}}?\b(?:to|at)\s+{NUM}\s*points",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return _to_num(m.group(1))
    return None


def _earnings(text, label):
    """Find average daily earnings for a vessel class, in dollars per day."""
    for pat in (
        rf"earnings for {label}.{{0,160}}?\b(?:to|at)\s+\$\s*{NUM}",
        rf"{label}.{{0,100}}?average daily earnings.{{0,160}}?\$\s*{NUM}",
        rf"average daily earnings.{{0,60}}?{label}.{{0,160}}?\$\s*{NUM}",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return _to_num(m.group(1))
    return None


def parse_report(text):
    """Extract every figure the report happens to carry. Missing is fine."""
    out = {}
    level = _index_level(text, "baltic")
    if level is None:
        m = re.search(rf"\bBaltic\b.{{0,180}}?\b(?:to|at)\s+{NUM}\s*points", text, re.I)
        level = _to_num(m.group(1)) if m else None
    if _bounded("bdi", level):
        out["bdi"] = level

    for label, index_key, tce_key in SEGMENTS:
        lvl = _bounded(index_key, _index_level(text, label))
        if lvl:
            out[index_key] = lvl
        tce = _bounded(tce_key, _earnings(text, label))
        if tce:
            out[tce_key] = tce
    return out


def _rfc822(raw):
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(raw).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def fetch_dry_rates(log=print, max_articles=4):
    """Return {id: {"value", "as_of", "source_url", "headline"}}.

    Walks the most recent reports newest-first and takes the first value found
    for each figure, because no single report carries every one of them.
    """
    xml = fetch(SEARCH_FEED)
    found, sources = {}, []

    for block in re.findall(r"<item>(.*?)</item>", xml, re.S)[: max_articles * 3]:
        title = re.search(r"<title>(.*?)</title>", block, re.S)
        link = re.search(r"<link>(.*?)</link>", block, re.S)
        date = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
        if not (title and link):
            continue
        title = re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1)).strip()
        if not re.search(r"baltic|capesize|panamax", title, re.I):
            continue

        try:
            text = _article_text(link.group(1).strip())
        except FetchError as exc:
            log(f"  dry_rates: could not read '{title[:40]}' — {exc}")
            continue

        values = parse_report(text)
        if not values:
            continue
        as_of = _rfc822(date.group(1)) if date else None
        for key, value in values.items():
            if key not in found:
                found[key] = {"value": value, "as_of": as_of,
                              "source_url": link.group(1).strip(), "headline": title}
        sources.append(title[:48])
        if len(sources) >= max_articles:
            break

    if not found:
        raise FetchError("no parseable Baltic figures in the recent reports")
    log(f"  dry_rates: {len(found)} figures from {len(sources)} report(s): "
        + ", ".join(sorted(found)))
    return found
