"""Baltic Dry Index headline value, parsed from Hellenic Shipping News.

The official Baltic feed costs GBP 3,420/yr. Hellenic publishes a short daily
article whose title carries the number, e.g.
    "Baltic Dry Index climbs to 2680 up 87 points"
so we read their public RSS and pull the figure out of the title, falling back
to the article body. Only the headline BDI is available this way — the
sub-indices (BCI/BPI/BSI) are not, which is why those live in manual entry.
"""
import datetime as dt
import re

from http_util import FetchError, fetch

FEED = "https://www.hellenicshippingnews.com/feed/"

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


def fetch_bdi(log=print):
    """Return {"value", "change", "as_of", "source_url"} or raise FetchError."""
    xml = fetch(FEED)
    for item in _items(xml):
        if "baltic dry index" not in item["title"].lower():
            continue
        level, change = _parse_value_and_change(item["title"])
        if level is None:
            level, change = _parse_value_and_change(item["body"])
        if level is None:
            continue
        # Sanity band: the BDI has lived between ~200 and ~12,000 historically.
        if not 100 <= level <= 20000:
            log(f"  bdi: ignoring implausible value {level} from '{item['title']}'")
            continue
        return {
            "value": level,
            "change": change,
            "as_of": _rfc822(item["date"]),
            "source_url": item["link"],
            "headline": item["title"],
        }
    raise FetchError("no Baltic Dry Index article found in Hellenic feed")


def _rfc822(s):
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(s).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001 - a bad date must not lose us the value
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
