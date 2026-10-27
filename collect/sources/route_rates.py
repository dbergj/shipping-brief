"""Tanker, LNG and LPG route rates, read out of the weekly Baltic market report.

The Baltic Exchange licenses its route assessments, but it also writes a weekly
market commentary that quotes them, and Hellenic republishes that as plain text:

    "The TD3C route (270,000mt Middle East Gulf/China) dropped 88 points to
     WS1,208.13, giving a daily round-trip TCE of $1,287,851..."
    "On the BLNG1 Australia-Japan route, rates increased by $4,800 week-on-week
     to settle at $32,800/day."

So the VLCC, Suezmax, Aframax, clean product, LNG and LPG rows can all be
filled from prose, weekly and free, instead of sitting greyed out.

The hard part is not finding the number but picking the right one: a sentence
usually quotes the *change* before the *level* ("rising by $28,600 to
$313,200/day"). Every pattern here therefore anchors on a preposition that
introduces a level — of / to / at — and never on "by".
"""
import datetime as dt
import re

from dry_rates import _article_text, _rfc822
from http_util import FetchError, fetch

# Two weekly reports: one covers tankers, the other gas.
FEEDS = (
    "https://www.hellenicshippingnews.com/?s=VLCC+TCE&feed=rss2",
    "https://www.hellenicshippingnews.com/?s=LNG+shipping+rates+day&feed=rss2",
    "https://www.hellenicshippingnews.com/?s=BLNG+BLPG+route&feed=rss2",
)

# Our row id -> the Baltic route code quoted in the report.
ROUTES = {
    "vlcc_tce": "TD3C",
    "suezmax_tce": "TD20",
    "aframax_tce": "TD7",
    "lr2_tce": "TC1",
    "mr_tc2_tce": "TC2",
    "mr_tc14_tce": "TC14",
    "lng_blng1": "BLNG1",
    "lng_blng2": "BLNG2",
    "lng_blng3": "BLNG3",
    "vlgc_baltic": "BLPG1",
}

# TCE in dollars per day. The band is deliberately wide — these reports are
# quoting a market with Worldscale above 1,000 — but still rejects a misread
# tonne rate ($252.40) or a cargo value in millions.
BAND = (1_000, 3_000_000)

NUM = r"([\d][\d,]*(?:\.\d+)?)"
QUALIFIER = r"(?:just under |just over |about |around |almost |approximately |roughly )?"

# Anchored on a preposition that introduces a level, never on "by", so the
# week-on-week change is not mistaken for the rate itself.
PATTERNS = (
    rf"(?:TCE|earnings).{{0,160}}?\b(?:of|to|at)\s+{QUALIFIER}\$\s*{NUM}",
    rf"\b(?:settle|settled|close|closing|closed)\s+at\s+{QUALIFIER}\$\s*{NUM}",
    rf"\b(?:to|at)\s+{QUALIFIER}\$\s*{NUM}\s*/?\s*day",
)


def _window(text, code):
    """Text from a route code up to the next route code, or 500 chars."""
    m = re.search(rf"\b{re.escape(code)}\b", text)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"\b(?:T[CD]\d+[A-Z]?|BLNG\d|BLPG\d)\b", text[start:start + 600])
    end = start + (nxt.start() if nxt else 500)
    return text[start:end]


def _to_num(raw):
    try:
        return float(raw.replace(",", ""))
    except (AttributeError, ValueError):
        return None


def parse_routes(text, routes=None):
    """Extract {row_id: tce_per_day} for every route quoted in this report."""
    out = {}
    for row_id, code in (routes or ROUTES).items():
        window = _window(text, code)
        if not window:
            continue
        for pat in PATTERNS:
            m = re.search(pat, window, re.I)
            if not m:
                continue
            value = _to_num(m.group(1))
            if value is not None and BAND[0] <= value <= BAND[1]:
                out[row_id] = value
                break
    return out


def fetch_route_rates(log=print, max_articles=6):
    """Return {row_id: {"value", "as_of", "source_url", "headline"}}."""
    found = {}
    reports = 0

    for feed in FEEDS:
        try:
            xml = fetch(feed)
        except FetchError as exc:
            log(f"  route_rates: feed unavailable — {exc}")
            continue

        for block in re.findall(r"<item>(.*?)</item>", xml, re.S)[:8]:
            title = re.search(r"<title>(.*?)</title>", block, re.S)
            link = re.search(r"<link>(.*?)</link>", block, re.S)
            date = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
            if not (title and link):
                continue
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1)).strip()

            try:
                text = _article_text(link.group(1).strip())
            except FetchError:
                continue
            # Only the Baltic weekly quotes route codes; skip ordinary stories.
            if not re.search(r"\b(?:TD3C|TD20|TD7|BLNG1|BLPG1)\b", text):
                continue

            values = parse_routes(text)
            if not values:
                continue
            as_of = _rfc822(date.group(1)) if date else None
            new = 0
            for row_id, value in values.items():
                if row_id not in found:
                    found[row_id] = {"value": value, "as_of": as_of,
                                     "source_url": link.group(1).strip(),
                                     "headline": title}
                    new += 1
            if new:
                reports += 1
                log(f"  route_rates: +{new} from '{title[:44]}'")
            if reports >= max_articles:
                break

    if not found:
        raise FetchError("no parseable route rates in the recent weekly reports")
    log(f"  route_rates: {len(found)} routes: " + ", ".join(sorted(found)))
    return found
