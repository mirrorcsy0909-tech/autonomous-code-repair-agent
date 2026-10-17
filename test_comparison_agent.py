"""Offline checks. Fake responses/reference source only; no model or Docker calls."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
import zipfile
from unittest.mock import patch, Mock

import comparison_agent as app


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_submission_validates_entire_change_set_before_writing(self):
        allowed = ['src/dotenv/main.py']
        valid = '{"files":{"src/dotenv/main.py":"value = 1\\n"}}'
        self.assertEqual(app.parse_submission('```json\n' + valid + '\n```', allowed),
                         {'src/dotenv/main.py': 'value = 1\n'})
        invalid = [
            '{"files":{"tests/test_main.py":"pass"}}',
            '{"files":{"src/dotenv/main.py":"a","src/dotenv/main.py":"b"}}',
            '{"files":{}}',
            '{"files":{"src/dotenv/main.py":null}}',
            '{"files":{"src/dotenv/main.py":"ok"},"command":"anything"}',
        ]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                app.parse_submission(text, allowed)

    def test_prompt_contains_public_context_but_no_reference_or_supplement(self):
        run = self.root / 'prompt-run'
        run.mkdir()
        (run / 'baseline-tests.txt').write_text('1 failed, 56 passed in 0.10s\n')
        with patch.object(app.core, 'DATA', app.DATA):
            task = app.core.load_task('dotenv-256')
            prompt = app.build_prompt(run, task)
        self.assertIn('src/dotenv/main.py', prompt)
        self.assertIn('test_public_regression.py', prompt)
        self.assertIn('1 failed, 56 passed', prompt)
        # pytest.ini mentions the collection filename, but no supplementary content is supplied.
        self.assertNotIn('"tests/test_supplemental.py":', prompt)
        self.assertNotIn('test_file_binding_wins_for_expansion', prompt)
        self.assertNotIn('reference.patch', prompt)
        self.assertNotIn(task['reference_commit'], prompt)
        self.assertNotIn(task['issue_url'], prompt)

    def test_plan_uses_unique_fresh_pairs_and_alternates_order(self):
        jobs = app.make_jobs('offline', ['dotenv-256', 'dotenv-361'], 2)
        self.assertEqual(len(jobs), 8)
        self.assertEqual(len({x['run_id'] for x in jobs}), 8)
        self.assertEqual([x['workflow'] for x in jobs[:4]], ['oneshot', 'iterative', 'iterative', 'oneshot'])
        self.assertEqual([x['workflow'] for x in jobs[4:]], ['iterative', 'oneshot', 'oneshot', 'iterative'])

    def test_sdk_route_uses_responses_for_subscription_models(self):
        fake = Mock()
        fake.uses_responses_api.return_value = True
        app.invoke_once(fake, ['offline-message'])
        fake.responses.assert_called_once()
        fake.completion.assert_not_called()
        self.assertFalse(fake.responses.call_args.kwargs['store'])
        self.assertIsNone(fake.responses.call_args.kwargs['tools'])
        fake.uses_responses_api.return_value = False
        app.invoke_once(fake, ['offline-message'])
        fake.completion.assert_called_once()

    def make_row(self, job, state, image='offline-image'):
        run = self.root / job['run_id']
        run.mkdir()
        app.repair.write_json(run / 'manifest.json', {'task': {'base_commit': 'offline-base'}})
        app.repair.write_json(run / 'agent-run.json', {
            'mode': 'live_baseline' if job['workflow'] == 'oneshot' else 'live_agent',
            'workflow': job['workflow'], 'execution_status': 'error' if state == 'failed' else 'finished',
            'model': 'offline-model', 'image_id': image, 'max_output_tokens_per_call': 8192,
            'latency_seconds': 10, 'sdk_token_usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
        if state == 'passed':
            app.repair.write_json(run / 'verification.json', {
                'verification_status': 'passed', 'test_returncode': 0,
                'expected_test_count': 77, 'test_counts': {'passed': 77}})
            (run / 'verification-tests.txt').write_text('77 passed in 0.10s\n')

    def test_failed_and_incomplete_attempts_remain_in_each_denominator(self):
        jobs = app.make_jobs('metrics', ['dotenv-256', 'dotenv-361'], 1)
        states = {'dotenv-256': {'oneshot': 'failed', 'iterative': 'passed'},
                  'dotenv-361': {'oneshot': 'passed', 'iterative': 'incomplete'}}
        for job in jobs:
            self.make_row(job, states[job['task_id']][job['workflow']])
        with patch.object(app.core, 'RUNS', self.root):
            result = app.report('metrics', {'jobs': jobs})
        for workflow in app.WORKFLOWS:
            self.assertEqual(result['by_workflow'][workflow]['attempts_started'], 2)
            self.assertEqual(result['by_workflow'][workflow]['attempts_passed'], 1)
        self.assertFalse(result['all_rows_complete'])
        self.assertTrue(all(x['billed_cost'] is None for x in result['rows']))

    def test_changed_image_marks_pair_as_unmatched(self):
        jobs = app.make_jobs('config', ['dotenv-256'], 1)
        for index, job in enumerate(jobs):
            self.make_row(job, 'passed', image=f'offline-image-{index}')
        with patch.object(app.core, 'RUNS', self.root):
            result = app.report('config', {'jobs': jobs})
        self.assertFalse(result['pairs'][0]['matched_task_model_image_output_cap'])

    def test_export_includes_partial_results_and_excludes_environment_files(self):
        jobs = app.make_jobs('export-check', ['dotenv-256'], 1)
        self.make_row(jobs[0], 'failed')
        self.make_row(jobs[1], 'passed')
        app.repair.write_json(self.root / 'export-check-comparison-plan.json', {'jobs': jobs})
        (self.root / '.env').write_text('NOT_FOR_EXPORT=private\n')
        (self.root / jobs[0]['run_id'] / '.env').write_text('NOT_FOR_EXPORT=private\n')
        with patch.object(app.core, 'RUNS', self.root), patch.object(app, 'ROOT', self.root):
            output = app.export_results('export-check')
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            self.assertTrue(any(name.endswith('comparison-summary.json') for name in names))
            self.assertEqual(sum(name.endswith('agent-run.json') for name in names), 2)
            self.assertFalse(any(name.endswith('.env') for name in names))

    def test_oneshot_calls_model_once_applies_only_source_and_invokes_verifier(self):
        with patch.object(app.core, 'DATA', app.DATA):
            task = app.core.load_task('dotenv-256')
            run = self.root / 'one'
            run.mkdir()
            repo = run / 'workspace'
            shutil.copytree(app.core.check_fixture(task), repo)
            base = app.repair.initialize_git(repo)
            app.repair.write_json(run / 'manifest.json', {'task': task, 'baseline_commit': base,
                'baseline_returncode': 1, 'image_id': 'offline-image', 'workflow': 'oneshot'})
            (run / 'baseline-tests.txt').write_text('1 failed, 56 passed in 0.10s\n')
            # Use only known upstream reference text for the fake response.
            with tempfile.TemporaryDirectory() as tmp:
                fixed = Path(tmp) / 'fixed'
                shutil.copytree(repo, fixed)
                app.repair.command(['git', 'apply', '--whitespace=error'], fixed,
                    input_text=(app.DATA / task['id'] / 'reference.patch').read_text())
                source = (fixed / 'src/dotenv/main.py').read_text()
            operations = []
            class FakeSandbox:
                def __init__(self, *args):
                    pass
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def request(self, operation, path, content):
                    operations.append(operation)
                    (repo / path).write_text(content)
                    return {'exit_code': 0}
                def export_sources(self):
                    pass
            fake = Mock(model='offline-model')
            fake.model = 'offline-model'
            fake.model_copy.return_value = fake
            fake.uses_responses_api.return_value = True
            fake.responses.return_value = SimpleNamespace(message=SimpleNamespace(tool_calls=[],
                content=[SimpleNamespace(text=json.dumps({'files': {'src/dotenv/main.py': source}}))]))
            fake.metrics.get_snapshot.return_value.model_dump.return_value = {
                'accumulated_token_usage': {'prompt_tokens': 100, 'completion_tokens': 20}, 'accumulated_cost': 0.1}
            with patch.object(app.core, 'get_image_for_task', return_value='offline-image'), \
                 patch.object(app.repair, 'configured_llm', return_value=(fake, 'offline')), \
                 patch.object(app.core, 'Sandbox', FakeSandbox), \
                 patch.object(app.core, 'verify', return_value={'verification_status': 'passed'}) as verifier, \
                 patch('dotenv.load_dotenv'):
                result = app.oneshot(run)
            fake.responses.assert_called_once()
            self.assertEqual(operations, ['write_file'])
            verifier.assert_called_once_with(run, 'offline-image')
            self.assertEqual(result['verification_status'], 'passed')
            report = json.loads((run / 'agent-run.json').read_text())
            self.assertEqual(report['logical_llm_requests'], 1)
            self.assertEqual(report['sdk_token_usage']['completion_tokens'], 20)
            self.assertIsNone(report['billed_cost'])


if __name__ == '__main__':
    unittest.main()
