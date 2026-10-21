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

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent),
                str(pathlib.Path(__file__).resolve().parent / "sources")]

import registry as reg  # noqa: E402
from bdi import fetch_bdi  # noqa: E402
from bunkers import fetch_all_grades  # noqa: E402
from eia import fetch_eia  # noqa: E402
from news_oslo import fetch_oslo, resolve_issuers  # noqa: E402
from news_rss import fetch_feeds  # noqa: E402
from news_sec import fetch_sec  # noqa: E402
from yahoo import fetch_quotes  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LATEST = DATA / "latest.json"
HISTORY = DATA / "history"
ARCHIVE = DATA / "news_archive.json"
MANUAL = DATA / "manual" / "rates.json"

ARCHIVE_DAYS = 30
NEWS_SHOWN = 40

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
# Prices
# --------------------------------------------------------------------------- #
def collect_metrics(previous):
    """Return the metric list, carrying forward anything we could not refresh."""
    prev_by_id = {m["id"]: m for m in previous.get("metrics", [])}

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

    bunkers = {}
    try:
        grades = fetch_all_grades(log=log)
        bunkers = grades.get("VLSFO", {})
        log(f"  bunkers: VLSFO for {len(bunkers)} ports")
    except Exception as exc:  # noqa: BLE001
        note_error("bunkers", exc)

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
        if fresh is None and spec["id"] in eia_values:
            e = eia_values[spec["id"]]
            fresh = {"value": e["value"], "change": e.get("change"),
                     "as_of": e["as_of"], "source": "EIA (official)"}

        if spec["source"] == "bdi" and bdi and fresh is None:
            fresh = {"value": bdi["value"], "change": bdi.get("change"),
                     "as_of": bdi["as_of"], "source": "Baltic Exchange via Hellenic Shipping News",
                     "source_url": bdi.get("source_url")}

        row = {k: spec[k] for k in ("id", "label", "unit", "group") if k in spec}
        row["blurb"] = spec.get("blurb", "")
        row["kpi"] = bool(spec.get("kpi"))

        if fresh:
            row.update(fresh)
            row["stale"] = False
        else:
            old = prev_by_id.get(spec["id"])
            if old and old.get("value") is not None:
                # Carry forward rather than blanking, and say that we did.
                row.update({"value": old["value"], "change": None,
                            "as_of": old.get("as_of"), "source": old.get("source")})
                row["stale"] = True
                log(f"  carried forward {spec['id']} from {old.get('as_of')}")
            else:
                row.update({"value": None, "change": None, "as_of": None, "source": None})
                row["stale"] = True

        age = age_days(row.get("as_of"))
        row["_spec_max_age"] = spec.get("max_age_days", 4)
        row["age_days"] = round(age, 2) if age is not None else None
        if age is not None and age > spec.get("max_age_days", 4):
            row["stale"] = True
        row.pop("_spec_max_age", None)
        out.append(row)

    out.extend(derive_metrics(out))
    return out


def derive_metrics(metrics):
    """Metrics computed from other metrics — no extra source to fail.

    A derived value is only as fresh as its stalest input, so staleness
    propagates rather than quietly presenting old arithmetic as current.
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
            ages = [i["age_days"] for i in inputs if i.get("age_days") is not None]
            row.update({
                "value": round(value, 2),
                "as_of": min((i["as_of"] for i in inputs if i.get("as_of")), default=None),
                "source": "derived: " + " − ".join(spec["inputs"]),
                "stale": any(i["stale"] for i in inputs),
                "age_days": round(max(ages), 2) if ages else None,
            })
            log(f"  derived: {spec['id']} = {row['value']} {spec['unit']}"
                + (" (stale inputs)" if row["stale"] else ""))
        else:
            missing = [s for s, i in zip(spec["inputs"], inputs)
                       if not (i and isinstance(i.get("value"), (int, float)))]
            log(f"  derived: {spec['id']} unavailable — needs {', '.join(missing)}")
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Freight routes that need a paid Baltic subscription
# --------------------------------------------------------------------------- #
def collect_rates():
    """Build the rate table. Rows with no number render as inactive.

    Official Baltic route assessments cost GBP 3,420/yr, so these are inactive
    by default. They still carry their label and explanation, because knowing
    what a VLCC TD3C voyage is has teaching value with or without today's
    number. Drop a value into data/manual/rates.json and the row activates.
    """
    manual = load_json(MANUAL, {})
    values = manual.get("rates", {}) if isinstance(manual, dict) else {}

    rows = []
    for spec in reg.MANUAL_RATES:
        raw = values.get(spec["id"])
        row = {
            "id": spec["id"], "label": spec["label"], "unit": spec["unit"],
            "segment": spec["segment"], "blurb": spec.get("blurb", ""),
            "value": None, "as_of": manual.get("as_of"), "source": None,
            "status": "unavailable",
            "why_unavailable": "Official Baltic route data requires a paid subscription "
                               "(£3,420/yr). This row activates automatically if a value "
                               "is supplied.",
        }
        if isinstance(raw, (int, float)):
            row.update({"value": raw, "status": "ok", "source": manual.get("source", "manual entry")})
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
        company += fetch_oslo(reg.OSLO_TICKERS, log=log)
    except Exception as exc:  # noqa: BLE001
        note_error("oslo", exc)

    try:
        company += fetch_sec(reg.SEC_COMPANIES, reg.SEC_FORMS_KEPT, log=log)
    except Exception as exc:  # noqa: BLE001
        note_error("sec", exc)

    try:
        market += fetch_feeds(reg.FEEDS, log=log)
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
        kept.sort(key=lambda i: i.get("published") or "", reverse=True)
        merged[bucket] = kept
        log(f"  archive: {bucket}: {len(fresh)} fresh, {len(kept)} kept in {ARCHIVE_DAYS}-day window")
    return merged


# --------------------------------------------------------------------------- #
def market_tone(metrics):
    """A one-line summary built from arithmetic, not a language model."""
    moved = [m for m in metrics if m.get("change") not in (None, 0) and not m["stale"]]
    if not moved:
        return "No fresh moves to report yet today."
    up = [m for m in moved if m["change"] > 0]
    down = [m for m in moved if m["change"] < 0]
    biggest = max(moved, key=lambda m: abs(m["change"] / m["value"]) if m["value"] else 0)
    pct = (biggest["change"] / biggest["value"] * 100) if biggest["value"] else 0
    return (f"{len(up)} of {len(moved)} tracked prices rose and {len(down)} fell. "
            f"Biggest mover: {biggest['label']} {pct:+.1f}% to "
            f"{biggest['value']:,.2f} {biggest['unit']}.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print findings, write nothing")
    args = ap.parse_args()

    log(f"shipping-brief collector — {now_iso()}")
    previous = load_json(LATEST, {})

    log("prices:")
    metrics = collect_metrics(previous)
    log("rates:")
    rates = collect_rates()
    log("news:")
    company, market = collect_news()
    archive = merge_archive(company, market)

    snapshot = {
        "generated_at": now_iso(),
        "tone": market_tone(metrics),
        "metrics": metrics,
        "rates": rates,
        "news": {"company": archive["company"][:NEWS_SHOWN],
                 "market": archive["market"][:NEWS_SHOWN]},
        "diagnostics": {
            "errors": errors,
            "metrics_fresh": sum(1 for m in metrics if not m["stale"]),
            "metrics_total": len(metrics),
            "rates_active": sum(1 for r in rates if r["status"] == "ok"),
            "rates_total": len(rates),
        },
    }

    print()
    print(f"{'metric':<16} {'value':>12}  {'chg':>9}  {'age':>6}  state")
    for m in metrics:
        val = f"{m['value']:,.2f}" if isinstance(m["value"], (int, float)) else "—"
        chg = f"{m['change']:+,.2f}" if isinstance(m["change"], (int, float)) else "—"
        age = f"{m['age_days']:.1f}d" if m["age_days"] is not None else "—"
        print(f"{m['id']:<16} {val:>12}  {chg:>9}  {age:>6}  {'STALE' if m['stale'] else 'ok'}")
    print(f"\ntone: {snapshot['tone']}")
    print(f"news: {len(snapshot['news']['company'])} company, {len(snapshot['news']['market'])} market")

    if args.dry_run:
        print("\n--dry-run: nothing written")
    else:
        DATA.mkdir(exist_ok=True)
        HISTORY.mkdir(parents=True, exist_ok=True)
        LATEST.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False), encoding="utf-8")
        ARCHIVE.write_text(json.dumps(archive, indent=1, ensure_ascii=False), encoding="utf-8")
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
