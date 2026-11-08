# image_diff.py

Compare two screenshots (`PATH` or `ZIP::member`) and print one line: size, changed pixels, bbox, max channel delta.

Canonical implementation lives in this repository (`tools/image_diff.py`). Python 3.11+ is required. PNG decoding and pixel comparison use the vendored `pillow_min.py` from [Ironmate](https://github.com/myon-bioinformatics/Ironmate) (recorded in `vendor.lock.json`); no third-party Python packages are needed.

```bash
python tools/image_diff.py a.png b.png [--clip X,Y,W,H] [--viewport-width CSS_PX] [--tolerance N]
```

The installed distribution includes the same pinned backend and its Ironmate
license as wheel data. After `uv pip install .` (inside an active virtual environment, e.g. after `uv venv`), run
`python -m image_diff a.png b.png` from any directory. No Pillow install is needed.
Built-wheel tests install into isolated `--target` and `--prefix` directories and
compare real PNGs with site packages disabled, checking the golden stdout and
exit codes as well as the packaged backend and license bytes.

Exit `0` = identical within tolerance, `1` = differs, `2` = input error or missing vendored backend.

See also [screenshot-evidence.md](screenshot-evidence.md) for how this fits next to `check_png.py` and receipt validators.

## Regression tests

Install test dependencies (`uv run --locked`) and run:

```bash
uv run --locked pytest tests/python/test_browser_test_kit_image_diff.py
```

Golden stdout lines and PNG inputs live under `tests/fixtures/image_diff/` so regressions do not depend on Pillow. The missing-backend exit-2 diagnostic is tested by pointing `image_diff` at a non-existent vendored copy.

Downstream consumers (for example Flutter) may vendor a fixed copy of `tools/image_diff.py` with provenance metadata. That adoption path does not enroll the script in `vendor.lock.json`; update vendored copies from reviewed merges of this repository.
