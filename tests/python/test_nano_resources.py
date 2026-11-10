import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import nano_resources as resources


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


    def test_remaining_adapter_help_through_meter(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'm.json'
            result = subprocess.run([sys.executable, '-S', resources.__file__,
                '--cgroup', tmp, str(output), '--', sys.executable, '-S',
                str(Path(resources.__file__).with_name('nano_bun.py')), '--help'],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--prepared', result.stdout)
            report = json.loads(output.read_text())
            self.assertEqual(report['cgroup']['scope'], tmp)
            self.assertEqual(report['cgroup']['cpu_usage_usec']['status'], 'unmeasured')
            self.assertFalse(report['cgroup']['budget_verified'])

    def test_missing_command_and_sampler_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'm.json'
            with patch.object(resources.subprocess, 'call', side_effect=OSError('no executable')):
                with self.assertRaises(OSError):
                    resources.measure(['unused'], output)
            self.assertFalse(output.exists())
            with self.assertRaises(SystemExit) as exc:
                resources.main([str(output), '--'])
            self.assertEqual(exc.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
