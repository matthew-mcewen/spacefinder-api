"""Shared fixtures. Tests run offline: every download fails as if the network were down,
unless the test is marked `live`."""
import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app import airspace, config, feeds, main, traffic
from world import FEED, FIRS, VATGLASSES_FILES, make_zip


def _offline(url):
    raise httpx.ConnectError(f"network disabled in tests: {url}")


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch, request):
    """Empty memos and download history for every test; no network unless marked live."""
    monkeypatch.setattr(feeds, "_attempts", {})
    monkeypatch.setattr(airspace, "_memo", feeds.Memo())
    monkeypatch.setattr(traffic, "_memo", feeds.Memo())
    if not request.node.get_closest_marker("live"):
        monkeypatch.setattr(feeds, "_download", _offline)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """An empty temporary cache directory."""
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def world(cache):
    """The cache, pre-seeded with fresh synthetic VatSpy, VatGlasses and datafeed files."""
    (cache / "Boundaries.geojson").write_text(json.dumps(FIRS))
    (cache / "vatglasses.zip").write_bytes(make_zip(VATGLASSES_FILES))
    (cache / "vatsim-data.json").write_text(json.dumps(FEED))
    return cache


@pytest.fixture
def sky():
    """The synthetic Airspace, built straight from memory."""
    return airspace.Airspace(airspace.load_firs(FIRS), airspace.load_volumes(zipfile.ZipFile(io.BytesIO(make_zip(VATGLASSES_FILES)))))


@pytest.fixture
def snap(sky):
    """The synthetic datafeed assigned against the synthetic Airspace."""
    return traffic.build(FEED, sky)


@pytest.fixture
def client(world):
    """HTTP client for the app, backed by the seeded cache."""
    return TestClient(main.app)
