import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import nano_resources as resources
import nano_bun_fanout as fanout


class ResourceTests(unittest.TestCase):
    def fixture(self, root):
        for name, value in {'cpu.stat': 'usage_usec 123\nnr_throttled 2\nthrottled_usec 30',
                            'cpu.max': '50000 100000', 'memory.current': '1024',
                            'memory.peak': '9999', 'memory.max': '4096',
                            'memory.events': 'oom 0\noom_kill 0'}.items():
            (root / name).write_text(value)

    def test_read_missing_malformed_and_unlimited(self):
        self.assertTrue(all(x['status'] == 'unmeasured' for x in resources.read_cgroup(None).values()))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            result = resources.read_cgroup(root)
            self.assertEqual(result['cpu.max']['value']['quota_usec'], 50000)
            self.assertEqual(result['memory.peak']['value'], 9999)
            for name, malformed in [('cpu.stat', 'bad'), ('cpu.max', '0 0'),
                                    ('memory.current', '-1'), ('memory.events', 'oom -1')]:
                (root / name).write_text(malformed)
                self.assertEqual(resources.read_cgroup(root)[name]['status'], 'unmeasured')
            (root / 'cpu.max').write_text('max 100000')
            (root / 'memory.max').write_text('max')
            self.assertIsNone(resources.read_cgroup(root)['cpu.max']['value']['quota_usec'])
            self.assertEqual(resources.read_cgroup(root)['memory.max']['value'], 'max')
            with patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
                self.assertEqual(resources.read_cgroup(root)['cpu.max']['status'], 'unmeasured')

    def test_scope_peak_is_sampled_not_historical_and_deltas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            meter = resources.ScopeMeter(root, interval=10).start()
            (root / 'memory.current').write_text('2048')
            (root / 'cpu.stat').write_text('usage_usec 200\nnr_throttled 3\nthrottled_usec 50')
            report = meter.finish()
            self.assertEqual(report['sampled_peak_memory_bytes']['value'], 2048)
            self.assertEqual(report['cpu_usage_usec']['value'], 77)
            self.assertEqual(report['throttled_usec']['value'], 20)
            self.assertEqual(report['nr_throttled']['value'], 1)
            self.assertFalse(report['budget_verified'])
            before = resources.read_cgroup(root)
            (root / 'cpu.stat').write_text('usage_usec 1')
            self.assertEqual(resources.counter_delta(before, resources.read_cgroup(root),
                                                     'cpu.stat', 'usage_usec')['status'], 'unmeasured')
        report = resources.ScopeMeter().start().finish()
        self.assertEqual(report['sampled_peak_memory_bytes']['status'], 'unmeasured')
        with self.assertRaises(ValueError):
            resources.ScopeMeter(interval=0)

    def test_throughput_same_corpus_for_1_2_4_and_invalid_duration(self):
        rows = [{'receipt': {'passed': 2}} for _ in range(10)]
        for concurrency, elapsed in [(1, 10), (2, 5), (4, 3)]:
            result = resources.aggregate(rows, elapsed, elapsed + 2)
            self.assertEqual(result['successful_tests'], 20)
            self.assertEqual(result['workers_per_execution_second'], 10 / elapsed)
            self.assertFalse(result['comparison_eligible'])
        with self.assertRaises(ValueError):
            resources.aggregate(rows, 0, 2)

    def test_real_child_failure_and_success_with_python_S(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'measurement.json'
            for code in (0, 7):
                result = subprocess.run([sys.executable, '-S', resources.__file__, str(output), '--',
                                         sys.executable, '-S', '-c', f'raise SystemExit({code})'])
                self.assertEqual(result.returncode, code)
                data = json.loads(output.read_text())
                self.assertEqual(data['returncode'], code)
                self.assertGreater(data['wall_seconds'], 0)
                self.assertEqual(data['worker_throttled_usec']['status'], 'unmeasured')
                if sys.platform == 'linux':
                    self.assertGreater(data['peak_process_rss_bytes']['value'], 0)

    def test_unknown_rss_units_and_unavailable_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'm.json'
            with patch.object(resources.subprocess, 'call', return_value=0), \
                    patch.object(sys, 'platform', 'unknown'):
                resources.measure(['unused'], output)
            self.assertEqual(json.loads(output.read_text())['peak_process_rss_bytes']['status'], 'unmeasured')
            with patch.dict(sys.modules, {'resource': None}), \
                    patch.object(resources.subprocess, 'call', return_value=0):
                resources.measure(['unused'], output)
            self.assertEqual(json.loads(output.read_text())['cpu_seconds']['status'], 'unmeasured')

    def test_metered_fanout_1_2_4_with_offline_stub_workers(self):
        # Exercises orchestration, receipt validation and batch aggregation;
        # this is a fixture workload, not a Bun benchmark.
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / 'seed'
            seed.mkdir()
            (seed / 'bun-cache').mkdir()
            (seed / 'cache-seal.json').write_text(json.dumps(fanout.experiment.tree(seed / 'bun-cache')))
            expected = {'a': 'hash'}
            (seed / 'summary.json').write_text(json.dumps({'status': 'passed', 'conditions': {
                'npm-cold': {'receipt': {'artifact_sha256': expected}}}}))
            receipt = dict(passed=2, unique_tests=2, skipped=0, network_allowed=False,
                           artifact_sha256=expected)
            def stub(command, private):
                job = private / 'jobs' / 'job'
                job.mkdir(parents=True)
                (job / 'receipt.json').write_text(json.dumps(receipt))
                output = Path(command[command.index('--') - 1])
                output.write_text(json.dumps({'returncode': 0, 'wall_seconds': 1.0}))
                return 0
            for concurrency in (1, 2, 4):
                with patch.object(fanout.shutil, 'which', return_value=sys.executable), \
                        patch.object(fanout, 'prepared_snapshot', return_value=seed), \
                        patch.object(fanout, 'run_worker', side_effect=stub):
                    result = fanout.batch(seed, Path(tmp) / f'batch-{concurrency}',
                                          workers=4, concurrency=concurrency, measure_resources=True)
                self.assertEqual(result['throughput']['successful_tests'], 8)
                self.assertEqual(len(result['results']), 4)
                self.assertTrue(all(row['resources']['returncode'] == 0 for row in result['results']))
                self.assertFalse(result['resources']['budget_verified'])

    def test_failed_batch_still_saves_scope_and_no_throughput(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'failed'
            with patch.object(fanout.shutil, 'which', return_value=None):
                with self.assertRaises(fanout.adapter.Error):
                    fanout.batch(Path(tmp), root, measure_resources=True)
            report = json.loads((root / 'batch.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertNotIn('throughput', report)
            self.assertEqual(report['resources']['throttled_usec']['status'], 'unmeasured')


if __name__ == '__main__':
    unittest.main()
