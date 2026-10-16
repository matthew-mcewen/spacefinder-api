import sys

# Fail fast with a clear message: older Pythons otherwise die later on syntax errors or odd pip resolutions.
if sys.version_info < (3, 11):
    raise RuntimeError(f"spacefinder-api needs Python 3.11+, but this is {sys.version.split()[0]}. See README > Quick start.")
