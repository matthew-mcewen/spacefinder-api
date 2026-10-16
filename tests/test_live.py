"""Opt-in smoke tests against the real VATSIM, VatSpy and VatGlasses servers: `pytest -m live`.
They use the project's real cache/ directory, so the 180 s datafeed limit is respected."""
import json
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import airspace, config, geo, main, svgmap, traffic

pytestmark = pytest.mark.live


def test_real_datasets_parse():
    """Both real datasets load, with plausible sizes, numeric limits and in-range boxes."""
    sky = airspace.current()
    assert len(sky.firs) > 1000 and len(sky.fir_ids) > 500
    assert len(sky.volumes) > 10000 and len(sky.volume_ids) > 5000
    assert all(isinstance(s.floor, float) and isinstance(s.ceiling, float) for s in sky.volumes)
    assert all(-180 <= s.bbox.w <= s.bbox.e <= 180 and -90 <= s.bbox.s <= s.bbox.n <= 90 for s in sky.firs + sky.volumes)


def test_real_snapshot_builds_quickly():
    """The whole network is assigned in well under 5 s (about 0.2 s expected)."""
    airspace.current()  # load datasets first, so only the assignment is timed
    t = time.perf_counter()
    snap = traffic.current()
    assert time.perf_counter() - t < 5
    assert snap.positions, "nobody connected to VATSIM?"
    assert set(snap.positions) == set(snap.firs) == set(snap.volumes)


def test_real_grid_index_matches_brute_force():
    """For every pilot, the indexed FIR lookup equals a scan of every shape, with no index."""
    snap = traffic.current()
    for cs, (lon, lat) in snap.positions.items():
        brute = sorted({s.id for s in snap.sky.firs if geo.point_in_rings(lon, lat, s.rings)})
        assert snap.firs[cs] == brute, cs


def test_real_reported_volumes_really_contain_the_aircraft():
    """Each reported volume's polygon and altitude band really do contain the aircraft."""
    snap = traffic.current()
    feed = json.loads((config.CACHE_DIR / "vatsim-data.json").read_bytes())
    fl = {p["callsign"].upper(): p["altitude"] / 100 for p in feed["pilots"]}
    shapes = {}
    for s in snap.sky.volumes:
        shapes.setdefault(s.id, []).append(s)
    for cs, ids in snap.volumes.items():
        lon, lat = snap.positions[cs]
        for vid in ids:
            assert any(geo.point_in_rings(lon, lat, s.rings) and geo.in_band(fl[cs], s.floor, s.ceiling) for s in shapes[vid]), (cs, vid)


def test_real_api_and_map():
    """Real data goes through the HTTP layer, and both map views are valid SVG."""
    c = TestClient(main.app)
    assert isinstance(c.get("/targetsInside", params={"fir": "EGTT"}).json(), int)
    cs = next(iter(traffic.current().positions))
    body = c.get("/aircraft", params={"acid": cs}).json()
    assert body["callsign"] == cs and isinstance(body["insideFir"], list)
    for params in ({}, {"fir": "WSJC,WMFC"}):
        r = c.get("/map.svg", params=params)
        assert r.status_code == 200
        ET.fromstring(r.text)
