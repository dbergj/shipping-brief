"""Live exchange quotes from CNBC's public quote service.

This exists because the dashboard spent 3 November showing Brent at $117.19
from 30 October. Brent was $105.85 that morning. Neither of the sources then in
front of it could do better:

  * Yahoo's chart endpoint is rate-limited to the point of uselessness from a
    shared CI runner — five consecutive runs returned 429 for every symbol,
    and it 429s this laptop too.
  * The daily oil report is syndicated to Hellenic only when it is syndicated.
    No price report appeared there on 2 November at all: the feed carried a
    UBS *forecast*, an ING commentary that said Brent closed "above $113", and
    a piece on pump prices. None of those quote a settlement, and the parser
    was right to reject all three.
  * EIA is official and three to five days in arrears by design.

CNBC's quote service is keyless, needs no registration, returns a precise
exchange timestamp, and supplies the change and the previous settlement itself
so none of it has to be derived. One request per symbol — comma-separated
batching silently returns a single junk row rather than an error.

The `name` field is the reason to prefer this over a bare price feed: the
service states which instrument it just priced, so the mapping can be
ASSERTED rather than trusted. Yahoo's `GOGL` resolving to a Google leveraged
ETF instead of Golden Ocean was a silent, plausible-looking wrong number, and
EXPECT below is how that class of error gets caught here instead of shipped.
"""
import datetime as dt
import re

from http_util import FetchError, fetch_json

API = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
       "?symbols={symbol}&requestMethod=itv&noform=1&partnerId=2&fund=1"
       "&exthrs=1&output=json")

# metric id -> (CNBC symbol, pattern the returned instrument name must match,
#               plausible range for the value)
#
# The name pattern is a hard assertion, not a hint: if CNBC ever repoints a
# symbol, the adapter drops the value and says so rather than publishing a
# number from the wrong instrument under our label.
EXPECT = {
    "brent":      ("@LCO.1", r"brent",                    (20.0, 400.0)),
    "wti":        ("@CL.1",  r"wti|west texas",           (20.0, 400.0)),
    "henry_hub":  ("@NG.1",  r"natural\s*gas",            (0.3, 60.0)),
    "coal_api2":  ("@ATW.1", r"api2|rotterdam\s*coal",    (20.0, 1000.0)),
    "gasoil":     ("@HO.1",  r"heating\s*oil|ulsd",       (0.3, 30.0)),
    "gasoline":   ("@RB.1",  r"rbob|gasoline",            (0.3, 30.0)),
    "bdry":       ("BDRY",   r"dry\s*bulk",               (1.0, 500.0)),
    "bwet":       ("BWET",   r"tanker",                   (1.0, 5000.0)),
}


def _num(raw):
    """CNBC formats numbers for display: '149,892', '+1.08', '+1.03%'."""
    if raw in (None, "", "-", "--", "N/A", "UNCH"):
        return None
    # float() already understands a leading minus; stripping the sign and
    # reapplying it by hand negated twice and turned -56.32 into +56.32.
    cleaned = str(raw).replace(",", "").replace("%", "").replace("+", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _when(quote):
    """The exchange timestamp, as an ISO string, or None.

    Two shapes appear: a full ISO instant for a live future
    ('2026-11-03T11:49:01.000+0100') and a bare date for an equity close
    ('2026-11-02'). A thin contract can supply neither — ICE API2 coal returns
    last_time null — and in that case this returns None and the caller DROPS
    the value. Stamping it with the collection time instead would invent an
    observation date, which is the exact failure this whole contract exists to
    prevent.
    """
    raw = quote.get("last_time") or quote.get("settleDate")
    if not raw:
        return None
    raw = str(raw).strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?"
                 r"([+-]\d{2}):?(\d{2})?", raw)
    if m:
        date, time, oh, om = m.groups()
        offset = dt.timedelta(hours=int(oh), minutes=int(om or 0) * (1 if int(oh) >= 0 else -1))
        naive = dt.datetime.fromisoformat(f"{date}T{time}")
        return (naive - offset).replace(tzinfo=dt.timezone.utc).isoformat(timespec="seconds")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return dt.datetime.fromisoformat(raw).replace(
            tzinfo=dt.timezone.utc).isoformat(timespec="seconds")
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _clean_change(change, value):
    """Drop a change that is really 'no previous close known'.

    ICE API2 coal comes back as last=142.15 with change=-142.15: the service is
    differencing against a previous close of zero. Published as-is that is a
    100% crash on the dashboard.
    """
    if change is None or value is None:
        return None
    if abs(abs(change) - abs(value)) < 0.01:
        return None
    return change


def fetch_cnbc(metric_ids, log=print):
    """Return {metric_id: {"value", "change", "as_of", "source", "instrument"}}."""
    out = {}
    for mid in metric_ids:
        if mid not in EXPECT:
            continue
        symbol, name_re, (lo, hi) = EXPECT[mid]
        try:
            data = fetch_json(API.format(symbol=symbol.replace("@", "%40")))
        except FetchError as exc:
            log(f"  cnbc: {mid} ({symbol}) unavailable — {exc}")
            continue

        try:
            quotes = data["FormattedQuoteResult"]["FormattedQuote"]
            quote = quotes[0] if isinstance(quotes, list) else quotes
        except (KeyError, IndexError, TypeError):
            log(f"  cnbc: {mid} ({symbol}) returned an unexpected shape")
            continue

        if quote.get("code") not in (0, "0"):
            log(f"  cnbc: {mid} — no such symbol {symbol}")
            continue

        name = str(quote.get("name") or quote.get("altName") or "")
        if not re.search(name_re, name, re.I):
            # The symbol resolved to something we did not ask for. Refuse it.
            log(f"  cnbc: REFUSING {mid} — {symbol} is '{name}', "
                f"which does not match /{name_re}/")
            continue

        value = _num(quote.get("last"))
        if value is None or not (lo <= value <= hi):
            log(f"  cnbc: {mid} — {value} is outside the plausible "
                f"{lo:g}–{hi:g} band for {name}")
            continue

        as_of = _when(quote)
        if not as_of:
            log(f"  cnbc: {mid} — {name} carries no exchange timestamp, skipping "
                f"rather than dating it with the collection time")
            continue

        out[mid] = {
            "value": value,
            "change": _clean_change(_num(quote.get("change")), value),
            "as_of": as_of,
            "source": f"{name} via CNBC",
            "instrument": name,
        }
        log(f"  cnbc: {mid} = {value:g} ({name}) at {as_of}")
    return out
