"""Instrument bounded XMLTV/gzip parsing; never create a catalogue client."""
import sys
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
with atheris.instrument_imports():
    from fuzz.contracts import fuzz_xmltv

atheris.Setup(sys.argv, fuzz_xmltv)
atheris.Fuzz()
