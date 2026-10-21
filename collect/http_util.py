"""Shared HTTP helper. Stdlib only.

Everything the collector fetches goes through here, so politeness, retries and
backoff are decided in exactly one place. Yahoo returns 429 readily when hit
with concurrent per-symbol requests, which is why callers batch and why this
module serialises with a floor on the gap between requests to a host.
"""
import gzip
import hashlib
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Yahoo blocks requests without a browser-ish UA outright.
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
}

MIN_GAP_SECONDS = 1.1
_last_hit = defaultdict(float)

# Local development cache. Off in CI, where we always want today's real data.
# Set SHIPDASH_CACHE_MIN=30 while working on the site to avoid re-hitting APIs
# that rate-limit (Yahoo will 429 an IP that asks too often).
CACHE_DIR = pathlib.Path(__file__).resolve().parent.parent / ".cache"
CACHE_MINUTES = int(os.environ.get("SHIPDASH_CACHE_MIN", "0"))


def _cache_path(url):
    return CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".txt")


def _cache_read(url):
    if CACHE_MINUTES <= 0:
        return None
    p = _cache_path(url)
    if p.exists() and (time.time() - p.stat().st_mtime) < CACHE_MINUTES * 60:
        return p.read_text(encoding="utf-8")
    return None


def _cache_write(url, body):
    if CACHE_MINUTES <= 0:
        return
    CACHE_DIR.mkdir(exist_ok=True)
    _cache_path(url).write_text(body, encoding="utf-8")


class FetchError(Exception):
    """Raised when a URL could not be retrieved after retries.

    `status` carries the final HTTP code when there was one, so callers can tell
    a rate limit (retry later, stop hammering) from a 404 (fix the URL).
    """

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _throttle(host):
    gap = time.monotonic() - _last_hit[host]
    if gap < MIN_GAP_SECONDS:
        time.sleep(MIN_GAP_SECONDS - gap)
    _last_hit[host] = time.monotonic()


def fetch(url, *, headers=None, timeout=20, retries=3, backoff=2.5, method=None, body=None):
    """GET a URL and return the decoded body as str.

    Retries on 429 and 5xx with exponential backoff. Raises FetchError once the
    retries are spent so the caller can carry forward its last known value.
    """
    cache_key = url if body is None else f"{url}|{body!r}"
    cached = _cache_read(cache_key)
    if cached is not None:
        return cached

    host = urllib.parse.urlsplit(url).netloc
    hdrs = dict(HEADERS)
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)

    last = None
    last_status = None
    for attempt in range(retries):
        _throttle(host)
        try:
            req = urllib.request.Request(url, headers=hdrs, data=body, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                text = raw.decode("utf-8", errors="replace")
                _cache_write(cache_key, text)
                return text
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            last_status = exc.code
            # 404/403 will not fix themselves; only back off on throttling and server faults.
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except Exception as exc:  # noqa: BLE001 - network errors are varied and all retryable
            last = f"{type(exc).__name__}: {exc}"
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt))

    raise FetchError(f"{url} failed: {last}", status=last_status)


def fetch_json(url, **kwargs):
    try:
        return json.loads(fetch(url, **kwargs))
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url} returned non-JSON: {exc}") from exc
