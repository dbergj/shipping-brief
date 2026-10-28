"""European gas prices, read out of the daily wholesale gas report.

Dutch TTF is licensed by ICE and no free provider publishes it, which left the
most important European gas benchmark permanently blank. But the daily gas
market report quotes it in prose, the same way Reuters quotes the Baltic:

    "The benchmark Dutch TTF front-month contract rose 3.6% to trade at
     97.19 euros per megawatt-hour (MWh)"

So TTF is read from the sentence. The unit is the anchor: a figure followed by
"euros per megawatt-hour" in a European gas report is the TTF front month,
whether or not the sentence happens to name it.

As everywhere else here, the pattern anchors on a preposition that introduces a
level (to / at / of) rather than on the percentage change that usually precedes
it, and a sentence that does not parse yields nothing.
"""
import re

from dry_rates import _article_text, _rfc822
from http_util import FetchError, fetch

FEEDS = (
    "https://www.hellenicshippingnews.com/?s=TTF+euros+per+megawatt&feed=rss2",
    "https://www.hellenicshippingnews.com/?s=European+gas+prices+TTF&feed=rss2",
)

NUM = r"([\d]+(?:[.,]\d+)?)"
QUALIFIER = r"(?:about |around |almost |nearly |roughly )?"

# European gas has traded between a few euros and ~340/MWh at the 2022 peak.
TTF_BAND = (1, 500)

PATTERNS = (
    rf"\b(?:to|at|of)\s+{QUALIFIER}{NUM}\s*euros?\s*per\s*megawatt",
    rf"\b(?:to|at|of)\s+{QUALIFIER}{NUM}\s*(?:euros?|€)\s*/?\s*MWh",
    rf"\b(?:to|at|of)\s+{QUALIFIER}€\s*{NUM}",
)


def _to_num(raw):
    try:
        return float(raw.replace(",", "."))
    except (AttributeError, ValueError):
        return None


def parse_ttf(text):
    """First quoted TTF level in the report, which is the current one.

    Later figures in these articles are the previous session's close or a
    historical peak, so only the first match is taken.
    """
    for pat in PATTERNS:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        value = _to_num(m.group(1))
        if value is not None and TTF_BAND[0] <= value <= TTF_BAND[1]:
            return value
    return None


def fetch_gas_prices(log=print):
    """Return {"ttf": {"value", "as_of", "source_url", "headline"}} or {}."""
    for feed in FEEDS:
        try:
            xml = fetch(feed)
        except FetchError as exc:
            log(f"  gas_prices: feed unavailable — {exc}")
            continue

        for block in re.findall(r"<item>(.*?)</item>", xml, re.S)[:6]:
            title = re.search(r"<title>(.*?)</title>", block, re.S)
            link = re.search(r"<link>(.*?)</link>", block, re.S)
            date = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
            if not (title and link):
                continue
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1)).strip()
            # Only the daily wholesale report quotes a settlement level.
            if not re.search(r"gas", title, re.I):
                continue

            try:
                text = _article_text(link.group(1).strip())
            except FetchError:
                continue
            if "megawatt" not in text.lower() and "mwh" not in text.lower():
                continue

            value = parse_ttf(text)
            if value is None:
                continue
            log(f"  gas_prices: TTF {value} EUR/MWh from '{title[:44]}'")
            return {"ttf": {"value": value,
                            "as_of": _rfc822(date.group(1)) if date else None,
                            "source_url": link.group(1).strip(),
                            "headline": title}}
    log("  gas_prices: no quoted TTF level found in the recent gas reports")
    return {}
