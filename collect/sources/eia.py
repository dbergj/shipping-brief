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

# The /v2/seriesid/ route accepts a legacy series ID directly and resolves the
# category path itself. Using it avoids guessing the dataset hierarchy, which is
# where this adapter would otherwise be one wrong path segment from silence.
API = ("https://api.eia.gov/v2/seriesid/{series}"
       "?api_key={key}&sort[0][column]=period&sort[0][direction]=desc&length=4")

# metric id -> EIA series id
SERIES = {
    "brent": "RBRTE",       # Europe Brent spot FOB, USD/barrel, daily
    "wti": "RWTC",          # Cushing WTI spot FOB, USD/barrel, daily
    "henry_hub": "RNGWHHD",  # Henry Hub natural gas spot, USD/mmBtu, daily
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
        series = SERIES[mid]
        try:
            data = fetch_json(API.format(key=key, series=series))
        except FetchError as exc:
            log(f"  eia: {mid} unavailable — {exc}")
            continue

        rows = ((data or {}).get("response") or {}).get("data") or []
        points = []
        for row in rows:
            # The seriesid route names the number "value"; some datasets use the
            # series id as the column name instead.
            raw = row.get("value", row.get(series))
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
