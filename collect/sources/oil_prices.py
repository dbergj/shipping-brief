"""Brent and WTI read out of the daily oil market report, as prose.

EIA is official and authoritative, but it publishes spot prices three to five
days in arrears. For a morning brief that is useless and actively misleading:
on 31 October the newest EIA observation was 27 October, and the brief narrated
a four-day-old $123.10 as though crude had moved that morning.

The daily oil report is syndicated to Hellenic twice a day and quotes the
levels in its lead paragraph, in a shape that has not varied:

    "As of 05:45 ET (09:45 GMT), Brent crude futures rose 2.1% to $117.19 per
     barrel, while U.S. West Texas Intermediate (WTI) crude futures gained
     1.8% to $103.88 per barrel."

    "Brent crude futures, the global oil benchmark, fell by 1.4% to $115.04 a
     barrel by 06:42 ET (10:42 GMT), while U.S. West Texas Intermediate crude
     futures had declined by 2.1% to $101.13 a barrel."

These are ICE Brent and NYMEX WTI front-month futures, which is what the market
actually quotes — not the EIA spot series. The dashboard labels them as such.

Three traps, all of which appear in live articles and all of which are tested:

  * The intraday high: "Brent futures rose to as high as $117.84 per barrel
    earlier in the day". Taking the highest or the last match gets this.
  * Forecasts and history: "Brent is forecast to average $91", "Brent moved
    from below $60", "risen by 13% since the start of September to around $107".
  * Differentials and spreads: "the Brent spread is about $3", "Brent-family
    North Sea crude into Northwestern Europe is $10".

So: only articles whose title is a daily oil report are mined at all, every
match must be a movement verb followed by a preposition introducing a level and
then a per-barrel unit, a disqualifier list rejects the rest, and only the
FIRST surviving match per benchmark is used.
"""
import datetime as dt
import re

from dry_rates import _article_text
from http_util import FetchError, fetch

FEEDS = (
    "https://www.hellenicshippingnews.com/?s=oil+prices&feed=rss2",
    "https://www.hellenicshippingnews.com/?s=Brent+crude+futures&feed=rss2",
)

# Only a daily price report is mined. Articles that merely mention a Brent level
# in passing — a refining-margin analysis, a World Bank forecast, a Saudi output
# release — are skipped even though they parse, because their numbers are
# monthly averages, forecasts or differentials rather than today's level.
TITLE_OK = re.compile(
    r"^oil\b(?!\s+(?:tanker|product|major|trader|firm|giant|demand|output|"
    r"export|discover))", re.I)
TITLE_MOVE = re.compile(
    r"\b(?:rise|rises|rising|rose|climb|climbs|gain|gains|jump|jumps|surge|"
    r"surges|advance|advances|rebound|rebounds|up|higher|fall|falls|fell|drop|"
    r"drops|decline|declines|slip|slips|sink|sinks|sank|ease|eases|dip|dips|"
    r"retreat|retreats|lower|down|steady|steadies|hold|holds|hover|hovers|"
    r"flat|mixed|little changed|settle|settles)\b", re.I)

BENCHMARKS = {
    # Brent, but never "Brent-family", "Brent spread" or "Brent pricing".
    "brent": r"Brent(?:\s+(?:crude|oil))?(?:\s+(?:futures|prices))?"
             r"(?!\s*[-‐-―]|\s+spread|\s+pricing)",
    "wti": r"(?:U\.S\.\s+)?(?:West\s+Texas\s+Intermediate|WTI)(?:\s*\(WTI\))?"
           r"(?:\s+crude)?(?:\s+(?:futures|oil))?",
}

MOVE = (r"(?:rose|climbed|gained|jumped|advanced|surged|rebounded|fell|dropped|"
        r"declined|slipped|sank|eased|dipped|retreated|settled|closed|traded|"
        r"was\s+(?:up|down)|were\s+(?:up|down)|"
        r"had\s+(?:risen|declined|fallen|gained|slipped)|"
        r"is\s+trading|are\s+trading|currently\s+trading|last\s+traded)")

NUM = r"(\d{2,3}(?:\.\d{1,2})?)"
# Bounded gaps, never [^.]: a character class excluding "." cannot cross
# "2.1%" or "$117.19", and that mistake has already cost two adapters.
LEVEL = rf"(?:to|at)\s+\$\s*{NUM}"
UNIT = r"\s*(?:per\s+barrel|a\s+barrel|/\s*bbl|/b\b)"

# Phrases that mean the figure beside them is not today's traded level.
DISQUALIFY = re.compile(
    r"as\s+(?:high|low)\s+as|forecast|expect|project|averag|could|would|"
    r"might|since|earlier\s+in\s+the\s+(?:day|week)|last\s+year|spread|"
    r"differential|target|estimate|per\s+month|month-on-month|"
    r"end\s+of\s+the\s+month|for\s+the\s+week|outlook|scenario", re.I)

# USD per barrel. Wide enough for any real market, tight enough to reject a
# percentage, a spread of "$3", or a cargo value in millions.
BAND = (20.0, 400.0)

CLOCK = re.compile(r"\((\d{1,2}):(\d{2})\s*GMT\)", re.I)


def _benchmark_pattern(name_re):
    return re.compile(
        rf"(?:{name_re}).{{0,70}}?\b{MOVE}\b.{{0,40}}?{LEVEL}{UNIT}", re.I)


_COMPILED = {mid: _benchmark_pattern(rx) for mid, rx in BENCHMARKS.items()}


# A disqualifier can sit either side of the figure: "forecast to average $91"
# before it, "$103.5 per barrel at the end of the month" after it. The forward
# look stops at the end of the sentence, because the sentence that FOLLOWS a
# good match is very often the intraday high ("Brent futures rose to as high
# as $117.84"), and reading into it would veto the correct figure.
SENTENCE_END = re.compile(r"[.;:]\s")


def _span(text, match):
    tail = text[match.end():match.end() + 90]
    cut = SENTENCE_END.search(tail)
    if cut:
        tail = tail[:cut.start()]
    return text[max(0, match.start() - 40):match.end()] + tail


def parse_oil(text):
    """Return {metric_id: price} for each benchmark quoted as today's level."""
    out = {}
    for mid, pattern in _COMPILED.items():
        for m in pattern.finditer(text):
            if DISQUALIFY.search(_span(text, m)):
                continue
            try:
                value = float(m.group(1))
            except (TypeError, ValueError):
                continue
            if BAND[0] <= value <= BAND[1]:
                out[mid] = value
                break  # the FIRST clean match is the lead figure
    return out


def _pubdate(raw):
    """RFC-822 pubDate as an aware UTC datetime, or None.

    dry_rates._rfc822 returns an ISO string, which cannot be sorted against
    other dates or narrowed to a clock time, so this parses its own.
    """
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(raw).astimezone(dt.timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _observed_at(text, pub):
    """Article date, narrowed to the GMT clock time in the lead if it gives one."""
    if not pub:
        return None
    clock = CLOCK.search(text[:600])
    if not clock:
        return pub
    try:
        return pub.replace(hour=int(clock.group(1)), minute=int(clock.group(2)),
                           second=0, microsecond=0)
    except ValueError:
        return pub


def fetch_oil_prices(log=print, max_articles=10):
    """Return {metric_id: {"value", "as_of", "source_url", "headline"}}."""
    seen = {}
    for feed in FEEDS:
        try:
            xml = fetch(feed)
        except FetchError as exc:
            log(f"  oil_prices: feed unavailable — {exc}")
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
            seen[url] = (re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1)).strip(),
                         _pubdate(date.group(1)))

    reports = [(u, t, d) for u, (t, d) in seen.items()
               if d and TITLE_OK.match(t) and TITLE_MOVE.search(t)]
    reports.sort(key=lambda r: r[2], reverse=True)
    if not reports:
        raise FetchError("no daily oil report found in the recent feeds")

    out = {}
    for url, title, pub in reports[:max_articles]:
        try:
            text = _article_text(url)
        except FetchError:
            continue
        values = parse_oil(text)
        if not values:
            continue
        as_of = _observed_at(text, pub)
        new = [mid for mid in values if mid not in out]
        for mid in new:
            out[mid] = {"value": values[mid],
                        "as_of": as_of.isoformat(timespec="seconds"),
                        "source_url": url, "headline": title}
        if new:
            log(f"  oil_prices: {', '.join(f'{m}={values[m]}' for m in new)} "
                f"from {as_of:%d %b %H:%M}Z '{title[:40]}'")
        if len(out) == len(BENCHMARKS):
            break

    if not out:
        raise FetchError("daily oil reports found but no parseable level in them")
    return out
