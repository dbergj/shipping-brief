#!/usr/bin/env python3
"""Build the morning brief.

Runs every adapter, merges the results with yesterday's snapshot, and writes
data/latest.json plus a dated file in data/history/.

The contract that keeps the dashboard trustworthy:
  - an adapter failing never aborts the run and never blanks a number;
  - the previous value is carried forward and flagged stale with its original
    timestamp, so the page can grey it out and say how old it is;
  - nothing is ever invented. A number with no source is null and renders as
    inactive, which is how the paid Baltic routes behave.

Usage:
    python3 collect/run.py            # write the snapshot
    python3 collect/run.py --dry-run  # print what it found, write nothing
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import textwrap

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent),
                str(pathlib.Path(__file__).resolve().parent / "sources")]

import brief as brief_mod  # noqa: E402
import registry as reg  # noqa: E402
from bdi import fetch_bdi  # noqa: E402
from bunkers import fetch_all_grades  # noqa: E402
from dry_rates import fetch_dry_rates  # noqa: E402
from gas_prices import fetch_gas_prices  # noqa: E402
from route_rates import fetch_route_rates  # noqa: E402
from oil_prices import fetch_oil_prices  # noqa: E402
from eia import fetch_eia  # noqa: E402
from fred import fetch_fred  # noqa: E402
from news_oslo import fetch_oslo, resolve_issuers  # noqa: E402
from news_rss import fetch_feeds  # noqa: E402
from news_sec import fetch_sec  # noqa: E402
from yahoo import fetch_quotes  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LATEST = DATA / "latest.json"
HISTORY = DATA / "history"
ARCHIVE = DATA / "news_archive.json"
STORYLINES = DATA / "storylines.json"
SPEND = DATA / "brief_spend.json"
MANUAL = DATA / "manual" / "rates.json"

ARCHIVE_DAYS = 30
NEWS_SHOWN = 40

# A story with no development for this long is closed automatically. Ageing is
# done here rather than by the model: it is a calendar rule, and leaving it to
# the model invites "tensions continue" filler to keep a dead story alive.
STORY_STALE_DAYS = 12
STORY_DROP_DAYS = 30

errors = []


def log(msg):
    print(msg)


def note_error(where, exc):
    errors.append(f"{where}: {exc}")
    log(f"  !! {where} failed: {exc}")


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def age_days(iso):
    if not iso:
        return None
    try:
        then = dt.datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - then).total_seconds() / 86400


# --------------------------------------------------------------------------- #
# Dating and freshness
# --------------------------------------------------------------------------- #
#
# Every published number carries two dates, and they answer different questions:
#
#   as_of        — when the market printed this figure
#   collected_at — when this run fetched it
#
# They are usually different, and conflating them is exactly what let a
# four-day-old Brent print of $123.10 be narrated as a move that had happened
# that morning. Nothing reaches the page or the brief without both.

FRESH_CURRENT = "current"   # newest observation is within this row's cadence
FRESH_LAGGED = "lagged"     # source answered; it publishes in arrears
FRESH_STALE = "stale"       # this run could not refresh it; value carried forward
FRESH_UNKNOWN = "unknown"   # no value, or no date on the value


def age_sessions(as_of_iso, collected_at_iso):
    """Trading sessions between an observation and now — weekends excluded.

    A Monday-morning brief quoting Friday's close is one session behind, which
    is exactly right; measured in calendar days it looks three days late and
    would be flagged every Monday. Conversely a three-day gap mid-week really
    does mean a day was missed. Public holidays are not modelled — the cost of
    that is an occasional "lagged" badge on a day the market was shut, which
    errs toward telling the reader a number is old rather than hiding it.
    """
    observed = _parse_iso(as_of_iso)
    now = _parse_iso(collected_at_iso)
    if observed is None or now is None:
        return None
    start, end = observed.date(), now.date()
    if end <= start:
        return 0
    sessions, day = 0, start
    for _ in range((end - start).days):
        day += dt.timedelta(days=1)
        if day.weekday() < 5:
            sessions += 1
    return sessions


def _parse_iso(iso):
    if not iso:
        return None
    try:
        then = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return then.replace(tzinfo=dt.timezone.utc) if then.tzinfo is None else then


def as_of_label(iso):
    """"Fri 30 Oct 09:45 GMT", or "Fri 30 Oct" when the source gives no clock.

    Midnight is treated as "no clock" rather than as 00:00, because a daily
    series dated 2026-10-27T00:00:00Z is an end-of-day figure, not one struck
    at midnight, and printing a time there would invent a precision the source
    never claimed.
    """
    then = _parse_iso(iso)
    if then is None:
        return None
    stamp = then.strftime("%a %d %b")
    if (then.hour, then.minute) != (0, 0):
        stamp += then.strftime(" %H:%M GMT")
    return stamp


def stamp_dates(row, *, max_age_days, collected_at, refreshed=None):
    """Attach age, a human observation date, and a freshness verdict to a row.

    This is the only place any of those four fields is set, so the page, the
    brief and the logs cannot disagree about how current a figure is.
    """
    if refreshed is None:
        refreshed = bool(row.get("refreshed", row.get("value") is not None))
    row["collected_at"] = collected_at
    row["as_of_label"] = as_of_label(row.get("as_of"))
    row["max_sessions"] = max_age_days

    age = age_days(row.get("as_of"))
    row["age_days"] = round(age, 2) if age is not None else None
    sessions = age_sessions(row.get("as_of"), collected_at)
    row["age_sessions"] = sessions

    today = _parse_iso(collected_at)
    observed = _parse_iso(row.get("as_of"))
    row["observed_today"] = bool(
        today and observed and observed.date() == today.date())

    if row.get("value") is None or sessions is None:
        row["freshness"] = FRESH_UNKNOWN
    elif not refreshed:
        row["freshness"] = FRESH_STALE
    elif sessions > max_age_days:
        row["freshness"] = FRESH_LAGGED
    else:
        row["freshness"] = FRESH_CURRENT

    # Kept for the existing page and brief code, derived from the one verdict
    # above so the two can never drift apart.
    row["stale"] = row["freshness"] == FRESH_STALE
    row["lagged"] = row["freshness"] == FRESH_LAGGED
    return row


# --------------------------------------------------------------------------- #
# Prices
# --------------------------------------------------------------------------- #
def collect_metrics(previous, collected_at=None):
    """Return the metric list, carrying forward anything we could not refresh."""
    prev_by_id = {m["id"]: m for m in previous.get("metrics", [])}
    # One timestamp for the whole snapshot: if prices and rates each stamped
    # their own, two rows collected seconds apart could land on different
    # calendar days at midnight and disagree about what "today" means.
    collected_at = collected_at or now_iso()

    quotes = {}
    yahoo_symbols = [m["symbol"] for m in reg.METRICS if m["source"] == "yahoo"]
    if yahoo_symbols:
        try:
            quotes = fetch_quotes(yahoo_symbols, log=log)
        except Exception as exc:  # noqa: BLE001 - adapter must never kill the run
            note_error("yahoo", exc)
        # fetch_quotes returns partial results rather than raising, so a total
        # wipeout (a rate-limited IP, say) would otherwise pass silently.
        if not quotes:
            note_error("yahoo", f"no quotes returned for any of {len(yahoo_symbols)} symbols")
        elif len(quotes) < len(yahoo_symbols):
            log(f"  yahoo: partial — {len(quotes)}/{len(yahoo_symbols)} symbols")

    # Official fallback for the core three, used only where Yahoo came back empty.
    yahoo_by_id = {m["id"]: quotes.get(m.get("symbol")) for m in reg.METRICS}
    need_fallback = [mid for mid, q in yahoo_by_id.items() if not q]
    eia_values = {}
    if need_fallback:
        try:
            eia_values = fetch_eia(need_fallback, log=log)
        except Exception as exc:  # noqa: BLE001
            note_error("eia", exc)
        # Third line of defence. Yahoo can rate-limit and EIA's key service has
        # its own outages, so a second official provider is worth the 40 lines.
        still_missing = [m for m in need_fallback if m not in eia_values]
        if still_missing:
            try:
                eia_values.update(fetch_fred(still_missing, log=log))
            except Exception as exc:  # noqa: BLE001
                note_error("fred", exc)

    bunkers = {}
    try:
        grades = fetch_all_grades(log=log)
        bunkers = grades.get("VLSFO", {})
        log(f"  bunkers: VLSFO for {len(bunkers)} ports")
    except Exception as exc:  # noqa: BLE001
        note_error("bunkers", exc)

    dry = {}
    try:
        dry = fetch_dry_rates(log=log)
    except Exception as exc:  # noqa: BLE001
        note_error("dry_rates", exc)

    # Tanker, LNG and LPG routes, from the Baltic weekly commentary.
    try:
        dry.update(fetch_route_rates(log=log))
    except Exception as exc:  # noqa: BLE001
        note_error("route_rates", exc)

    gas = {}
    try:
        gas = fetch_gas_prices(log=log)
    except Exception as exc:  # noqa: BLE001
        note_error("gas_prices", exc)

    # Brent and WTI from the daily oil report. This outranks EIA because EIA
    # publishes spot in arrears: a morning brief needs the level the market
    # last traded at, timestamped, not a correct figure from four days ago.
    oil = {}
    try:
        oil = fetch_oil_prices(log=log)
    except Exception as exc:  # noqa: BLE001
        note_error("oil_prices", exc)

    bdi = None
    try:
        bdi = fetch_bdi(log=log)
        log(f"  bdi: {bdi['value']} ({bdi['change']:+d})" if bdi.get("change") is not None
            else f"  bdi: {bdi['value']}")
    except Exception as exc:  # noqa: BLE001
        note_error("bdi", exc)

    out = []
    for spec in reg.METRICS:
        fresh = None
        if spec["source"] == "yahoo":
            q = quotes.get(spec["symbol"])
            if q:
                fresh = {"value": q["value"], "change": q.get("change"),
                         "as_of": q["as_of"], "source": f"yahoo:{spec['symbol']}"}
        elif spec["source"] == "bunkers":
            b = bunkers.get(spec["port"])
            if b:
                fresh = {"value": b["value"], "change": b.get("change"),
                         "as_of": b["as_of"], "source": "shipandbunker.com"}
        if fresh is None and spec["id"] in oil:
            o = oil[spec["id"]]
            fresh = {"value": o["value"], "change": None, "as_of": o["as_of"],
                     "source": "ICE/NYMEX front-month via daily oil report",
                     "source_url": o.get("source_url")}

        if fresh is None and spec["id"] in eia_values:
            e = eia_values[spec["id"]]
            fresh = {"value": e["value"], "change": e.get("change"),
                     "as_of": e["as_of"], "source": "EIA (official)"}

        if spec["id"] in gas and fresh is None:
            g = gas[spec["id"]]
            fresh = {"value": g["value"], "change": None, "as_of": g["as_of"],
                     "source": "Daily European gas report",
                     "source_url": g.get("source_url")}

        if spec["source"] == "dry_rates" and spec["id"] in dry and fresh is None:
            d = dry[spec["id"]]
            fresh = {"value": d["value"], "change": None, "as_of": d["as_of"],
                     "source": "Baltic Exchange via Reuters/Hellenic",
                     "source_url": d.get("source_url")}

        if spec["source"] == "bdi" and fresh is None:
            if bdi:
                fresh = {"value": bdi["value"], "change": bdi.get("change"),
                         "as_of": bdi["as_of"],
                         "source": "Baltic Exchange via Hellenic Shipping News",
                         "source_url": bdi.get("source_url")}
            elif "bdi" in dry:
                d = dry["bdi"]
                fresh = {"value": d["value"], "change": None, "as_of": d["as_of"],
                         "source": "Baltic Exchange via Reuters/Hellenic",
                         "source_url": d.get("source_url")}

        row = {k: spec[k] for k in ("id", "label", "unit", "group") if k in spec}
        row["blurb"] = spec.get("blurb", "")
        row["kpi"] = bool(spec.get("kpi"))
        for k in ("fallback", "no_free_source"):
            if spec.get(k):
                row[k] = spec[k]

        if fresh:
            # The prose sources quote a level but no change, which left the
            # brief saying "change unknown" for crude — the one thing a morning
            # brief most needs. Derive it against the last stored observation,
            # but only when that observation is from a genuinely earlier moment,
            # so a re-read of the same figure never shows a move of zero as if
            # the market had been flat.
            # Only against the SAME source. EIA quotes Brent *spot* while the
            # oil report quotes front-month *futures*; differencing one against
            # the other produces a move of several dollars that no market made.
            # A missing change is honest, a fabricated one is not.
            old_row = prev_by_id.get(spec["id"])
            if (fresh.get("change") is None and old_row
                    and isinstance(old_row.get("value"), (int, float))
                    and old_row.get("source") == fresh.get("source")
                    and old_row.get("as_of") and fresh.get("as_of")
                    and old_row["as_of"] < fresh["as_of"]):
                fresh["change"] = round(fresh["value"] - old_row["value"], 4)
                fresh["change_since"] = old_row["as_of"]
                fresh["change_since_label"] = as_of_label(old_row["as_of"])
            row.update(fresh)
            row["stale"] = False
            # The source answered this run. Whether its newest observation is a
            # few days old is a property of the source, not a failure here.
            row["refreshed"] = True
        else:
            old = prev_by_id.get(spec["id"])
            if old and old.get("value") is not None:
                # Carry forward rather than blanking, and say that we did.
                row.update({"value": old["value"], "change": None,
                            "as_of": old.get("as_of"), "source": old.get("source")})
                row["stale"] = True
                row["refreshed"] = False
                log(f"  carried forward {spec['id']} from {old.get('as_of')}")
            else:
                row.update({"value": None, "change": None, "as_of": None, "source": None})
                row["stale"] = True
                row["refreshed"] = False

        stamp_dates(row, max_age_days=spec.get("max_age_days", 4),
                    collected_at=collected_at, refreshed=row.get("refreshed"))
        out.append(row)

    out.extend(derive_metrics(out, collected_at))
    return out, dry


def derive_metrics(metrics, collected_at):
    """Metrics computed from other metrics — no extra source to fail.

    A derived value is only as fresh as its stalest input, so both the
    observation date and the freshness verdict propagate rather than quietly
    presenting old arithmetic as current. The as_of taken is the OLDER of the
    two inputs: a spread is only as of the moment both legs were last priced.
    """
    by_id = {m["id"]: m for m in metrics}
    rows = []
    for spec in reg.DERIVED:
        inputs = [by_id.get(i) for i in spec["inputs"]]
        row = {
            "id": spec["id"], "label": spec["label"], "unit": spec["unit"],
            "group": spec["group"], "blurb": spec.get("blurb", ""), "kpi": False,
            "value": None, "change": None, "as_of": None, "source": None,
            "stale": True, "age_days": None, "derived_from": spec["inputs"],
        }
        if all(i and isinstance(i.get("value"), (int, float)) for i in inputs):
            if spec["kind"] == "crack":
                product, crude = inputs
                # Product futures quote $/gallon; crude quotes $/barrel.
                value = product["value"] * reg.GALLONS_PER_BARREL - crude["value"]
            else:
                value = inputs[0]["value"] - inputs[1]["value"]
            row.update({
                "value": round(value, 2),
                "as_of": min((i["as_of"] for i in inputs if i.get("as_of")), default=None),
                "source": "derived: " + " − ".join(spec["inputs"]),
            })
            # A derived row is never fresher than its worst input, so it
            # inherits that verdict instead of being re-judged on its own age.
            worst = max(inputs, key=lambda i: i.get("age_days") or 0)
            stamp_dates(row, max_age_days=worst.get("max_sessions", 1),
                        collected_at=collected_at,
                        refreshed=all(not i.get("stale") for i in inputs))
            row["derived_freshness_from"] = worst["id"]
            log(f"  derived: {spec['id']} = {row['value']} {spec['unit']} "
                f"({row['freshness']}, as at {row['as_of_label']})")
        else:
            missing = [s for s, i in zip(spec["inputs"], inputs)
                       if not (i and isinstance(i.get("value"), (int, float)))]
            log(f"  derived: {spec['id']} unavailable — needs {', '.join(missing)}")
            stamp_dates(row, max_age_days=4, collected_at=collected_at,
                        refreshed=False)
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Freight routes that need a paid Baltic subscription
# --------------------------------------------------------------------------- #
def collect_rates(dry=None, collected_at=None):
    """Build the rate table. Rows with no number render as inactive.

    Official Baltic route assessments cost GBP 3,420/yr, so these are inactive
    by default. They still carry their label and explanation, because knowing
    what a VLCC TD3C voyage is has teaching value with or without today's
    number. Drop a value into data/manual/rates.json and the row activates.
    """
    manual = load_json(MANUAL, {})
    values = manual.get("rates", {}) if isinstance(manual, dict) else {}
    dry = dry or {}
    collected_at = collected_at or now_iso()

    rows = []
    for spec in reg.MANUAL_RATES:
        raw = values.get(spec["id"])
        cadence = spec.get("cadence", "weekly")
        row = {
            "id": spec["id"], "label": spec["label"], "unit": spec["unit"],
            "segment": spec["segment"], "blurb": spec.get("blurb", ""),
            "cadence": cadence,
            "value": None, "as_of": manual.get("as_of"), "source": None,
            "status": "unavailable",
            "why_unavailable": "Not quoted in the latest published market report. The "
                               "Baltic assessment itself is a paid product; this row "
                               "fills whenever the figure appears in the public "
                               "commentary.",
        }
        # A figure quoted in the daily Reuters report beats a hand-entered one.
        # A weekly route assessment is expected to be up to a week behind, so
        # judging it against the daily limit would mark the whole tanker table
        # lagged every day but publication day.
        limit = 2 if cadence == "daily" else 9

        if spec["id"] in dry:
            d = dry[spec["id"]]
            row.update({"value": d["value"], "status": "ok", "as_of": d["as_of"],
                        "source": "Baltic Exchange via Reuters/Hellenic",
                        "source_url": d.get("source_url")})
            row.pop("why_unavailable", None)
        elif isinstance(raw, (int, float)):
            # A hand-entered rate used to carry no date at all, which made it
            # indistinguishable from a figure collected this morning.
            row.update({"value": raw, "status": "ok",
                        "as_of": manual.get("as_of"),
                        "source": manual.get("source", "manual entry")})
            row.pop("why_unavailable", None)

        stamp_dates(row, max_age_days=limit, collected_at=collected_at,
                    refreshed=row["value"] is not None)
        rows.append(row)

    live = sum(1 for r in rows if r["status"] == "ok")
    log(f"  rates: {live}/{len(rows)} active, {len(rows) - live} inactive (paid data)")
    return rows


# --------------------------------------------------------------------------- #
# News
# --------------------------------------------------------------------------- #
def collect_news():
    company, market = [], []

    try:
        resolve_issuers(reg.OSLO_TICKERS, reg.OSLO_INACTIVE, log=log)
        company += fetch_oslo(reg.OSLO_TICKERS, log=log,
                              rank_map=reg.OSLO_CATEGORY_RANK,
                              routine=reg.OSLO_ROUTINE_CATEGORIES,
                              routine_rank=reg.OSLO_ROUTINE_RANK)
    except Exception as exc:  # noqa: BLE001
        note_error("oslo", exc)

    try:
        company += fetch_sec(reg.SEC_COMPANIES, reg.SEC_FORMS_KEPT, log=log)
    except Exception as exc:  # noqa: BLE001
        note_error("sec", exc)

    try:
        market += fetch_feeds(reg.FEEDS, log=log, nordic_terms=reg.NORDIC_TERMS)
    except Exception as exc:  # noqa: BLE001
        note_error("rss", exc)

    return company, market


def merge_archive(fresh_company, fresh_market):
    """Accumulate news over days.

    Oslo's API exposes only the current day and offers no backfill, so the
    archive is how the company feed becomes useful: each run adds that day's
    items and drops anything older than ARCHIVE_DAYS.
    """
    archive = load_json(ARCHIVE, {"company": [], "market": []})
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=ARCHIVE_DAYS)).isoformat()

    merged = {}
    for bucket, fresh in (("company", fresh_company), ("market", fresh_market)):
        seen = {}
        for item in list(archive.get(bucket, [])) + list(fresh):
            key = item.get("link") or item.get("title")
            if not key:
                continue
            # Later wins, so a re-fetch can correct an earlier parse.
            seen[key] = item
        kept = [i for i in seen.values() if (i.get("published") or "9999") >= cutoff]
        if bucket == "company":
            # Substantive filings first, routine compliance notices after, each
            # group newest-first. Sorting purely by time buries a fleet deal
            # under three shareholding disclosures filed an hour later.
            kept.sort(key=lambda i: (i.get("rank", 4), _neg_time(i)))
        else:
            kept.sort(key=lambda i: i.get("published") or "", reverse=True)
        merged[bucket] = kept
        log(f"  archive: {bucket}: {len(fresh)} fresh, {len(kept)} kept in {ARCHIVE_DAYS}-day window")
    return merged


# --------------------------------------------------------------------------- #
def _neg_time(item):
    """Sort key that puts newer items first inside a rank group."""
    return tuple(-ord(c) for c in (item.get("published") or ""))


def interleave_by_source(items, per_round=1):
    """Round-robin across sources so no single publisher dominates the column.

    Caps are applied at fetch time, but a straight time sort still clusters one
    source together when it posts in bursts. This spreads them out.
    """
    buckets = {}
    for it in items:
        buckets.setdefault(it.get("source", "?"), []).append(it)
    for rows in buckets.values():
        rows.sort(key=lambda i: i.get("published") or "", reverse=True)
    # Sources whose newest item is most recent go first in each round.
    order = sorted(buckets, key=lambda s: buckets[s][0].get("published") or "", reverse=True)

    out = []
    while any(buckets[s] for s in order):
        for s in order:
            for _ in range(per_round):
                if buckets[s]:
                    out.append(buckets[s].pop(0))
    return out


def setup_hints(metrics):
    """Turn missing data into something the reader can actually act on.

    A tile that reads "awaiting data" every morning is indistinguishable from a
    broken dashboard. These say which of the two it is, and what would fix it.
    """
    import os

    hints = []
    needs_key = [m for m in metrics
                 if m.get("value") is None and m.get("fallback")]
    have_key = os.environ.get("FRED_API_KEY") or os.environ.get("EIA_API_KEY")
    if needs_key and not have_key:
        names = ", ".join(m["label"] for m in needs_key)
        hints.append({
            "problem": f"{names} could not be fetched.",
            "fix": "Yahoo Finance refuses requests from servers, so the scheduled run "
                   "cannot reach it. Add a free FRED_API_KEY (or EIA_API_KEY) as a "
                   "repository secret and these come from an official source instead. "
                   "Key: https://fredaccount.stlouisfed.org/apikey",
        })

    unavailable = [m for m in metrics if m.get("value") is None and m.get("no_free_source")]
    if unavailable:
        hints.append({
            "problem": f"{len(unavailable)} prices have no free source at all.",
            "fix": "These are exchange-licensed assessments. They are listed so you know "
                   "they exist and what they mean, the same way the paid Baltic routes "
                   "are — not because something is broken.",
        })
    return hints


def record_spend(written, today):
    """Keep a running total of what the written brief has cost.

    Shown on the page so the decision to stay on this model, or move to a
    cheaper one, is made against real numbers rather than my estimate.
    """
    data = load_json(SPEND, {})
    if not isinstance(data, dict):
        data = {}
    runs = data.get("runs", 0)
    total = float(data.get("total_usd", 0.0))
    if written and written.get("cost_usd"):
        runs += 1
        total = round(total + float(written["cost_usd"]), 4)
        data["last_run"] = today
        data.setdefault("first_run", today)
    data.update({"runs": runs, "total_usd": total,
                 "model": (written or {}).get("model") or data.get("model")})
    if runs:
        data["avg_usd"] = round(total / runs, 4)
        data["projected_monthly_usd"] = round(total / runs * 30, 2)
    return data


def load_storylines():
    data = load_json(STORYLINES, {})
    return data.get("stories", []) if isinstance(data, dict) else []


def merge_storylines(previous, updated, today, log):
    """Fold the model's update into the stored stories, then age them.

    The model decides substance; this decides time. Each story keeps the date it
    was opened and the date it last genuinely developed, so "day 6 of this" is
    counted from the record rather than from the model's recollection.
    """
    if updated is None:
        log("  storylines: no usable update returned; carrying yesterday's forward")
        updated = [dict(s) for s in previous]

    prev_by_id = {s.get("id"): s for s in previous}
    out = []
    for s in updated:
        old = prev_by_id.get(s["id"], {})
        first_seen = old.get("first_seen") or today
        # Only a real development moves the clock; a null `latest` does not.
        last_update = today if s.get("latest") else old.get("last_update", first_seen)
        try:
            age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(last_update)).days
        except ValueError:
            age = 0

        status = s.get("status", "watch")
        if status != "closed" and age >= STORY_STALE_DAYS:
            status = "closed"
            log(f"  storylines: closing '{s['id']}' — {age} days without a development")

        s.update({
            "first_seen": first_seen,
            "last_update": last_update,
            "days_running": max(0, (dt.date.fromisoformat(today)
                                    - dt.date.fromisoformat(first_seen)).days) + 1,
            "days_since_update": age,
            "status": status,
        })
        out.append(s)

    # Drop long-closed stories entirely so the file cannot grow without bound.
    kept = []
    for s in out:
        if s["status"] == "closed" and s.get("days_since_update", 0) >= STORY_DROP_DAYS:
            log(f"  storylines: dropping '{s['id']}' after {s['days_since_update']} quiet days")
            continue
        kept.append(s)

    live = sum(1 for s in kept if s["status"] != "closed")
    log(f"  storylines: {live} live, {len(kept) - live} closed")
    return kept


def market_tone(metrics):
    """A one-line summary built from arithmetic, not a language model.

    Only CURRENT rows count. A lagged row often still carries a change figure —
    EIA supplies one — and counting it would report a move from several
    sessions ago as though it happened this morning, which is the same error
    the written brief was making.
    """
    moved = [m for m in metrics
             if m.get("change") not in (None, 0)
             and m.get("freshness") == FRESH_CURRENT]
    if not moved:
        dated = [m for m in metrics
                 if m.get("value") is not None and m.get("as_of_label")]
        if dated:
            newest = max(dated, key=lambda m: m.get("as_of") or "")
            return ("No price has refreshed with a move yet today. The newest "
                    f"figure is {newest['label']} as at {newest['as_of_label']}.")
        return "No fresh moves to report yet today."
    up = [m for m in moved if m["change"] > 0]
    down = [m for m in moved if m["change"] < 0]
    biggest = max(moved, key=lambda m: abs(m["change"] / m["value"]) if m["value"] else 0)
    pct = (biggest["change"] / biggest["value"] * 100) if biggest["value"] else 0
    return (f"{len(up)} of {len(moved)} tracked prices rose and {len(down)} fell. "
            f"Biggest mover: {biggest['label']} {pct:+.1f}% to "
            f"{biggest['value']:,.2f} {biggest['unit']} "
            f"(as at {biggest.get('as_of_label') or 'an unknown date'}).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print findings, write nothing")
    ap.add_argument("--show-prompt", action="store_true",
                    help="print the written brief's prompt instead of calling the API")
    args = ap.parse_args()

    log(f"shipping-brief collector — {now_iso()}")
    previous = load_json(LATEST, {})

    snapshot_time = now_iso()
    log("prices:")
    metrics, dry = collect_metrics(previous, collected_at=snapshot_time)
    log("rates:")
    rates = collect_rates(dry, collected_at=snapshot_time)
    log("news:")
    company, market = collect_news()
    archive = merge_archive(company, market)

    company_items = archive["company"][:NEWS_SHOWN]
    market_items = interleave_by_source(archive["market"])[:NEWS_SHOWN]
    nordic_n = sum(1 for i in market_items if i.get("nordic"))
    log(f"  news: {len(market_items)} market items across "
        f"{len({i.get('source') for i in market_items})} sources, {nordic_n} Nordic")

    snapshot = {
        "generated_at": snapshot_time,
        "tone": market_tone(metrics),
        "metrics": metrics,
        "rates": rates,
        "news": {"company": company_items, "market": market_items},
        "diagnostics": {
            "errors": errors,
            "hints": setup_hints(metrics),
            # Counted on the freshness verdict, not on "not stale": a row with
            # no value at all is not stale (nothing was carried forward) and
            # must not be tallied as a healthy one.
            "metrics_fresh": sum(1 for m in metrics
                                 if m.get("freshness") == FRESH_CURRENT),
            "metrics_total": len(metrics),
            # How many figures are genuinely today's — the number the brief's
            # dating rules hinge on, recorded so a bad day is visible later.
            "observed_today": sum(1 for r in list(metrics) + list(rates)
                                  if r.get("observed_today")),
            "priced_rows": sum(1 for r in list(metrics) + list(rates)
                               if r.get("value") is not None),
            "rates_active": sum(1 for r in rates if r["status"] == "ok"),
            "rates_total": len(rates),
        },
    }

    log("brief:")
    today = dt.date.today().isoformat()
    previous_stories = load_storylines()
    written = brief_mod.generate(snapshot, company_items, market_items,
                                 storylines=previous_stories,
                                 log=log, dry_run=args.show_prompt)
    snapshot["brief"] = written

    stories = merge_storylines(
        previous_stories,
        written.get("storylines") if written else None,
        today, log)
    snapshot["storylines"] = [s for s in stories if s["status"] != "closed"]
    snapshot["storylines_closed"] = [s for s in stories if s["status"] == "closed"][:4]

    spend = record_spend(written, today)
    snapshot["brief_spend"] = spend
    if spend.get("runs"):
        log(f"  brief: ${spend['total_usd']:.3f} over {spend['runs']} runs "
            f"(~${spend['projected_monthly_usd']:.2f}/month at this rate)")

    # The summary prints the observation date for every row, because "ok" next
    # to an age of 3.8 days is how a lagged figure passed for a live one.
    print()
    print(f"{'metric':<16} {'value':>12}  {'chg':>9}  {'observed':<18} {'sess':>4}  state")
    for row in list(metrics) + [r for r in rates if r.get("value") is not None]:
        val = f"{row['value']:,.2f}" if isinstance(row.get("value"), (int, float)) else "—"
        chg = f"{row['change']:+,.2f}" if isinstance(row.get("change"), (int, float)) else "—"
        when = row.get("as_of_label") or "—"
        sess = row.get("age_sessions")
        sess = str(sess) if isinstance(sess, int) else "—"
        state = {"current": "ok", "lagged": "LAGGED", "stale": "STALE"}.get(
            row.get("freshness"), "—")
        print(f"{row['id']:<16} {val:>12}  {chg:>9}  {when:<18} {sess:>4}  {state}")
    print(f"\ntone: {snapshot['tone']}")
    if snapshot.get("brief"):
        print("\nwritten brief:")
        print(textwrap.indent(snapshot["brief"]["text"], "  "))
    if snapshot.get("storylines"):
        print("\nrunning stories:")
        for s in snapshot["storylines"]:
            mark = "*" if s.get("latest") else " "
            print(f"  {mark} [{s['status']}] day {s['days_running']}: {s['title']}")
    print(f"news: {len(snapshot['news']['company'])} company, {len(snapshot['news']['market'])} market")

    if args.dry_run:
        print("\n--dry-run: nothing written")
    else:
        DATA.mkdir(exist_ok=True)
        HISTORY.mkdir(parents=True, exist_ok=True)
        LATEST.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False), encoding="utf-8")
        ARCHIVE.write_text(json.dumps(archive, indent=1, ensure_ascii=False), encoding="utf-8")
        SPEND.write_text(json.dumps(spend, indent=1), encoding="utf-8")
        STORYLINES.write_text(json.dumps(
            {"updated": now_iso(), "stories": stories}, indent=1, ensure_ascii=False),
            encoding="utf-8")
        # History keeps only the numbers, which is all the sparklines need.
        day = dt.date.today().isoformat()
        (HISTORY / f"{day}.json").write_text(json.dumps({
            "date": day,
            "values": {m["id"]: m["value"] for m in metrics if m["value"] is not None},
        }, indent=1), encoding="utf-8")
        print(f"\nwrote {LATEST.relative_to(ROOT)}, {ARCHIVE.relative_to(ROOT)}, history/{day}.json")

    if errors:
        print(f"\n{len(errors)} adapter(s) failed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
