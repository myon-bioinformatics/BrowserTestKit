#!/usr/bin/env python3
"""Opt-in same-host Docker/npm image versus private offline Bun + Node jobs."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
import uuid
import nano_bun as adapter
import nano_bun_experiment as experiment

ORDERS = (('nano', 'docker'), ('docker', 'nano'), ('nano', 'docker'))
TOOLS = Path(__file__).resolve().parent


def image_ref(value):
    adapter.require(re.fullmatch(r'node:26\.0\.0-bookworm-slim@sha256:[0-9a-f]{64}', value) is not None,
                    'base must be node:26.0.0-bookworm-slim pinned by SHA-256 digest')
    return value


def command(argv, folder, label, timeout=600):
    """Persist command, status, duration and logs, including on timeout."""
    started = time.monotonic()
    record = {'command': [str(x) for x in argv], 'returncode': None}
    try:
        with (folder / f'{label}.stdout').open('w') as out, (folder / f'{label}.stderr').open('w') as err:
            result = subprocess.run(record['command'], stdout=out, stderr=err, timeout=timeout)
        record['returncode'] = result.returncode
        adapter.require(result.returncode == 0, f'{label} failed; see {folder}')
    except BaseException as exc:
        record['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        record['seconds'] = time.monotonic() - started
        (folder / f'{label}.json').write_text(json.dumps(record, indent=2))
    return (folder / f'{label}.stdout').read_text().strip()


def load(path):
    return json.loads(path.read_text())


def validate(receipt, expected):
    adapter.require(receipt.get('passed') == receipt.get('unique_tests') == 2
                    and receipt.get('skipped') == 0 and len(expected) == 4
                    and receipt.get('artifact_sha256') == expected
                    and receipt.get('source_commit') == adapter.SOURCE_COMMIT, 'output/test/source mismatch')


def copy_prepared_snapshot(seed, context):
    """Flatten the preparation helper's unique job into the Docker contract."""
    candidates = list((seed / 'prepared').iterdir())
    adapter.require(len(candidates) == 1 and candidates[0].is_dir(),
                    'expected exactly one prepared snapshot')
    prepared = candidates[0]
    # Reject malformed, altered or symlinked inputs before copying/building.
    adapter.read_prepared(prepared)
    shutil.copytree(prepared, context / 'prepared')
    adapter.read_prepared(context / 'prepared')
    return prepared


def copy_private_cache(source, destination, seal):
    """Copy bytes independently and relocate only validated cache-local links."""
    experiment.verify_cache(source, seal)
    source_root = source.resolve(strict=True)
    relocated = dict(seal, files={name: dict(entry) for name, entry in seal['files'].items()})
    for name, entry in seal['files'].items():
        if 'link' not in entry:
            continue
        link = source / name
        try:
            target = link.resolve(strict=True)
            relative_target = target.relative_to(source_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise adapter.Error(f'cache link must resolve inside seed: {name}') from exc
        adapter.require(target.is_file() or target.is_dir(), f'unsupported cache link target: {name}')
        relocated['files'][name] = {
            'link': os.path.relpath(destination / relative_target, (destination / name).parent)}
    # copy2 creates independent regular files; preserve links temporarily so
    # copying never follows a link back into the seed or outside the cache.
    copying = time.monotonic()
    shutil.copytree(source, destination, symlinks=True)
    for name, entry in relocated['files'].items():
        if 'link' in entry:
            link = destination / name
            link.unlink()
            link.symlink_to(entry['link'])
    copy_seconds = time.monotonic() - copying
    experiment.verify_cache(source, seal)
    experiment.verify_cache(destination, relocated)
    destination_root = destination.resolve(strict=True)
    for name, entry in relocated['files'].items():
        if 'link' in entry:
            target = (destination / name).resolve(strict=True)
            adapter.require(target.is_relative_to(destination_root),
                            f'copied cache link escaped destination: {name}')
    return relocated, copy_seconds


def nano_sample(seed, root, expected, node, prepared):
    started = time.monotonic()
    seal = load(seed / 'cache-seal.json')
    copied_seal, copy_seconds = copy_private_cache(seed / 'bun-cache', root / 'cache', seal)
    (root / 'cache-seal.json').write_text(json.dumps(copied_seal, indent=2) + '\n')
    command([sys.executable, '-I', '-B', TOOLS / 'nano_bun.py', '--prepared', prepared,
             '--work-root', root / 'jobs', '--cache-root', root / 'cache', '--node', node,
             '--bun', seed / 'bun', '--python', Path(sys.executable).resolve()], root, 'adapter')
    execution_path_seconds = time.monotonic() - started
    jobs = list((root / 'jobs').glob('*/receipt.json'))
    adapter.require(len(jobs) == 1, 'missing/duplicate nano receipt')
    job = jobs[0].parent
    receipt = load(jobs[0])
    validate(receipt, expected)
    adapter.require(receipt.get('network_allowed') is False, 'nano was not offline')
    adapter.require(load(job / 'runtime.json')['node_version'] == experiment.NODE, 'wrong nano Node')
    adapter.require(load(job / 'runtime.json')['node_sha256'] == load(seed / 'summary.json')['node_sha256'],
                    'nano Node binary changed since seed')
    return {'kind': 'nano', 'cache_copy_seconds': copy_seconds,
            'migration_seconds': load(job / 'migration.json')['seconds'],
            'dependency_install_seconds': load(job / 'install.json')['seconds'],
            'test_seconds': load(job / 'test.json')['seconds'],
            'adapter_wall_seconds': load(root / 'adapter.json')['seconds'],
            'cache_logical_bytes': seal['logical_bytes'],
            'node_modules_logical_bytes': experiment.tree(job / 'project/node_modules')['logical_bytes'],
            'execution_path_wall_seconds': execution_path_seconds,
            'receipt': receipt, 'total_wall_seconds': time.monotonic() - started}


def docker_sample(image, root, expected, node_sha):
    started = time.monotonic()
    name = 'btk-nano-' + uuid.uuid4().hex
    report = {'kind': 'docker'}
    try:
        command(['docker', 'create', '--name', name, '--network=none',
                 '--env', f'EXPECTED_NODE_SHA256={node_sha}', image], root, 'create')
        command(['docker', 'start', name], root, 'start')
        # Runtime readiness is checked by the exec below. Exec/IPC overhead is
        # reported separately from the worker's own test timer.
        command(['docker', 'exec', name, 'python3', '/opt/tools/nano_docker_worker.py', 'test'], root, 'exec', 240)
        report['execution_path_wall_seconds'] = time.monotonic() - started
    finally:
        # Preserve evidence before deleting the writable layer, even after failure.
        try:
            command(['docker', 'inspect', '--size', name], root, 'inspect', 30)
            command(['docker', 'cp', name + ':/evidence', root / 'evidence'], root, 'collect', 30)
        finally:
            command(['docker', 'rm', '--force', name], root, 'remove', 30)
    receipt = load(root / 'evidence/receipt.json')
    validate(receipt, expected)
    adapter.require(receipt.get('status') == 'passed', 'Docker worker failed')
    inspect = load(root / 'inspect.stdout')[0]
    config = inspect['HostConfig']
    adapter.require(config['NetworkMode'] == 'none', 'Docker network isolation missing')
    adapter.require(config.get('Memory', 0) == config.get('NanoCpus', 0) == config.get('CpuQuota', 0) == 0,
                    'unexpected Docker-only resource quota')
    adapter.require(receipt['runtime']['node_sha256'] == node_sha, 'unequal Node binaries')
    report.update(create_seconds=load(root / 'create.json')['seconds'],
                  start_api_seconds=load(root / 'start.json')['seconds'],
                  exec_wall_seconds=load(root / 'exec.json')['seconds'],
                  test_seconds=load(root / 'evidence/test.json')['seconds'],
                  receipt=receipt, total_wall_seconds=time.monotonic() - started,
                  writable_layer_bytes=inspect['SizeRw'],
                  cache_copy_seconds=0, dependency_install_seconds=0)
    return report


def comparison(samples, expected):
    adapter.require(len(samples) == 6 and [x['kind'] for x in samples] == [x for order in ORDERS for x in order],
                    'incomplete/incorrect trial plan')
    for sample in samples:
        validate(sample['receipt'], expected)
    return {kind: {metric: statistics.median(x[metric] for x in samples if x['kind'] == kind)
                   for metric in ('test_seconds', 'execution_path_wall_seconds', 'total_wall_seconds')}
            for kind in ('nano', 'docker')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-image', required=True)
    args = parser.parse_args(argv)
    image_ref(args.base_image)
    root = args.output.resolve()
    checkout = TOOLS.parent
    adapter.require(root != checkout and checkout not in root.parents, 'evidence must be outside checkout')
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {'status': 'failed', 'samples': [], 'base_image': args.base_image,
              'source': adapter.SOURCE_COMMIT, 'orders': ORDERS,
              'scope': 'same host; sequential samples; no requested CPU/RAM caps; no fanout or shared-budget claim',
              'runner': {k: os.environ.get(k) for k in ('ImageOS', 'ImageVersion', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')},
              'host': {'platform': platform.platform(), 'cpu_count': os.cpu_count(),
                       'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                       'meminfo': Path('/proc/meminfo').read_text(),
                       'cgroup': Path('/proc/self/cgroup').read_text(),
                       'python_version': list(sys.version_info[:3])}}
    report['implementation_sha256'] = {
        name: adapter.sha256((TOOLS / name).read_bytes())
        for name in ('nano_bun.py', 'nano_bun_experiment.py', 'nano_docker_compare.py', 'nano_docker_worker.py')}
    image = 'btk-comparison:' + uuid.uuid4().hex
    started = time.monotonic()
    try:
        adapter.require(platform.machine() == 'x86_64', 'Linux x86_64 only')
        command(['docker', 'version'], root, 'docker-version', 30)
        command(['docker', 'info'], root, 'docker-info', 30)
        # Runner-imposed limits are distinct from requested experimental caps.
        report['host']['visible_cgroup_v2_limits'] = {
            name: (Path('/sys/fs/cgroup') / name).read_text()
            for name in ('cpu.max', 'memory.max', 'cpuset.cpus.effective')
            if (Path('/sys/fs/cgroup') / name).is_file()}
        seed = root / 'seed'
        command([sys.executable, '-I', '-B', '-c',
                 'import runpy,sys; sys.path.insert(0,' + repr(str(TOOLS)) + '); runpy.run_path(' +
                 repr(str(TOOLS / 'nano_bun_experiment.py')) + ',run_name="__main__")',
                 '--output', seed], root, 'seed', 600)
        summary = load(seed / 'summary.json')
        adapter.require(summary['status'] == 'passed', 'seed experiment failed')
        expected = summary['conditions']['npm-cold']['receipt']['artifact_sha256']
        report['expected_artifact_sha256'] = expected
        context_started = time.monotonic()
        context = root / 'context'
        context.mkdir()
        prepared = copy_prepared_snapshot(seed, context)
        (context / 'tools').mkdir()
        for name in ('nano_bun.py', 'nano_bun_experiment.py', 'nano_docker_worker.py'):
            shutil.copyfile(TOOLS / name, context / 'tools' / name)
        shutil.copyfile(checkout / 'experiments/nano-docker/Dockerfile', context / 'Dockerfile')
        report['context_copy_seconds'] = time.monotonic() - context_started
        report['context_seal'] = experiment.tree(context)
        command(['docker', 'pull', '--platform=linux/amd64', args.base_image], root, 'base-pull')
        command(['docker', 'build', '--platform=linux/amd64', '--no-cache', '--progress=plain',
                 '--target', 'runtime', '--build-arg', 'BASE_IMAGE=' + args.base_image, str(context)], root, 'runtime-build')
        # Reuse the just-built runtime layers; force a fresh dependency RUN.
        command(['docker', 'build', '--platform=linux/amd64', '--progress=plain',
                 '--build-arg', 'BASE_IMAGE=' + args.base_image,
                 '--build-arg', 'DEPENDENCY_NONCE=' + uuid.uuid4().hex,
                 '--tag', image, str(context)], root, 'dependency-build')
        command(['docker', 'image', 'inspect', image], root, 'image-inspect', 30)
        report['image_id'] = load(root / 'image-inspect.stdout')[0]['Id']
        report['image_uncompressed_bytes'] = load(root / 'image-inspect.stdout')[0]['Size']
        # Extract layer-local npm timers and package inventories before sampling.
        name = 'btk-evidence-' + uuid.uuid4().hex
        try:
            command(['docker', 'create', '--name', name, image], root, 'evidence-create', 30)
            command(['docker', 'cp', name + ':/opt/dependency-evidence', root / 'dependency-evidence'], root, 'dependency-collect', 30)
        finally:
            command(['docker', 'rm', '--force', name], root, 'evidence-remove', 30)
        adapter.require(load(root / 'dependency-evidence/runtime.json')['node_sha256'] == summary['node_sha256'],
                        'Docker/host Node binary mismatch')
        node = Path(shutil.which('node')).resolve()
        for trial, order in enumerate(ORDERS, 1):
            for kind in order:
                folder = root / f'trial-{trial}-{kind}'
                folder.mkdir(mode=0o700)
                sample = (nano_sample(seed, folder, expected, node, prepared) if kind == 'nano' else
                          docker_sample(image, folder, expected, summary['node_sha256']))
                sample['trial'] = trial
                report['samples'].append(sample)
        report['medians'] = comparison(report['samples'], expected)
        report['status'] = 'passed'
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        try:
            command(['docker', 'image', 'rm', image], root, 'image-remove', 60)
        except Exception as exc:
            report['cleanup_error'] = str(exc)
        report['experiment_wall_seconds'] = time.monotonic() - started
        (root / 'docker-summary.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
