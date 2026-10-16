"""main.py: every HTTP route, end to end, against the seeded cache."""
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import main
from world import EXPECTED


# --- /aircraft ------------------------------------------------------------------------------------

@pytest.mark.parametrize("callsign", EXPECTED)
def test_aircraft(client, callsign):
    """insideFir and insideVolume for each connected callsign."""
    firs, volumes = EXPECTED[callsign]
    r = client.get("/aircraft", params={"acid": callsign})
    assert r.status_code == 200
    assert r.json() == {"callsign": callsign, "insideFir": firs, "insideVolume": volumes}


def test_aircraft_is_case_insensitive(client):
    """acid=low1 finds LOW1, and surrounding whitespace is ignored."""
    assert client.get("/aircraft", params={"acid": " low1 "}).json()["callsign"] == "LOW1"


def test_aircraft_not_connected(client):
    """An unknown callsign gives 404."""
    assert client.get("/aircraft", params={"acid": "QFA1"}).status_code == 404


def test_aircraft_requires_acid(client):
    """A missing acid parameter gives 422."""
    assert client.get("/aircraft").status_code == 422


# --- /aircraftInside and /targetsInside ---------------------------------------------------------------

@pytest.mark.parametrize("params, expected", [
    ({"fir": "AAAA"}, ["EDGE", "HIGH1", "LOW1", "SEA"]),
    ({"fir": "AAAA,HOLE"}, ["EDGE", "HIGH1", "LOW1", "RING", "SEA"]),
    ({"fir": "plgn"}, []),
    ({"volume": "xx/High"}, ["EDGE", "HIGH1"]),
    ({"volume": "yy/Tower"}, ["DONUT", "RING"]),
])
def test_aircraft_inside_and_targets_inside(client, params, expected):
    """/aircraftInside returns sorted callsigns, and /targetsInside the matching integer."""
    r = client.get("/aircraftInside", params=params)
    assert r.status_code == 200 and r.json() == expected
    r = client.get("/targetsInside", params=params)
    assert r.status_code == 200 and r.json() == len(expected)


def test_volume_id_with_spaces_and_slash(client, world):
    """Volume ids are URL-encoded like any query value."""
    assert client.get("/aircraftInside?volume=zz%2FSouth").json() == ["SOUTH"]


@pytest.mark.parametrize("route", ["/aircraftInside", "/targetsInside"])
@pytest.mark.parametrize("params", [{}, {"fir": "AAAA", "volume": "xx/High"}])
def test_exactly_one_target_required(client, route, params):
    """Giving neither fir nor volume, or both, is a 422."""
    assert client.get(route, params=params).status_code == 422


@pytest.mark.parametrize("route", ["/aircraftInside", "/targetsInside"])
@pytest.mark.parametrize("params", [{"fir": "NOPE"}, {"fir": "AAAA,NOPE"}, {"volume": "xx/Nope"}])
def test_unknown_target_is_404(client, route, params):
    """An unknown FIR or volume gives 404, not an empty answer."""
    r = client.get(route, params=params)
    assert r.status_code == 404
    assert "unknown id" in r.json()["detail"]


# --- /map.svg -------------------------------------------------------------------------------------

def test_map_svg(client):
    """The world map is served as image/svg+xml and parses as XML."""
    r = client.get("/map.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert ET.fromstring(r.text).get("width") == "1200"


def test_map_svg_for_firs(client):
    """?fir=A,B renders a zoomed map."""
    r = client.get("/map.svg", params={"fir": "AAAA,HOLE"})
    assert r.status_code == 200
    assert ET.fromstring(r.text).get("height") != "600"


def test_map_svg_unknown_fir(client):
    """An unknown FIR gives 404."""
    assert client.get("/map.svg", params={"fir": "NOPE"}).status_code == 404


# --- discovery ------------------------------------------------------------------------------------

def test_firs(client):
    """/firs lists every boundary id."""
    assert client.get("/firs").json() == ["AAAA", "AAAA-N", "HOLE", "PLGN", "SPLT"]


def test_volumes(client):
    """/volumes lists every volume id."""
    assert client.get("/volumes").json() == ["xx/High", "xx/Low", "yy/Tower", "zz/South"]


# --- data source failures -------------------------------------------------------------------------

@pytest.mark.parametrize("route", ["/aircraft?acid=LOW1", "/targetsInside?fir=AAAA", "/map.svg", "/firs"])
def test_no_data_and_no_network_is_503(cache, route):
    """An empty cache with the network down gives 503."""
    r = TestClient(main.app).get(route)
    assert r.status_code == 503
    assert "data source unavailable" in r.json()["detail"]


def test_stale_data_still_served_when_network_down(world):
    """With a stale cache and the network down, the API still answers (from the stale copy)."""
    import os
    import time
    old = time.time() - 3600 * 48
    for f in world.iterdir():
        os.utime(f, (old, old))
    assert TestClient(main.app).get("/targetsInside", params={"fir": "AAAA"}).json() == 4


def test_openapi_docs(client):
    """FastAPI's interactive docs are available."""
    assert client.get("/docs").status_code == 200
    assert {"/aircraft", "/aircraftInside", "/targetsInside", "/map.svg", "/firs", "/volumes"} <= set(client.get("/openapi.json").json()["paths"])
