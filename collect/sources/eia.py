"""Official US Energy Information Administration prices — the oil/gas fallback.

Yahoo is undocumented and rate-limits hard (it will 429 an IP that asks too
often, which is a real risk on shared CI runners). EIA is an official
government API, free with a registered key, and does not rate-limit at this
volume. It is the authoritative source for Brent, WTI and Henry Hub; it has no
equivalent for TTF, coal or equities, so it backs up the core three only.

Set EIA_API_KEY to enable. Without a key this adapter stays dormant rather than
failing the run. Register at https://www.eia.gov/opendata/register.php

EIA spot series publish with a few days' lag, so values here are "last official
close", not today's — the snapshot records the real as_of either way.
"""
import os

from http_util import FetchError, fetch_json

# The /v2/seriesid/ route takes a legacy v1 series ID and resolves the category
# path itself. The ID must be fully qualified — "PET.RBRTE.D", not "RBRTE" —
# which is what a bare ID returning 404 taught us. Both spellings are tried, so
# the adapter self-heals if EIA ever accepts the short form.
API = ("https://api.eia.gov/v2/seriesid/{series}"
       "?api_key={key}&sort[0][column]=period&sort[0][direction]=desc&length=4")

# metric id -> candidate series IDs, most-likely first.
SERIES = {
    # Europe Brent spot FOB, USD/barrel, daily
    "brent": ("PET.RBRTE.D", "RBRTE"),
    # Cushing WTI spot FOB, USD/barrel, daily
    "wti": ("PET.RWTC.D", "RWTC"),
    # Henry Hub natural gas spot, USD/mmBtu, daily
    "henry_hub": ("NG.RNGWHHD.D", "RNGWHHD"),
}


def available():
    return bool(os.environ.get("EIA_API_KEY"))


def fetch_eia(metric_ids, log=print):
    """Return {metric_id: {"value", "change", "as_of"}} for what it can get."""
    key = os.environ.get("EIA_API_KEY")
    if not key:
        log("  eia: no EIA_API_KEY set, fallback dormant")
        return {}

    out = {}
    for mid in metric_ids:
        if mid not in SERIES:
            continue
        data = None
        last_error = None
        for series in SERIES[mid]:
            try:
                data = fetch_json(API.format(key=key, series=series))
                break
            except FetchError as exc:
                last_error = exc
                # A 404 means this spelling of the id is wrong; try the next.
                if getattr(exc, "status", None) != 404:
                    break
        if data is None:
            log(f"  eia: {mid} unavailable — {last_error}")
            continue

        rows = ((data or {}).get("response") or {}).get("data") or []
        points = []
        for row in rows:
            # The seriesid route names the number "value"; some datasets use the
            # series id as the column name instead.
            raw = row.get("value")
            if raw is None:
                # Some datasets name the column after the series instead.
                for k, v in row.items():
                    if k not in ("period", "series", "seriesDescription", "units"):
                        raw = v
                        break
            if raw in (None, "", "."):
                continue
            try:
                points.append((row["period"], float(raw)))
            except (KeyError, TypeError, ValueError):
                continue
        if not points:
            log(f"  eia: {mid} returned no usable rows")
            continue

        period, value = points[0]
        change = round(value - points[1][1], 4) if len(points) > 1 else None
        out[mid] = {"value": value, "change": change, "as_of": f"{period}T00:00:00+00:00"}
        log(f"  eia: {mid} = {value} ({period})")
    return out
