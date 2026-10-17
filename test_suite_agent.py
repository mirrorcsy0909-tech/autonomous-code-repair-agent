"""Offline harness contract tests: no Docker, model, credentials or freezegun needed."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import suite_agent as suite


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def prepared_reference(self, task_id):
        task = suite.load_task(task_id)
        run = self.root / task_id
        run.mkdir()
        repo = run / 'workspace'
        shutil.copytree(suite.check_fixture(task), repo)
        base = suite.repair.initialize_git(repo)
        suite.repair.write_json(run / 'manifest.json', {
            'task': task, 'baseline_commit': base, 'baseline_returncode': 1, 'image_id': 'offline-image'})
        suite.repair.command(['git', 'apply', '--whitespace=error'], repo,
            input_text=(suite.DATA / task_id / 'reference.patch').read_text())
        return run, task

    def test_all_reference_patches_replay_on_clean_fixtures(self):
        case = self
        for task_id in suite.catalog():
            with self.subTest(task_id=task_id):
                run, task = self.prepared_reference(task_id)
                class FakeSandbox:
                    def __init__(self, repo, image, allowed):
                        self.repo = repo
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        pass
                    def request(self, operation):
                        case.assertEqual(operation, 'run_tests')
                        case.assertNotEqual(self.repo, run / 'workspace')
                        case.assertTrue((self.repo / 'tests/test_supplemental.py').is_file())
                        case.assertFalse((self.repo / 'reference.patch').exists())
                        for rel in task['allowed_files']:
                            case.assertEqual((self.repo / rel).read_bytes(), (run / 'workspace' / rel).read_bytes())
                        for original in (suite.DATA / task_id / 'fixture/tests').rglob('*.py'):
                            rel = original.relative_to(suite.DATA / task_id / 'fixture')
                            case.assertEqual((self.repo / rel).read_bytes(), original.read_bytes())
                        return {'exit_code': 0, 'stdout': f"{task['verification_test_count']} passed in 1.00s\n"}
                with patch.object(suite, 'Sandbox', FakeSandbox):
                    result = suite.verify(run, 'offline-image')
                self.assertEqual(result['verification_status'], 'passed')

    def test_modified_test_is_rejected_before_container_execution(self):
        run, task = self.prepared_reference('itsdangerous-237')
        (run / 'workspace/tests/test_issue_237.py').write_text('raise SystemExit(0)\n')
        with patch.object(suite, 'Sandbox') as backend:
            result = suite.verify(run, 'offline-image')
            backend.assert_not_called()
        self.assertEqual(result['reason'], 'patch_scope_violation')

    def test_image_change_is_rejected(self):
        run, task = self.prepared_reference('itsdangerous-124')
        with self.assertRaisesRegex(RuntimeError, 'Image differs'):
            suite.verify(run, 'other-image')

    def make_result(self, run_id, status, mode='live_agent'):
        run = self.root / run_id
        run.mkdir()
        suite.repair.write_json(run / 'agent-run.json', {
            'task_id': run_id, 'mode': mode, 'model': 'offline-model', 'image_id': 'offline-image',
            'max_iterations': 20, 'execution_status': 'error' if status == 'failed' else 'started',
            'latency_seconds': 10})
        if status == 'passed':
            suite.repair.write_json(run / 'verification.json', {
                'verification_status': 'passed', 'test_returncode': 0,
                'expected_test_count': 468, 'test_counts': {'passed': 468}})
            (run / 'verification-tests.txt').write_text('468 passed in 1.00s\n')
        return {'run_id': run_id, 'task_id': run_id}

    def test_report_keeps_failed_and_incomplete_live_attempts_in_denominator(self):
        jobs = [self.make_result('ok', 'passed'), self.make_result('bad', 'failed'),
                self.make_result('unfinished', 'incomplete'), self.make_result('smoke', 'passed', 'smoke')]
        with patch.object(suite, 'RUNS', self.root):
            report = suite.write_report('offline-report', {'jobs': jobs})
        self.assertEqual(report['live_attempts_started'], 3)
        self.assertEqual(report['successful_live_attempts'], 1)
        self.assertAlmostEqual(report['observed_success_fraction'], 1/3)
        self.assertFalse(report['all_rows_complete'])
        self.assertTrue(all(x['billed_cost'] is None for x in report['rows']))
        self.assertTrue(all(x['token_count'] is None for x in report['rows']))

    def test_prepare_failure_not_counted_as_model_attempt(self):
        job = {'run_id': 'baseline-failed', 'task_id': 'itsdangerous-237', 'prepare_returncode': 2}
        with patch.object(suite, 'RUNS', self.root):
            row = suite.summarize_run(job['run_id'], job)
        self.assertEqual(row['status'], 'prepare_failed')
        self.assertFalse(row['live_agent_started'])

    def test_batch_collision_is_detected_before_docker_or_model_calls(self):
        (self.root / 'collision-itsdangerous-124').mkdir()
        with patch.object(suite, 'RUNS', self.root), patch.object(suite.single, 'get_image') as get_image:
            with self.assertRaisesRegex(ValueError, 'already exist'):
                suite.batch('collision', ['itsdangerous-124'], [])
            get_image.assert_not_called()

    def test_log_distinguishes_nonzero_test_result_and_operation_error(self):
        log = self.root / 'calls.jsonl'
        backend = suite.Sandbox(self.root, 'offline-image', [], log)
        with patch.object(suite.BaseSandbox, 'request', side_effect=[
            {'exit_code': 1, 'stdout': '1 failed'}, {'exit_code': 2, 'stderr': 'missing file'}]):
            backend.request('run_tests')
            backend.request('read_file', 'AGENTS.md')
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        self.assertEqual(rows[0]['status'], 'nonzero')
        self.assertEqual(rows[1]['exit_code'], 2)
        run = self.root / 'logged'
        run.mkdir()
        shutil.copy2(log, run / 'tool-calls.jsonl')
        with patch.object(suite, 'RUNS', self.root):
            row = suite.summarize_run('logged')
        self.assertEqual(row['model_tool_calls'], 2)
        self.assertEqual(row['tool_errors'], 1)


if __name__ == '__main__':
    unittest.main()
