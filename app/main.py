"""HTTP routes: thin glue over traffic, airspace and svgmap. Run with `uvicorn app.main:app`."""
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

from app import airspace, feeds, svgmap, traffic

app = FastAPI(title="Sector Density & Operations Rate API")


@app.exception_handler(airspace.UnknownId)
def _unknown_id(_, e):
    return JSONResponse({"detail": f"unknown id: {e}"}, status_code=404)


@app.exception_handler(feeds.FeedUnavailable)
def _feed_down(_, e):
    return JSONResponse({"detail": f"data source unavailable: {e}"}, status_code=503)


@app.get("/aircraft")
def aircraft(acid: str) -> dict:
    """The FIR boundaries and VatGlasses volumes one aircraft is inside."""
    snap, cs = traffic.current(), acid.strip().upper()
    if cs not in snap.positions:
        raise HTTPException(404, f"{cs} is not connected")
    return {"callsign": cs, "insideFir": snap.firs[cs], "insideVolume": snap.volumes[cs]}


@app.get("/aircraftInside")
def aircraft_inside(fir: str | None = None, volume: str | None = None) -> list[str]:
    """Callsigns inside ?fir=A,B (family match, union) or ?volume=<exact id>."""
    return traffic.inside(traffic.current(), *_target(fir, volume))


@app.get("/targetsInside")
def targets_inside(fir: str | None = None, volume: str | None = None) -> int:
    """How many aircraft /aircraftInside would return."""
    return len(aircraft_inside(fir, volume))


@app.get("/map.svg")
def map_svg(fir: str | None = None) -> Response:
    """The whole network, or ?fir=A,B framed with 5 degrees of padding."""
    snap = traffic.current()
    return Response(svgmap.render(snap, svgmap.view(snap.sky, fir)), media_type="image/svg+xml")


@app.get("/firs")
def firs() -> list[str]:
    """Every VatSpy boundary id."""
    return airspace.current().fir_ids


@app.get("/volumes")
def volumes() -> list[str]:
    """Every VatGlasses volume id."""
    return airspace.current().volume_ids


def _target(fir: str | None, volume: str | None) -> tuple[str, str]:
    if (fir is None) == (volume is None):
        raise HTTPException(422, "give exactly one of ?fir= or ?volume=")
    return ("fir", fir) if fir is not None else ("volume", volume)
