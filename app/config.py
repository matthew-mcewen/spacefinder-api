"""Every knob in one place."""
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"

DATAFEED_URL = "https://data.vatsim.net/v3/vatsim-data.json"
VATSPY_URL = "https://github.com/vatsimnetwork/vatspy-data-project/releases/latest/download/Boundaries.geojson"
VATGLASSES_URL = "https://github.com/lennycolton/vatglasses-data/archive/refs/heads/main.zip"

DATAFEED_TTL = 180       # seconds: the datafeed is fetched at most this often
STATIC_TTL = 24 * 3600   # seconds: VatSpy / VatGlasses refresh interval
HTTP_TIMEOUT = 60        # seconds

GRID_DEG = 1.0           # spatial index cell size, degrees
MAP_PX = 1200            # longest side of the SVG map, pixels
MAP_PAD_DEG = 5.0        # padding around watched FIRs on the map, degrees
