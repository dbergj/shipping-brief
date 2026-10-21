"""Yahoo Finance quotes.

Yahoo's endpoint is undocumented and returns HTTP 429 readily, so this adapter
asks for every symbol in as few requests as possible: one batched `spark` call
first, and only if that fails does it fall back to per-symbol `chart` calls,
which the shared http module serialises and backs off on.

Returns {symbol: {"value", "change", "as_of"}} for whatever it managed to get.
Partial success is normal and fine — run.py carries forward the rest.
"""
import datetime as dt

from http_util import FetchError, fetch_json

SPARK = "https://query1.finance.yahoo.com/v7/finance/spark?symbols={syms}&range=5d&interval=1d"
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"

# Consecutive 429s before we stop asking. Yahoo refuses the whole IP, not one
# symbol, so continuing is guaranteed waste.
RATE_LIMIT_GIVE_UP = 3


def _as_of(ts):
    if not ts:
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def _from_closes(closes):
    """Last close and the change against the prior close, ignoring null gaps."""
    vals = [c for c in closes if c is not None]
    if not vals:
        return None, None
    value = vals[-1]
    change = round(value - vals[-2], 4) if len(vals) > 1 else None
    return value, change


def _try_spark(symbols):
    out = {}
    url = SPARK.format(syms=",".join(symbols))
    data = fetch_json(url)
    # spark returns {"SYM": {"close": [...], "timestamp": [...]}, ...}
    # and has historically also used a {"spark": {"result": [...]}} shape.
    if isinstance(data, dict) and "spark" in data:
        rows = data["spark"].get("result") or []
        data = {r["symbol"]: (r.get("response") or [{}])[0] for r in rows}
    for sym, payload in (data or {}).items():
        if not isinstance(payload, dict):
            continue
        closes = payload.get("close") or []
        ts = payload.get("timestamp") or []
        value, change = _from_closes(closes)
        if value is None:
            continue
        out[sym] = {"value": value, "change": change, "as_of": _as_of(ts[-1] if ts else None)}
    return out


def _try_chart(sym):
    data = fetch_json(CHART.format(sym=sym))
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise FetchError(f"{sym}: empty chart result")
    meta = result[0].get("meta") or {}
    value = meta.get("regularMarketPrice")
    if value is None:
        raise FetchError(f"{sym}: no regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    change = round(value - prev, 4) if prev else None
    return {
        "value": value,
        "change": change,
        "as_of": _as_of(meta.get("regularMarketTime")),
        "name": meta.get("shortName") or meta.get("longName"),
    }


def fetch_quotes(symbols, log=print):
    """Fetch many symbols, batching first. Never raises; returns what it got."""
    symbols = list(dict.fromkeys(symbols))
    got = {}

    try:
        got = _try_spark(symbols)
        log(f"  yahoo: batched spark returned {len(got)}/{len(symbols)}")
    except FetchError as exc:
        log(f"  yahoo: batch failed ({exc}), falling back to per-symbol")

    missing = [s for s in symbols if s not in got]
    # Circuit breaker: once Yahoo is rate-limiting, every further request will
    # also be refused, and each one costs three retries with backoff. Stop after
    # a few consecutive 429s instead of grinding through the whole list.
    consecutive_429 = 0
    for i, sym in enumerate(missing):
        if consecutive_429 >= RATE_LIMIT_GIVE_UP:
            log(f"  yahoo: rate-limited, skipping remaining {len(missing) - i} symbols")
            break
        try:
            got[sym] = _try_chart(sym)
            consecutive_429 = 0
        except FetchError as exc:
            consecutive_429 = consecutive_429 + 1 if exc.status == 429 else 0
            log(f"  yahoo: {sym} unavailable — {exc}")
    return got
