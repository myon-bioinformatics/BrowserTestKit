#!/usr/bin/env python3
"""Manual Linux experiment; all evidence is written outside the checkout."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
import io
from types import SimpleNamespace
import nano_bun as adapter

PARENT = '3128b4019b9949a6ccc80c686cd84c93f04af119'
HELPER_SHA = '9e042eb70a80c75f99aafb1956386b3f5e27ee795ec501d3bcece3a369d09777'
NODE = 'v26.0.0'
NPM = '11.9.0'
BUN_URL = 'https://github.com/oven-sh/bun/releases/download/bun-v1.4.4/bun-linux-x64.zip'


def tree(path):
    """Logical bytes and content identity (not physical blocks or RSS)."""
    entries = {}
    for p in sorted(path.rglob('*')):
        name = str(p.relative_to(path))
        if p.is_symlink():
            entries[name] = {'link': os.readlink(p)}
        elif p.is_file():
            entries[name] = {'bytes': p.stat().st_size, 'sha256': adapter.sha256(p.read_bytes())}
    return {'logical_bytes': sum(e.get('bytes', 0) for e in entries.values()), 'files': entries}


def verify_cache(path, expected):
    adapter.require(tree(path) == expected, 'cache contents changed or corrupted; refusing warm run')


def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read(100 * 1024 * 1024)


def prepare(root):
    data = download(f'https://raw.githubusercontent.com/myon-bioinformatics/myon-bioinformatics/{PARENT}/tools/nano_node.py')
    adapter.require(adapter.sha256(data) == HELPER_SHA, 'parent helper hash mismatch')
    helper = root / 'nano_node.py'
    helper.write_bytes(data)
    spec = importlib.util.spec_from_file_location('fixed_nano_node', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = root / 'source'
    for name, expected in module.BTK_SOURCE_BLOBS.items():
        data = download(f'https://raw.githubusercontent.com/myon-bioinformatics/browser-test-kit/{adapter.SOURCE_COMMIT}/{name}')
        adapter.require(module.git_blob(data) == expected, f'source mismatch: {name}')
        dest = source / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return module.prepare(source, root / 'prepared', feature=adapter.FEATURE)


def npm_inventory(project, job, snapshot):
    # npm's standard hidden lock is metadata, not an installed package. Preserve
    # it as evidence while using the unchanged Bun inventory validator.
    hidden_lock = project / 'node_modules/.package-lock.json'
    adapter.require(hidden_lock.is_file() and not hidden_lock.is_symlink(), 'missing npm installed lock')
    saved_lock = job / 'npm-installed-lock.json'
    hidden_lock.rename(saved_lock)
    try:
        inventory = adapter.validate_inventory(project, json.loads(snapshot['package-lock.json']))
    finally:
        saved_lock.rename(hidden_lock)
    return inventory


def baseline(root, prepared, node, npm):
    job = root / 'npm-baseline'
    job.mkdir(mode=0o700)
    project = job / 'project'
    snapshot = adapter.read_prepared(prepared)
    for name, data in snapshot.items():
        p = project / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    bins = job / 'bin'
    bins.mkdir()
    (bins / 'node').symlink_to(node)
    (bins / 'python').symlink_to(Path(sys.executable).resolve())
    env = adapter.clean_env(job, bins)
    config = job / 'user.npmrc'
    config.write_text('')
    global_config = job / 'global.npmrc'
    global_config.write_text('')
    cache = job / 'npm-cache'
    adapter.execute([node, npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund',
                     '--registry=https://registry.npmjs.org/', '--fetch-retries=0', '--fetch-timeout=30000',
                     f'--userconfig={config}', f'--globalconfig={global_config}', f'--cache={cache}'],
                    project, env, job, 'install')
    inventory = npm_inventory(project, job, snapshot)
    adapter.execute([node, project / 'node_modules/@playwright/test/cli.js', 'test', '--config',
                     project / 'playwright.browserless.config.ts', 'tests/browserless/mirror.spec.ts',
                     '--grep', '(' + '|'.join(adapter.TITLES) + ')$', '--workers=1', '--reporter=json',
                     '--output', job / 'test-results'], project, env, job, 'test')
    result = adapter.validate_test_report(json.loads((job / 'test.stdout').read_text()))
    adapter.require(all((project / n).read_bytes() == d for n, d in snapshot.items()), 'npm changed inputs')
    adapter.require(not any((job / 'browsers').iterdir()), 'unexpected browser files')
    result.update(inventory)
    result['artifact_sha256'] = artifacts(job)
    result['npm_installed_lock_sha256'] = adapter.sha256((project / 'node_modules/.package-lock.json').read_bytes())
    (job / 'receipt.json').write_text(json.dumps(result, indent=2))
    return job


def artifacts(job):
    result = {str(p.relative_to(job / 'test-results')): adapter.sha256(p.read_bytes())
              for p in (job / 'test-results').rglob('*.json') if p.name != '.last-run.json'}
    adapter.require(len(result) == 4, 'expected exactly four outputs')
    return result


def measure(job, cache):
    return {'install': json.loads((job / 'install.json').read_text()),
            'test': json.loads((job / 'test.json').read_text()),
            'migration': json.loads((job / 'migration.json').read_text()) if (job / 'migration.json').exists() else None,
            'node_modules': tree(job / 'project/node_modules'), 'cache': tree(cache),
            'receipt': json.loads((job / 'receipt.json').read_text())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    summary = {'status': 'failed', 'conditions': {}, 'parent': PARENT, 'helper_sha256': HELPER_SHA,
               'source': adapter.SOURCE_COMMIT, 'bun_url': BUN_URL,
               'scope': 'package cache cold/warm, not OS/network cold; logical bytes, not disk blocks/RSS',
               'runner': {k: os.environ.get(k) for k in ('ImageOS', 'ImageVersion', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')}}
    try:
        node = Path(shutil.which('node')).resolve()
        npm = Path(shutil.which('npm')).resolve()
        adapter.require(subprocess.check_output([node, '--version'], text=True).strip() == NODE, 'wrong Node')
        adapter.require(subprocess.check_output([node, npm, '--version'], text=True).strip() == NPM, 'wrong npm')
        summary.update(node_version=NODE, npm_version=NPM, node_sha256=adapter.sha256(node.read_bytes()))
        started = time.monotonic()
        prepared = prepare(root)
        summary['snapshot_preparation_seconds'] = time.monotonic() - started
        started = time.monotonic()
        archive = download(BUN_URL)
        (root / 'bun.zip').write_bytes(archive)
        data = zipfile.ZipFile(io.BytesIO(archive)).read('bun-linux-x64/bun')
        adapter.require(adapter.sha256(data) == adapter.BUN_SHA256, 'Bun executable hash mismatch')
        bun = root / 'bun'
        bun.write_bytes(data)
        bun.chmod(0o700)
        adapter.require(subprocess.check_output([bun, '--version'], text=True).strip() == adapter.BUN_VERSION, 'wrong Bun')
        summary['bun_preparation'] = {'seconds': time.monotonic() - started, 'archive_bytes': len(archive),
                                      'executable_bytes': len(data), 'sha256': adapter.BUN_SHA256}
        cache = root / 'bun-cache'
        work = root / 'jobs'
        params = SimpleNamespace(prepared=prepared, work_root=work, cache_root=cache,
                                 node=node, bun=bun, python=Path(sys.executable).resolve(), allow_network=True)
        # No Actions restore or broad fallback key: this cache starts absent on every dispatch.
        adapter.require(not cache.exists(), 'cold cache must be absent')
        cold = adapter.run(params)
        summary['conditions']['bun-cold'] = measure(cold, cache)
        seal = tree(cache)
        (root / 'cache-seal.json').write_text(json.dumps(seal, indent=2))
        verify_cache(cache, seal)
        params.allow_network = False
        warm = adapter.run(params)
        summary['conditions']['bun-warm-offline'] = measure(warm, cache)
        npm_job = baseline(root, prepared, node, npm)
        summary['conditions']['npm-cold'] = measure(npm_job, npm_job / 'npm-cache')
        expected = artifacts(npm_job)
        adapter.require(artifacts(cold) == artifacts(warm) == expected, 'output SHA-256 mismatch')
        adapter.require(adapter.sha256(node.read_bytes()) == summary['node_sha256'], 'Node changed')
        summary['status'] = 'passed'
    except BaseException as exc:
        summary['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        # Keep logs, inputs, reports and receipts, not installed code or package caches.
        (root / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
