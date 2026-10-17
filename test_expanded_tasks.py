"""Offline integration contracts for the third repository; no Docker/model calls."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import comparison_agent as app
import execution_control as control

DATA = Path(__file__).resolve().parent / 'expanded_tasks'


class ExpandedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def prepare_reference(self, task_id):
        task = app.core.load_task(task_id)
        run = self.root / task_id
        run.mkdir()
        shutil.copytree(app.core.check_fixture(task), run / 'workspace')
        base = app.repair.initialize_git(run / 'workspace')
        control.write_json(run / 'manifest.json', {'task': task, 'baseline_commit': base,
            'baseline_returncode': 1, 'image_id': 'offline-image'})
        app.repair.command(['git', 'apply', '--whitespace=error'], run / 'workspace',
                           input_text=(DATA / task_id / 'reference.patch').read_text())
        return run, task

    def test_all_five_prompts_fit_context_and_keep_private_checks_out(self):
        with patch.object(app.core, 'DATA', DATA):
            for task_id in app.core.catalog():
                task = app.core.load_task(task_id)
                run = self.root / task_id
                run.mkdir()
                (run / 'baseline-tests.txt').write_text((DATA / task_id / 'source-baseline.txt').read_text())
                prompt = app.build_prompt(run, task)
                self.assertLessEqual(len(prompt), 160000)
                self.assertIn('test_public_regression.py', prompt)
                self.assertNotIn(task['reference_commit'], prompt)
                self.assertNotIn(task['issue_url'], prompt)
                self.assertNotIn('"tests/test_supplemental.py":', prompt)
                self.assertNotIn('reference.patch', prompt)
                self.assertIn('known_excluded_tests', task)

    def test_all_reference_patches_replay_with_immutable_tests_and_exact_exclusions(self):
        case = self
        with patch.object(app.core, 'DATA', DATA):
            for task_id in app.core.catalog():
                run, task = self.prepare_reference(task_id)
                class FakeSandbox:
                    def __init__(self, repo, *args): self.repo = repo
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def request(self, operation):
                        case.assertEqual(operation, 'run_tests')
                        case.assertNotEqual(self.repo, run / 'workspace')
                        case.assertTrue((self.repo / 'tests/test_supplemental.py').is_file())
                        for p in (DATA / task_id / 'fixture/tests').rglob('*'):
                            if p.is_file():
                                case.assertEqual(p.read_bytes(), (self.repo / p.relative_to(DATA / task_id / 'fixture')).read_bytes())
                        return {'exit_code': 0, 'stdout': f"{task['verification_test_count']} passed, 3 deselected in 0.50s\n"}
                with patch.object(app.core, 'Sandbox', FakeSandbox):
                    result = app.core.verify(run, 'offline-image')
                self.assertEqual(result['verification_status'], 'passed')
                self.assertEqual(result['expected_deselected_count'], 3)

    def test_extra_exclusions_or_missing_passes_are_rejected(self):
        with patch.object(app.core, 'DATA', DATA):
            run, task = self.prepare_reference('sqlparse-701')
            class FakeSandbox:
                def __init__(self, *args): pass
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def request(self, operation):
                    return {'exit_code': 0, 'stdout': f"{task['verification_test_count']} passed, 4 deselected in 0.50s\n"}
            with patch.object(app.core, 'Sandbox', FakeSandbox):
                result = app.core.verify(run, 'offline-image')
        self.assertEqual(result['verification_status'], 'failed')

    def test_report_accepts_only_the_declared_exclusion_count(self):
        job = app.make_jobs('expanded-row', ['sqlparse-701'], 1)[0]
        run = self.root / job['run_id']
        run.mkdir()
        control.write_json(run / 'agent-run.json', {'mode': 'live_baseline', 'execution_status': 'finished'})
        verification = {'verification_status': 'passed', 'test_returncode': 0,
            'expected_test_count': 453, 'expected_deselected_count': 3,
            'test_counts': {'passed': 453, 'deselected': 3}}
        control.write_json(run / 'verification.json', verification)
        (run / 'verification-tests.txt').write_text('453 passed, 3 deselected in 0.50s\n')
        with patch.object(app.core, 'RUNS', self.root):
            self.assertEqual(app.read_row(job)['status'], 'passed')
            verification['expected_deselected_count'] = 0
            control.write_json(run / 'verification.json', verification)
            self.assertEqual(app.read_row(job)['status'], 'failed')

    def test_batch_workers_receive_the_selected_catalog(self):
        with patch.object(app, 'DATA', DATA), patch.object(app.core, 'DATA', DATA), \
             patch.object(app.core, 'RUNS', self.root), \
             patch.object(app.core, 'get_image_for_task', return_value='offline-image'), \
             patch.object(control, 'supervise', return_value=0) as supervised, \
             patch.object(app, 'report', return_value={'all_rows_complete': True, 'rows': []}), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(app.compare('expanded-routing', ['sqlparse-701'], 1), 0)
        self.assertEqual(supervised.call_count, 4)
        for call in supervised.call_args_list:
            args = call.args[0]
            self.assertEqual(args[args.index('--data-dir') + 1], str(DATA))
            self.assertIn('--worker', args)

    def test_smoke_with_custom_catalog_selects_its_first_task(self):
        argv = ['comparison_agent.py', 'smoke', '--data-dir', str(DATA), '--worker']
        with patch.object(sys, 'argv', argv), patch.object(app, 'DATA', DATA), \
             patch.object(app.core, 'DATA', DATA), \
             patch.object(app.core, 'get_image_for_task', return_value='offline-image'), \
             patch.object(app.core, 'smoke') as smoke:
            self.assertEqual(app.main(), 0)
        self.assertEqual(smoke.call_args.args[0], 'offline-image')
        self.assertEqual(len(smoke.call_args.args[1]), 5)

    def test_new_image_build_sends_only_the_dockerfile(self):
        observed = []
        def build(command, **kwargs):
            context = Path(command[-1])
            observed.append(command)
            self.assertEqual([p.name for p in context.iterdir()], ['Dockerfile'])
            self.assertEqual((context / 'Dockerfile').read_bytes(), (DATA.parent / 'Dockerfile.expanded').read_bytes())
        with patch.object(sys, 'argv', ['expanded_agent.py', 'build']), \
             patch.object(app, 'DATA', DATA), patch.object(app.core, 'DATA', DATA), \
             patch.object(app, 'IMAGE', 'code-repair-python:expanded-v1'), \
             patch.object(app, 'DOCKERFILE', DATA.parent / 'Dockerfile.expanded'), \
             patch.object(app.subprocess, 'run', side_effect=build), \
             patch.object(app.repair, 'configured_llm') as model:
            self.assertEqual(app.main(), 0)
        self.assertEqual(observed[0][3], 'code-repair-python:expanded-v1')
        model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
