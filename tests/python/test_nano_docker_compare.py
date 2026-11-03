"""Offline contract/failure tests; never start Docker or download packages."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[2] / 'tools'


def module(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


adapter = module('nano_bun')
with mock.patch.dict(sys.modules, nano_bun=adapter):
    experiment = module('nano_bun_experiment')
with mock.patch.dict(sys.modules, nano_bun=adapter, nano_bun_experiment=experiment):
    compare = module('nano_docker_compare')
    worker = module('nano_docker_worker')


def receipt():
    return {'passed': 2, 'unique_tests': 2, 'skipped': 0, 'source_commit': adapter.SOURCE_COMMIT,
            'artifact_sha256': {str(i): str(i) * 64 for i in range(4)}}


class ComparisonTests(unittest.TestCase):
    def test_digest_and_exact_node_version_required(self):
        ref = 'node:26.0.0-bookworm-slim@sha256:' + 'a' * 64
        self.assertEqual(compare.image_ref(ref), ref)
        for bad in ('node:26', ref + '\n', ref.replace('26.0.0', '24.0.0'), ref.replace('a', 'A')):
            with self.assertRaises(adapter.Error):
                compare.image_ref(bad)

    def test_only_complete_fixed_plan_can_emit_medians(self):
        samples = [{'kind': kind, 'receipt': receipt(), 'test_seconds': i + 1,
                    'execution_path_wall_seconds': i + 2, 'total_wall_seconds': i + 3}
                   for i, order in enumerate(compare.ORDERS) for kind in order]
        expected = receipt()['artifact_sha256']
        self.assertEqual(compare.comparison(samples, expected)['nano']['test_seconds'], 2)
        for invalid in (samples[:-1], list(reversed(samples))):
            with self.assertRaises(adapter.Error):
                compare.comparison(invalid, expected)

    def test_changed_outputs_skip_missing_test_wrong_source_rejected(self):
        expected = receipt()['artifact_sha256']
        for change in ({'skipped': 1}, {'unique_tests': 1}, {'passed': 1},
                       {'source_commit': 'wrong'}, {'artifact_sha256': {}},
                       {'artifact_sha256': dict(expected, extra='bad')}):
            with self.assertRaises(adapter.Error):
                compare.validate(dict(receipt(), **change), expected)

    def test_timeout_and_nonzero_have_failed_logs_and_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for outcome in (subprocess.TimeoutExpired('docker', 1), mock.Mock(returncode=3)):
                with mock.patch.object(compare.subprocess, 'run', side_effect=outcome if isinstance(outcome, Exception) else None,
                                       return_value=outcome):
                    with self.assertRaises((subprocess.TimeoutExpired, adapter.Error)):
                        compare.command(['docker', 'fake'], root, 'phase', 1)
                status = json.loads((root / 'phase.json').read_text())
                self.assertIn('error', status)
                self.assertGreaterEqual(status['seconds'], 0)
                self.assertTrue((root / 'phase.stderr').is_file())

    def test_container_failure_collects_evidence_and_removes_container(self):
        calls = []
        def execute(argv, folder, label, timeout=600):
            calls.append((argv, label))
            if label == 'exec':
                raise adapter.Error('injected test failure')
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(compare, 'command', side_effect=execute):
            with self.assertRaisesRegex(adapter.Error, 'injected'):
                compare.docker_sample('image', Path(temp), {}, 'sha')
        self.assertEqual([label for _, label in calls], ['create', 'start', 'exec', 'inspect', 'collect', 'remove'])
        self.assertIn('--network=none', calls[0][0])

    def test_collection_failure_still_removes_container(self):
        calls = []
        def execute(argv, folder, label, timeout=600):
            calls.append(label)
            if label == 'collect':
                raise adapter.Error('copy failed')
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(compare, 'command', side_effect=execute):
            with self.assertRaises(adapter.Error):
                compare.docker_sample('image', Path(temp), {}, 'sha')
        self.assertEqual(calls[-1], 'remove')

    def test_docker_success_and_resource_mismatch_gate(self):
        for quota in (0, 10000):
            with self.subTest(quota=quota), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                def execute(argv, folder, label, timeout=600):
                    (root / (label + '.json')).write_text(json.dumps({'seconds': 0.1}))
                    if label == 'inspect':
                        (root / 'inspect.stdout').write_text(json.dumps([{
                            'HostConfig': {'NetworkMode': 'none', 'Memory': 0, 'NanoCpus': 0, 'CpuQuota': quota},
                            'SizeRw': 42}]))
                    if label == 'collect':
                        evidence = root / 'evidence'
                        evidence.mkdir()
                        (evidence / 'receipt.json').write_text(json.dumps(dict(receipt(), status='passed',
                                                                            runtime={'node_sha256': 'node'})))
                        (evidence / 'test.json').write_text(json.dumps({'seconds': 0.05}))
                with mock.patch.object(compare, 'command', side_effect=execute):
                    if quota:
                        with self.assertRaisesRegex(adapter.Error, 'resource quota'):
                            compare.docker_sample('image', root, receipt()['artifact_sha256'], 'node')
                    else:
                        result = compare.docker_sample('image', root, receipt()['artifact_sha256'], 'node')
                        self.assertEqual(result['writable_layer_bytes'], 42)
                        self.assertEqual(result['test_seconds'], 0.05)

    def test_worker_wrong_node_writes_failed_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def path(value):
                return root / value.lstrip('/')
            with mock.patch.object(worker, 'Path', side_effect=path), \
                 mock.patch.object(adapter, 'read_prepared', return_value={}), \
                 mock.patch.object(worker, 'runtime', return_value=(None, None, {'node_sha256': 'bad'})), \
                 mock.patch.dict(worker.os.environ, EXPECTED_NODE_SHA256='good'):
                with self.assertRaisesRegex(adapter.Error, 'binary mismatch'):
                    worker.test()
            report = json.loads((root / 'evidence/receipt.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertNotIn('artifact_sha256', report)

    def test_summary_failure_never_has_medians(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'result'
            with mock.patch.object(sys, 'argv', ['compare', '--output', str(root), '--base-image',
                                                'node:26.0.0-bookworm-slim@sha256:' + 'a' * 64]), \
                 mock.patch.object(compare, 'command', side_effect=adapter.Error('no Docker daemon')):
                with self.assertRaisesRegex(adapter.Error, 'no Docker'):
                    compare.main()
            report = json.loads((root / 'docker-summary.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertNotIn('medians', report)


if __name__ == '__main__':
    unittest.main()
