"""BTK_BROWSER_ENGINES chooses which optional engines a browser lane collects (offline collect-only)."""
import os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def collect(engines: str | None) -> str:
    env = dict(os.environ)
    env.pop("BTK_BROWSER_ENGINES", None)
    if engines is not None:
        env["BTK_BROWSER_ENGINES"] = engines
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/python/test_interaction.py", "--collect-only", "-q", "-m", "browser"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_default_collects_every_engine_and_chromium_only_drops_the_optional_ones():
    everything = collect(None)
    assert "firefox" in everything and "webkit" in everything
    chromium = collect("chromium")
    assert "chromium" in chromium
    assert "firefox" not in chromium and "webkit" not in chromium
