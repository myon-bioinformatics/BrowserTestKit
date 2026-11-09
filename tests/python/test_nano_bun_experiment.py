"""Offline failure gates for the manual experiment."""
import importlib.util, json, sys, tempfile, unittest
from pathlib import Path; from unittest import mock

TOOLS = Path(__file__).resolve().parents[2] / 'tools'
spec = importlib.util.spec_from_file_location('nano_bun', TOOLS / 'nano_bun.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
# Load without depending on pytest's working directory or global path changes.
with mock.patch.dict(sys.modules, nano_bun=adapter):
    spec = importlib.util.spec_from_file_location('experiment', TOOLS / 'nano_bun_experiment.py')
    experiment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experiment)


class ExperimentTests(unittest.TestCase):
    def test_cache_corruption_and_missing_entry_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = root / 'package'
            payload.write_bytes(b'good')
            seal = experiment.tree(root)
            experiment.verify_cache(root, seal)
            payload.write_bytes(b'evil')  # Same length: a size-only check is insufficient.
            with self.assertRaisesRegex(adapter.Error, 'corrupted'):
                experiment.verify_cache(root, seal)
            payload.unlink()
            with self.assertRaises(adapter.Error):
                experiment.verify_cache(root, seal)

    def test_extra_cache_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seal = experiment.tree(root)
            (root / 'unexpected').write_bytes(b'x')
            with self.assertRaises(adapter.Error):
                experiment.verify_cache(root, seal)

    def test_npm_metadata_restored_even_on_inventory_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            project = job / 'project'
            modules = project / 'node_modules'
            modules.mkdir(parents=True)
            hidden = modules / '.package-lock.json'
            hidden.write_text('{}')
            def reject(*args):
                self.assertFalse(hidden.exists())
                raise adapter.Error('bad package graph')
            with mock.patch.object(adapter, 'validate_inventory', side_effect=reject):
                with self.assertRaisesRegex(adapter.Error, 'bad package graph'):
                    experiment.npm_inventory(project, job, {'package-lock.json': b'{}'})
            self.assertEqual(hidden.read_bytes(), b'{}')

    def test_npm_baseline_receipt_includes_metadata_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = {'package.json': b'{}', 'package-lock.json': b'{"packages": {}}'}
            def execute(command, project, env, job, label):
                if label == 'install':
                    modules = project / 'node_modules'
                    modules.mkdir()
                    (modules / '.package-lock.json').write_bytes(b'{}')
                else:
                    (job / 'test.stdout').write_text('{}')
                    outputs = job / 'test-results'
                    outputs.mkdir()
                    for i in range(4):
                        (outputs / f'{i}.json').write_text('{}')
            with mock.patch.object(adapter, 'read_prepared', return_value=snapshot), \
                 mock.patch.object(adapter, 'execute', side_effect=execute), \
                 mock.patch.object(adapter, 'validate_inventory', return_value={'package_copies': 30}), \
                 mock.patch.object(adapter, 'validate_test_report', return_value={'passed': 2}):
                job = experiment.baseline(root, root, Path('/bin/true'), Path('/bin/true'))
            result = json.loads((job / 'receipt.json').read_text())
            self.assertEqual(result['npm_installed_lock_sha256'], adapter.sha256(b'{}'))
            self.assertEqual(result['package_copies'], 30)
            self.assertEqual(len(result['artifact_sha256']), 4)

    def test_wrong_lock_stops_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'receipt.json').write_text(json.dumps({'feature': adapter.FEATURE, 'source_commit': adapter.SOURCE_COMMIT}))
            project = root / 'browser-test-kit'
            project.mkdir()
            (project / 'package.json').write_bytes(b'{}')
            with self.assertRaisesRegex(adapter.Error, 'source/lock mismatch'):
                adapter.read_prepared(root)

    def test_runtime_failure_writes_failed_summary_and_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'evidence'
            with mock.patch.object(sys, 'argv', ['experiment', '--output', str(root)]), \
                 mock.patch.object(experiment.shutil, 'which', return_value='/bin/false'), \
                 mock.patch.object(experiment.subprocess, 'check_output', side_effect=RuntimeError('failed execution')):
                with self.assertRaisesRegex(RuntimeError, 'failed execution'):
                    experiment.main()
            result = json.loads((root / 'summary.json').read_text())
            self.assertEqual(result['status'], 'failed')
            self.assertIn('failed execution', result['error'])
            self.assertEqual(result['conditions'], {})

    def test_empty_or_partial_outputs_cannot_compare_equal(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(adapter.Error, 'four outputs'):
                experiment.artifacts(Path(temp))


if __name__ == '__main__':
    unittest.main()
