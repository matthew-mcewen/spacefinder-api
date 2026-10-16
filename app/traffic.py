"""Traffic: where every connected pilot is, worked out once per datafeed refresh."""
import json
from dataclasses import dataclass

from app import airspace, config, feeds, geo
from app.airspace import Airspace


@dataclass(frozen=True)
class Snapshot:
    updated: str                     # datafeed update_timestamp
    positions: dict[str, geo.Point]  # callsign -> (lon, lat)
    firs: dict[str, list[str]]       # callsign -> VatSpy boundary ids containing it
    volumes: dict[str, list[str]]    # callsign -> VatGlasses volume ids containing it (3D)
    sky: Airspace                    # the airspace these were computed against


def build(feed: dict, sky: Airspace) -> Snapshot:
    """Assign every pilot to its FIRs (2D) and volumes (3D: altitude must sit in the band too)."""
    positions, firs, volumes = {}, {}, {}
    for p in feed["pilots"]:
        cs, lon, lat, fl = p["callsign"].upper(), p["longitude"], p["latitude"], p["altitude"] / 100
        positions[cs] = (lon, lat)
        firs[cs] = airspace.containing(sky.fir_index, lon, lat)
        volumes[cs] = airspace.containing(sky.vol_index, lon, lat, fl)
    return Snapshot(feed["general"]["update_timestamp"], positions, firs, volumes, sky)


def inside(snap: Snapshot, kind: str, query: str) -> list[str]:
    """Sorted callsigns inside any of the comma-separated FIRs (kind='fir'), or inside one exact volume (kind='volume')."""
    if kind == "fir":
        ids = set(airspace.match_firs(snap.sky, query))
        return sorted(cs for cs, firs in snap.firs.items() if not ids.isdisjoint(firs))
    if query not in snap.sky.volume_ids:
        raise airspace.UnknownId(query)
    return sorted(cs for cs, volumes in snap.volumes.items() if query in volumes)


_memo = feeds.Memo()


def current() -> Snapshot:
    """The latest Snapshot. The datafeed is fetched at most every DATAFEED_TTL seconds, and the
    snapshot is rebuilt only when the feed file (or the airspace) actually changes."""
    feed = feeds.fetch_cached(config.DATAFEED_URL, "vatsim-data.json", config.DATAFEED_TTL, check=json.loads)
    sky = airspace.current()
    return _memo.get((feed.stat().st_mtime_ns, sky), lambda: build(json.loads(feed.read_bytes()), sky))
