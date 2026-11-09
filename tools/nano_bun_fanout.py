#!/usr/bin/env python3
"""Ten offline independent workers, using private copies of one warmed cache."""
from __future__ import annotations
import argparse, json, os, shutil, signal, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor; from pathlib import Path; from threading import Event
import nano_bun_experiment as experiment
import nano_bun as adapter


def check_receipts(receipts, expected, count):
    adapter.require(len(receipts) == count, 'missing worker receipts')
    for receipt in receipts:
        adapter.require(receipt.get('passed') == 2 and receipt.get('unique_tests') == 2
                        and receipt.get('skipped') == 0 and receipt.get('network_allowed') is False
                        and receipt.get('artifact_sha256') == expected, 'worker output/test mismatch')


def run_worker(command, private, timeout=480):
    """Bound each adapter and retain logs, including partial timeout output."""
    with (private / 'worker.stdout').open('w') as out, (private / 'worker.stderr').open('w') as err:
        process = subprocess.Popen(command, stdout=out, stderr=err, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            # SIGTERM lets the adapter clean up its separately sessioned test
            # children while holding its cache lock. Bound that cleanup too.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
            raise


def batch(seed, root, workers=10, concurrency=1):
    root.mkdir(mode=0o700)
    report = {'status': 'failed', 'workers': workers, 'concurrency': concurrency}
    started = time.monotonic()
    try:
        node = shutil.which('node')
        adapter.require(node is not None, 'Node executable not found on PATH; install the fixed Node runtime before fanout')
        node = str(Path(node).resolve())
        summary = json.loads((seed / 'summary.json').read_text())
        adapter.require(summary['status'] == 'passed', 'seed experiment failed')
        expected = summary['conditions']['npm-cold']['receipt']['artifact_sha256']
        seal = json.loads((seed / 'cache-seal.json').read_text())
        experiment.verify_cache(seed / 'bun-cache', seal)
        jobs = []
        copy_started = time.monotonic()
        for index in range(workers):
            private = root / f'worker-{index:02d}'
            private.mkdir(mode=0o700)
            cache = private / 'cache'
            # Real copies: no symlinks/hardlinks to another worker's writable data.
            shutil.copytree(seed / 'bun-cache', cache)
            experiment.verify_cache(cache, seal)
            jobs.append(private)
        report['copy_seconds'] = time.monotonic() - copy_started
        report['cache_logical_bytes'] = seal['logical_bytes'] * workers
        failed = Event()
        def execute(private):
            adapter.require(not failed.is_set(), 'batch already failed; worker not started')
            try:
                return execute_one(private)
            except BaseException:
                failed.set()
                raise
        def execute_one(private):
            command = [sys.executable, '-I', '-B', str(Path(adapter.__file__).resolve()),
                       '--prepared', str(seed / 'prepared'), '--work-root', str(private / 'jobs'),
                       '--cache-root', str(private / 'cache'), '--node', node,
                       '--bun', str(seed / 'bun'), '--python', str(Path(sys.executable).resolve())]
            begin = time.monotonic()
            code = run_worker(command, private)
            adapter.require(code == 0, f'worker failed: {private.name}')
            paths = list((private / 'jobs').glob('*/receipt.json'))
            adapter.require(len(paths) == 1, 'expected one worker receipt')
            receipt = json.loads(paths[0].read_text())
            return {'worker': private.name, 'seconds': time.monotonic() - begin, 'receipt': receipt}
        run_started = time.monotonic()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(execute, jobs))
        report['execution_seconds'] = time.monotonic() - run_started
        report['results'] = results
        check_receipts([x['receipt'] for x in results], expected, workers)
        experiment.verify_cache(seed / 'bun-cache', seal)
        report['status'] = 'passed'
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['total_seconds'] = time.monotonic() - started
        (root / 'batch.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {'status': 'failed', 'batches': [], 'scope': 'private cache copies; no shared writable cache; no CPU/RAM quotas',
              'checkout': os.environ.get('GITHUB_SHA'), 'runner_image': os.environ.get('ImageVersion')}
    try:
        seed = root / 'seed'
        command = [sys.executable, '-I', '-B', '-c',
                   'import runpy,sys; sys.path.insert(0,"tools"); runpy.run_path("tools/nano_bun_experiment.py",run_name="__main__")',
                   '--output', str(seed)]
        begin = time.monotonic()
        subprocess.run(command, check=True, timeout=600)
        report['seed_seconds'] = time.monotonic() - begin
        # Rotate order to reduce systematic order bias. Every cell gets fresh copies.
        for trial, order in enumerate(((1, 2, 4), (2, 4, 1), (4, 1, 2)), 1):
            for concurrency in order:
                result = batch(seed, root / f'trial-{trial}-parallel-{concurrency}', concurrency=concurrency)
                report['batches'].append(result)
        report['status'] = 'passed'
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (root / 'fanout-summary.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
