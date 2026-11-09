#!/usr/bin/env python3
"""Opt-in, fixed-snapshot Bun installation followed by unchanged Node tests.

Linux x86_64 only. No runtime download, fallback resolver or checkout mutation.
The cache must belong to one trusted user; this helper holds an exclusive lock
through installation AND tests because Bun may hardlink cache files into jobs.
"""
from __future__ import annotations

import argparse, hashlib, json, os, platform, re, signal, stat, subprocess, tempfile, time
from collections import Counter; from contextlib import contextmanager; from pathlib import Path

FEATURE = "browser-test-kit-document-mirrors"
SOURCE_COMMIT = "111f6d78befae28acfb198a45b72e5aa0e59dcde"
BUN_VERSION = "1.4.4"
BUN_SHA256 = "10702245766b225e70bbf6235fd6b47214fd94b4760d00ffaed8ab0df57ac235"
BUN_LOCK_SHA256 = "49ce401eed94ca7c4711460e169ae1c2cceefc9a88a7370bfcd813945246915a"
MANIFEST_SHA256 = "91b053fd5836eab929768f8b8ead6f318176bee14387b37df442cde890ab6863"
LOCK_SHA256 = "6eee408764ee4b265dfe152b9a87708d3119412af903a776e789833d598c775c"
SOURCE_BLOBS = {
    "tools/mirror_facts.mjs": "9b63b223619e2051aa1df81a574b8734ae4fbd17",
    "tools/mirror_check.py": "c37ee194ca98245755458ce1a17f41a1aea47c79",
    "tests/browserless/mirror.spec.ts": "30d14101cd1b294a05d1b817cd908e824858eea8",
    "tests/python/test_mirror_check.py": "f7ae34a8e1e738d44f9a7af5e7dc4e5332b2b9d5",
    "playwright.browserless.config.ts": "1d3990ea0e09c37a8ab82d74b8368a9d97da436f",
    "fixtures/mirror.md": "7f4ee2e50ebce5682d508d382caa8e5a76a4b750",
    "LICENSE": "4ec4989b801bf1a3df6184d21f79e6e7ed5931f6",
}
TITLES = (
    "markdown-it and marked agree on the CommonMark fixture",
    "HTML reader emits complete facts accepted by compare",
)


class Error(ValueError):
    """A closed gate; callers must not substitute an unrestricted installer."""


def require(condition, message):
    if not condition:
        raise Error(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def regular_bytes(path):
    require(not any(p.is_symlink() for p in (path, *path.parents)), f"symlink input: {path}")
    require(path.is_file() and path.stat().st_size <= 2 * 1024 * 1024, f"invalid input: {path}")
    data = path.read_bytes()
    require(len(data) <= 2 * 1024 * 1024, f"input grew: {path}")
    return data


def read_prepared(prepared_root):
    receipt = json.loads(regular_bytes(prepared_root / "receipt.json"))
    require(receipt.get("feature") == FEATURE and receipt.get("source_commit") == SOURCE_COMMIT,
            "wrong prepared feature/source; use the pinned nano preparation")
    project = prepared_root / "browser-test-kit"
    result = {}
    for name in ("package.json", "package-lock.json", *SOURCE_BLOBS):
        data = regular_bytes(project / name)
        if name in SOURCE_BLOBS:
            actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            expected = SOURCE_BLOBS[name]
        else:
            actual = sha256(data)
            expected = MANIFEST_SHA256 if name == "package.json" else LOCK_SHA256
        require(actual == expected, f"prepared source/lock mismatch: {name}")
        result[name] = data
    return result


def private_directory(path):
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
            f"expected absolute non-symlink directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    require(path.is_dir() and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            f"directory must be owned by this user with mode 0700: {path}")


def clean_env(job, bin_dir):
    env = {k: v for k, v in os.environ.items() if k in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    for key in ("NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE"):
        if key in os.environ:
            path = Path(os.environ[key])
            require(path.is_absolute() and path.is_file(), f"invalid existing CA configuration: {key}")
            path.read_bytes()  # Require readable existing trust; never disable TLS.
            env[key] = str(path)
    for name in ("home", "config", "tmp", "cache-other", "browsers"):
        (job / name).mkdir(mode=0o700, exist_ok=True)
    env.update(PATH=str(bin_dir), HOME=str(job / "home"), XDG_CONFIG_HOME=str(job / "config"),
               XDG_CACHE_HOME=str(job / "cache-other"), TMPDIR=str(job / "tmp"),
               PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1", PLAYWRIGHT_BROWSERS_PATH=str(job / "browsers"),
               CI="1", NO_COLOR="1", TZ="UTC")
    return env


def probe_runtime(node, bun, python, env):
    require(platform.system() == "Linux" and platform.machine() == "x86_64",
            "only reviewed Linux x86_64 is supported")
    for tool in (node, bun, python):
        require(tool.is_absolute() and tool.is_file() and os.access(tool, os.X_OK), f"invalid executable: {tool}")
    require(sha256(bun.read_bytes()) == BUN_SHA256, "unreviewed Bun executable; expected official Linux x64 1.4.4")
    def output(command):
        return subprocess.run([str(x) for x in command], env=env, cwd=env["HOME"],
                              capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    require(output([bun, "--version"]) == BUN_VERSION, "wrong Bun version")
    info = json.loads(output([node, "-p", "JSON.stringify({release:process.release.name,version:process.version,abi:process.versions.modules,platform:process.platform,arch:process.arch,execPath:process.execPath,bun:!!process.versions.bun})"]))
    require(info.get("release") == "node" and not info.get("bun") and info.get("platform") == "linux"
            and info.get("arch") == "x64" and Path(info.get("execPath", "")) == node,
            "expected the explicit real Node executable, not Bun or a launcher")
    require(re.fullmatch(r"v\d+\.\d+\.\d+", info.get("version", "")) is not None
            and int(info["version"].split(".")[0][1:]) >= 24
            and str(info.get("abi", "")).isdigit(), "Node >=24 with an explicit ABI is required")
    py = json.loads(output([python, "-I", "-B", "-S", "-c", "import json,sys; print(json.dumps(list(sys.version_info[:3])))"]))
    require(py[0] == 3 and py[1] >= 11, "Python 3.11+ comparator interpreter required")
    return {"platform": info["platform"], "arch": info["arch"], "node_version": info["version"],
            "node_abi": info["abi"], "node_sha256": sha256(node.read_bytes()),
            "bun_version": BUN_VERSION, "bun_sha256": BUN_SHA256, "python_version": py}


def cache_key(runtime):
    fields = {k: runtime[k] for k in ("platform", "arch", "bun_version", "bun_sha256",
                                    "node_version", "node_abi", "node_sha256")}
    fields.update(npm_lock_sha256=LOCK_SHA256, bun_lock_sha256=BUN_LOCK_SHA256, format=1)
    return sha256(json.dumps(fields, sort_keys=True).encode())


@contextmanager
def cache_lock(cache_root, key):
    import fcntl  # The platform gate runs first; no pretend Windows locking.
    require(re.fullmatch(r"[0-9a-f]{64}", key) is not None, "invalid cache key")
    private_directory(cache_root)
    directory = cache_root / key
    private_directory(directory)
    fd = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                "unsafe cache lock file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Error("cache busy; another job owns this key (no concurrent mutation)") from exc
        packages = directory / "packages"
        private_directory(packages)
        active = directory / "active.json"
        require(not active.exists() and not active.is_symlink(),
                "cache needs inspection after a failed/interrupted job; use a new cache root")
        with active.open("x") as marker:
            json.dump({"pid": os.getpid(), "started": time.time()}, marker)
        yield packages
        active.unlink()  # Failure/SIGKILL deliberately leaves a quarantine marker.
    finally:
        os.close(fd)


def install_command(bun, project, cache, config, allow_network):
    command = [str(bun), "install", "--frozen-lockfile", "--ignore-scripts", "--no-env-file",
               "--linker=hoisted", "--registry=https://registry.npmjs.org/",
               f"--cache-dir={cache}", f"--config={config}"]
    if not allow_network:
        command.append("--offline")
    return command


def validate_inventory(project, original_lock):
    locked = {k: v for k, v in original_lock["packages"].items() if k}
    identity = lambda k, v: (k.split("node_modules/")[-1], v["version"])
    expected = Counter(identity(k, v) for k, v in locked.items())
    installed = {}
    modules = project / "node_modules"
    require(modules.is_dir() and not modules.is_symlink(), "missing private node_modules")
    for directory in (modules, *modules.rglob("node_modules")):
        require(not directory.is_symlink(), "symlink node_modules is unsupported")
        for entry in directory.iterdir():
            if entry.name.startswith("."):
                require(entry.name == ".bin" and entry.is_dir() and not entry.is_symlink(),
                        f"unexpected hidden installation entry: {entry.name}")
                require(all(p.is_file() or p.is_symlink() for p in entry.iterdir()), "unexpected nested .bin content")
                continue
            for package in entry.iterdir() if entry.name.startswith("@") else (entry,):
                require(package.is_dir() and not package.is_symlink(), "unexpected package path")
                data = json.loads(regular_bytes(package / "package.json"))
                installed[package] = data
    actual = Counter((v["name"], v["version"]) for v in installed.values())
    require(actual in (expected, expected + Counter({("domelementtype", "3.0.0"): 2})),
            "installed identities differ from the reviewed 30/32-copy layout")
    def resolve(base, name, records):
        for parent in (base, *base.parents):
            candidate = parent / "node_modules" / name
            if candidate in records:
                value = records[candidate]
                return (value.get("name", candidate.name), value["version"])
        raise Error(f"unresolved dependency: {name}")
    expected_records = {project / k: dict(v, name=identity(k, v)[0]) for k, v in locked.items()}
    def edges(records):
        return Counter(((v["name"], v["version"]), name, resolve(k, name, records))
                       for k, v in records.items() for name in v.get("dependencies", {}))
    require(edges(installed) == edges(expected_records), "installed dependency graph changed")
    for name in original_lock["packages"][""]["devDependencies"]:
        require(resolve(project, name, installed) == identity("node_modules/" + name, locked["node_modules/" + name]),
                f"wrong direct dependency: {name}")
    return {"package_copies": sum(actual.values()), "unique_identities": len(actual),
            "dependency_edges": sum(edges(installed).values())}


def validate_test_report(report):
    require(not report.get("errors"), "test runner reported errors")
    cases = []
    def visit(suite):
        for spec in suite.get("specs", []):
            for test in spec["tests"]:
                cases.append((spec["title"], test["status"], [r["status"] for r in test["results"]]))
        for child in suite.get("suites", []):
            visit(child)
    visit(report)
    require(sorted(c[0] for c in cases) == sorted(TITLES)
            and all(c[1:] == ("expected", ["passed"]) for c in cases),
            "expected exactly two unchanged passing tests with zero skips/retries")
    return {"unique_tests": 2, "passed": 2, "skipped": 0}


@contextmanager
def cancellation():
    def terminate(signum, frame):
        raise Error("cancelled by SIGTERM; child processes will be stopped")
    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def stop_group(process):
    # Do not release the cache lock while interrupted cleanup leaves children alive.
    previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            if sig == signal.SIGTERM:
                time.sleep(0.1)
        process.wait(timeout=10)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def execute(command, project, env, job, label):
    started = time.monotonic()
    failure = None
    with (job / f"{label}.stdout").open("w") as out, (job / f"{label}.stderr").open("w") as err:
        process = subprocess.Popen([str(x) for x in command], cwd=project, env=env, stdout=out,
                                   stderr=err, start_new_session=True)
        try:
            code = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            stop_group(process)
            code = "timeout"
        except BaseException as exc:
            stop_group(process)
            code, failure = "cancelled", exc
    (job / f"{label}.json").write_text(json.dumps({"command": [str(x) for x in command],
        "returncode": code, "seconds": time.monotonic() - started}, indent=2) + "\n")
    if failure is not None:
        raise failure
    require(code == 0, f"{label} failed ({code}); see {job}; offline cache misses never fall back to network")


def run(args):
    require(platform.system() == "Linux" and platform.machine() == "x86_64", "only Linux x86_64 is supported")
    snapshot = read_prepared(args.prepared)
    checkout = Path(__file__).resolve().parents[1]
    for path in (args.work_root, args.cache_root):
        require(path == path.resolve(), "job/cache paths must be canonical absolute paths")
        require(checkout != path and checkout not in path.parents, "job/cache must be outside the checkout")
        prepared = args.prepared.resolve()
        require(prepared != path and prepared not in path.parents, "job/cache must be outside prepared inputs")
        private_directory(path)
    job = Path(tempfile.mkdtemp(prefix="btk-bun-", dir=args.work_root))
    try:
        binaries = job / "bin"
        binaries.mkdir(mode=0o700)
        node, bun, python = (p.resolve(strict=True) for p in (args.node, args.bun, args.python))
        for name, executable in (("node", node), ("python", python)):
            (binaries / name).symlink_to(executable)
        env = clean_env(job, binaries)
        runtime = probe_runtime(node, bun, python, env)
        runtime["existing_ca_files"] = {k: {"path": env[k], "sha256": sha256(Path(env[k]).read_bytes())}
                                        for k in ("NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE") if k in env}
        key = cache_key(runtime)
        (job / "runtime.json").write_text(json.dumps(dict(runtime, cache_key=key), indent=2) + "\n")
        project = job / "project"
        project.mkdir(mode=0o700)
        for name, data in snapshot.items():
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        config = job / "bunfig.toml"
        config.write_text("")
        (project / ".npmrc").write_text("")
        def unchanged():
            require(all((project / name).read_bytes() == data for name, data in snapshot.items()),
                    "staged sources/lock changed")
        with cancellation(), cache_lock(args.cache_root, key) as cache:
            env["BUN_INSTALL_CACHE_DIR"] = str(cache)
            execute([bun, "pm", "migrate", "--offline", "--ignore-scripts", "--no-env-file", f"--config={config}"],
                    project, env, job, "migration")
            unchanged()
            require(sha256((project / "bun.lock").read_bytes()) == BUN_LOCK_SHA256,
                    "Bun lock differs from the reviewed identities/integrities/graph")
            execute(install_command(bun, project, cache, config, args.allow_network), project, env, job, "install")
            unchanged()
            require(sha256((project / "bun.lock").read_bytes()) == BUN_LOCK_SHA256, "Bun lock changed during install")
            inventory = validate_inventory(project, json.loads(snapshot["package-lock.json"]))
            execute([node, project / "node_modules/@playwright/test/cli.js", "test", "--config",
                     project / "playwright.browserless.config.ts", "tests/browserless/mirror.spec.ts",
                     "--grep", "(" + "|".join(TITLES) + ")$", "--workers=1", "--reporter=json",
                     "--output", job / "test-results"], project, env, job, "test")
            result = validate_test_report(json.loads((job / "test.stdout").read_text()))
            unchanged()
            require(sha256(node.read_bytes()) == runtime["node_sha256"], "Node executable changed")
            require(sha256(bun.read_bytes()) == BUN_SHA256, "Bun executable changed")
            require(sha256((project / "bun.lock").read_bytes()) == BUN_LOCK_SHA256, "Bun lock changed during tests")
            require(not any((job / "browsers").iterdir()), "unexpected browser files")
            artifacts = {str(p.relative_to(job / "test-results")): sha256(p.read_bytes())
                         for p in (job / "test-results").rglob("*.json") if p.name != ".last-run.json"}
            require(len(artifacts) == 4, "expected four document-fact artifacts")
            (job / "receipt.json").write_text(json.dumps(dict(result, **inventory, cache_key=key,
                source_commit=SOURCE_COMMIT, installer="bun", runtime="node", network_allowed=args.allow_network,
                artifact_sha256=artifacts,
                cache_trust="same trusted user; exclusive lock held through tests; no automatic deletion"), indent=2) + "\n")
        return job
    except BaseException as exc:
        (job / "failure.txt").write_text(str(exc) + "\n")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prepared", "work-root", "cache-root", "node", "bun", "python"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--allow-network", action="store_true", help="explicitly permit fixed package downloads; default is offline")
    args = parser.parse_args()
    try:
        print(run(args))
    except (Error, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, f"nano-bun: {exc}\n")


if __name__ == "__main__":
    main()
