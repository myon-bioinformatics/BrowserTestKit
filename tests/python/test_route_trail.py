import importlib.util, json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("route_trail", ROOT / "scripts" / "route_trail.py")
route_trail = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(route_trail)

REGISTRY = route_trail.load_registry((ROOT / "fixtures" / "route_trail_registry.json").read_text(encoding="utf-8"))
DART = """const pageEmojis = <String, String>{
  '/': '🏠',
  '/hub': '🗺️',
  '/screen1': '🌍',
};
const pageRouteAliases = <String, String>{
  '/old-hub': '/hub',
};"""


@pytest.mark.parametrize(("trail", "routes"), [
    ("🏠 → 🗺", ["/", "/hub"]),
    ("🏠 ↔ 🗺", ["/", "/hub", "/"]),
    ("🏠→🗺→🌍↔🕒", ["/", "/hub", "/screen1", "/tools/time", "/screen1"]),
    ("🏠 -> 🗺 <-> 🌍", ["/", "/hub", "/screen1", "/hub"]),
    ("🗺", ["/hub"]),
])
def test_trail_grammar(trail, routes):
    assert [r["route"] for r in route_trail.resolve(trail, REGISTRY)] == routes


def test_variation_selector_is_ignored_both_ways():
    """EMOJI_VARIATION_SELECTOR: a typed 🗺 (no U+FE0F) must find the registered 🗺️, and vice versa."""
    assert "️" in "🗺️" and "️" not in "🗺"
    assert route_trail.resolve("🗺", REGISTRY)[0]["route"] == route_trail.resolve("🗺️", REGISTRY)[0]["route"]


def test_urls_for_hash_and_path_routing():
    assert route_trail.resolve("🏠→🗺", REGISTRY, base="https://x.github.io/app/")[1]["url"] == "https://x.github.io/app/#/hub"
    assert route_trail.resolve("🗺", REGISTRY, base="https://x/app/", hash_routing=False)[0]["url"] == "https://x/app/hub"


def test_dart_map_literal_and_alias_values_are_skipped():
    registry = route_trail.load_registry(DART)
    assert registry == {"🏠": "/", "🗺": "/hub", "🌍": "/screen1"}
    assert route_trail.load_registry(DART, map_name="pageEmojis") == registry
    with pytest.raises(ValueError, match="map not found"):
        route_trail.load_registry(DART, map_name="missing")


@pytest.mark.parametrize(("text", "message"), [
    ('{"/a": "🗺", "/b": "🗺️"}', "two routes"),   # collide once the selector is removed
    ('{"a": "b"}', "/route"),
    ("no pairs here", "no /route"),
])
def test_bad_registries(text, message):
    with pytest.raises(ValueError, match=message):
        route_trail.load_registry(text)


@pytest.mark.parametrize(("trail", "message"), [
    ("🏠 → 🦄", "unknown page emoji: 🦄"),
    ("→ 🏠", "alternate"),
    ("🏠 → ", "alternate"),
    ("🏠 →→ 🗺", "alternate"),
])
def test_bad_trails(trail, message):
    with pytest.raises(ValueError, match=message):
        route_trail.resolve(trail, REGISTRY)


def test_cli(tmp_path, capsys):
    registry = str(ROOT / "fixtures" / "route_trail_registry.json")
    assert route_trail.main(["🏠 ↔ 🗺", "--registry", registry, "--base", "https://x/"]) == 0
    assert capsys.readouterr().out.splitlines() == ["https://x/#/", "https://x/#/hub", "https://x/#/"]
    assert route_trail.main(["🗺", "--registry", registry, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["route"] == "/hub"
    assert route_trail.main(["🦄", "--registry", registry]) == 2
    assert "unknown page emoji" in capsys.readouterr().err
