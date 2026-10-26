"""Baltic Dry Index headline value, parsed from Hellenic Shipping News.

The official Baltic feed costs GBP 3,420/yr. Hellenic publishes a short daily
article whose title carries the number, e.g.
    "Baltic Dry Index climbs to 2680 up 87 points"
so we read their public RSS and pull the figure out of the title, falling back
to the article body. Only the headline BDI is available this way — the
sub-indices (BCI/BPI/BSI) are not, which is why those rows stay inactive.

Read from the site's *search* feed rather than the main one. The main feed holds
only the latest 20 articles across every topic, and Hellenic publishes enough
each day that the afternoon BDI story has scrolled off it by the time the 06:00
collector runs — which is exactly how this silently stopped updating. The search
feed contains only matching articles, so it spans roughly 14 trading days and
the number is always there.
"""
import datetime as dt
import re

from http_util import FetchError, fetch

# Only articles matching the query, so the daily number does not scroll away.
SEARCH_FEED = "https://www.hellenicshippingnews.com/?s=baltic+dry+index&feed=rss2"
MAIN_FEED = "https://www.hellenicshippingnews.com/feed/"

UP_WORDS = ("climb", "rise", "rose", "gain", "up", "jump", "advance", "increase", "higher")
DOWN_WORDS = ("fall", "fell", "drop", "slip", "decline", "down", "lose", "lost", "lower", "ease")


def _items(xml):
    for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
        title = re.search(r"<title>(.*?)</title>", block, re.S)
        link = re.search(r"<link>(.*?)</link>", block, re.S)
        body = re.search(r"<content:encoded>(.*?)</content:encoded>", block, re.S) or re.search(
            r"<description>(.*?)</description>", block, re.S
        )
        date = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
        clean = lambda m: re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1)).strip() if m else ""
        yield {
            "title": re.sub(r"<[^>]+>", "", clean(title)),
            "link": clean(link),
            "body": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", clean(body))),
            "date": clean(date),
        }


def _parse_value_and_change(text):
    """Pull (index level, change) out of a BDI sentence.

    The level is the number introduced by "to"; the change is the number
    attached to "points". Written defensively because the wording varies and a
    wrong number here is worse than no number.
    """
    low = text.lower()

    level = None
    m = re.search(r"\bto\s+([\d][\d,\.]*)", low)
    if m:
        level = m.group(1)
    else:
        # "...climbed 87 points, 2680 points" — take the largest plausible figure.
        nums = [n for n in re.findall(r"\b(\d[\d,]{2,6})\b", low)]
        if nums:
            level = max(nums, key=lambda n: int(n.replace(",", "")))
    if level is None:
        return None, None
    level = int(float(level.replace(",", "")))

    change = None
    m = re.search(r"\b(\d[\d,]*)\s*point", low)
    if m:
        change = int(float(m.group(1).replace(",", "")))
        # If "points" attached to the level itself, there is no change figure.
        if change == level:
            change = None
    if change is not None:
        tail = low[: m.start()] + low[m.end():]
        if any(w in tail for w in DOWN_WORDS) and not any(
            w in low.split("index", 1)[-1][:40] for w in UP_WORDS
        ):
            change = -change
        elif any(w in low for w in DOWN_WORDS) and not any(w in low for w in UP_WORDS):
            change = -change
    return level, change


def _scan(xml, log):
    """First item that yields a plausible index level. Feeds are newest-first.

    Hellenic runs two kinds of BDI story: a numeric one ("falls to 2628 down 32
    points") and a commentary one ("Snaps 4-Day Winning Run"). Only the first
    carries a usable figure; the plausibility band below rejects the other,
    where "4-Day" would otherwise be read as an index level.
    """
    for item in _items(xml):
        if "baltic dry index" not in item["title"].lower():
            continue
        level, change = _parse_value_and_change(item["title"])
        if level is None or not 100 <= level <= 20000:
            level, change = _parse_value_and_change(item["body"])
        if level is None:
            continue
        # Sanity band: the BDI has lived between ~200 and ~12,000 historically.
        if not 100 <= level <= 20000:
            log(f"  bdi: skipping '{item['title'][:50]}' — no plausible level in it")
            continue
        return {
            "value": level,
            "change": change,
            "as_of": _rfc822(item["date"]),
            "source_url": item["link"],
            "headline": item["title"],
        }
    return None


def fetch_bdi(log=print):
    """Return {"value", "change", "as_of", "source_url"} or raise FetchError."""
    for name, url in (("search", SEARCH_FEED), ("main", MAIN_FEED)):
        try:
            hit = _scan(fetch(url), log)
        except FetchError as exc:
            log(f"  bdi: {name} feed unavailable — {exc}")
            continue
        if hit:
            if name == "main":
                log("  bdi: search feed had nothing; fell back to the main feed")
            return hit
        log(f"  bdi: no usable article in the {name} feed")
    raise FetchError("no Baltic Dry Index article found in either Hellenic feed")


def _rfc822(s):
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(s).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001 - a bad date must not lose us the value
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
