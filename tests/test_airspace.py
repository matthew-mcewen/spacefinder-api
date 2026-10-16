"""airspace.py: dataset parsing, id rules and point lookups."""
import io
import json
import math
import os
import zipfile

import pytest

from app import airspace, config
from app.airspace import UnknownId
from app.feeds import FeedUnavailable
from app.geo import BBox
from world import FIRS, VATGLASSES_FILES, make_zip, vg_square


def volumes_from(files: dict):
    return airspace.load_volumes(zipfile.ZipFile(io.BytesIO(make_zip(files))))


def by_id(shapes, sid):
    return [s for s in shapes if s.id == sid]


# --- VatSpy --------------------------------------------------------------------------------------

def test_load_firs_one_shape_per_polygon():
    """Each polygon becomes a Shape. Repeated ids (antimeridian halves) are kept."""
    firs = airspace.load_firs(FIRS)
    assert [s.id for s in firs] == ["AAAA", "AAAA-N", "HOLE", "SPLT", "SPLT", "PLGN"]


def test_load_firs_keeps_holes():
    """Hole rings stay with their polygon."""
    (hole,) = by_id(airspace.load_firs(FIRS), "HOLE")
    assert len(hole.rings) == 2


def test_load_firs_plain_polygon_geometry():
    """Polygon features are accepted as well as MultiPolygon."""
    (plgn,) = by_id(airspace.load_firs(FIRS), "PLGN")
    assert plgn.bbox == BBox(40, 0, 50, 10)


def test_firs_have_no_vertical_limits():
    """FIRs are 2D: surface to unlimited."""
    assert all(s.floor == -math.inf and s.ceiling == math.inf for s in airspace.load_firs(FIRS))


def test_fir_bbox_and_coordinate_order():
    """Rings hold (lon, lat) tuples and the bbox matches."""
    (a,) = by_id(airspace.load_firs(FIRS), "AAAA")
    assert a.bbox == BBox(0, 0, 10, 10)
    assert a.rings[0][1] == (10, 0)


# --- VatGlasses ----------------------------------------------------------------------------------

def test_volume_ids_are_namespaced_by_file():
    """Ids are '<file stem>/<airspace id>' for both flat files and folder/airspace.json files."""
    assert {s.id for s in volumes_from(VATGLASSES_FILES)} == {"xx/Low", "xx/High", "yy/Tower", "zz/South"}


def test_repeated_volume_ids_keep_every_sector():
    """Two airspace entries with the same id give two shapes under one id."""
    assert len(by_id(volumes_from(VATGLASSES_FILES), "xx/Low")) == 2


def test_sectors_with_too_few_points_are_skipped():
    """A sector with fewer than 3 points isn't a polygon, so it's skipped."""
    assert not by_id(volumes_from(VATGLASSES_FILES), "xx/Empty")


def test_non_geometry_files_are_ignored():
    """README, positions.json and ownership/*.json are never parsed as geometry."""
    files = {k: v for k, v in VATGLASSES_FILES.items() if "positions" in k or "ownership" in k or "README" in k}
    assert volumes_from(files) == []


@pytest.mark.parametrize("vid, floor, ceiling", [
    ("xx/High", 245, math.inf),        # no max -> unlimited
    ("yy/Tower", -math.inf, 25),       # min 0 -> surface
    ("zz/South", 100, 200),
])
def test_vertical_limits(vid, floor, ceiling):
    """VatGlasses min/max become floor/ceiling flight levels."""
    (s,) = by_id(volumes_from(VATGLASSES_FILES), vid)
    assert (s.floor, s.ceiling) == (floor, ceiling)


def test_missing_min_is_surface():
    """No min at all also means the surface."""
    assert all(s.floor == -math.inf for s in by_id(volumes_from(VATGLASSES_FILES), "xx/Low"))


def test_volume_coordinates_are_lon_lat_and_signed():
    """[lat, lon] DMS points become (lon, lat) floats, with the south and west signs kept."""
    (s,) = by_id(volumes_from(VATGLASSES_FILES), "zz/South")
    assert s.bbox == BBox(-20, -20, -10, -10)
    assert s.rings[0][0] == (-20, -20)


def test_dict_form_without_id_falls_back_to_key():
    """In dict-form airspace, an entry with no 'id' is named by its key."""
    files = {"r-main/data/qq/airspace.json": {"airspace": {"K9": {"sectors": [{"points": vg_square(0, 0, 1, 1)}]}}}}
    assert [s.id for s in volumes_from(files)] == ["qq/K9"]


def test_string_flight_levels_are_numbers():
    """min/max given as strings still parse to numbers."""
    files = {"r-main/data/qq.json": {"airspace": [{"id": "S", "sectors": [{"min": "50", "max": "150", "points": vg_square(0, 0, 1, 1)}]}]}}
    (s,) = volumes_from(files)
    assert (s.floor, s.ceiling) == (50, 150)


@pytest.mark.parametrize("name, stem", [
    ("vatglasses-data-main/data/eg.json", "eg"),
    ("vatglasses-data-main/data/bk-la-ld.json", "bk-la-ld"),
    ("vatglasses-data-main/data/zny/airspace.json", "zny"),
    ("vatglasses-data-main/data/zny/positions.json", None),
    ("vatglasses-data-main/data/zau/ownership/highlow.json", None),
    ("vatglasses-data-main/data/", None),
    ("vatglasses-data-main/data/zny/", None),
    ("vatglasses-data-main/README.md", None),
    ("vatglasses-data-main/package.json", None),
])
def test_stem(name, stem):
    """Which zip entries hold geometry, and the namespace for their ids."""
    assert airspace._stem(name) == stem


# --- id rules ------------------------------------------------------------------------------------

@pytest.mark.parametrize("query, fir_id, expected", [
    ("SBCW", "SBCW", True),
    ("SBCW", "SBCW-CS", True),       # family: sub-sectors included
    ("sbcw", "SBCW-CS", True),       # case-insensitive
    ("SBCW-CS", "SBCW-CS", True),
    ("SBCW-CS", "SBCW-CSE", False),  # not a prefix match
    ("SBCW-CS", "SBCW", False),      # children don't match parents
    ("SBC", "SBCW", False),
    ("SBCW", "SBCWX", False),
])
def test_fir_matches(query, fir_id, expected):
    """FIR family rule."""
    assert airspace.fir_matches(query, fir_id) is expected


@pytest.mark.parametrize("text, expected", [
    ("EGTT", ["EGTT"]), ("EGTT,EGPX", ["EGTT", "EGPX"]), (" EGTT , EGPX ,", ["EGTT", "EGPX"]), ("", []), (",", []),
])
def test_split_ids(text, expected):
    """Comma lists split cleanly, ignoring whitespace and empty entries."""
    assert airspace.split_ids(text) == expected


def test_match_firs_expands_family(sky):
    """A parent query also covers its sub-sectors."""
    assert airspace.match_firs(sky, "AAAA") == ["AAAA", "AAAA-N"]


def test_match_firs_union_and_case(sky):
    """Comma lists are unioned, deduplicated and case-insensitive."""
    assert airspace.match_firs(sky, "aaaa-n, HOLE, AAAA") == ["AAAA", "AAAA-N", "HOLE"]


@pytest.mark.parametrize("text", ["NOPE", "AAAA,NOPE", "", " , "])
def test_match_firs_unknown(sky, text):
    """Any query that matches nothing is an error, rather than a silent zero."""
    with pytest.raises(UnknownId):
        airspace.match_firs(sky, text)


# --- Airspace + lookups --------------------------------------------------------------------------

def test_airspace_id_lists(sky):
    """The id lists are sorted and deduplicated."""
    assert sky.fir_ids == ["AAAA", "AAAA-N", "HOLE", "PLGN", "SPLT"]
    assert sky.volume_ids == ["xx/High", "xx/Low", "yy/Tower", "zz/South"]


@pytest.mark.parametrize("lon, lat, expected", [
    (5, 2, ["AAAA"]), (5, 8, ["AAAA", "AAAA-N"]), (25, 5, []), (22, 5, ["HOLE"]),
    (175, 5, ["SPLT"]), (-175, 5, ["SPLT"]), (45, 5, ["PLGN"]), (-100, -50, []),
])
def test_containing_firs(sky, lon, lat, expected):
    """FIR lookups go grid -> bbox -> ray cast."""
    assert airspace.containing(sky.fir_index, lon, lat) == expected


@pytest.mark.parametrize("lon, lat, fl, expected", [
    (5, 2, 100, ["xx/Low"]), (5, 2, 245, ["xx/High"]), (5, 2, -0.5, ["xx/Low"]),
    (-15, -15, 150, ["zz/South"]), (-15, -15, 50, []), (-15, -15, 200, []),
])
def test_containing_volumes_is_3d(sky, lon, lat, fl, expected):
    """Volume lookups check the altitude band too."""
    assert airspace.containing(sky.vol_index, lon, lat, fl) == expected


# --- current() -----------------------------------------------------------------------------------

def test_current_loads_from_cache(world):
    """current() builds the Airspace from the cached files."""
    assert airspace.current().fir_ids == ["AAAA", "AAAA-N", "HOLE", "PLGN", "SPLT"]


def test_current_is_memoised(world):
    """Unchanged source files mean the same Airspace object, with no re-parse."""
    assert airspace.current() is airspace.current()


def test_current_reloads_when_a_source_file_changes(world):
    """A new mtime on a source file triggers a re-parse."""
    first = airspace.current()
    path = config.CACHE_DIR / "Boundaries.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": FIRS["features"][:1]}))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    second = airspace.current()
    assert second is not first
    assert second.fir_ids == ["AAAA"]


def test_current_without_cache_or_network(cache):
    """No cache and no network means FeedUnavailable."""
    with pytest.raises(FeedUnavailable):
        airspace.current()
