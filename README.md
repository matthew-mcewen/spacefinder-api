# Sector Density & Operations Rate API

A small FastAPI service that answers two questions about the live VATSIM network:

- Which airspace is an aircraft in?
- How busy is an airspace right now?

It can also draw a minimalist SVG traffic map.

- **FIRs** come from the [VatSpy data project](https://github.com/vatsimnetwork/vatspy-data-project) (2D boundaries).
- **Volumes** come from [VatGlasses](https://github.com/lennycolton/vatglasses-data) (3D sectors with floor and ceiling).
- **Traffic** comes from the [VATSIM v3 datafeed](https://data.vatsim.net/v3/vatsim-data.json). It is fetched **at most once every 180 s**.

## Quick start

Requires **Python 3.11+** (developed on 3.14). macOS's built-in `python3` is 3.9 and is too old, so install a newer one first (e.g. `brew install python@3.12`, then use `python3.12`).

From the project folder (on macOS/Linux, use `python3.12` in place of `python` and `.venv/bin/python` in place of `.venv/Scripts/python`):

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

```bash
.venv/Scripts/python -m uvicorn app.main:app
```

Then open http://127.0.0.1:8000/docs for interactive docs. The first request downloads the datasets (about 6 MB) into `cache/` and takes a few seconds. After that, responses are instant.

## Endpoints

All endpoints are `GET` with query parameters.

| Endpoint | Parameters | Returns |
|---|---|---|
| `/aircraft` | `acid=QFA1` | `{"callsign": "QFA1", "insideFir": [...], "insideVolume": [...]}`, or 404 if not connected |
| `/aircraftInside` | `fir=EGTT,EGPX` **or** `volume=eg/Pembroke (35)` | `["BAW115", "EZY225", ...]` (sorted callsigns) |
| `/targetsInside` | same as above | `22` (the count as a plain integer) |
| `/map.svg` | optional `fir=WSJC,WMFC` | `image/svg+xml` |
| `/firs` | none | every VatSpy boundary id |
| `/volumes` | none | every VatGlasses volume id |

```bash
curl "http://127.0.0.1:8000/aircraft?acid=ACA880"
```
→ `{"callsign":"ACA880","insideFir":["EGTT","EGTT-W","EGVV"],"insideVolume":["eg/Pembroke (35)"]}`

```bash
curl "http://127.0.0.1:8000/targetsInside?fir=EGTT"
```

```bash
curl -G "http://127.0.0.1:8000/aircraftInside" --data-urlencode "volume=eg/Pembroke (35)"
```

Errors:
- `404`: unknown callsign, FIR or volume. An unknown id is never a silent zero.
- `422`: give exactly one of `fir` / `volume`.
- `503`: no data has ever been fetched and the source is unreachable.

### Rules worth knowing

- **`insideFir` lists every matching VatSpy boundary.** VatSpy has parent boundaries and overlapping sub-sectors (`SBCW`, `SBCW-CS`, `SBCW-CSE`), so an aircraft is usually in more than one.
- **FIR queries match a family.** `fir=SBCW` covers `SBCW` and every `SBCW-*` sub-sector. `fir=SBCW-CS` covers only `SBCW-CS` (not `SBCW-CSE`).
- **FIR queries are flexible.** They are case-insensitive, and comma-separated lists are unioned without duplicates.
- **Volumes are 3D.** The aircraft must be inside the polygon and at `floor ≤ FL < ceiling`, with FL = altitude ÷ 100.
  - The ceiling is exclusive, so an aircraft at exactly FL245 on a 245 split counts once, in the upper sector.
  - A missing or zero VatGlasses `min` means the surface (so aircraft below sea level still count). A missing `max` means unlimited.
- **Volume ids are `<VatGlasses file>/<airspace id>`**, e.g. `eg/Lakes High (4)` or `zny/BRNAN (08)`, because bare ids repeat across (and within) files.
  - A `volume=` query takes one exact, case-sensitive id, since names can contain commas.
  - Use `/volumes` to discover ids.
- **Australia** is split into sector-level boundaries in VatSpy (`YBAR`, `YMEK`, …). There is no `YMMM` or `YBBB` id.

### The map

- Aircraft are 3×3 px pluses, boundaries are faint 0.5 px lines, and there's no text or flight data.
- It is monochrome. It follows the viewer's light/dark preference through `@media (prefers-color-scheme: dark)` inside the SVG.
- Without `fir`, it shows the whole world at 1200×600.
- With `fir=A,B`, it frames the combined extent of those FIRs plus 5° on every side. The longest side is 1200 px.

## How it works

```
             feeds.py: fetch_cached(url, name, ttl)  ->  cache/<name>
             ├─ vatsim-data.json     ≤ 1 download / 180 s
             ├─ Boundaries.geojson   ≤ 1 download / 24 h
             └─ vatglasses.zip       ≤ 1 download / 24 h
                    │                              │
    airspace.py: parse into Shapes,      traffic.py: Snapshot = for every pilot,
    build a grid index per dataset  ──►  which FIRs (2D) and volumes (3D) contain it
                    │                              │
                    └──────────► main.py routes ◄──┴── svgmap.py
```

The modules:

| File | What's in it |
|---|---|
| [app/config.py](app/config.py) | Every URL, TTL, path and size constant |
| [app/geo.py](app/geo.py) | **All the hot maths.** Pure functions, no I/O (see below) |
| [app/feeds.py](app/feeds.py) | Disk cache with per-file TTL, plus `Memo` (recompute only when inputs change) |
| [app/airspace.py](app/airspace.py) | VatSpy/VatGlasses parsing into flat `Shape` lists, id rules, point lookup |
| [app/traffic.py](app/traffic.py) | Builds the `Snapshot` (pilot → FIRs/volumes) and the inside/count queries |
| [app/svgmap.py](app/svgmap.py) | SVG rendering |
| [app/main.py](app/main.py) | HTTP routes, thin glue only |

How the work is spread out:
- **Per datafeed refresh (≤ every 180 s):** every pilot is assigned once, taking about 0.2 s for 2000 pilots. That result is memoised.
- **Per request:** dictionary lookups only.
- **Per dataset refresh (≤ daily):** the airspace is re-parsed, taking about 1 s.

### Where the maths lives: `app/geo.py`

| Function | Does |
|---|---|
| `dms_to_deg` | VatGlasses `[-]DDMMSS` / `[-]DDDMMSS` → decimal degrees |
| `bbox`, `union`, `pad`, `contains`, `overlaps` | Bounding-box arithmetic (`pad` clamps to the world) |
| `point_in_rings` | Even-odd ray cast. Holes are just extra rings. West/south edges count as inside and east/north as outside, so a point on a shared border belongs to exactly one shape |
| `in_band` | Vertical test: floor inclusive, ceiling exclusive |
| `GridIndex` | 1° buckets: a point only tests shapes whose bbox touches its cell |
| `projector` | Equirectangular map projection with longitude scaled by cos(mid-latitude) |

A point lookup is always **grid cell → bbox → altitude band → ray cast** (`airspace.containing`).

### Caching

- Each file in `cache/` is served from disk while it's younger than its TTL. Otherwise it's re-downloaded and written atomically (temp file + rename).
- The file's mtime is the clock, so the 180 s limit also holds across restarts.
- **If a download fails, the stale copy is served**, and the failure counts as an attempt. An outage therefore costs one retry per TTL, not one per request.
- Concurrent requests share a single download.

## Tests

```bash
.venv/Scripts/python -m pytest
```

That runs 289 offline unit tests in a few seconds. The live smoke tests against the real servers are opt-in. They use the real `cache/` dir, so they respect the 180 s limit:

```bash
.venv/Scripts/python -m pytest -m live
```

The unit tests never touch the network. An autouse fixture in [tests/conftest.py](tests/conftest.py) makes every download fail as if offline. The data comes from a tiny synthetic world in [tests/world.py](tests/world.py), which is written into a temp cache dir so the real code paths run:

- **FIRs:** a parent with an overlapping sub-sector, a FIR with a hole, an antimeridian-split FIR, and a plain `Polygon` feature.
- **VatGlasses zip:** list-form and dict-form files, ignored `positions.json` and `ownership/` files, Low/High sectors stacked at FL245, a repeated id, and an empty sector.
- **Pilots:** inside and outside each shape, in the hole, exactly at FL245, below sea level, and with a lower-case callsign.

| File | Tests | Covers |
|---|---|---|
| [test_geo.py](tests/test_geo.py) | 101 | DMS parsing (sign, 6/7 digits, zero); bbox/union/pad/clamp/overlap; ray cast on square, concave, triangle, hole, negative coordinates, open vs closed rings, shared-edge convention; band edges; grid index (multi-cell, negative cells, custom size); projection (corners, north-up, cos-lat, aspect) |
| [test_feeds.py](tests/test_feeds.py) | 22 | Download when missing; **bodies that fail to parse (HTML error pages, truncated JSON or zip) never replace the cache**; no refetch inside the TTL; **never more than one fetch per 180 s** (simulated clock); refetch after the TTL; TTL survives restarts; stale fallback; no retry storm during an outage; 5xx handling; redirects and timeouts; atomic write; concurrent callers share one download; `Memo` |
| [test_airspace.py](tests/test_airspace.py) | 63 | VatSpy parsing (MultiPolygon/Polygon, holes, repeated ids); VatGlasses parsing (namespaced ids, list/dict forms, skipped files and sectors, min/max → floor/ceiling, lon/lat order and signs, string FLs); `_stem`; FIR family rule and its negatives; comma splitting; unknown ids; 2D and 3D lookups; `current()` memoising and reloading |
| [test_traffic.py](tests/test_traffic.py) | 38 | Every synthetic pilot's FIRs and volumes; parent and sub listed together; FL245 split; upper-casing; FIR family, union and dedupe; exact volume queries; empty vs unknown; snapshot memoised and rebuilt when the feed or airspace changes; no downloads within the TTL |
| [test_svgmap.py](tests/test_svgmap.py) | 22 | Valid SVG; light/dark CSS; subtle boundary style; no text or callsigns; one 3×3 px plus per aircraft, snapped to pixel centres; world size; FIR framing (+5°, comma union, case, clamping); culling of off-view aircraft and boundaries; path rounding and dedupe |
| [test_api.py](tests/test_api.py) | 41 | Every route end to end: bodies, status codes (200/404/422/503), content types, URL-encoded volume ids, stale data served while offline, OpenAPI docs |
| [test_version.py](tests/test_version.py) | 2 | Python < 3.11 fails at import with a clear message |
| [test_live.py](tests/test_live.py) | 5 (opt-in) | Real datasets parse with plausible sizes; a snapshot builds in under 5 s; **the grid index matches a brute-force scan for every live pilot**; every reported volume really contains the aircraft in 3D; real API and maps |

## Data sources & licences

- **VATSIM datafeed**: © VATSIM.
- **VatSpy data project**: [CC BY-SA 4.0](https://github.com/vatsimnetwork/vatspy-data-project).
- **VatGlasses data**: [CC BY-NC-SA 4.0](https://github.com/lennycolton/vatglasses-data). **Non-commercial use only**; credit the VatGlasses contributors.

Data is downloaded at runtime into `cache/` (gitignored) and is not redistributed by this repo.

## Known limitations

- **Antimeridian FIRs:** VatSpy splits them (NZZO, PAZA, KZAK…), so `/map.svg?fir=` for one of them frames the full width of the world.
- **Runway-dependent VatGlasses sectors:** all variants match. There's no runway-configuration awareness.
- **Geometry only:** volumes ignore who is online and VatGlasses sector ownership.
