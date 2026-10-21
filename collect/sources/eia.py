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

API = (
    "https://api.eia.gov/v2/{route}/data/"
    "?api_key={key}&frequency=daily&data[0]=value"
    "&facets[series][]={series}&sort[0][column]=period&sort[0][direction]=desc&length=2"
)

# metric id -> (EIA route, series id)
SERIES = {
    "brent": ("petroleum/pri/spt", "RBRTE"),
    "wti": ("petroleum/pri/spt", "RWTC"),
    "henry_hub": ("natural-gas/pri/fut", "RNGWHHD"),
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
        route, series = SERIES[mid]
        try:
            data = fetch_json(API.format(route=route, key=key, series=series))
        except FetchError as exc:
            log(f"  eia: {mid} unavailable — {exc}")
            continue

        rows = ((data or {}).get("response") or {}).get("data") or []
        points = []
        for row in rows:
            try:
                points.append((row["period"], float(row["value"])))
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
