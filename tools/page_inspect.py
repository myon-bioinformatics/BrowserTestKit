#!/usr/bin/env python3
"""Compatibility entry point for the page inspection API; implementation lives in btk."""
from pathlib import Path as _CompatPath

# Execute in this namespace so imports (including private helpers and monkeypatches)
# retain the same module-global behavior as the original page_inspect module.
_implementation = _CompatPath(__file__).with_name("btk.py")
exec(compile(_implementation.read_bytes(), str(_implementation), "exec"), globals())
