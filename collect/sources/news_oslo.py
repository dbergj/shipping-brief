"""Company announcements from Euronext Oslo Børs' NewsWeb API.

This is real regulatory disclosure — the same filings a professional reads —
rather than trade-press commentary, which makes it the better basis for the
"company news" section. The public JSON API carries the ticker in `issuerSign`,
so we pull the whole recent list once and keep the shipping names.

Two limits worth knowing, both established by testing:
  - newsweb.oslobors.no/rss is a JavaScript app shell, not a feed. The API
    below is the working path.
  - the list endpoint only ever returns roughly the current day's messages.
    `issuer=<id>` filters that same day rather than giving history, and
    fromDate combined with issuer returns nothing. So there is no backfill:
    the collector must run daily and accumulate, which run.py does.
"""
import datetime as dt

from http_util import FetchError, fetch_json

LIST_URL = "https://api3.oslo.oslobors.no/v1/newsreader/list?category=&issuer=&messageTitle="
ISSUERS_URL = "https://api3.oslo.oslobors.no/v1/newsreader/issuers"
MESSAGE_URL = "https://newsweb.oslobors.no/message/{id}"


def resolve_issuers(tickers, inactive=None, log=print):
    """Check our ticker list against the exchange's own issuer directory.

    Returns {sign: {"id", "name", "active"}}. Logs anything we ask for that the
    exchange does not list, which is how the ADSC/COOL and Golden Ocean
    problems surfaced — those would otherwise just produce no news forever.
    """
    try:
        data = fetch_json(ISSUERS_URL, method="POST", body=b"{}")
    except FetchError as exc:
        log(f"  oslo: issuer directory unavailable ({exc}); skipping validation")
        return {}

    found = {}
    for entry in ((data or {}).get("data") or {}).get("issuers") or []:
        sign = (entry.get("issuerSign") or entry.get("symbol") or entry.get("id") or "").upper()
        if not sign:
            continue
        # Prefer the active record when a sign has been reused.
        if sign not in found or entry.get("isActive"):
            found[sign] = {
                "id": entry.get("issuerId"),
                "name": entry.get("name"),
                "active": bool(entry.get("isActive")),
            }

    resolved, unknown, dormant = {}, [], []
    for sign in tickers:
        hit = found.get(sign.upper())
        if not hit:
            unknown.append(sign)
        else:
            resolved[sign.upper()] = hit
            if not hit["active"]:
                dormant.append(sign)
    if unknown:
        log(f"  oslo: not in exchange directory, will never yield news: {', '.join(unknown)}")
    if dormant:
        log(f"  oslo: listed but inactive: {', '.join(dormant)}")
    for sign, why in (inactive or {}).items():
        log(f"  oslo: skipping {sign} — {why}")
    return resolved


def _iso(raw):
    if not raw:
        return None
    try:
        return (
            dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            .astimezone(dt.timezone.utc)
            .isoformat(timespec="seconds")
        )
    except Exception:  # noqa: BLE001
        return None


def fetch_oslo(tickers, log=print, rank_map=None, routine=(), routine_rank=8):
    """Return announcements whose issuerSign is in `tickers` (dict or set).

    Each announcement carries a `rank` and a `routine` flag derived from the
    exchange's own category. A bank crossing a 5% ownership threshold is a
    legally required filing but it is not the day's news, and on a thin day
    those notifications would otherwise be the entire company feed.
    """
    rank_map = rank_map or {}
    data = fetch_json(LIST_URL)
    messages = ((data or {}).get("data") or {}).get("messages") or []
    if not messages:
        raise FetchError("Oslo NewsWeb returned no messages")

    out = []
    for m in messages:
        sign = (m.get("issuerSign") or "").upper()
        if sign not in tickers:
            continue
        cats = [c.get("category_en") or c.get("category_no") for c in (m.get("category") or [])]
        cats = [c for c in cats if c]
        category = cats[0] if cats else None
        is_routine = any(c in routine for c in cats)
        rank = routine_rank if is_routine else min(
            (rank_map.get(c, 4) for c in cats), default=4)

        out.append({
            "title": (m.get("title") or "").strip(),
            "link": MESSAGE_URL.format(id=m.get("messageId") or m.get("id")),
            "published": _iso(m.get("publishedTime")),
            "source": "Oslo Børs",
            "ticker": sign,
            "company": m.get("issuerName") or (tickers.get(sign) if isinstance(tickers, dict) else sign),
            "category": _pretty(category),
            "kind": "company",
            "routine": is_routine,
            "rank": rank,
            "nordic": True,
        })
    n_routine = sum(1 for o in out if o["routine"])
    log(f"  oslo: {len(out)} shipping announcements out of {len(messages)} messages "
        f"({n_routine} routine)")
    return out


def _pretty(category):
    """Exchange categories are shouted in full legalese; shorten for display."""
    if not category:
        return None
    mapping = {
        "MAJOR SHAREHOLDINGS NOTIFICATION": "Shareholding disclosure",
        "ADDITIONAL REGULATED INFORMATION REQUIRED TO BE DISCLOSED UNDER THE LAWS OF A MEMBER STATE":
            "Regulated information",
        "ACQUISITION OR DISPOSAL OF AN ISSUER'S OWN SHARES": "Own-share transaction",
        "HALF YEAR FINANCIAL REPORT": "Half-year report",
        "ANNUAL FINANCIAL REPORT": "Annual report",
        "NON-REGULATORY PRESS RELEASES": "Press release",
        "INSIDE INFORMATION": "Inside information",
        "PROSPECTUS / ADMISSION DOCUMENT": "Prospectus",
        "ADJUSTMENT OF INTEREST RATE": "Interest rate adjustment",
    }
    return mapping.get(category, category.title())
