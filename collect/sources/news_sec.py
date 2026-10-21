"""US-listed shipping company filings from SEC EDGAR.

Only 6-K and 8-K are kept: those are the "something just happened" filings
(earnings, fleet deals, contracts). Annual reports and ownership forms would
drown the feed.

SEC asks API users to identify themselves in the User-Agent. Set SEC_CONTACT to
an email if you want to be a good citizen at volume; the default is fine for a
once-a-day personal dashboard.
"""
import datetime as dt
import os

from http_util import FetchError, fetch_json

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"
INDEX_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"

CONTACT = os.environ.get("SEC_CONTACT", "shipping-brief educational dashboard")
HEADERS = {"User-Agent": CONTACT, "Host": "data.sec.gov"}

LOOKBACK_DAYS = 14


def fetch_sec(companies, forms, log=print):
    """Return recent 6-K/8-K filings across `companies`."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=LOOKBACK_DAYS)).date()
    out = []
    for comp in companies:
        try:
            data = fetch_json(SUBMISSIONS.format(cik=comp["cik"]), headers=HEADERS)
        except FetchError as exc:
            log(f"  sec: {comp['ticker']} unavailable — {exc}")
            continue

        recent = (data.get("filings") or {}).get("recent") or {}
        form_list = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accs = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        descs = recent.get("primaryDocDescription") or []

        kept = 0
        for i, form in enumerate(form_list):
            if form not in forms:
                continue
            try:
                filed = dt.date.fromisoformat(dates[i])
            except (ValueError, IndexError):
                continue
            if filed < cutoff:
                continue
            acc = accs[i] if i < len(accs) else ""
            doc = docs[i] if i < len(docs) else ""
            link = (
                FILING_URL.format(
                    cik_int=int(comp["cik"]), acc_nodash=acc.replace("-", ""), doc=doc
                )
                if acc and doc
                else INDEX_URL.format(cik=comp["cik"], form=form)
            )
            desc = (descs[i] if i < len(descs) else "") or form
            out.append({
                "title": f"{comp['name']}: {desc}",
                "link": link,
                "published": f"{filed.isoformat()}T12:00:00+00:00",
                "source": "SEC EDGAR",
                "ticker": comp["ticker"],
                "company": comp["name"],
                "category": form,
                "kind": "company",
            })
            kept += 1
        if kept:
            log(f"  sec: {comp['ticker']}: {kept} filings")
    return out
