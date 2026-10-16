"""Airspace geometry: VatSpy FIR boundaries and VatGlasses sector volumes, as flat lists of Shapes.

FIR ids are VatSpy boundary ids ("EGTT", "SBCW-CS").
Volume ids are "<VatGlasses data file>/<airspace id>" ("eg/Lakes High (4)"), because bare ids repeat.
"""
import io
import json
import math
import zipfile
from dataclasses import dataclass

from app import config, feeds, geo


class UnknownId(LookupError):
    """A requested FIR or volume id doesn't exist in the dataset."""


@dataclass(frozen=True, slots=True)
class Shape:
    """One polygon (outer ring + holes) with vertical limits in flight levels."""
    id: str
    rings: tuple
    bbox: geo.BBox
    floor: float = -math.inf
    ceiling: float = math.inf


class Airspace:
    """Every FIR and volume shape, with a grid index over each for fast point lookups."""

    def __init__(self, firs: list[Shape], volumes: list[Shape]):
        self.firs, self.volumes = firs, volumes
        self.fir_index, self.vol_index = _index(firs), _index(volumes)
        self.fir_ids = sorted({s.id for s in firs})
        self.volume_ids = sorted({s.id for s in volumes})


# --- loading -------------------------------------------------------------------------------------

def load_firs(geojson: dict) -> list[Shape]:
    """VatSpy Boundaries.geojson -> one Shape per polygon. Repeated ids are kept
    (VatSpy splits FIRs that straddle the antimeridian into two features)."""
    shapes = []
    for f in geojson["features"]:
        g = f["geometry"]
        for poly in g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]:
            rings = tuple(tuple((p[0], p[1]) for p in ring) for ring in poly)
            shapes.append(Shape(f["properties"]["id"], rings, geo.bbox(rings)))
    return shapes


def load_volumes(zf: zipfile.ZipFile) -> list[Shape]:
    """VatGlasses data-repo zip -> one Shape per sector. A missing (or zero) `min` means the
    surface, so aircraft below sea level still count. A missing `max` means unlimited."""
    shapes = []
    for name in zf.namelist():
        stem = _stem(name)
        if stem is None:
            continue
        airspace = json.loads(zf.read(name)).get("airspace", [])
        for key, a in airspace.items() if isinstance(airspace, dict) else enumerate(airspace):
            for s in a.get("sectors", []):
                if len(s.get("points", [])) < 3:
                    continue
                ring = tuple((geo.dms_to_deg(p[1]), geo.dms_to_deg(p[0])) for p in s["points"])  # [lat, lon] -> (lon, lat)
                floor = float(s.get("min") or 0) or -math.inf
                ceiling = math.inf if s.get("max") is None else float(s["max"])
                shapes.append(Shape(f"{stem}/{a.get('id', key)}", (ring,), geo.bbox((ring,)), floor, ceiling))
    return shapes


def _stem(name: str) -> str | None:
    """Which zip entries hold geometry, and the namespace for their ids:
    '…/data/eg.json' -> 'eg', '…/data/zny/airspace.json' -> 'zny', anything else -> None."""
    _, found, rel = name.partition("/data/")
    if not found or not rel.endswith(".json"):
        return None
    folder, _, file = rel.partition("/")
    if not file:
        return rel.removesuffix(".json")
    return folder if file == "airspace.json" else None


def _index(shapes: list[Shape]) -> geo.GridIndex:
    index = geo.GridIndex(config.GRID_DEG)
    for s in shapes:
        index.add(s, s.bbox)
    return index


# --- queries -------------------------------------------------------------------------------------

def containing(index: geo.GridIndex, lon: float, lat: float, fl: float = 0.0) -> list[str]:
    """Sorted ids of every indexed shape containing the point: grid cell -> bbox -> altitude band -> ray cast."""
    return sorted({
        s.id for s in index.near(lon, lat)
        if geo.contains(s.bbox, lon, lat) and geo.in_band(fl, s.floor, s.ceiling) and geo.point_in_rings(lon, lat, s.rings)
    })


def split_ids(text: str) -> list[str]:
    """'EGTT, EGPX' -> ['EGTT', 'EGPX']"""
    return [t.strip() for t in text.split(",") if t.strip()]


def fir_matches(query: str, fir_id: str) -> bool:
    """Family rule, case-insensitive: 'SBCW' matches 'SBCW' and 'SBCW-CS', but 'SBCW-CS' doesn't match 'SBCW-CSE'."""
    q, f = query.upper(), fir_id.upper()
    return f == q or f.startswith(q + "-")


def match_firs(sky: Airspace, text: str) -> list[str]:
    """Expand comma-separated FIR queries into the boundary ids they cover. Raises UnknownId if any match nothing."""
    queries = split_ids(text)
    if not queries:
        raise UnknownId(text)
    ids = set()
    for q in queries:
        hits = [f for f in sky.fir_ids if fir_matches(q, f)]
        if not hits:
            raise UnknownId(q)
        ids.update(hits)
    return sorted(ids)


# --- live dataset --------------------------------------------------------------------------------

_memo = feeds.Memo()


def current() -> Airspace:
    """The latest Airspace, re-parsed only when a cached source file changes (at most daily)."""
    spy = feeds.fetch_cached(config.VATSPY_URL, "Boundaries.geojson", config.STATIC_TTL, check=json.loads)
    vg = feeds.fetch_cached(config.VATGLASSES_URL, "vatglasses.zip", config.STATIC_TTL, check=lambda b: zipfile.ZipFile(io.BytesIO(b)))
    return _memo.get(
        (spy.stat().st_mtime_ns, vg.stat().st_mtime_ns),
        lambda: Airspace(load_firs(json.loads(spy.read_bytes())), load_volumes(zipfile.ZipFile(io.BytesIO(vg.read_bytes())))),
    )
