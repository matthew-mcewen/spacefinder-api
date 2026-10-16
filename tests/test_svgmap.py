"""svgmap.py: the minimalist traffic map."""
import re
import xml.etree.ElementTree as ET

import pytest

from app import geo, svgmap
from app.airspace import UnknownId
from app.geo import BBox

NS = "{http://www.w3.org/2000/svg}"
PLUS = re.compile(r"M([\d.-]+),([\d.-]+)h3M([\d.-]+),([\d.-]+)v3")


def paths(svg: str) -> dict[str, str]:
    """class -> d attribute of every <path>."""
    return {p.get("class"): p.get("d") for p in ET.fromstring(svg).iter(f"{NS}path")}


def pluses(svg: str) -> list[tuple[float, ...]]:
    return [tuple(map(float, m)) for m in PLUS.findall(paths(svg)["ac"])]


def test_valid_svg_document(snap):
    """The output parses as SVG XML with a background and two paths: boundaries and aircraft."""
    root = ET.fromstring(svgmap.render(snap, geo.WORLD))
    assert root.tag == f"{NS}svg"
    assert root.find(f"{NS}rect").get("class") == "bg"
    assert set(paths(svgmap.render(snap, geo.WORLD))) == {"fir", "ac"}


def test_follows_light_and_dark_preference(snap):
    """Light colours by default, swapped under prefers-color-scheme: dark."""
    svg = svgmap.render(snap, geo.WORLD)
    assert "svg{--bg:#fff;--fg:#000}" in svg
    assert "@media (prefers-color-scheme:dark){svg{--bg:#000;--fg:#fff}}" in svg


def test_boundaries_are_subtle(snap):
    """Boundaries are a faint, thin, unfilled stroke."""
    assert ".fir{fill:none;stroke:var(--fg);stroke-opacity:.15;stroke-width:.5" in svgmap.render(snap, geo.WORLD)


def test_no_labels_or_flight_details(snap):
    """No text elements, callsigns or FIR names appear anywhere."""
    svg = svgmap.render(snap, geo.WORLD)
    assert "<text" not in svg
    assert not any(cs in svg for cs in snap.positions)
    assert "AAAA" not in svg and "Private Person" not in svg


def test_world_view_size(snap):
    """The world map is 1200x600 px."""
    root = ET.fromstring(svgmap.render(snap, geo.WORLD))
    assert (root.get("width"), root.get("height"), root.get("viewBox")) == ("1200", "600", "0 0 1200 600")


def test_one_plus_per_aircraft(snap):
    """On the world view, every connected aircraft gets exactly one plus."""
    assert len(pluses(svgmap.render(snap, geo.WORLD))) == len(snap.positions)


def test_plus_is_3x3_px(snap):
    """Each plus is a 3 px horizontal and a 3 px vertical stroke, crossing at their midpoints."""
    for hx, hy, vx, vy in pluses(svgmap.render(snap, geo.WORLD)):
        assert vx - hx == 1.5    # vertical stroke sits at the middle of the horizontal one
        assert hy - vy == 1.5    # horizontal stroke sits at the middle of the vertical one


def test_plus_snaps_to_pixel_centre():
    """Centres snap to .5 so 1 px strokes stay crisp."""
    assert svgmap._plus(10.2, 20.9) == "M9,20.5h3M10.5,19v3"


# --- view -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("fir", [None, ""])
def test_view_defaults_to_world(sky, fir):
    """With no FIR given, the whole world is shown."""
    assert svgmap.view(sky, fir) == geo.WORLD


@pytest.mark.parametrize("fir, box", [
    ("AAAA", BBox(-5, -5, 15, 15)),         # extent + 5 degrees
    ("AAAA-N", BBox(-5, 0, 15, 15)),        # a sub-sector on its own
    ("AAAA,HOLE", BBox(-5, -5, 35, 15)),    # comma list: combined extent
    ("aaaa, plgn", BBox(-5, -5, 55, 15)),   # case-insensitive, with spaces
    ("SPLT", BBox(-180, -5, 180, 15)),      # clamped (antimeridian FIR -> world-wide)
])
def test_view_frames_watched_firs(sky, fir, box):
    """The watched FIRs' combined extent, padded by 5 degrees and clamped to the world."""
    assert svgmap.view(sky, fir) == box


def test_view_unknown_fir(sky):
    """An unknown FIR is an error."""
    with pytest.raises(UnknownId):
        svgmap.view(sky, "AAAA,NOPE")


def test_fir_view_shows_only_aircraft_inside_it(snap, sky):
    """Only aircraft inside the view box are drawn."""
    svg = svgmap.render(snap, svgmap.view(sky, "AAAA"))   # -5..15 x -5..15
    assert len(pluses(svg)) == 5                          # LOW1, HIGH1, EDGE, SEA, LOWEXT


def test_fir_view_culls_far_boundaries(snap, sky):
    """Only boundaries touching the view are drawn: AAAA and AAAA-N here, not HOLE, SPLT or PLGN."""
    assert paths(svgmap.render(snap, svgmap.view(sky, "AAAA")))["fir"].count("M") == 2


def test_fir_view_is_square_for_square_extent_at_equator(snap, sky):
    """A square extent near the equator gives a near-square canvas whose longest side is MAP_PX."""
    root = ET.fromstring(svgmap.render(snap, svgmap.view(sky, "AAAA")))
    w, h = int(root.get("width")), int(root.get("height"))
    assert h == 1200 and 1190 <= w <= 1200


# --- ring paths -----------------------------------------------------------------------------------

def test_ring_path_closed_and_rounded():
    """Rings become 'M…L…Z' with points rounded to 0.1 px."""
    assert svgmap._ring([(0, 0), (1, 0), (1, 1)], lambda x, y: (x * 10.04, y * 10.04)) == "M0.0,0.0L10.0,0.0L10.0,10.0Z"


def test_ring_path_drops_repeated_points():
    """Consecutive points that round to the same pixel are dropped."""
    assert svgmap._ring([(0, 0), (0.001, 0), (1, 0)], lambda x, y: (x, y)) == "M0.0,0.0L1.0,0.0Z"


def test_ring_path_degenerate():
    """A ring that collapses to one pixel draws nothing."""
    assert svgmap._ring([(0, 0), (0.01, 0.01)], lambda x, y: (x, y)) == ""
