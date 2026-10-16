"""Hot maths. Every geometric calculation lives here: pure functions, no I/O.

Points are always (lon, lat) in decimal degrees, i.e. (x, y).
Flight levels (FL) are hundreds of feet: 35000 ft == FL350.
"""
import math
from collections import defaultdict
from typing import NamedTuple

Point = tuple[float, float]  # (lon, lat)


class BBox(NamedTuple):
    w: float  # min lon
    s: float  # min lat
    e: float  # max lon
    n: float  # max lat


WORLD = BBox(-180.0, -90.0, 180.0, 90.0)


def dms_to_deg(s: str) -> float:
    """VatGlasses '[-]DDMMSS' (lat) or '[-]DDDMMSS' (lon) -> signed decimal degrees."""
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("-")
    return sign * (int(s[:-4]) + int(s[-4:-2]) / 60 + int(s[-2:]) / 3600)


def bbox(rings) -> BBox:
    """Bounding box of one or more rings."""
    xs = [x for ring in rings for x, _ in ring]
    ys = [y for ring in rings for _, y in ring]
    return BBox(min(xs), min(ys), max(xs), max(ys))


def union(boxes) -> BBox:
    """Smallest box covering every box given."""
    boxes = list(boxes)
    return BBox(min(b.w for b in boxes), min(b.s for b in boxes), max(b.e for b in boxes), max(b.n for b in boxes))


def pad(b: BBox, deg: float) -> BBox:
    """Grow a box by `deg` on every side, clamped to the world."""
    return BBox(max(b.w - deg, -180), max(b.s - deg, -90), min(b.e + deg, 180), min(b.n + deg, 90))


def contains(b: BBox, lon: float, lat: float) -> bool:
    """Is the point inside (or on the edge of) the box?"""
    return b.w <= lon <= b.e and b.s <= lat <= b.n


def overlaps(a: BBox, b: BBox) -> bool:
    """Do two boxes touch or intersect?"""
    return a.w <= b.e and b.w <= a.e and a.s <= b.n and b.s <= a.n


def point_in_rings(lon: float, lat: float, rings) -> bool:
    """Even-odd ray cast: shoot a ray due east from the point and count the edges it crosses.
    An odd count means inside. Holes are just extra rings: crossing one flips the answer back.
    Rings may be closed (first == last) or open; both work."""
    inside = False
    for ring in rings:
        x1, y1 = ring[-1]
        for x2, y2 in ring:
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
            x1, y1 = x2, y2
    return inside


def in_band(fl: float, floor: float, ceiling: float) -> bool:
    """Vertical test. The floor is inclusive and the ceiling exclusive, so stacked sectors never double-count."""
    return floor <= fl < ceiling


class GridIndex:
    """Buckets items into cell_deg x cell_deg cells by their bounding box. A point lookup
    then only tests the handful of items whose box touches the point's cell."""

    def __init__(self, cell_deg: float = 1.0):
        self.cell = cell_deg
        self.cells = defaultdict(list)

    def _key(self, lon: float, lat: float) -> tuple[int, int]:
        return math.floor(lon / self.cell), math.floor(lat / self.cell)

    def add(self, item, b: BBox) -> None:
        (x0, y0), (x1, y1) = self._key(b.w, b.s), self._key(b.e, b.n)
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                self.cells[gx, gy].append(item)

    def near(self, lon: float, lat: float) -> list:
        """Candidates only: callers still run the exact test."""
        return self.cells.get(self._key(lon, lat), [])


def projector(b: BBox, max_px: float):
    """Equirectangular projection of box `b` onto a canvas whose longest side is `max_px`.
    Longitude is squeezed by cos(mid-latitude) so zoomed-in views aren't stretched sideways.
    Returns (to_xy, width, height). y grows downward, so north is up."""
    kx = math.cos(math.radians((b.s + b.n) / 2))
    w_deg, h_deg = (b.e - b.w) * kx, b.n - b.s
    scale = max_px / max(w_deg, h_deg)

    def to_xy(lon: float, lat: float) -> tuple[float, float]:
        return (lon - b.w) * kx * scale, (b.n - lat) * scale

    return to_xy, w_deg * scale, h_deg * scale
