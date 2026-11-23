"""Select browser engines for the browser lanes with BTK_BROWSER_ENGINES (default: all engines); expose the vendored test helpers."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

OPTIONAL_ENGINES = ("firefox", "webkit")

# Shared test helpers from vendor/, importable by name in every test module here:
# cli_args.run_cli runs this repository's Python scripts, fleet_check.node finds Node.
for _name in ("cli_args", "fleet_check"):
    _spec = importlib.util.spec_from_file_location(_name, Path(__file__).resolve().parents[2] / "vendor" / f"{_name}.py")
    sys.modules[_name] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules[_name])


def _selected_engines() -> set[str]:
    raw = os.environ.get("BTK_BROWSER_ENGINES", "chromium,firefox,webkit")
    return {name.strip() for name in raw.split(",") if name.strip()}


def pytest_collection_modifyitems(config, items):
    selected = _selected_engines()
    kept, dropped = [], []
    for item in items:
        params = getattr(getattr(item, "callspec", None), "params", {})
        engine = params.get("browser_name", params.get("browser"))
        if engine in OPTIONAL_ENGINES and engine not in selected:
            dropped.append(item)
        else:
            kept.append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept
