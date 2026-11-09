"""Cross-runtime oracle; reads score grids from stdin, returns Python evidence."""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import hit_probe


def evaluate(case):
    batches = []

    def probe(points):
        batches.append([list(p) for p in points])
        return [case["scores"][y][x] for x, y in points]

    result = hit_probe.refine(case["width"], case["height"], probe, **case.get("options", {}))
    fact = hit_probe.factorize(result["rows"], rank=case.get("rank", 3), scores=result["scores"])
    return {
        "evidence": hit_probe.to_json(result, rank=case.get("rank", 3)),
        "rows": [str(r) for r in result["rows"]],
        "factors": [{**f, "rows": str(f["rows"]), "cols": str(f["cols"])} for f in fact["factors"]],
        "uncertain": result["uncertain"],
        "pairs": hit_probe.boundary_pairs(result["rows"], case["width"], limit=case.get("limit", 8)),
        "exhaustive": [str(r) for r in hit_probe.exhaustive(case["width"], case["height"], probe,
                       threshold=case.get("options", {}).get("threshold", 0.5))],
        "batches": batches,
    }


if __name__ == "__main__":
    print(json.dumps([evaluate(case) for case in json.load(sys.stdin)]))
