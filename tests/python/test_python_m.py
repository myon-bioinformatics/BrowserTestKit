import os, subprocess, sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.mark.parametrize("module", sorted(path.stem for path in SCRIPTS.glob("*.py")))
def test_every_script_runs_as_a_module(module: str, tmp_path: Path) -> None:
    """pyproject.toml installs each scripts/*.py as a top-level module; each must support `python -m NAME --help`."""
    env = dict(os.environ, PYTHONPATH=str(SCRIPTS), PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-S", "-m", module, "--help"], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "usage" in done.stdout.lower()
