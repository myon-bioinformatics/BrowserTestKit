import pathlib, sys, unittest
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

    @patch.object(fanout.shutil, 'which', return_value='/dummy/node')
    def test_batch_executes_every_worker_and_checks_failure(self, which):
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            seed = root / 'seed'
            seed.mkdir()
            (seed / 'bun-cache').mkdir()
            (seed / 'cache-seal.json').write_text(json.dumps(fanout.experiment.tree(seed / 'bun-cache')))
            (seed / 'summary.json').write_text(json.dumps({'status': 'passed', 'conditions': {'npm-cold': {'receipt': {'artifact_sha256': {'a': 'hash'}}}}}))
            calls = []
            def run(command, private):
                self.assertNotIn('--allow-network', command)
                work = pathlib.Path(command[command.index('--work-root') + 1])
                job = work / 'job'
                job.mkdir(parents=True)
                (job / 'receipt.json').write_text(json.dumps(self.receipt()))
                calls.append(str(work))
                return 0
            with patch.object(fanout, 'run_worker', side_effect=run):
                result = fanout.batch(seed, root / 'batch', workers=10, concurrency=4)
            self.assertEqual(len(set(calls)), 10)
            self.assertEqual(result['status'], 'passed')
            which.assert_called_once_with('node')
            with patch.object(fanout, 'run_worker', return_value=1):
                with self.assertRaises(fanout.adapter.Error):
                    fanout.batch(seed, root / 'failed', workers=2, concurrency=2)
            self.assertEqual(json.loads((root / 'failed/batch.json').read_text())['status'], 'failed')
            import subprocess
            with patch.object(fanout, 'run_worker', side_effect=subprocess.TimeoutExpired('fake', 480)) as run:
                with self.assertRaises(subprocess.TimeoutExpired):
                    fanout.batch(seed, root / 'timeout', workers=10, concurrency=1)
            self.assertEqual(run.call_count, 1)
            report = json.loads((root / 'timeout/batch.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertIn('TimeoutExpired', report['error'])

    def test_missing_node_is_controlled_and_reported_before_copy(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with patch.object(fanout.shutil, 'which', return_value=None), \
                    patch.object(fanout.shutil, 'copytree') as copy:
                with self.assertRaisesRegex(fanout.adapter.Error, 'Node executable not found'):
                    fanout.batch(root / 'unused-seed', root / 'batch')
            copy.assert_not_called()
            report = json.loads((root / 'batch/batch.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertIn('Node executable not found', report['error'])

    @unittest.skipUnless(sys.platform == 'linux', 'Linux-only fanout process groups')
    def test_timeout_retains_partial_logs_and_reaps_worker(self):
        import tempfile
        import subprocess
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            command = [sys.executable, '-u', '-c',
                       'import os,time; print(os.getpid(), flush=True); '
                       'print("partial error", file=__import__("sys").stderr, flush=True); time.sleep(60)']
            with self.assertRaises(subprocess.TimeoutExpired):
                fanout.run_worker(command, root, timeout=0.5)
            pid = int((root / 'worker.stdout').read_text().strip())
            self.assertIn('partial error', (root / 'worker.stderr').read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

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
