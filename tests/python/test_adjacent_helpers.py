"""Canonical helper loading must work in source and installed flat layouts."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("module,dependency", [("git_commit", "gh_ops"), ("repo_overview", "gh_identity")])
@pytest.mark.parametrize("layout", ["source", "installed"])
def test_helpers_load_adjacent_canonical_module(tmp_path, module, dependency, layout):
    target = tmp_path / ("checkout/tools" if layout == "source" else "installed")
    target.mkdir(parents=True)
    for name in {module, dependency, "gh_identity"}:
        shutil.copyfile(ROOT / "tools" / (name + ".py"), target / (name + ".py"))
    outside = tmp_path / "outside"
    outside.mkdir()
    # Neither cwd nor a competing PYTHONPATH module may substitute for the vendored sibling.
    (outside / (dependency + ".py")).write_text("raise RuntimeError('wrong dependency')\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(target), str(outside)))}
    command = ([sys.executable, "-S", str(target / (module + ".py")), "--help"]
               if layout == "source" else [sys.executable, "-S", "-m", module, "--help"])
    result = subprocess.run(command, cwd=outside, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
