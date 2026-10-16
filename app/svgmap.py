"""Minimalist SVG traffic map: faint VatSpy boundaries plus a 3x3 px plus per aircraft.
No labels and no flight data. Monochrome, and it follows the viewer's light/dark preference."""
import math

from app import airspace, config, geo
from app.traffic import Snapshot

STYLE = (
    "svg{--bg:#fff;--fg:#000}"
    "@media (prefers-color-scheme:dark){svg{--bg:#000;--fg:#fff}}"
    ".bg{fill:var(--bg)}"
    ".fir{fill:none;stroke:var(--fg);stroke-opacity:.15;stroke-width:.5;stroke-linejoin:round}"
    ".ac{fill:none;stroke:var(--fg);stroke-width:1}"
)


def view(sky: airspace.Airspace, fir: str | None) -> geo.BBox:
    """The whole world, or the watched FIRs' combined extent plus MAP_PAD_DEG on every side."""
    if not fir:
        return geo.WORLD
    ids = set(airspace.match_firs(sky, fir))
    return geo.pad(geo.union(s.bbox for s in sky.firs if s.id in ids), config.MAP_PAD_DEG)


def render(snap: Snapshot, box: geo.BBox) -> str:
    to_xy, w, h = geo.projector(box, config.MAP_PX)
    boundaries = "".join(_ring(r, to_xy) for s in snap.sky.firs if geo.overlaps(s.bbox, box) for r in s.rings)
    aircraft = "".join(_plus(*to_xy(lon, lat)) for lon, lat in snap.positions.values() if geo.contains(box, lon, lat))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}">'
        f'<style>{STYLE}</style><rect class="bg" width="100%" height="100%"/>'
        f'<path class="fir" d="{boundaries}"/><path class="ac" d="{aircraft}"/></svg>'
    )


def _ring(ring, to_xy) -> str:
    """One closed path segment. Points are rounded to 0.1 px and repeats dropped, to keep the SVG small."""
    pts = []
    for lon, lat in ring:
        x, y = to_xy(lon, lat)
        p = f"{x:.1f},{y:.1f}"
        if not pts or pts[-1] != p:
            pts.append(p)
    return f"M{'L'.join(pts)}Z" if len(pts) > 1 else ""


def _plus(x: float, y: float) -> str:
    """A 3x3 px plus centred on a pixel centre, so the 1 px strokes render crisply."""
    x, y = math.floor(x) + 0.5, math.floor(y) + 0.5
    return f"M{x - 1.5:g},{y:g}h3M{x:g},{y - 1.5:g}v3"
