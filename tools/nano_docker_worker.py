#!/usr/bin/env python3
"""Image dependency preparation and one unchanged two-test Docker sample."""
import json, os, shutil, subprocess, sys, time
from pathlib import Path
import nano_bun as adapter
import nano_bun_experiment as experiment


def runtime():
    node = Path(shutil.which('node')).resolve()
    npm = Path(shutil.which('npm')).resolve()
    adapter.require(subprocess.check_output([node, '--version'], text=True).strip() == experiment.NODE, 'wrong Docker Node')
    adapter.require(subprocess.check_output([node, npm, '--version'], text=True).strip() == experiment.NPM, 'wrong Docker npm')
    return node, npm, {'node_version': experiment.NODE, 'node_sha256': adapter.sha256(node.read_bytes()),
                       'python_version': list(sys.version_info[:3]),
                       'packages': subprocess.check_output(['dpkg-query', '-W'], text=True)}


def prepare():
    job = Path('/opt/dependency-evidence')
    job.mkdir()
    project = Path('/opt/project')
    snapshot = adapter.read_prepared(Path('/opt/prepared'))
    for name, data in snapshot.items():
        p = project / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    node, npm, identity = runtime()
    bins = job / 'bin'
    bins.mkdir()
    (bins / 'node').symlink_to(node)
    (bins / 'python').symlink_to(Path(sys.executable).resolve())
    env = adapter.clean_env(job, bins)
    for name in ('user.npmrc', 'global.npmrc'):
        (job / name).write_text('')
    # New image layer, empty private npm cache; ignore lifecycle scripts.
    adapter.execute([node, npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund',
                     '--fetch-retries=0', '--fetch-timeout=30000',
                     '--registry=https://registry.npmjs.org/', f'--cache={job / "npm-cache"}',
                     f'--userconfig={job / "user.npmrc"}', f'--globalconfig={job / "global.npmrc"}'],
                    project, env, job, 'install')
    inventory = experiment.npm_inventory(project, job, snapshot)
    adapter.require(all((project / n).read_bytes() == d for n, d in snapshot.items()), 'Docker install changed inputs')
    (job / 'runtime.json').write_text(json.dumps(identity, indent=2))
    (job / 'inventory.json').write_text(json.dumps(inventory, indent=2))
    (job / 'capacity.json').write_text(json.dumps({
        'node_modules_logical_bytes': experiment.tree(project / 'node_modules')['logical_bytes'],
        'npm_cache_logical_bytes': experiment.tree(job / 'npm-cache')['logical_bytes']}))
    shutil.rmtree(job / 'npm-cache')


def test():
    job = Path('/evidence')
    job.mkdir(exist_ok=True)
    report = {'status': 'failed'}
    started = time.monotonic()
    try:
        project = Path('/opt/project')
        snapshot = adapter.read_prepared(Path('/opt/prepared'))
        node, _, identity = runtime()
        adapter.require(identity['node_sha256'] == os.environ['EXPECTED_NODE_SHA256'], 'host/container Node binary mismatch')
        bins = job / 'bin'
        bins.mkdir()
        (bins / 'node').symlink_to(node)
        (bins / 'python').symlink_to(Path(sys.executable).resolve())
        env = adapter.clean_env(job, bins)
        adapter.execute([node, project / 'node_modules/@playwright/test/cli.js', 'test', '--config',
                         project / 'playwright.browserless.config.ts', 'tests/browserless/mirror.spec.ts',
                         '--grep', '(' + '|'.join(adapter.TITLES) + ')$', '--workers=1', '--reporter=json',
                         '--output', job / 'test-results'], project, env, job, 'test')
        report.update(adapter.validate_test_report(json.loads((job / 'test.stdout').read_text())))
        adapter.require(all((project / n).read_bytes() == d for n, d in snapshot.items()), 'Docker test changed inputs')
        adapter.require(not any((job / 'browsers').iterdir()), 'unexpected browsers')
        report.update(status='passed', source_commit=adapter.SOURCE_COMMIT,
                      artifact_sha256=experiment.artifacts(job), runtime=identity)
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['worker_wall_seconds'] = time.monotonic() - started
        (job / 'receipt.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    {'prepare': prepare, 'test': test}[sys.argv[1]]()
