"""Maritime trade-press headlines from public RSS and Atom feeds.

Maritime Executive serves Atom (<entry>/<updated>) while the rest serve RSS
(<item>/<pubDate>), so both shapes are handled. Regex rather than a parser
because these feeds are small and we want zero dependencies in CI; anything
malformed is skipped rather than aborting the run.
"""
import datetime as dt
import html as html_mod
import re
from email.utils import parsedate_to_datetime

from http_util import FetchError, fetch

MAX_PER_FEED = 12


def _clean(text):
    text = re.sub(r"<!\[CDATA\[|\]\]>", "", text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


def _iso(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        pass
    try:  # Atom uses ISO-8601
        return (
            dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            .astimezone(dt.timezone.utc)
            .isoformat(timespec="seconds")
        )
    except Exception:  # noqa: BLE001
        return None


def _first(block, *patterns):
    for pat in patterns:
        m = re.search(pat, block, re.S)
        if m:
            return m.group(1)
    return ""


def parse_feed(xml, source_name, kind="rss"):
    blocks = re.findall(r"<entry[\s>](.*?)</entry>", xml, re.S) if kind == "atom" else []
    if not blocks:
        blocks = re.findall(r"<item[\s>](.*?)</item>", xml, re.S)
    if not blocks:  # some feeds use <item> with no attributes
        blocks = re.findall(r"<item>(.*?)</item>", xml, re.S)

    items = []
    for b in blocks[:MAX_PER_FEED]:
        title = _clean(_first(b, r"<title[^>]*>(.*?)</title>"))
        if not title:
            continue
        # Atom puts the URL in an attribute; RSS in element text.
        link = _clean(_first(b, r"<link[^>]*>([^<]+)</link>")) or _first(
            b, r'<link[^>]*href="([^"]+)"'
        )
        published = _iso(
            _first(b, r"<pubDate>(.*?)</pubDate>", r"<updated>(.*?)</updated>",
                   r"<published>(.*?)</published>", r"<dc:date>(.*?)</dc:date>")
        )
        summary = _clean(
            _first(b, r"<description>(.*?)</description>", r"<summary[^>]*>(.*?)</summary>")
        )
        items.append({
            "title": title,
            "link": link.strip(),
            "published": published,
            "summary": summary[:320],
            "source": source_name,
            "kind": "market",
        })
    return items


def fetch_feeds(feeds, log=print):
    """Fetch every feed; a failure logs and is skipped."""
    out = []
    for feed in feeds:
        try:
            xml = fetch(feed["url"])
            items = parse_feed(xml, feed["name"], feed.get("kind", "rss"))
            log(f"  rss: {feed['name']}: {len(items)} items")
            out.extend(items)
        except FetchError as exc:
            log(f"  rss: {feed['name']} unavailable — {exc}")
    return out
