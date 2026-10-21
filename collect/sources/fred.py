"""Oil and gas prices from FRED (US Federal Reserve Bank of St. Louis).

A second official fallback behind Yahoo and EIA. It exists because the two
preceding options can each be unavailable for reasons outside this project:
Yahoo is undocumented and rate-limits hard, and EIA's key registration was
returning "access denied" when this was written. FRED is a separate institution
with its own free key, so all three failing at once is unlikely.

FRED republishes the same underlying EIA series, so the numbers agree; it
carries the same few days' publication lag, and the snapshot records the real
observation date either way.

Set FRED_API_KEY to enable. Free key: https://fredaccount.stlouisfed.org/apikey
"""
import os

from http_util import FetchError, fetch_json

API = ("https://api.stlouisfed.org/fred/series/observations"
       "?series_id={series}&api_key={key}&file_type=json"
       "&sort_order=desc&limit=4")

# metric id -> FRED series id
SERIES = {
    "brent": "DCOILBRENTEU",      # Brent, Europe, USD/barrel, daily
    "wti": "DCOILWTICO",          # WTI, Cushing, USD/barrel, daily
    "henry_hub": "DHHNGSP",       # Henry Hub spot, USD/mmBtu, daily
}


def available():
    return bool(os.environ.get("FRED_API_KEY"))


def fetch_fred(metric_ids, log=print):
    """Return {metric_id: {"value", "change", "as_of"}} for what it can get."""
    key = os.environ.get("FRED_API_KEY")
    if not key:
        log("  fred: no FRED_API_KEY set, fallback dormant")
        return {}

    out = {}
    for mid in metric_ids:
        series = SERIES.get(mid)
        if not series:
            continue
        try:
            data = fetch_json(API.format(series=series, key=key))
        except FetchError as exc:
            log(f"  fred: {mid} unavailable — {exc}")
            continue

        points = []
        for row in (data or {}).get("observations") or []:
            # FRED marks missing days with "." rather than omitting them.
            if row.get("value") in (None, ".", ""):
                continue
            try:
                points.append((row["date"], float(row["value"])))
            except (KeyError, TypeError, ValueError):
                continue
        if not points:
            log(f"  fred: {mid} returned no usable observations")
            continue

        date, value = points[0]
        change = round(value - points[1][1], 4) if len(points) > 1 else None
        out[mid] = {"value": value, "change": change, "as_of": f"{date}T00:00:00+00:00"}
        log(f"  fred: {mid} = {value} ({date})")
    return out
