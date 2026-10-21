"""US-listed shipping company filings from SEC EDGAR.

Only 6-K and 8-K are kept: those are the "something just happened" filings.

Getting a readable headline takes a second step. The submissions feed describes
a 6-K only as "6-K" — useless on a dashboard — because the substance lives in an
exhibit (usually EX-99.1, the press release). So for each filing we open the
filing's index, find that exhibit, and lift its real headline and opening
sentence. Falling back to the form type only happens when that fails.

SEC asks API users to identify themselves in the User-Agent. Set SEC_CONTACT to
an email if you want to be a good citizen at volume; the default is fine for a
once-a-day personal dashboard.
"""
import datetime as dt
import html as html_mod
import json
import os
import pathlib
import re

from http_util import FetchError, fetch, fetch_json

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/"
INDEX_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"

CONTACT = os.environ.get("SEC_CONTACT", "shipping-brief educational dashboard")
DATA_HEADERS = {"User-Agent": CONTACT, "Host": "data.sec.gov"}
WWW_HEADERS = {"User-Agent": CONTACT}

LOOKBACK_DAYS = 14

# A filed document never changes, and the 14-day lookback means the same filings
# reappear every morning. Caching the extracted headline by accession number
# turns a daily re-crawl of a dozen documents into one fetch per new filing, and
# keeps us well-behaved towards SEC's servers. The file is committed, so CI
# benefits too.
CACHE_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "sec_cache.json"
CACHE_KEEP_DAYS = 90
# Each enriched filing costs two extra requests, and http_util spaces requests
# ~1.1s apart to stay polite. This caps the worst case at roughly a minute.
MAX_ENRICH = 18

# Cover-page boilerplate that appears before the real headline. Matched as whole
# lines...
BOILERPLATE = re.compile(
    r"^(ex[\s\-]?99|exhibit|exhibit\s*99\.?\d*|document|press release|form\s+6-k|"
    r"form\s+8-k|6-k|8-k|united states|securities and exchange commission|washington.*|"
    r"report of foreign private issuer|for the month of.*|commission file number.*|"
    r"\(.*\)|[\d.\s]+|.*\.html?)$",
    re.I,
)

# ...and as substrings, because some cover pages run the legal preamble together
# into one long line that a whole-line pattern would let through.
BOILERPLATE_PHRASES = (
    "report of foreign private issuer",
    "pursuant to rule 13a-16",
    "securities exchange act of 1934",
    "indicate by check mark",
    "translation of registrant",
    "address of principal executive",
    "exact name of registrant",
)

# Street addresses read like prose but are not headlines.
ADDRESS = re.compile(
    r"^\d+[,\s].{0,60}\b(street|st\.|avenue|ave\.|road|rd\.|boulevard|blvd|house|"
    r"place|square|drive|way|floor|suite|quay|strand|gate|veien|gata)\b", re.I)

# "HAMILTON, BERMUDA, October 12, 2026 - DHT Holdings, Inc." — a newswire
# dateline, not a headline. Some releases have no headline at all and open
# straight into the dateline, so the text after the dash plus the next sentence
# is the best available description.
DATELINE = re.compile(
    r"^[A-Z][\w .'\-]{1,40}(,\s*[A-Z][\w .'\-]{1,40}){0,2},?\s+"
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\.?\s+\d{1,2},?\s+\d{4}\s*[—–\-]\s*(?P<rest>.*)$",
    re.I,
)

# 8-K item codes carry real meaning; map the ones a shipping company uses.
ITEM_MEANINGS = {
    "1.01": "entered a material agreement",
    "1.02": "terminated a material agreement",
    "2.01": "completed an acquisition or disposal of assets",
    "2.02": "reported results",
    "2.03": "took on a financial obligation",
    "3.01": "listing or compliance notice",
    "3.02": "sold unregistered equity",
    "5.02": "board or senior management change",
    "5.07": "shareholder vote results",
    "7.01": "issued a regulation FD disclosure",
    "8.01": "made another material announcement",
}


def _text_lines(html):
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    t = re.sub(r"<[^>]+>", "\n", t)
    t = html_mod.unescape(t)
    out = []
    for line in t.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            out.append(line)
    return out


def _is_boilerplate(line):
    if BOILERPLATE.match(line):
        return True
    if ADDRESS.match(line):
        return True
    low = line.lower()
    return any(p in low for p in BOILERPLATE_PHRASES)


def _headline_from_exhibit(lines):
    """First line that reads like a press-release headline.

    Returns None when the filing is only a cover page, so the caller can fall
    back to a plain-English description instead of printing legal preamble.
    """
    for idx, line in enumerate(lines[:40]):
        if _is_boilerplate(line):
            continue
        if line.count("|") or line.lower().startswith(("title:", "date:", "time:")):
            continue

        dl = DATELINE.match(line)
        if dl:
            rest = dl.group("rest").strip(" -–—")
            # The dateline often ends with just the company name; the sentence
            # that follows carries the actual news.
            nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
            if len(rest) < 60 and nxt and not _is_boilerplate(nxt):
                rest = f"{rest} {nxt}".strip()
            rest = re.sub(r"\s*\((?:NYSE|Nasdaq|NASDAQ|OSE|OSL)[^)]*\)\s*", " ", rest)
            rest = re.sub(r"\s*\(\s*[\u201c\"][^)]*\)\s*", " ", rest)
            rest = re.sub(r"\s+", " ", rest).strip(" :;,")
            if 25 <= len(rest) <= 300:
                return rest[:300]
            continue

        if 25 <= len(line) <= 300:
            return line
    return None


def _summary_from_exhibit(lines, headline):
    started = False
    for line in lines[:60]:
        if line == headline:
            started = True
            continue
        if not started:
            continue
        if len(line) > 80:
            return line[:400]
    return None


def _load_cache():
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache, log):
    cutoff = (dt.date.today() - dt.timedelta(days=CACHE_KEEP_DAYS)).isoformat()
    pruned = {k: v for k, v in cache.items() if (v.get("filed") or "9999") >= cutoff}
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(pruned, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log(f"  sec: could not write cache — {exc}")


def _enrich(cik, accession, form, log):
    """Pull the real headline and opening line out of a filing's exhibit."""
    acc = accession.replace("-", "")
    base = ARCHIVE.format(cik_int=int(cik), acc=acc)
    try:
        listing = fetch_json(base + "index.json", headers=WWW_HEADERS)
    except FetchError as exc:
        log(f"  sec: could not list {accession} — {exc}")
        return None, None

    files = (listing.get("directory") or {}).get("item") or []
    htm = [f for f in files if f.get("name", "").lower().endswith((".htm", ".html"))
           and "index" not in f.get("name", "").lower()]
    if not htm:
        return None, None

    # Prefer an EX-99 exhibit — that is the press release. Otherwise take the
    # largest document, which is the one with actual content in it.
    def size(f):
        try:
            return int(f.get("size") or 0)
        except (TypeError, ValueError):
            return 0

    exhibits = [f for f in htm if re.search(r"ex(?:hibit)?[\-_ ]?99", f.get("name", ""), re.I)]
    if exhibits:
        target = max(exhibits, key=size)
    elif form == "8-K":
        # An 8-K's primary document states the item itself, so it is worth reading.
        target = max(htm, key=size)
    else:
        # A 6-K with no exhibit is a bare cover page. There is no news in it, and
        # mining it just yields the filer's address or the legal preamble — so
        # say plainly that a filing happened instead of inventing a headline.
        return None, None

    try:
        lines = _text_lines(fetch(base + target["name"], headers=WWW_HEADERS))
    except FetchError as exc:
        log(f"  sec: could not read {target['name']} — {exc}")
        return None, None

    headline = _headline_from_exhibit(lines)
    return headline, _summary_from_exhibit(lines, headline) if headline else None


def _describe_items(items):
    codes = [c.strip() for c in (items or "").split(",") if c.strip()]
    meanings = [ITEM_MEANINGS[c] for c in codes if c in ITEM_MEANINGS]
    return meanings[0] if meanings else None


def fetch_sec(companies, forms, log=print, enrich=True):
    """Return recent 6-K/8-K filings with readable headlines where possible."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=LOOKBACK_DAYS)).date()
    candidates = []

    for comp in companies:
        try:
            data = fetch_json(SUBMISSIONS.format(cik=comp["cik"]), headers=DATA_HEADERS)
        except FetchError as exc:
            log(f"  sec: {comp['ticker']} unavailable — {exc}")
            continue

        recent = (data.get("filings") or {}).get("recent") or {}
        forms_list = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accs = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        items = recent.get("items") or []

        for i, form in enumerate(forms_list):
            if form not in forms:
                continue
            try:
                filed = dt.date.fromisoformat(dates[i])
            except (ValueError, IndexError):
                continue
            if filed < cutoff:
                continue
            candidates.append({
                "company": comp, "form": form, "filed": filed,
                "acc": accs[i] if i < len(accs) else "",
                "doc": docs[i] if i < len(docs) else "",
                "items": items[i] if i < len(items) else "",
            })

    candidates.sort(key=lambda c: c["filed"], reverse=True)
    cache = _load_cache()
    out = []
    enriched = 0
    from_cache = 0
    for c in candidates:
        comp = c["company"]
        acc_nodash = c["acc"].replace("-", "")
        link = (ARCHIVE.format(cik_int=int(comp["cik"]), acc=acc_nodash) + c["doc"]
                if c["acc"] and c["doc"]
                else INDEX_URL.format(cik=comp["cik"], form=c["form"]))

        headline = summary = None
        cached = cache.get(c["acc"])
        if cached is not None:
            headline, summary = cached.get("headline"), cached.get("summary")
            from_cache += 1
        elif enrich and c["acc"] and enriched < MAX_ENRICH:
            headline, summary = _enrich(comp["cik"], c["acc"], c["form"], log)
            enriched += 1
            # Record misses too — a cover-page-only filing has no headline and
            # re-checking it every morning would be pure waste.
            cache[c["acc"]] = {"headline": headline, "summary": summary,
                               "filed": c["filed"].isoformat()}

        if not headline:
            # Last resort: say what kind of filing it is in plain English rather
            # than printing "6-K", which tells a reader nothing.
            what = _describe_items(c["items"])
            headline = (f"{comp['name']} {what}" if what
                        else f"{comp['name']} filed a {c['form']} "
                             f"({'periodic report' if c['form'] == '6-K' else 'material event'})")

        out.append({
            "title": headline,
            "summary": summary or "",
            "link": link,
            "published": f"{c['filed'].isoformat()}T12:00:00+00:00",
            "source": "SEC EDGAR",
            "ticker": comp["ticker"],
            "company": comp["name"],
            "category": c["form"],
            "kind": "company",
            "rank": 2,
        })

    if enriched:
        _save_cache(cache, log)
    log(f"  sec: {len(out)} filings — {enriched} newly read, {from_cache} from cache")
    return out
