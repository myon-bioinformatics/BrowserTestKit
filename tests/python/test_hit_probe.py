import importlib.util, json, math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("hit_probe", ROOT / "scripts" / "hit_probe.py")
hit_probe = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(hit_probe)

W, H = 64, 48


def shape_probe(inside, calls=None):
    def probe(points):
        if calls is not None:
            calls.append(len(points))
        return [1.0 if inside(x, y) else 0.0 for x, y in points]
    return probe


SHAPES = {
    "box": lambda x, y: 10 <= x <= 40 and 8 <= y <= 30,
    "disc": lambda x, y: (x - 32) ** 2 + (y - 24) ** 2 <= 15 ** 2,
    "box_with_overlay": lambda x, y: 10 <= x <= 50 and 8 <= y <= 40 and not (30 <= x <= 50 and 8 <= y <= 25),
    "rotated": lambda x, y: abs((x - 32) * math.cos(0.5) + (y - 24) * math.sin(0.5)) <= 14
    and abs(-(x - 32) * math.sin(0.5) + (y - 24) * math.cos(0.5)) <= 9,
    "empty": lambda x, y: False,
}


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_refine_matches_exhaustive_with_fewer_probes(name):
    inside = SHAPES[name]
    calls = []
    result = hit_probe.refine(W, H, shape_probe(inside, calls), stride=8)
    assert result["rows"] == hit_probe.exhaustive(W, H, shape_probe(inside))
    assert result["probes"] == sum(calls) < result["cells"] * 0.5
    assert len(calls) == result["rounds"]  # one batch per round, so a browser needs few round trips


def test_random_rotated_boxes_match_exhaustive():
    import random
    rng = random.Random(7)
    for _ in range(60):
        a, cx, cy = rng.uniform(0, 3.14), rng.uniform(20, 44), rng.uniform(15, 33)
        hw, hh = rng.uniform(8, 20), rng.uniform(5, 12)

        def inside(x, y):
            u = (x - cx) * math.cos(a) + (y - cy) * math.sin(a)
            v = -(x - cx) * math.sin(a) + (y - cy) * math.cos(a)
            return abs(u) <= hw and abs(v) <= hh
        result = hit_probe.refine(W, H, shape_probe(inside))
        assert result["rows"] == hit_probe.exhaustive(W, H, shape_probe(inside)), (a, cx, cy, hw, hh)
        assert result["probes"] < result["cells"] * 0.4


def test_old_corner_rule_misses_a_poking_corner():
    """CORNER_POKES_BETWEEN_CORNERS in docs/antipatterns.md.

    With this rotated box, settling a block from its four agreeing corners alone missed 3 cells
    (a corner of the box pokes in between them). The neighbour check must find them.
    """
    a, cx, cy, hw, hh = 2.27, 25.5, 32.0, 18.8, 5.2

    def inside(x, y):
        u = (x - cx) * math.cos(a) + (y - cy) * math.sin(a)
        v = -(x - cx) * math.sin(a) + (y - cy) * math.cos(a)
        return abs(u) <= hw and abs(v) <= hh
    assert hit_probe.refine(W, H, shape_probe(inside))["rows"] == hit_probe.exhaustive(W, H, shape_probe(inside))


def test_plain_box_is_rank_one_and_others_leave_residual():
    box = hit_probe.refine(W, H, shape_probe(SHAPES["box"]))
    fact = hit_probe.factorize(box["rows"], rank=1, scores=box["scores"])
    assert fact["residual"] == 0
    assert hit_probe.ranges(fact["factors"][0]["cols"]) == "10-40"
    assert hit_probe.ranges(fact["factors"][0]["rows"]) == "8-30"
    assert fact["factors"][0]["scale"] == 1.0
    for name in ("disc", "rotated"):
        result = hit_probe.refine(W, H, shape_probe(SHAPES[name]))
        assert hit_probe.factorize(result["rows"], rank=1)["residual"] > 0


def test_overlay_cut_is_exact_with_two_factors():
    result = hit_probe.refine(W, H, shape_probe(SHAPES["box_with_overlay"]))
    assert hit_probe.factorize(result["rows"], rank=1)["residual"] > 0
    assert hit_probe.factorize(result["rows"], rank=2)["residual"] == 0


def test_factors_never_cover_a_miss():
    result = hit_probe.refine(W, H, shape_probe(SHAPES["disc"]))
    for factor in hit_probe.factorize(result["rows"], rank=5)["factors"]:
        for y in range(H):
            if factor["rows"] >> y & 1:
                assert result["rows"][y] & factor["cols"] == factor["cols"]


def test_low_confidence_scores_are_refined_and_reported():
    # Fractional scores near the edge behave like low-confidence decisions.
    def probe(points):
        return [min(1.0, max(0.0, (20.5 - x) / 2 + 0.5)) for x, _ in points]
    result = hit_probe.refine(W, 8, probe, stride=8, margin=0.25)
    assert result["rows"] == hit_probe.exhaustive(W, 8, probe)
    assert result["uncertain"] and all(19 <= x <= 22 for x, _ in result["uncertain"])


def test_boundary_pairs_cross_the_edge():
    result = hit_probe.refine(W, H, shape_probe(SHAPES["box"]))
    pairs = hit_probe.boundary_pairs(result["rows"], W, limit=6)
    assert len(pairs) == 6
    for (ix, iy), (ox, oy) in pairs:
        assert SHAPES["box"](ix, iy) and not SHAPES["box"](ox, oy)
        assert abs(ix - ox) + abs(iy - oy) == 1


def test_input_errors():
    with pytest.raises(ValueError, match="width"):
        hit_probe.refine(0, 4, shape_probe(SHAPES["box"]))
    with pytest.raises(ValueError, match="0..1"):
        hit_probe.refine(4, 4, lambda pts: [2.0] * len(pts))
    with pytest.raises(ValueError, match="number of scores"):
        hit_probe.refine(4, 4, lambda pts: [])
    with pytest.raises(ValueError, match="budget"):
        hit_probe.refine(W, H, shape_probe(SHAPES["disc"]), max_probes=10)


def test_tiny_grids():
    assert hit_probe.refine(1, 1, lambda pts: [1.0])["rows"] == [1]
    assert hit_probe.refine(3, 1, shape_probe(lambda x, y: x == 1), stride=1)["rows"] == [2]


def test_to_json_and_cli(tmp_path, capsys):
    result = hit_probe.refine(W, H, shape_probe(SHAPES["box_with_overlay"]))
    data = hit_probe.to_json(result, rank=2)
    assert data["schema"] == "btk-hit-probe/1" and data["residual"] == 0
    assert len(data["map"]) == H and all(len(row) == W for row in data["map"])
    path = tmp_path / "map.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert hit_probe.main(["factorize", str(path), "--rank", "2"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].endswith("factors=2 residual=0") and len(out) == 3
    path.write_text(json.dumps({"map": ["01", "1"]}), encoding="utf-8")
    assert hit_probe.main(["factorize", str(path)]) == 2
    assert "equal length" in capsys.readouterr().err
