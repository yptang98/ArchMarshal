#!/usr/bin/env python3
"""Compatibility wrapper for the package CLI."""

# ruff: noqa: E402, I001 -- the source checkout path must be inserted before import.

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from archmarshal.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["inventory", *sys.argv[1:]]))
