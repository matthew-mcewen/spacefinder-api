"""traffic.py: assigning pilots to airspace, and the inside/count queries."""
import os

import pytest

from app import airspace, config, feeds, traffic
from app.airspace import UnknownId
from world import EXPECTED, FEED


@pytest.mark.parametrize("callsign", EXPECTED)
def test_assignment(snap, callsign):
    """Every synthetic pilot lands in exactly the FIRs (2D) and volumes (3D) expected."""
    firs, volumes = EXPECTED[callsign]
    assert snap.firs[callsign] == firs
    assert snap.volumes[callsign] == volumes


def test_parent_and_sub_sector_both_listed(snap):
    """VatSpy parents and sub-sectors overlap, and both are reported, sorted."""
    assert snap.firs["HIGH1"] == ["AAAA", "AAAA-N"]


def test_fl245_boundary_goes_to_upper_sector_only(snap):
    """At exactly FL245, a Low (max 245) / High (min 245) split counts the aircraft once, in High."""
    assert snap.volumes["EDGE"] == ["xx/High"]


def test_callsigns_are_upper_cased(snap):
    """Callsigns are stored upper-case."""
    assert "DONUT" in snap.positions and "donut" not in snap.positions


def test_positions_recorded(snap):
    """Positions are (lon, lat) for every pilot."""
    assert snap.positions["SOUTH"] == (-15, -15)
    assert len(snap.positions) == len(FEED["pilots"])


def test_updated_timestamp(snap):
    """The snapshot carries the datafeed's own timestamp."""
    assert snap.updated == "2026-10-16T00:00:00Z"


# --- inside ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("query, expected", [
    ("AAAA", ["EDGE", "HIGH1", "LOW1", "SEA"]),               # family: includes AAAA-N traffic
    ("AAAA-N", ["HIGH1"]),
    ("aaaa-n", ["HIGH1"]),
    ("SPLT", ["EAST", "WEST"]),                              # both antimeridian halves
    ("AAAA,HOLE", ["EDGE", "HIGH1", "LOW1", "RING", "SEA"]),  # union
    ("AAAA,AAAA-N", ["EDGE", "HIGH1", "LOW1", "SEA"]),        # no duplicates
    ("PLGN", []),                                            # known FIR, empty sky
])
def test_inside_fir(snap, query, expected):
    """FIR queries use the family rule, comma union and dedupe, and return sorted callsigns."""
    assert traffic.inside(snap, "fir", query) == expected


@pytest.mark.parametrize("query, expected", [
    ("xx/High", ["EDGE", "HIGH1"]),
    ("xx/Low", ["LOW1", "LOWEXT", "SEA"]),   # both pieces of the repeated id
    ("yy/Tower", ["DONUT", "RING"]),
    ("zz/South", ["SOUTH"]),
])
def test_inside_volume(snap, query, expected):
    """Volume queries match one exact id."""
    assert traffic.inside(snap, "volume", query) == expected


def test_inside_known_volume_with_no_traffic(sky):
    """A known volume with nobody in it gives an empty list, not an error."""
    empty = traffic.build({"general": {"update_timestamp": "t"}, "pilots": []}, sky)
    assert traffic.inside(empty, "volume", "xx/High") == []


@pytest.mark.parametrize("kind, query", [
    ("fir", "NOPE"), ("fir", "AAAA,NOPE"), ("fir", ""),
    ("volume", "xx/Nope"), ("volume", "XX/HIGH"),   # volume ids are exact and case-sensitive
])
def test_inside_unknown_id(snap, kind, query):
    """Unknown ids raise UnknownId (a 404 over HTTP)."""
    with pytest.raises(UnknownId):
        traffic.inside(snap, kind, query)


# --- current() -------------------------------------------------------------------------------------

def bump_mtime(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_current_builds_from_cache(world):
    """current() reads the cached datafeed and airspace."""
    assert traffic.current().firs["HIGH1"] == ["AAAA", "AAAA-N"]


def test_current_does_not_download_within_ttl(world, monkeypatch):
    """With a fresh cache, many requests mean zero downloads."""
    calls = []
    monkeypatch.setattr(feeds, "_download", lambda url: calls.append(url))
    for _ in range(50):
        traffic.current()
    assert calls == []


def test_current_is_memoised(world):
    """An unchanged feed file returns the same Snapshot object, with no recompute."""
    assert traffic.current() is traffic.current()


def test_current_rebuilds_when_feed_changes(world):
    """A new feed file (new mtime) triggers a rebuild."""
    first = traffic.current()
    bump_mtime(config.CACHE_DIR / "vatsim-data.json")
    assert traffic.current() is not first


def test_current_rebuilds_when_airspace_changes(world):
    """New airspace data triggers a rebuild even if the feed is unchanged."""
    first = traffic.current()
    bump_mtime(config.CACHE_DIR / "vatglasses.zip")
    second = traffic.current()
    assert second is not first
    assert second.sky is not first.sky
