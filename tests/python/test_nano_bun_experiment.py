"""Offline failure gates for the manual experiment."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

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
