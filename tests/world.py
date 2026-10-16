"""A tiny synthetic world shared by the tests (lon range x lat range, degrees).

FIRs (VatSpy)
    AAAA      0..10 x 0..10      parent
    AAAA-N    0..10 x 5..10      sub-sector overlapping AAAA's north half
    HOLE     20..30 x 0..10      with a hole at 24..26 x 4..6
    SPLT    170..180 and -180..-170 x 0..10   two features, one id (antimeridian split)
    PLGN     40..50 x 0..10      a plain Polygon feature (the rest are MultiPolygon)

Volumes (VatGlasses)
    xx/Low     0..10 x 0..10 plus a 10..12 x 0..2 piece   surface..FL245
    xx/High    0..10 x 0..10                              FL245..unlimited
    yy/Tower  20..30 x 0..10                              surface..FL25 (dict-form folder file)
    zz/South -20..-10 x -20..-10                          FL100..FL200
"""
import io
import json
import zipfile


def square(w, s, e, n):
    """Closed GeoJSON ring of [lon, lat] pairs."""
    return [[w, s], [e, s], [e, n], [w, n], [w, s]]


def dms(deg: int, digits: int) -> str:
    """Whole degrees -> VatGlasses DMS string: dms(-7, 7) == '-0070000'."""
    return ("-" if deg < 0 else "") + f"{abs(deg):0{digits - 4}d}0000"


def vg_square(w, s, e, n):
    """VatGlasses points: [lat, lon] DMS strings."""
    return [[dms(lat, 6), dms(lon, 7)] for lon, lat in square(w, s, e, n)]


def feature(fid, coordinates, kind="MultiPolygon"):
    return {"type": "Feature", "properties": {"id": fid, "oceanic": "0"}, "geometry": {"type": kind, "coordinates": coordinates}}


FIRS = {"type": "FeatureCollection", "features": [
    feature("AAAA", [[square(0, 0, 10, 10)]]),
    feature("AAAA-N", [[square(0, 5, 10, 10)]]),
    feature("HOLE", [[square(20, 0, 30, 10), square(24, 4, 26, 6)]]),
    feature("SPLT", [[square(170, 0, 180, 10)]]),
    feature("SPLT", [[square(-180, 0, -170, 10)]]),
    feature("PLGN", [square(40, 0, 50, 10)], kind="Polygon"),
]}

VATGLASSES_FILES = {
    "vatglasses-data-main/README.md": "not geometry",
    "vatglasses-data-main/data/xx.json": {"airspace": [
        {"id": "Low", "group": "XX", "owner": ["A"], "sectors": [{"max": 245, "points": vg_square(0, 0, 10, 10)}]},
        {"id": "High", "group": "XX", "owner": ["B"], "sectors": [{"min": 245, "points": vg_square(0, 0, 10, 10)}]},
        {"id": "Low", "group": "XX", "owner": ["A"], "sectors": [{"max": 245, "points": vg_square(10, 0, 12, 2)}]},
        {"id": "Empty", "group": "XX", "sectors": [{"min": 0, "max": 100, "points": [["000000", "0000000"]]}]},
    ], "positions": {"A": {"type": "CTR"}}},
    "vatglasses-data-main/data/yy/airspace.json": {"airspace": {
        "T1": {"id": "Tower", "group": "YY", "sectors": [{"min": 0, "max": 25, "points": vg_square(20, 0, 30, 10)}]},
    }},
    "vatglasses-data-main/data/yy/positions.json": {"positions": {"T": {"type": "TWR"}}},
    "vatglasses-data-main/data/yy/ownership/default.json": {"airspace": {"Tower": [["T1", "T2"]]}},
    "vatglasses-data-main/data/zz.json": {"airspace": [
        {"id": "South", "group": "ZZ", "sectors": [{"min": 100, "max": 200, "points": vg_square(-20, -20, -10, -10)}]},
    ]},
}


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content if isinstance(content, str) else json.dumps(content))
    return buf.getvalue()


def pilot(callsign, lon, lat, alt):
    return {"cid": 1, "name": "Private Person", "callsign": callsign, "latitude": lat, "longitude": lon,
            "altitude": alt, "groundspeed": 450, "heading": 90, "flight_plan": None}


FEED = {"general": {"update_timestamp": "2026-10-16T00:00:00Z"}, "pilots": [
    pilot("LOW1", 5, 2, 10000),
    pilot("HIGH1", 5, 8, 35000),
    pilot("EDGE", 5, 2, 24500),      # exactly FL245: belongs to High only
    pilot("SEA", 5, 2, -50),         # below sea level: still in the surface-based Low
    pilot("donut", 25, 5, 0),        # in HOLE's hole; lower-case callsign
    pilot("RING", 22, 5, 1000),
    pilot("EAST", 175, 5, 30000),
    pilot("WEST", -175, 5, 30000),
    pilot("SOUTH", -15, -15, 15000),
    pilot("LOWEXT", 11, 1, 0),
    pilot("NOWHERE", -100, -50, 30000),
]}

# callsign -> (insideFir, insideVolume)
EXPECTED = {
    "LOW1": (["AAAA"], ["xx/Low"]),
    "HIGH1": (["AAAA", "AAAA-N"], ["xx/High"]),
    "EDGE": (["AAAA"], ["xx/High"]),
    "SEA": (["AAAA"], ["xx/Low"]),
    "DONUT": ([], ["yy/Tower"]),
    "RING": (["HOLE"], ["yy/Tower"]),
    "EAST": (["SPLT"], []),
    "WEST": (["SPLT"], []),
    "SOUTH": ([], ["zz/South"]),
    "LOWEXT": ([], ["xx/Low"]),
    "NOWHERE": ([], []),
}
