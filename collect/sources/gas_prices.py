"""European gas prices, read out of the daily wholesale gas report.

Dutch TTF is licensed by ICE and no free provider publishes it — CNBC, which
supplies every other exchange price here, has no TTF symbol at all (ten
candidates checked). But the daily gas commentary quotes it in prose, the same
way Reuters quotes the Baltic, so TTF is read from the sentence.

Two publishers carry it, and they write it differently:

    "The benchmark Dutch TTF front-month contract rose 3.6% to trade at
     97.19 euros per megawatt-hour (MWh)"

    "TTF settling 8.7% lower on the day at a little under EUR95/MWh"

Both forms are parsed. The unit is the anchor: a figure followed by a
euro-per-megawatt-hour unit in a European gas report is the TTF front month,
whether or not the sentence happens to name it. As everywhere else here, the
pattern anchors on a preposition that introduces a level (to / at / of) rather
than on the percentage change that usually precedes it.

Two things this file learned the hard way, on 3 November, when TTF had sat at a
five-session-old 96.29 while fresh quotes were published daily:

  * The article title is not a usable filter. The gate required "gas" in the
    title, and the piece carrying that day's TTF settlement was called "The
    Commodities Feed: False US-Iran hopes or the real deal?". It was skipped
    before anything was even read. The text decides now, not the headline.
  * Candidates must be sorted by date. The old version walked feed order and
    returned the first article that parsed, which is not necessarily the newest.

Where the source hedges ("a little under EUR95/MWh") the value is kept but
marked approximate, and the page renders it with a "≈" rather than implying a
precision the report did not claim.
"""
import email.utils as eu
import re

from dry_rates import _article_text, _rfc822
from http_util import FetchError, fetch

FEEDS = (
    "https://www.hellenicshippingnews.com/?s=TTF+euros+per+megawatt&feed=rss2",
    "https://www.hellenicshippingnews.com/?s=European+gas+prices+TTF&feed=rss2",
    "https://www.hellenicshippingnews.com/?s=TTF+MWh&feed=rss2",
    "https://www.hellenicshippingnews.com/?s=Commodities+Feed+European+gas&feed=rss2",
)

NUM = r"([\d]+(?:[.,]\d+)?)"
CUR = r"(?:€|EUR|euros?)"

# Split in two so the caller can tell an exact quote from a hedged one.
HEDGE = r"(?:a little |just |slightly |somewhat |marginally )?(?:under|over|above|below)\s+"
SOFT = r"(?:about|around|almost|nearly|roughly|close to|approximately)\s+"
QUALIFIER = rf"(?:{HEDGE}|{SOFT})?"

# European gas has traded between a few euros and ~340/MWh at the 2022 peak.
TTF_BAND = (1, 500)

PATTERNS = (
    # "to trade at 97.19 euros per megawatt-hour"
    rf"\b(?:to|at|of)\s+{QUALIFIER}{NUM}\s*euros?\s*per\s*megawatt",
    # "at 95 EUR/MWh"
    rf"\b(?:to|at|of)\s+{QUALIFIER}{NUM}\s*{CUR}\s*/?\s*MWh",
    # "at a little under EUR95/MWh"  — currency before the number
    rf"\b(?:to|at|of)\s+{QUALIFIER}{CUR}\s*{NUM}\s*/?\s*(?:MWh|per\s*megawatt)",
    # "to €95" — last, because a bare euro figure is the weakest evidence
    rf"\b(?:to|at|of)\s+{QUALIFIER}{CUR}\s*{NUM}\b",
)

APPROX = re.compile(rf"(?:{HEDGE}|{SOFT})$", re.I)

# A forecast, a target or a seasonal average is not today's level. These are
# currently rejected anyway, because none of them puts a bare preposition in
# front of the number ("forecast to average 70 euros"), but relying on that is
# relying on an accident of phrasing rather than on an intention.
DISQUALIFY = re.compile(
    r"forecast|expect|project|target|estimate|average|outlook|scenario|"
    r"next\s+(?:year|summer|winter|month)|by\s+20\d\d|per\s+cent\s+chance", re.I)


def _to_num(raw):
    try:
        return float(raw.replace(",", "."))
    except (AttributeError, ValueError):
        return None


def parse_ttf(text):
    """First quoted TTF level in the report, which is the current one.

    Returns {"value", "approximate"} or None. Later figures in these articles
    are the previous session's close or a historical peak, so only the first
    match is taken.
    """
    for pat in PATTERNS:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        value = _to_num(m.group(1))
        if value is None or not (TTF_BAND[0] <= value <= TTF_BAND[1]):
            continue
        if DISQUALIFY.search(text[max(0, m.start() - 60):m.end() + 40]):
            continue
        # Whatever sat between the preposition and the number tells us whether
        # the report committed to this figure or hedged it.
        lead = text[m.start():m.start(1)]
        return {"value": value, "approximate": bool(APPROX.search(lead.strip() + " ")
                                                    or re.search(HEDGE, lead, re.I)
                                                    or re.search(SOFT, lead, re.I))}
    return None


def _candidates(log):
    """Every article in the gas feeds, newest first, de-duplicated."""
    seen = {}
    for feed in FEEDS:
        try:
            xml = fetch(feed)
        except FetchError as exc:
            log(f"  gas_prices: feed unavailable — {exc}")
            continue
        for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
            link = re.search(r"<link>(.*?)</link>", block, re.S)
            title = re.search(r"<title>(.*?)</title>", block, re.S)
            date = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
            if not (link and title and date):
                continue
            url = link.group(1).strip()
            if url in seen:
                continue
            try:
                when = eu.parsedate_to_datetime(date.group(1))
            except (TypeError, ValueError):
                continue
            seen[url] = (re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1)).strip(),
                         date.group(1), when)
    return sorted(((u,) + v for u, v in seen.items()), key=lambda r: r[3], reverse=True)


def fetch_gas_prices(log=print, max_articles=10):
    """Return {"ttf": {"value", "as_of", "source_url", "headline"}} or {}."""
    candidates = _candidates(log)
    if not candidates:
        log("  gas_prices: no articles in the gas feeds")
        return {}

    tried = 0
    for url, title, raw_date, _when in candidates:
        if tried >= max_articles:
            break
        try:
            text = _article_text(url)
        except FetchError:
            continue
        # The TEXT decides, not the title: the piece carrying a day's TTF
        # settlement is often a general commodities column.
        low = text.lower()
        if "mwh" not in low and "megawatt" not in low:
            continue
        tried += 1
        found = parse_ttf(text)
        if not found:
            continue
        note = " (approximate — the report hedged it)" if found["approximate"] else ""
        log(f"  gas_prices: TTF {found['value']} EUR/MWh{note} from '{title[:44]}'")
        return {"ttf": {"value": found["value"],
                        "approximate": found["approximate"],
                        "as_of": _rfc822(raw_date),
                        "source_url": url,
                        "headline": title}}
    log(f"  gas_prices: no quoted TTF level in the {tried} most recent gas reports")
    return {}
