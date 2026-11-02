import pathlib
import sys
import unittest
from unittest.mock import patch

TOOLS = pathlib.Path(__file__).resolve().parents[2] / 'tools'
sys.path.insert(0, str(TOOLS))
import nano_bun_fanout as fanout


class FanoutTests(unittest.TestCase):
    def receipt(self):
        return dict(passed=2, unique_tests=2, skipped=0, network_allowed=False, artifact_sha256={'a': 'hash'})

    def test_complete_set(self):
        fanout.check_receipts([self.receipt() for _ in range(10)], {'a': 'hash'}, 10)

    def test_missing_worker(self):
        with self.assertRaises(fanout.adapter.Error):
            fanout.check_receipts([self.receipt()], {'a': 'hash'}, 10)

    def test_reject_wrong_output_skips_network_and_partial_tests(self):
        for changes in ({'artifact_sha256': {}}, {'skipped': 1}, {'network_allowed': True}, {'passed': 1}, {'unique_tests': 3}):
            with self.subTest(changes=changes), self.assertRaises(fanout.adapter.Error):
                fanout.check_receipts([dict(self.receipt(), **changes)], {'a': 'hash'}, 1)

    def test_batch_executes_every_worker_and_checks_failure(self):
        import tempfile
        import json
        import subprocess
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            seed = root / 'seed'
            seed.mkdir()
            (seed / 'bun-cache').mkdir()
            (seed / 'cache-seal.json').write_text(json.dumps(fanout.experiment.tree(seed / 'bun-cache')))
            (seed / 'summary.json').write_text(json.dumps({'status': 'passed', 'conditions': {'npm-cold': {'receipt': {'artifact_sha256': {'a': 'hash'}}}}}))
            calls = []
            def run(command, **kwargs):
                self.assertNotIn('--allow-network', command)
                work = pathlib.Path(command[command.index('--work-root') + 1])
                job = work / 'job'
                job.mkdir(parents=True)
                (job / 'receipt.json').write_text(json.dumps(self.receipt()))
                calls.append(str(work))
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            with patch.object(fanout.subprocess, 'run', side_effect=run):
                result = fanout.batch(seed, root / 'batch', workers=10, concurrency=4)
            self.assertEqual(len(set(calls)), 10)
            self.assertEqual(result['status'], 'passed')
            with patch.object(fanout.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='injected')):
                with self.assertRaises(fanout.adapter.Error):
                    fanout.batch(seed, root / 'failed', workers=2, concurrency=2)
            self.assertEqual(json.loads((root / 'failed/batch.json').read_text())['status'], 'failed')

    def test_copies_are_not_shared_inodes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / 'source'
            source.mkdir()
            (source / 'package').write_bytes(b'original')
            fanout.shutil.copytree(source, root / 'worker')
            (root / 'worker/package').write_bytes(b'changed')
            self.assertEqual((source / 'package').read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
