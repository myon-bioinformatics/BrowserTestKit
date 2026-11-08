# Running the scripts with `python -m`

Every `tools/*.py` is stdlib-only at runtime. `pyproject.toml` installs each one as a top-level module, so after installation it runs from any directory, like `python -m zipfile`, with no clone or file path, or from a checkout as `python -S tools/NAME.py`. The former `scripts/` directory was merged into `tools/`; the installed module names are unchanged, including `page_inspect`, `pw_install_plan`, `repo_overview`, and `image_diff`. `page_inspect` delegates to the current BTK implementation in both Python and Node; the image-diff backend remains vendored and packaged.

```sh
# one-off: nothing cloned, uv caches the build
uv run --no-project --with "git+https://github.com/myon-bioinformatics/browser-test-kit" python -m btk --help

# or install once, then use it anywhere
uv venv && uv pip install "git+https://github.com/myon-bioinformatics/browser-test-kit"
python -m btk --help

# inside a clone, without installing
PYTHONPATH=tools python -S -m btk --help
```

Module names are the file names (`btk`, `gh_ops`, `route_trail`, ...), so install into an environment without other modules of the same names, such as a uv run or a virtualenv. `tests/python/test_python_m.py` runs `python -S -m NAME --help` for every script, and CI installs the repository and runs one module from outside the checkout.

## Standard-library one-liners

The stdlib alone can size up a checkout, a data file, or a running process from a single command line, no extra files or packages. `python -m zipfile -c sel.zip a b` zips only the files you name, narrowing what a reviewer or a scanning tool sees; `json.tool`/`pprint` make structured data readable at a glance; `tempfile` hands you a scratch directory when you don't want to touch the repo.

Every command below was actually run (not just `--help`) under this repo's system Python (3.11), and under 3.9 and 3.13 via `uv run --no-project --python 3.9|3.13 python ...`. **Min Python** is the oldest of those three it worked on.

**A. `python -m` CLIs**

| Purpose | Command | Min Python |
|---|---|---|
| Pretty-print/validate JSON (`--json-lines` for JSONL) | `python -m json.tool --sort-keys in.json` | 3.9+ |
| Zip only the files you name (also `-l` list, `-e` extract, `-t` test) | `python -m zipfile -c sel.zip a.txt b.txt` | 3.9+ |
| Same, as a `.tar` (`-l`/`-c`/`-e`) | `python -m tarfile -c sel.tar a.txt b.txt` | 3.9+ |
| gzip/gunzip a file in place | `python -m gzip -d file.txt.gz` | 3.9+ |
| base64 encode/decode a stream | `echo hi \| python -m base64 -e` | 3.9+ |
| Serve a directory read-only, bound to localhost | `python -m http.server --bind 127.0.0.1 --directory DIR 8000` | 3.9+ |
| One-line OS/Python platform string | `python -m platform` | 3.9+ |
| Installation scheme and path config | `python -m sysconfig` | 3.9+ |
| Effective `sys.path` plus the user site dir | `python -m site` | 3.9+ |
| Print a month/year calendar | `python -m calendar 2026 9` | 3.9+ |
| Time a snippet | `python -m timeit "'-'.join(map(str, range(100)))"` | 3.9+ |
| Profile a script, sorted by cumulative time | `python -m cProfile -s cumtime script.py` | 3.9+ |
| Line-coverage summary for a script | `python -m trace --count --summary script.py` | 3.9+ |
| Show the token stream of a source file | `python -m tokenize file.py` | 3.9+ |
| Dump a file's parsed AST | `python -m ast file.py` | 3.9+ |
| Disassemble a file's bytecode | `python -m dis file.py` | 3.9+ |
| Source location + doc for `module:qualname` | `python -m inspect pkg.mod:Class.method --details` | 3.9+ |
| Search installed modules by keyword | `python -m pydoc -k zipfile` | 3.9+ |
| Syntax-check a file (`py_compile`) or a whole tree | `python -m compileall -q src/` | 3.9+ |
| Discover and run tests | `python -m unittest discover -v` | 3.9+ |
| Run a module's doctest examples | `python -m doctest mod.py -v` | 3.9+ |
| Run one SQL statement against a `.db` file | `python -m sqlite3 data.db "select 1"` | 3.12+ (no CLI before) |
| Generate a UUID | `python -m uuid` | 3.12+ (no CLI before) |
| Random choice/int/float from the shell | `python -m random -c heads tails` | 3.13+ (earlier just self-tests, ignoring args) |
| Create a throwaway virtualenv | `python -m venv .venv` | 3.9+ |
| Package a directory into one runnable `.pyz` | `python -m zipapp app_dir -o app.pyz -p "/usr/bin/env python3"` | 3.9+ |
| Guess a file's MIME type | `python -m mimetypes file.pdf` | 3.9+ |
| Open a URL in the default browser | `python -m webbrowser -t "https://example.com"` | 3.9+ |
| Print the Zen of Python | `python -m this` | 3.9+ |

`http.server --bind 127.0.0.1 --directory DIR` and `webbrowser` were checked via `--help`/source only (both flags exist on 3.9+); this cheatsheet doesn't start servers or open a browser.

**B. `python -c` one-liners**

| Purpose | Command | Min Python |
|---|---|---|
| Scratch directory outside the repo | `python -c "import tempfile; print(tempfile.mkdtemp())"` | 3.9+ |
| Pretty-print a JSON file | `python -c "import json,pprint,sys; pprint.pp(json.load(open(sys.argv[1])))" in.json` | 3.9+ |
| Zip only files changed vs `main` | `git diff --name-only main \| python -c "import sys,zipfile; z=zipfile.ZipFile('changed.zip','w'); [z.write(f) for f in sys.stdin.read().split()]; z.close()"` | 3.9+ |
| Count lines per file extension | `python -c "import pathlib,collections; c=collections.Counter(); [c.update({p.suffix or '(none)': len(p.read_bytes().splitlines())}) for p in pathlib.Path('.').rglob('*') if p.is_file()]; print(c)"` | 3.9+ |
| 10 largest files under the cwd | `python -c "import pathlib; fs=sorted((p for p in pathlib.Path('.').rglob('*') if p.is_file()), key=lambda p: p.stat().st_size, reverse=True); [print(p.stat().st_size, p) for p in fs[:10]]"` | 3.9+ |
| Which env var NAMES are set (never values) | `python -c "import os; [print(k) for k in sorted(os.environ)]"` | 3.9+ |
| Is a TCP port free to bind (raises if busy) | `python -c "import socket; s=socket.socket(); print(s.bind(('127.0.0.1', 8000)) or True)"` | 3.9+ |
| sha256 of one or more files | `python -c "import hashlib,sys; [print(hashlib.sha256(open(f,'rb').read()).hexdigest(), f) for f in sys.argv[1:]]" file1 file2` | 3.9+ |
| Human-readable tree, 2 levels deep | `python -c "import pathlib; r=pathlib.Path('.'); [print('  '*(len(p.relative_to(r).parts)-1)+p.name) for p in sorted(r.rglob('*')) if len(p.relative_to(r).parts)<=2]"` | 3.9+ |
| Decode a URL-encoded string | `python -c "import urllib.parse,sys; print(urllib.parse.unquote(sys.argv[1]))" 'a%20b'` | 3.9+ |

Never print secret values: the env-var check above lists names only, so a `TOKEN`/`KEY`/`SECRET` variable shows only that it's set.
