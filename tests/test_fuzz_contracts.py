"""Replay parser regressions as ordinary tests; timed mutation is RC-only."""
import runpy
from pathlib import Path


def test_committed_fuzz_inputs():
    directory = Path(__file__).resolve().parents[1] / "fuzz"
    contracts = runpy.run_path(str(directory / "contracts.py"))
    root = directory / "corpus"
    for name in ("playlist", "xmltv"):
        target = contracts[f"fuzz_{name}"]
        seeds = sorted((root / name).iterdir())
        assert seeds
        for seed in seeds:
            target(seed.read_bytes())
        for value in range(256):
            target(bytes([value]) * (value % 48))
