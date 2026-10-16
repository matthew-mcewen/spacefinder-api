"""Remote files through a small on-disk cache, plus a memo for values derived from them.

A cached file younger than its TTL is served from disk; otherwise it is re-downloaded.
The file's mtime is the clock, so the limit holds across restarts. A failed download
also counts as an attempt, so a VATSIM outage can't make us hammer the server.
"""
import logging
import os
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from app import config

log = logging.getLogger(__name__)
now = time.time  # swappable clock (tests)

_attempts: dict[Path, float] = {}  # path -> last download attempt (this process)
_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


class FeedUnavailable(Exception):
    """Download failed and there is no cached copy to fall back on."""


def fetch_cached(url: str, name: str, max_age: float, check: Callable[[bytes], object] | None = None) -> Path:
    """Make sure cache/<name> holds `url`, downloading at most once per `max_age` seconds.
    Returns the path. `check` parses the new bytes before they replace the cache, so an HTML
    error page or truncated body is never cached. If the download or check fails, a stale copy is kept and served."""
    path = config.CACHE_DIR / name
    with _lock_for(path):
        mtime = path.stat().st_mtime if path.exists() else None
        if mtime is not None and now() - max(mtime, _attempts.get(path, 0)) < max_age:
            return path
        _attempts[path] = now()
        try:
            data = _download(url)
            if check:
                check(data)
        except (httpx.HTTPError, ValueError, zipfile.BadZipFile) as e:
            if mtime is None:
                raise FeedUnavailable(url) from e
            log.warning("download failed, serving %.0fs-old cache of %s: %s", now() - mtime, url, e)
            return path
        _write_atomic(path, data)
        return path


def _download(url: str) -> bytes:
    r = httpx.get(url, timeout=config.HTTP_TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    return r.content


def _write_atomic(path: Path, data: bytes) -> None:
    """Write to a temp file, then rename, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _lock_for(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(path, threading.Lock())


class Memo:
    """Holds one derived value, recomputed only when its key changes."""

    def __init__(self):
        self.key, self.value, self.lock = None, None, threading.Lock()

    def get(self, key, compute):
        with self.lock:
            if self.value is None or key != self.key:
                self.value, self.key = compute(), key
            return self.value
