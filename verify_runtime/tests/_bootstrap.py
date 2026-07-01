"""Shared test bootstrap: put verification/ on sys.path and expose `verify`."""

from __future__ import annotations

import sys
from pathlib import Path

VERIFICATION_DIR = Path(__file__).resolve().parent.parent
if str(VERIFICATION_DIR) not in sys.path:
    sys.path.insert(0, str(VERIFICATION_DIR))

import verify  # noqa: E402  (path set above)

__all__ = ["verify", "VERIFICATION_DIR"]
