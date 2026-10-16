"""geo.py: the hot maths."""
import math

import pytest

from app import geo
from app.geo import BBox

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]                                  # open ring
CONCAVE_C = [(0, 0), (10, 0), (10, 3), (3, 3), (3, 7), (10, 7), (10, 10), (0, 10)]  # notch opens east
DONUT = [[(0, 0), (10, 0), (10, 10), (0, 10)], [(4, 4), (6, 4), (6, 6), (4, 6)]]


# --- DMS parsing ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("000000", 0.0),
    ("-000000", 0.0),
    ("610000", 61.0),                           # 6-digit latitude
    ("-061900", -(6 + 19 / 60)),
    ("512341", 51 + 23 / 60 + 41 / 3600),
    ("1410000", 141.0),                         # 7-digit longitude
    ("-0070000", -7.0),
    ("0613045", 61.5125),
    ("-0613045", -61.5125),
    ("1795959", 179 + 59 / 60 + 59 / 3600),
])
def test_dms_to_deg(text, expected):
    """VatGlasses DMS strings parse to signed decimal degrees."""
    assert geo.dms_to_deg(text) == pytest.approx(expected)


# --- boxes ---------------------------------------------------------------------------------------

def test_bbox_single_ring():
    """A ring's box is its min/max lon and lat."""
    assert geo.bbox([[(3, -2), (7, 5), (-1, 1)]]) == BBox(-1, -2, 7, 5)


def test_bbox_spans_all_rings():
    """Every ring counts, holes included."""
    assert geo.bbox([[(0, 0), (1, 1)], [(5, -5), (6, 6)]]) == BBox(0, -5, 6, 6)


def test_union():
    """A union covers every box."""
    assert geo.union([BBox(0, 0, 1, 1), BBox(-5, 2, 3, 9)]) == BBox(-5, 0, 3, 9)


def test_union_accepts_generators():
    """union() takes any iterable, not just a list."""
    assert geo.union(b for b in [BBox(0, 0, 1, 1)]) == BBox(0, 0, 1, 1)


def test_pad():
    """Padding grows every side."""
    assert geo.pad(BBox(0, 0, 10, 10), 5) == BBox(-5, -5, 15, 15)


def test_pad_clamps_to_world():
    """Padding never goes past ±180 lon or ±90 lat."""
    assert geo.pad(BBox(-178, -88, 178, 88), 5) == geo.WORLD


@pytest.mark.parametrize("point, expected", [
    ((5, 5), True), ((0, 0), True), ((10, 10), True), ((10.01, 5), False), ((5, -0.01), False),
])
def test_contains(point, expected):
    """Box containment is inclusive of edges."""
    assert geo.contains(BBox(0, 0, 10, 10), *point) is expected


@pytest.mark.parametrize("other, expected", [
    (BBox(5, 5, 15, 15), True),      # overlap
    (BBox(10, 0, 20, 10), True),     # shared edge
    (BBox(2, 2, 3, 3), True),        # nested
    (BBox(11, 0, 20, 10), False),    # gap east
    (BBox(0, -9, 10, -1), False),    # gap south
])
def test_overlaps(other, expected):
    """Boxes overlap when they intersect or touch, in either order."""
    a = BBox(0, 0, 10, 10)
    assert geo.overlaps(a, other) is expected
    assert geo.overlaps(other, a) is expected


# --- point in polygon ----------------------------------------------------------------------------

@pytest.mark.parametrize("point, expected", [
    ((5, 5), True), ((0.001, 0.001), True), ((9.999, 9.999), True),
    ((-1, 5), False), ((11, 5), False), ((5, -1), False), ((5, 11), False), ((500, -80), False),
])
def test_square(point, expected):
    """Points in and around a square."""
    assert geo.point_in_rings(*point, [SQUARE]) is expected


@pytest.mark.parametrize("x", [-1, 0.5, 3, 7.25, 9.9, 12])
@pytest.mark.parametrize("y", [-1, 0.5, 3, 7.25, 9.9, 12])
def test_open_and_closed_rings_agree(x, y):
    """A ring that repeats its first point gives the same answer as one that doesn't."""
    assert geo.point_in_rings(x, y, [SQUARE]) == geo.point_in_rings(x, y, [SQUARE + [SQUARE[0]]])


@pytest.mark.parametrize("point, expected", [
    ((1, 5), True),     # spine of the C
    ((5, 1), True),     # bottom arm
    ((5, 8.5), True),   # top arm
    ((5, 5), False),    # inside the notch
    ((9, 5), False),    # notch, near the open side
])
def test_concave(point, expected):
    """A concave C shape: the notch is outside even though it's inside the bbox."""
    assert geo.point_in_rings(*point, [CONCAVE_C]) is expected


@pytest.mark.parametrize("point, expected", [((1, 1), True), ((5, 5), False), ((4.5, 5), False), ((3.9, 5), True)])
def test_hole(point, expected):
    """A hole ring removes its area from the outer ring."""
    assert geo.point_in_rings(*point, DONUT) is expected


def test_triangle():
    """A triangle with a slanted edge."""
    tri = [(0, 0), (10, 0), (0, 10)]
    assert geo.point_in_rings(2, 2, [tri])
    assert not geo.point_in_rings(6, 6, [tri])


def test_negative_coordinates():
    """South-west quadrant geometry works the same."""
    ring = [(-155, -45), (-145, -45), (-145, -35), (-155, -35)]
    assert geo.point_in_rings(-150, -40, [ring])
    assert not geo.point_in_rings(150, 40, [ring])


def test_shared_edge_belongs_to_exactly_one_shape():
    """Boundary convention: west/south edges are inside, east/north edges outside, so a point on
    the border between two adjacent shapes is counted once."""
    west, east = [(0, 0), (10, 0), (10, 10), (0, 10)], [(10, 0), (20, 0), (20, 10), (10, 10)]
    for point in [(10, 5), (10, 0.5), (10, 9.5)]:
        assert geo.point_in_rings(*point, [west]) + geo.point_in_rings(*point, [east]) == 1


# --- altitude band -------------------------------------------------------------------------------

@pytest.mark.parametrize("fl, floor, ceiling, expected", [
    (0, -math.inf, 245, True),
    (-0.5, -math.inf, 245, True),    # below sea level, surface-based sector
    (244.99, -math.inf, 245, True),
    (245, -math.inf, 245, False),    # ceiling is exclusive
    (245, 245, math.inf, True),      # floor is inclusive
    (244.99, 245, math.inf, False),
    (1e6, 245, math.inf, True),
])
def test_in_band(fl, floor, ceiling, expected):
    """Floor inclusive, ceiling exclusive."""
    assert geo.in_band(fl, floor, ceiling) is expected


# --- grid index ----------------------------------------------------------------------------------

def test_grid_finds_item_in_its_cell_only():
    """A small item is a candidate in its own cell, not its neighbours'."""
    g = geo.GridIndex(1)
    g.add("a", BBox(0.2, 0.2, 0.8, 0.8))
    assert g.near(0.5, 0.5) == ["a"]
    assert g.near(1.5, 0.5) == []


def test_grid_large_item_spans_cells():
    """An item is registered in every cell its box touches."""
    g = geo.GridIndex(1)
    g.add("big", BBox(-2.5, -0.5, 2.5, 0.5))
    assert all(g.near(x, 0) == ["big"] for x in (-2.9, -1, 0, 1, 2.9))
    assert g.near(3.1, 0) == []
    assert g.near(-3.1, 0) == []


def test_grid_negative_coordinates_floor_correctly():
    """-0.5 belongs to cell -1, not cell 0."""
    g = geo.GridIndex(1)
    g.add("sw", BBox(-0.9, -0.9, -0.1, -0.1))
    assert g.near(-0.5, -0.5) == ["sw"]
    assert g.near(0.5, 0.5) == []


def test_grid_custom_cell_size():
    """Cells can be bigger than a degree."""
    g = geo.GridIndex(5)
    g.add("a", BBox(0, 0, 4, 4))
    assert g.near(4.9, 4.9) == ["a"]
    assert g.near(5.1, 0) == []


def test_grid_many_items_share_a_cell():
    """Every item touching a cell is returned, in insertion order."""
    g = geo.GridIndex(1)
    for name in "abc":
        g.add(name, BBox(0.1, 0.1, 0.2, 0.2))
    assert g.near(0.5, 0.5) == ["a", "b", "c"]


def test_grid_lookup_does_not_grow_index():
    """Looking up empty cells doesn't add entries."""
    g = geo.GridIndex(1)
    g.near(50, 50)
    assert len(g.cells) == 0


# --- projection ----------------------------------------------------------------------------------

def test_world_projection():
    """The world maps to 1200x600 with corners and centre where you'd expect."""
    to_xy, w, h = geo.projector(geo.WORLD, 1200)
    assert (w, h) == (1200, 600)
    assert to_xy(-180, 90) == (0, 0)
    assert to_xy(180, -90) == (1200, 600)
    assert to_xy(0, 0) == (600, 300)


def test_projection_is_north_up():
    """Higher latitudes have smaller y."""
    to_xy, _, _ = geo.projector(BBox(0, 0, 10, 10), 100)
    assert to_xy(5, 9)[1] < to_xy(5, 1)[1]


def test_projection_squeezes_longitude_by_cos_mid_latitude():
    """At a 60° mid-latitude a degree of longitude is drawn half as wide as a degree of latitude."""
    to_xy, w, h = geo.projector(BBox(0, 50, 10, 70), 1200)
    assert (w, h) == (pytest.approx(300), pytest.approx(1200))
    assert to_xy(10, 50) == (pytest.approx(300), pytest.approx(1200))


@pytest.mark.parametrize("box", [BBox(0, 0, 40, 10), BBox(100, -40, 110, 30), BBox(-20, 60, 20, 80)])
def test_projection_longest_side_is_max_px(box):
    """Whatever the shape, the longest side is exactly max_px, with the aspect ratio preserved."""
    _, w, h = geo.projector(box, 800)
    assert max(w, h) == pytest.approx(800)
    assert w / h == pytest.approx((box.e - box.w) * math.cos(math.radians((box.s + box.n) / 2)) / (box.n - box.s))
