"""Instrument the production M3U parser before importing its contracts."""
import logging
import sys
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
with atheris.instrument_imports():
    from fuzz.contracts import fuzz_playlist

# Skipped unsupported entries are expected during mutation. Keep errors visible
# without writing the same count-only warning for every generated playlist.
logging.getLogger("epg_voice_gateway.catalog").setLevel(logging.ERROR)

atheris.Setup(sys.argv, fuzz_playlist)
atheris.Fuzz()
