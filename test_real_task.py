"""Offline verifier contract checks. No Docker engine, model, or credentials needed."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import real_task as runner


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / 'run'
        self.run.mkdir()
        self.repo = self.run / 'workspace'
        self.task = json.loads((runner.DATA / 'task.json').read_text())
        shutil.copytree(runner.DATA / 'fixtures' / self.task['fixture'], self.repo,
                        ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
        base = runner.repair.initialize_git(self.repo)
        runner.repair.write_json(self.run / 'manifest.json', {
            'task': self.task, 'baseline_commit': base, 'baseline_returncode': 1})
        runner.repair.command(['git', 'apply', '--whitespace=error'], self.repo,
            input_text=(runner.DATA / 'evaluator' / 'upstream-source.patch').read_text())

    def backend(self, result):
        case = self

        class FakeSandbox:
            def __init__(self, repo, image, allowed):
                self.repo = repo
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, operation):
                case.assertEqual(operation, 'run_tests')
                case.assertNotEqual(self.repo, case.repo)
                case.assertTrue((self.repo / 'tests/test_time_boundaries.py').is_file())
                case.assertFalse((self.repo / 'upstream-source.patch').exists())
                source = 'src/itsdangerous/timed.py'
                case.assertEqual((self.repo / source).read_bytes(), (case.repo / source).read_bytes())
                original = runner.DATA / 'fixtures' / case.task['fixture'] / 'tests/test_itsdangerous/test_timed.py'
                case.assertEqual((self.repo / 'tests/test_itsdangerous/test_timed.py').read_bytes(), original.read_bytes())
                return result

        return FakeSandbox

    def test_reference_patch_replayed_onto_clean_fixture_with_external_tests(self):
        result = {'exit_code': 0, 'stdout': '468 passed in 2.00s\n'}
        with patch.object(runner, 'Sandbox', self.backend(result)):
            verified = runner.verify(self.run, 'offline-image')
        self.assertEqual(verified['verification_status'], 'passed')
        self.assertEqual(verified['changed_files'], ['src/itsdangerous/timed.py'])

    def test_zero_exit_with_missing_tests_is_rejected(self):
        result = {'exit_code': 0, 'stdout': '415 passed in 1.00s\n'}
        with patch.object(runner, 'Sandbox', self.backend(result)):
            verified = runner.verify(self.run, 'offline-image')
        self.assertEqual(verified['verification_status'], 'failed')

    def test_modified_or_extra_test_file_rejected_before_execution(self):
        for name in ['tests/test_itsdangerous/test_timed.py', 'tests/extra_test.py']:
            with self.subTest(name=name):
                target = self.repo / name
                old = target.read_bytes() if target.exists() else None
                target.write_text('raise SystemExit(0)\n')
                with patch.object(runner, 'Sandbox') as backend:
                    verified = runner.verify(self.run, 'offline-image')
                    backend.assert_not_called()
                self.assertEqual(verified['reason'], 'patch_scope_violation')
                if old is None:
                    target.unlink()
                else:
                    target.write_bytes(old)


if __name__ == '__main__':
    unittest.main()
