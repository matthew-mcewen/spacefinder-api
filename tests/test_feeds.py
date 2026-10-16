"""feeds.py: the on-disk cache and its fetch limits."""
import io
import json
import os
import threading
import time
import zipfile

import httpx
import pytest

from app import config, feeds
from app.feeds import FeedUnavailable
from world import make_zip

URL, NAME, TTL = "https://example.test/feed.json", "feed.json", 180
REAL_DOWNLOAD = feeds._download  # captured before the autouse fixture swaps it out


@pytest.fixture
def downloads(monkeypatch):
    """Fake downloader: records each call and returns b'v1', b'v2', ..."""
    calls = []

    def fake(url):
        calls.append(url)
        return f"v{len(calls)}".encode()

    monkeypatch.setattr(feeds, "_download", fake)
    return calls


@pytest.fixture
def failing(monkeypatch):
    """Downloader that always fails like a network error, counting attempts."""
    calls = []

    def fake(url):
        calls.append(url)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(feeds, "_download", fake)
    return calls


@pytest.fixture
def clock(monkeypatch):
    """Controllable clock, starting at the real time: clock[0] += 181 moves it on."""
    t = [time.time()]
    monkeypatch.setattr(feeds, "now", lambda: t[0])
    return t


def test_downloads_when_not_cached(cache, downloads):
    """With no cached file, we download once and store it."""
    path = feeds.fetch_cached(URL, NAME, TTL)
    assert path == cache / NAME
    assert path.read_bytes() == b"v1"
    assert downloads == [URL]


def test_fresh_cache_is_not_refetched(cache, downloads):
    """A second call inside the TTL is served from disk."""
    feeds.fetch_cached(URL, NAME, TTL)
    assert feeds.fetch_cached(URL, NAME, TTL).read_bytes() == b"v1"
    assert len(downloads) == 1


def test_never_more_than_one_fetch_per_ttl(cache, downloads, clock):
    """Calling every second for 179 s costs one download. The next one comes only after 180 s."""
    for _ in range(180):
        feeds.fetch_cached(URL, NAME, TTL)
        clock[0] += 1
    assert len(downloads) == 1
    clock[0] += 1
    assert feeds.fetch_cached(URL, NAME, TTL).read_bytes() == b"v2"
    assert len(downloads) == 2


def test_stale_cache_is_refetched(cache, downloads, clock):
    """Once the TTL has passed, the file is replaced with fresh content."""
    feeds.fetch_cached(URL, NAME, TTL)
    clock[0] += TTL + 1
    assert feeds.fetch_cached(URL, NAME, TTL).read_bytes() == b"v2"


def test_ttl_survives_restarts(cache, downloads):
    """The file's mtime is the clock: a fresh file left by a previous run isn't refetched."""
    (cache / NAME).write_bytes(b"from last run")  # no in-memory history, like a restart
    assert feeds.fetch_cached(URL, NAME, TTL).read_bytes() == b"from last run"
    assert downloads == []


def test_old_file_from_previous_run_is_refetched(cache, downloads):
    """A file older than the TTL triggers a download, even on first use."""
    (cache / NAME).write_bytes(b"old")
    old = time.time() - TTL - 1
    os.utime(cache / NAME, (old, old))
    assert feeds.fetch_cached(URL, NAME, TTL).read_bytes() == b"v1"


def test_failed_download_serves_stale_copy(cache, failing, clock):
    """If the source is down, the stale cached copy is served instead of an error."""
    (cache / NAME).write_bytes(b"stale")
    clock[0] += TTL + 1
    assert feeds.fetch_cached(URL, NAME, TTL).read_bytes() == b"stale"
    assert len(failing) == 1


def test_failed_download_is_not_retried_within_ttl(cache, failing, clock):
    """A failed attempt also counts, so an outage costs one attempt per TTL, not one per request."""
    (cache / NAME).write_bytes(b"stale")
    clock[0] += TTL + 1
    for _ in range(10):
        feeds.fetch_cached(URL, NAME, TTL)
    assert len(failing) == 1
    clock[0] += TTL
    feeds.fetch_cached(URL, NAME, TTL)
    assert len(failing) == 2


def test_failed_download_without_cache_raises(cache, failing):
    """No cached copy and no network means FeedUnavailable."""
    with pytest.raises(FeedUnavailable):
        feeds.fetch_cached(URL, NAME, TTL)


def test_http_error_status_is_a_failed_download(cache, monkeypatch):
    """A 5xx from the server counts as a failure (raise_for_status)."""
    monkeypatch.setattr(feeds, "_download", REAL_DOWNLOAD)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(503, request=httpx.Request("GET", url)))
    with pytest.raises(FeedUnavailable):
        feeds.fetch_cached(URL, NAME, TTL)


def test_real_downloader_follows_redirects_and_times_out(cache, monkeypatch):
    """_download asks httpx to follow redirects (GitHub release assets redirect) and sets a timeout."""
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw)
        return httpx.Response(200, content=b"ok", request=httpx.Request("GET", url))

    monkeypatch.setattr(feeds, "_download", REAL_DOWNLOAD)
    monkeypatch.setattr(httpx, "get", fake_get)
    assert feeds.fetch_cached(URL, NAME, TTL).read_bytes() == b"ok"
    assert seen == {"timeout": config.HTTP_TIMEOUT, "follow_redirects": True}


def test_write_leaves_no_temp_file(cache, downloads):
    """Writes go via a temp file plus a rename, and nothing else is left behind."""
    feeds.fetch_cached(URL, NAME, TTL)
    assert [p.name for p in cache.iterdir()] == [NAME]


def test_creates_missing_cache_dir(tmp_path, monkeypatch, downloads):
    """The cache directory is created on first write."""
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "nested" / "cache")
    assert feeds.fetch_cached(URL, NAME, TTL).read_bytes() == b"v1"


def test_concurrent_callers_share_one_download(cache, monkeypatch):
    """Ten simultaneous requests for a cold cache cause a single download."""
    calls = []

    def slow(url):
        calls.append(url)
        time.sleep(0.05)
        return b"data"

    monkeypatch.setattr(feeds, "_download", slow)
    threads = [threading.Thread(target=feeds.fetch_cached, args=(URL, NAME, TTL)) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1


# --- content checks -------------------------------------------------------------------------------

@pytest.fixture
def serves(monkeypatch):
    """Make the downloader return the given bytes."""
    def set_body(body: bytes):
        monkeypatch.setattr(feeds, "_download", lambda url: body)
    return set_body


def test_invalid_body_never_replaces_cache(cache, serves, clock):
    """An HTML error page served with a 200 fails the check, and the stale copy is kept."""
    (cache / NAME).write_bytes(b'{"ok": true}')
    clock[0] += TTL + 1
    serves(b"<html>502 Bad Gateway</html>")
    assert feeds.fetch_cached(URL, NAME, TTL, check=json.loads).read_bytes() == b'{"ok": true}'


def test_invalid_body_without_cache_raises(cache, serves):
    """With nothing cached, a body that fails the check means FeedUnavailable, and nothing is written."""
    serves(b'{"truncated": ')
    with pytest.raises(FeedUnavailable):
        feeds.fetch_cached(URL, NAME, TTL, check=json.loads)
    assert list(cache.iterdir()) == []


def test_truncated_zip_fails_check(cache, serves):
    """A truncated zip fails a ZipFile check."""
    serves(make_zip({"a.json": "{}"})[:-30])
    with pytest.raises(FeedUnavailable):
        feeds.fetch_cached(URL, NAME, TTL, check=lambda b: zipfile.ZipFile(io.BytesIO(b)))


def test_valid_body_passes_check(cache, serves):
    """A body that parses is cached as usual."""
    serves(b'{"ok": true}')
    assert feeds.fetch_cached(URL, NAME, TTL, check=json.loads).read_bytes() == b'{"ok": true}'


def test_invalid_body_counts_as_attempt(cache, monkeypatch, clock):
    """A bad body isn't retried on every request: it costs one attempt per TTL, like an outage."""
    calls = []
    monkeypatch.setattr(feeds, "_download", lambda url: calls.append(url) or b"<html>")
    (cache / NAME).write_bytes(b"{}")
    clock[0] += TTL + 1
    for _ in range(5):
        feeds.fetch_cached(URL, NAME, TTL, check=json.loads)
    assert len(calls) == 1


def test_live_callers_check_their_downloads(world, monkeypatch, clock):
    """All three real datasets are checked: garbage downloads leave the API on its stale data."""
    from app import traffic
    monkeypatch.setattr(feeds, "_download", lambda url: b"<html>oops</html>")
    clock[0] += config.STATIC_TTL + 1
    assert traffic.current().firs["HIGH1"] == ["AAAA", "AAAA-N"]


# --- Memo ----------------------------------------------------------------------------------------

def test_memo_computes_once_per_key():
    """The same key returns the cached value without recomputing."""
    memo, runs = feeds.Memo(), []
    compute = lambda: runs.append(1) or len(runs)
    assert memo.get("k", compute) == 1
    assert memo.get("k", compute) == 1
    assert len(runs) == 1


def test_memo_recomputes_when_key_changes():
    """A new key triggers a recompute."""
    memo, runs = feeds.Memo(), []
    compute = lambda: runs.append(1) or len(runs)
    memo.get("a", compute)
    assert memo.get("b", compute) == 2
