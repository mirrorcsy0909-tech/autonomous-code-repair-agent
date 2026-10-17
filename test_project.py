"""Public CLI and checked-in examples; no live model or Docker daemon."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import project


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_catalog_has_ten_historical_tasks_with_one_feature_task(self):
        tasks = [task for name in project.CATALOGS for task in project.load_catalog(name)]
        self.assertEqual(len(tasks), 10)
        self.assertEqual(sum(task.get('task_kind') == 'feature_compatibility' for task in tasks), 1)
        self.assertEqual(project.check_fixtures('all'), 10)

    def test_fixture_edit_is_detected_without_executing_it(self):
        base = self.root / 'expanded_tasks/sqlparse-test'
        (base / 'fixture').mkdir(parents=True)
        original = b'x = 1\n'
        (base / 'fixture/source.py').write_bytes(b'raise RuntimeError("must not execute")\n')
        (base / 'fixture-sha256.json').write_text(json.dumps({'source.py': hashlib.sha256(original).hexdigest()}))
        (base / 'task.json').write_text(json.dumps({'id': 'sqlparse-test'}))
        (base.parent / 'catalog.json').write_text(json.dumps({'tasks': ['sqlparse-test']}))
        with patch.object(project, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'Fixture changed'):
            project.check_fixtures('sqlparse')

    def test_existing_sqlparse_report_routing_is_preserved(self):
        args = project.parser().parse_args(['report', '--catalog', 'sqlparse', '--prefix', 'expanded-002'])
        commands = project.delegated_commands(args)
        self.assertEqual(commands, [[sys.executable, str(project.ROOT / 'expanded_agent.py'),
                                    'report', '--prefix', 'expanded-002']])

    def test_cross_catalog_task_is_rejected_before_starting_subprocess(self):
        with patch.object(project.subprocess, 'run') as execute, self.assertRaises(ValueError):
            project.main(['compare', '--catalog', 'sqlparse', '--tasks', 'dotenv-256', '--prefix', 'invalid'])
        execute.assert_not_called()

    def test_all_catalog_smokes_have_unique_ids_and_stop_on_first_failure(self):
        args = project.parser().parse_args(['smoke', '--catalog', 'all', '--id', 'reference'])
        commands = project.delegated_commands(args)
        self.assertEqual(len(commands), 3)
        self.assertEqual(len({command[-1] for command in commands}), 3)
        with patch.object(project.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7)) as execute:
            self.assertEqual(project.main(['smoke', '--catalog', 'all', '--id', 'reference']), 7)
        execute.assert_called_once()

    def test_derived_smoke_id_is_validated_before_any_work(self):
        with patch.object(project.subprocess, 'run') as execute, self.assertRaises(ValueError):
            project.main(['smoke', '--catalog', 'all', '--id', 'x' * 63])
        execute.assert_not_called()

    def test_offline_doctor_does_not_read_environment_contents_or_use_network(self):
        for name in project.RUNTIME_FILES:
            (self.root / name).write_text('placeholder')
        (self.root / 'Dockerfile.expanded').write_text('placeholder')
        (self.root / '.env').write_text('LLM_API_KEY=private-example-value')
        def version(name):
            return '1.53.0' if name.startswith('openhands-') else '1'
        with patch.object(project, 'ROOT', self.root), \
             patch.object(project, 'check_fixtures', return_value=5), \
             patch.object(project.metadata, 'version', side_effect=version), \
             patch.object(project.shutil, 'which', return_value='/offline/git'), \
             patch.object(project.subprocess, 'run') as execute, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(project.doctor('sqlparse', offline=True), 0)
        execute.assert_not_called()
        self.assertNotIn('private-example-value', out.getvalue())
        script = 'import sys, project; project.load_catalog("sqlparse"); assert not any(n.startswith("openhands") for n in sys.modules)'
        subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).parent, check=True, timeout=10)

    def test_unreachable_docker_is_bounded_and_reported_as_failure(self):
        with patch.object(project.metadata, 'version', return_value='1.53.0'), \
             patch.object(project.shutil, 'which', return_value='/offline/executable'), \
             patch.object(project.subprocess, 'run', side_effect=subprocess.TimeoutExpired('docker', 5)) as execute, \
             redirect_stdout(io.StringIO()) as out:
            self.assertEqual(project.doctor('sqlparse'), 1)
        self.assertIn('FAIL  Docker daemon reachable', out.getvalue())
        self.assertEqual(execute.call_args.kwargs['timeout'], 5)

    def test_check_propagates_failures_and_uses_only_explicit_harness_tests(self):
        with patch.object(project.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as execute:
            self.assertEqual(project.main(['check']), 1)
        command = execute.call_args.args[0]
        self.assertEqual(command[:3], [sys.executable, '-m', 'unittest'])
        self.assertNotIn('discover', command)
        self.assertIn('test_reliability', command)
        self.assertEqual(execute.call_args.kwargs['env']['LITELLM_LOCAL_MODEL_COST_MAP'], 'True')

    def test_examples_recompute_their_recorded_counts_times_and_usage(self):
        examples = list((project.ROOT / 'examples/reports').glob('*.json'))
        self.assertEqual(len(examples), 3)
        for path in examples:
            example = json.loads(path.read_text())
            self.assertEqual(example['schema_version'], 1)
            self.assertEqual(len(example['source_review_sha256']), 64)
            for workflow, group in example['by_workflow'].items():
                rows = [row for row in example['rows'] if row['workflow'] == workflow and row['model_started']]
                self.assertEqual(group['attempts_started'], len(rows))
                self.assertEqual(group['attempts_passed'], sum(row['status'] == 'passed' for row in rows))
                self.assertEqual(group['median_latency_seconds'], statistics.median(row['latency_seconds'] for row in rows))
                for key in ('prompt_tokens', 'completion_tokens'):
                    self.assertEqual(group[key], sum(row[key] for row in rows))
            self.assertNotIn('/Users/', path.read_text())
            self.assertNotIn('LLM_API_KEY', path.read_text())

    def test_gitignore_excludes_run_copies_credentials_and_review_archives(self):
        shutil.copyfile(project.ROOT / '.gitignore', self.root / '.gitignore')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        paths = ['.env', '.env.local', 'runs/report.json', 'runs 2/workspace/code.py',
                 'expanded-002-review.zip', '.venv/secret', '.env.example', 'examples/reports/expanded-002.json']
        result = subprocess.run(['git', 'check-ignore', '--stdin'], input='\n'.join(paths),
            cwd=self.root, text=True, capture_output=True, check=False)
        self.assertEqual(set(result.stdout.splitlines()), set(paths[:6]))


if __name__ == '__main__':
    unittest.main()
