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

# Summaries are shown on the page so the reader does not have to open every
# article, so keep enough text to be worth reading.
SUMMARY_CHARS = 420
DEFAULT_CAP = 6


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


def is_nordic(text, terms):
    """Whether a story has a Norwegian or Nordic angle."""
    low = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    return any(t in low for t in terms)


def parse_feed(xml, source_name, kind="rss", cap=DEFAULT_CAP, nordic_terms=()):
    blocks = re.findall(r"<entry[\s>](.*?)</entry>", xml, re.S) if kind == "atom" else []
    if not blocks:
        blocks = re.findall(r"<item[\s>](.*?)</item>", xml, re.S)
    if not blocks:  # some feeds use <item> with no attributes
        blocks = re.findall(r"<item>(.*?)</item>", xml, re.S)

    items = []
    for b in blocks[:cap]:
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
        # Some feeds repeat the headline as the description; that adds nothing.
        if summary[:60].strip().lower() == title[:60].strip().lower():
            summary = summary[len(title):].strip(" —–-:")

        items.append({
            "title": title,
            "link": link.strip(),
            "published": published,
            "summary": summary[:SUMMARY_CHARS],
            "source": source_name,
            "kind": "market",
            "nordic": is_nordic(f"{title} {summary}", nordic_terms),
        })
    return items


def fetch_feeds(feeds, log=print, nordic_terms=()):
    """Fetch every feed; a failure logs and is skipped.

    Each feed contributes at most its own `cap`. Without that the most prolific
    publisher supplies most of the page simply by posting more often, which is
    not the same thing as being more relevant.
    """
    out = []
    for feed in feeds:
        try:
            xml = fetch(feed["url"])
            items = parse_feed(xml, feed["name"], feed.get("kind", "rss"),
                               cap=feed.get("cap", DEFAULT_CAP), nordic_terms=nordic_terms)
            nordic = sum(1 for i in items if i["nordic"])
            log(f"  rss: {feed['name']}: {len(items)} items ({nordic} Nordic)")
            out.extend(items)
        except FetchError as exc:
            log(f"  rss: {feed['name']} unavailable — {exc}")
    return out
