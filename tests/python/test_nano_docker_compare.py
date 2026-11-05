"""Offline contract/failure tests; never start Docker or download packages."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
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


class PreparedSnapshotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.seed = self.root / 'seed'
        self.context = self.root / 'context'
        self.context.mkdir()
        self.prepared = self.seed / 'prepared/nano-browser-test-kit-document-mirrors-random'
        project = self.prepared / 'browser-test-kit'
        contents = {'package.json': b'{"private":true}', 'package-lock.json': b'{"lockfileVersion":3}'}
        for name, data in contents.items():
            project.mkdir(parents=True, exist_ok=True)
            (project / name).write_bytes(data)
        (self.prepared / 'receipt.json').write_text(json.dumps({
            'feature': adapter.FEATURE, 'source_commit': adapter.SOURCE_COMMIT}))
        for name, value in (('SOURCE_BLOBS', {}),
                            ('MANIFEST_SHA256', adapter.sha256(contents['package.json'])),
                            ('LOCK_SHA256', adapter.sha256(contents['package-lock.json']))):
            patcher = mock.patch.object(adapter, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.contents = contents

    def test_nested_helper_snapshot_is_flattened_and_unchanged(self):
        prepared = compare.copy_prepared_snapshot(self.seed, self.context)
        self.assertEqual(prepared, self.prepared)
        self.assertEqual(adapter.read_prepared(prepared), self.contents)
        self.assertEqual(adapter.read_prepared(self.context / 'prepared'), self.contents)
        self.assertTrue((self.context / 'prepared/receipt.json').is_file())
        self.assertFalse((self.context / 'prepared' / prepared.name).exists())

    def test_missing_or_duplicate_snapshot_is_rejected(self):
        for extra in (False, True):
            with self.subTest(extra=extra):
                if extra:
                    (self.seed / 'prepared/second-job').mkdir()
                else:
                    self.prepared.rename(self.seed / 'saved')
                with self.assertRaisesRegex(adapter.Error, 'exactly one'):
                    compare.copy_prepared_snapshot(self.seed, self.context)
                self.assertFalse((self.context / 'prepared').exists())
                if not extra:
                    (self.seed / 'saved').rename(self.prepared)

    def test_tampered_receipt_and_symlink_snapshot_are_rejected(self):
        (self.prepared / 'receipt.json').write_text('{}')
        with self.assertRaisesRegex(adapter.Error, 'wrong prepared'):
            compare.copy_prepared_snapshot(self.seed, self.context)
        saved = self.seed / 'saved'
        self.prepared.rename(saved)
        self.prepared.symlink_to(saved, target_is_directory=True)
        with self.assertRaisesRegex(adapter.Error, 'symlink'):
            compare.copy_prepared_snapshot(self.seed, self.context)
        self.assertFalse((self.context / 'prepared').exists())

    def test_nano_uses_same_validated_snapshot_not_parent(self):
        prepared = compare.copy_prepared_snapshot(self.seed, self.context)
        sample = self.root / 'sample'
        sample.mkdir()
        (self.seed / 'bun-cache').mkdir()
        (self.seed / 'cache-seal.json').write_text('{}')
        with mock.patch.object(experiment, 'verify_cache'), \
             mock.patch.object(compare, 'command', side_effect=adapter.Error('offline stop')) as command:
            with self.assertRaisesRegex(adapter.Error, 'offline stop'):
                compare.nano_sample(self.seed, sample, {}, Path('/node'), prepared)
        argv = command.call_args.args[0]
        self.assertEqual(argv[argv.index('--prepared') + 1], prepared)
        self.assertNotEqual(prepared, self.seed / 'prepared')


class ComparisonTests(unittest.TestCase):
    def test_workflow_preflight_leaves_comparison_output_new(self):
        workflow = (TOOLS.parent / '.github/workflows/nano-docker-compare.yml').read_text()
        preflight = textwrap.dedent(workflow.split('        run: |\n', 1)[1].split('      - uses:', 1)[0])
        output_template = re.search(r'--output "([^"]+)"', workflow).group(1)
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as temp:
                image = 'node:26.0.0-bookworm-slim@sha256:' + 'a' * 64 if valid else 'node:26'
                result = subprocess.run(['bash', '-c', preflight], cwd=TOOLS.parent,
                                        env=dict(os.environ, RUNNER_TEMP=temp, GITHUB_SHA='test-ref', BASE_IMAGE=image),
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0 if valid else 1)
                output = Path(output_template.replace('$RUNNER_TEMP', temp))
                self.assertFalse(output.exists(), 'preflight must not create comparison output')
                self.assertTrue((Path(temp) / 'nano-docker-preflight/preflight.stdout').is_file())
                self.assertIn('${{ runner.temp }}/nano-docker-preflight/*.stdout', workflow)
                self.assertIn('${{ runner.temp }}/nano-docker-preflight/*.stderr', workflow)
                if valid:
                    with mock.patch.object(sys, 'argv', ['compare', '--output', str(output), '--base-image', image]), \
                         mock.patch.object(compare, 'command', side_effect=adapter.Error('offline stop')) as command:
                        with self.assertRaisesRegex(adapter.Error, 'offline stop'):
                            compare.main()
                    self.assertEqual(command.call_args_list[0].args[0], ['docker', 'version'])
                    report = json.loads((output / 'docker-summary.json').read_text())
                    self.assertEqual(report['status'], 'failed')
                    self.assertNotIn('medians', report)

    def test_existing_comparison_output_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(sys, 'argv', ['compare', '--output', temp, '--base-image',
                                                'node:26.0.0-bookworm-slim@sha256:' + 'a' * 64]), \
                 mock.patch.object(compare, 'command') as command:
                with self.assertRaises(FileExistsError):
                    compare.main()
                command.assert_not_called()

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
