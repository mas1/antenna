"""A polite HTTP client: disk cache, per-host pacing, retries with backoff.

Every collector goes through `get` / `get_json` / `post_json`. Nothing here
needs a third-party package, so the pipeline runs on a bare Python install.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import CACHE_PATH, CONTACT_EMAIL

USER_AGENT = f"antenna-sourcing/0.1 ({CONTACT_EMAIL})"

# Minimum seconds between requests to one host. Hosts not listed use DEFAULT.
# A few collectors (YC, FAA, USPTO, Ashby) pace themselves more slowly than
# anything here, per their source cards, and are left out on purpose.
HOST_INTERVAL = {
    "export.arxiv.org": 3.1,
    "www.sec.gov": 0.15,
    "efts.sec.gov": 0.15,
    "data.sec.gov": 0.15,
    "api.github.com": 0.05,
    "hn.algolia.com": 0.12,
    "hacker-news.firebaseio.com": 0.2,
    "api.openalex.org": 0.12,
    "api.ror.org": 0.12,
    "crt.sh": 2.0,
    "api.certspotter.com": 1.0,
    "dns.google": 0.35,
    "tranco-list.eu": 1.1,
    "web.archive.org": 1.0,
    "apps.fcc.gov": 1.0,
    "www.fpds.gov": 0.6,
    "adams-search.nrc.gov": 0.35,
    "www.sbir.gov": 0.5,
    "api.usaspending.gov": 0.5,
}
DEFAULT_INTERVAL = 0.35

_lock = threading.Lock()
_host_locks: dict[str, threading.Lock] = {}
_last_hit: dict[str, float] = {}
_stats = {"requests": 0, "cache_hits": 0, "errors": 0, "not_found": 0}

# Never wait longer than this on a Retry-After; fail and let the caller move on.
MAX_RETRY_AFTER = 30.0


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status
        self.url = url
        self.body = body


def _cache() -> sqlite3.Connection:
    Path(CACHE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS http_cache ("
        "key TEXT PRIMARY KEY, url TEXT, fetched_at REAL, status INTEGER, "
        "headers TEXT, body BLOB)"
    )
    return conn


def _key(method: str, url: str, body: bytes | None, vary: str) -> str:
    h = hashlib.sha256()
    h.update(method.encode())
    h.update(url.encode())
    h.update(body or b"")
    h.update(vary.encode())
    return h.hexdigest()


def _pace(host: str) -> None:
    with _lock:
        host_lock = _host_locks.setdefault(host, threading.Lock())
    with host_lock:
        interval = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
        wait = _last_hit.get(host, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


def request(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    ttl: float = 6 * 3600,
    timeout: float = 30,
    retries: int = 3,
    return_headers: bool = False,
) -> Any:
    """Fetch a URL, returning the body as text (or (text, headers)).

    Responses are cached on disk for `ttl` seconds, so re-running the
    pipeline while developing a collector costs nothing. Pass ttl=0 to skip
    the cache.
    """
    if params:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}, doseq=True
        )
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    vary = hdrs.get("Accept", "")
    key = _key(method, url, data, vary)

    conn = _cache()
    try:
        if ttl > 0:
            row = conn.execute(
                "SELECT fetched_at, status, headers, body FROM http_cache WHERE key=?",
                (key,),
            ).fetchone()
            if row and time.time() - row[0] < ttl:
                _stats["cache_hits"] += 1
                text = gzip.decompress(row[3]).decode("utf-8", "replace")
                if row[1] >= 400:
                    raise HttpError(row[1], url, text)
                return (text, json.loads(row[2])) if return_headers else text

        host = urllib.parse.urlparse(url).netloc
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            _pace(host)
            _stats["requests"] += 1
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    text = raw.decode("utf-8", "replace")
                    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                    if ttl > 0:
                        conn.execute(
                            "INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?,?,?)",
                            (
                                key,
                                url,
                                time.time(),
                                resp.status,
                                json.dumps(resp_headers),
                                gzip.compress(text.encode()),
                            ),
                        )
                        conn.commit()
                    return (text, resp_headers) if return_headers else text
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace") if e.fp else ""
                last_err = HttpError(e.code, url, body)
                # 404 and 4xx client errors will not get better with retries.
                if e.code in (400, 401, 404, 410, 422):
                    if ttl > 0 and e.code in (404, 410):
                        conn.execute(
                            "INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?,?,?)",
                            (key, url, time.time(), e.code, "{}", gzip.compress(b"")),
                        )
                        conn.commit()
                    if e.code in (404, 410):
                        # Expected when probing for something that may not exist.
                        _stats["not_found"] += 1
                        raise last_err from None
                    break
                if attempt == retries:
                    break
                retry_after = e.headers.get("Retry-After") if e.headers else None
                if retry_after and retry_after.isdigit() and float(retry_after) > MAX_RETRY_AFTER:
                    break  # the server wants a long pause: give up now
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 * (2**attempt)
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.5 * (2**attempt))
        _stats["errors"] += 1
        assert last_err is not None
        raise last_err
    finally:
        conn.close()


def get(url: str, **kw: Any) -> str:
    return request(url, **kw)


def download(url: str, dest: Path | str, *, headers: dict[str, str] | None = None,
             max_age: float = 20 * 3600, timeout: float = 300) -> Path:
    """Stream a large or binary file to disk, reusing a recent copy.

    For zips and multi-megabyte CSVs that should not go through the text
    cache. Returns the path. Raises HttpError on a bad status.
    """
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0 and time.time() - dest.stat().st_mtime < max_age:
        _stats["cache_hits"] += 1
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    _pace(urllib.parse.urlparse(url).netloc)
    _stats["requests"] += 1
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
        tmp.replace(dest)
        return dest
    except urllib.error.HTTPError as e:
        _stats["errors"] += 1
        tmp.unlink(missing_ok=True)
        raise HttpError(e.code, url) from e


def get_json(url: str, **kw: Any) -> Any:
    headers = {"Accept": "application/json", **(kw.pop("headers", None) or {})}
    return json.loads(request(url, headers=headers, **kw))


def post_json(url: str, payload: Any, **kw: Any) -> Any:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **(kw.pop("headers", None) or {}),
    }
    body = json.dumps(payload, sort_keys=True).encode()
    return json.loads(request(url, method="POST", data=body, headers=headers, **kw))


def stats() -> dict[str, int]:
    return dict(_stats)
