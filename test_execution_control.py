"""Offline control tests. No real model, credentials or Docker daemon is used."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
os.environ.setdefault('OPENHANDS_SUPPRESS_BANNER', '1')

import comparison_agent as app
import execution_control as control


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def quiet_supervise(self, command, **kwargs):
        with redirect_stdout(io.StringIO()) as output:
            result = control.supervise(command, cwd=Path(__file__).parent,
                directory=self.root / 'worker', heartbeat=0.05, grace=0.05, **kwargs)
        return result, output.getvalue()

    def test_deadline_stops_real_worker_and_records_timeout(self):
        result, output = self.quiet_supervise(
            [sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.25)
        record = control.read_json(self.root / 'worker/process.json')
        self.assertEqual(result, 124)
        self.assertEqual(record['status'], 'timed_out')
        self.assertFalse(record['model_attempt_started'])
        self.assertLess(record['elapsed_seconds'], 3)
        self.assertIn('stage=startup', output)

    def test_group_signal_reaps_worker_before_sending_signal(self):
        order = []
        process = Mock(pid=12345)
        process.poll.side_effect = lambda: order.append('reap')
        with patch.object(control.os, 'killpg', side_effect=lambda *args: order.append('signal')):
            self.assertTrue(control.signal_worker_group(process, signal.SIGTERM))
        self.assertEqual(order, ['reap', 'signal'])

    def test_mac_zombie_only_permission_error_is_a_completed_group(self):
        process = Mock(pid=12345)
        with patch.object(control.os, 'killpg', side_effect=PermissionError(1, 'offline mac zombie group')), \
             patch.object(control, 'live_group_pids', return_value=set()):
            control.stop_process_group(process, grace=0)
        process.wait.assert_called_once_with(timeout=5)

    def test_mac_zombie_probe_after_term_does_not_attempt_another_kill(self):
        process = Mock(pid=12345)
        with patch.object(control.os, 'killpg', side_effect=[None, PermissionError(1, 'offline zombie')]) as send, \
             patch.object(control, 'live_group_pids', return_value=set()):
            control.stop_process_group(process, grace=1)
        self.assertEqual([call.args[1] for call in send.call_args_list], [signal.SIGTERM, 0])

    def test_live_permission_denial_is_not_ignored(self):
        process = Mock(pid=12345)
        with patch.object(control.os, 'killpg', side_effect=PermissionError(1, 'offline real denial')), \
             patch.object(control, 'live_group_pids', return_value={12345}), self.assertRaises(PermissionError):
            control.stop_process_group(process, grace=0)
        process.wait.assert_not_called()

    def test_ps_group_probe_ignores_zombies_and_other_groups(self):
        result = subprocess.CompletedProcess([], 0, '12345 12345 Z\n12346 12345 S+\n99 99 R\n', '')
        with patch.object(control.subprocess, 'run', return_value=result):
            self.assertEqual(control.live_group_pids(12345), {12346})

    def test_termination_failure_is_recorded_without_repeating_failed_stop(self):
        process = Mock(pid=12345)
        process.poll.return_value = None
        with patch.object(control.subprocess, 'Popen', return_value=process), \
             patch.object(control, 'stop_process_group', side_effect=PermissionError(1, 'offline denial')) as stop:
            result, output = self.quiet_supervise(['offline-not-executed'], timeout=0.01)
        self.assertEqual(result, 2)
        stop.assert_called_once()
        self.assertEqual(control.read_json(self.root / 'worker/process.json')['status'], 'termination_failed')
        self.assertIn('termination unconfirmed', output)

    def test_timeout_kills_descendant_even_when_worker_ignores_term(self):
        marker = self.root / 'unexpected-orphan.txt'
        descendant = f'import time,pathlib;time.sleep(1.4);pathlib.Path({str(marker)!r}).write_text("orphan")'
        worker = ('import subprocess,sys,time,signal;'
                  'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                  f'subprocess.Popen([sys.executable,"-c",{descendant!r}]);time.sleep(30)')
        result, _ = self.quiet_supervise([sys.executable, '-c', worker], timeout=0.3)
        self.assertEqual(result, 124)
        time.sleep(1.5)
        self.assertFalse(marker.exists(), 'A descendant escaped the worker process group')

    def test_interrupt_records_interruption_and_terminates_worker(self):
        directory = self.root / 'interrupt'
        worker = 'import time;time.sleep(30)'
        script = ('import execution_control as c,sys;from pathlib import Path\n'
                  'try:\n'
                  f' c.supervise([sys.executable,"-c",{worker!r}],cwd=Path.cwd(),'
                  f'directory=Path({str(directory)!r}),timeout=20,grace=0.05)\n'
                  'except KeyboardInterrupt: sys.exit(130)\n')
        process = subprocess.Popen([sys.executable, '-c', script], cwd=Path(__file__).parent,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if control.read_json(directory / 'process.json').get('status') == 'running':
                break
            time.sleep(0.02)
        else:
            self.fail('Supervisor did not start')
        process.send_signal(signal.SIGINT)
        self.assertEqual(process.wait(timeout=5), 130)
        self.assertEqual(control.read_json(directory / 'process.json')['status'], 'interrupted')

    def test_failed_exit_is_not_a_timeout_and_raw_output_stays_local(self):
        result, output = self.quiet_supervise(
            [sys.executable, '-c', 'print("local-output");raise SystemExit(7)'], timeout=5)
        self.assertEqual(result, 7)
        self.assertEqual(control.read_json(self.root / 'worker/process.json')['status'], 'exited')
        self.assertNotIn('local-output', output)
        self.assertIn('local-output', (self.root / 'worker/worker.log').read_text())

    def test_cleanup_only_targets_registered_exact_container_names(self):
        directory = self.root / 'registry'
        directory.mkdir()
        name = 'repair-task-123456789abc'
        (directory / 'containers.jsonl').write_text('\n'.join(json.dumps({'name': x}) for x in (
            name, 'another-project', 'repair-task-123456789abc;echo invalid')) + '\n')
        responses = [subprocess.CompletedProcess([], 0, '', ''),
                     subprocess.CompletedProcess([], 0, 'unrelated-container\n', '')]
        with patch.object(control.subprocess, 'run', side_effect=responses) as docker:
            result = control.cleanup_containers(directory)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(docker.call_args_list[0].args[0], ['docker', 'rm', '-f', name])
        self.assertEqual(len(docker.call_args_list), 2)

    def test_cleanup_failure_changes_result_and_is_visible(self):
        with patch.object(control, 'cleanup_containers', return_value={'status': 'unconfirmed', 'names': []}):
            result, output = self.quiet_supervise([sys.executable, '-c', 'pass'], timeout=5)
        self.assertEqual(result, 2)
        self.assertEqual(control.read_json(self.root / 'worker/process.json')['status'], 'cleanup_failed')
        self.assertIn('cleanup unconfirmed', output)

    def test_partial_trace_preserves_rows_and_flags_uncertainty(self):
        path = self.root / 'trace.jsonl'
        path.write_text('{"operation":"read_file"}\n{"oper')
        rows, partial = control.read_jsonl(path)
        self.assertEqual(len(rows), 1)
        self.assertTrue(partial)

    def test_environment_limits_reject_unbounded_or_ambiguous_values(self):
        for name, value in [('REPAIR_RUN_TIMEOUT_SECONDS', '0'),
                            ('REPAIR_LLM_ATTEMPTS', '0'), ('REPAIR_LLM_ATTEMPTS', '9'),
                            ('REPAIR_LLM_TIMEOUT_SECONDS', 'None')]:
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}), self.assertRaises(ValueError):
                control.Limits.from_env()

    def make_row(self, job, *, model_started=True, worker_state='timed_out'):
        run = self.root / job['run_id']
        run.mkdir()
        agent = {'mode': 'live_agent' if job['workflow'] == 'iterative' else 'live_baseline',
                 'model': 'offline-model', 'auth': 'subscription', 'image_id': 'offline-image',
                 'max_output_tokens_per_call': 8192, 'execution_status': 'finished',
                 'model_attempt_started': model_started}
        control.write_json(run / 'agent-run.json', agent)
        control.write_json(run / 'verification.json', {'verification_status': 'passed',
            'test_returncode': 0, 'expected_test_count': 77, 'test_counts': {'passed': 77}})
        (run / 'verification-tests.txt').write_text('77 passed in 0.10s\n')
        control.write_json(control.worker_directory(self.root, job['run_id'], 'run') / 'process.json',
                           {'status': worker_state, 'last_stage': 'verify'})
        return run

    def test_timeout_overrides_pass_artifacts_and_stays_in_denominator(self):
        jobs = app.make_jobs('deadline', ['dotenv-256'], 1)
        for job in jobs:
            self.make_row(job)
        with patch.object(app.core, 'RUNS', self.root), redirect_stdout(io.StringIO()):
            summary = app.report('deadline', {'jobs': jobs})
        for workflow in app.WORKFLOWS:
            self.assertEqual(summary['by_workflow'][workflow]['attempts_started'], 1)
            self.assertEqual(summary['by_workflow'][workflow]['attempts_passed'], 0)
        self.assertTrue(all(r['status'] == 'timed_out' for r in summary['rows']))

    def test_auth_timeout_is_separate_from_started_model_attempts(self):
        job = app.make_jobs('auth', ['dotenv-256'], 1)[0]
        self.make_row(job, model_started=False)
        with patch.object(app.core, 'RUNS', self.root):
            row = app.read_row(job)
        self.assertFalse(row['model_started'])
        self.assertEqual(row['status'], 'model_not_started')
        self.assertEqual(row['failure_type'], 'timed_out')

    def test_preparation_timeout_without_model_record_is_terminal_preparation_failure(self):
        job = app.make_jobs('prepare-timeout', ['dotenv-256'], 1)[0]
        control.write_json(control.worker_directory(self.root, job['run_id'], 'prepare') / 'process.json',
                           {'status': 'timed_out', 'returncode': 124})
        with patch.object(app.core, 'RUNS', self.root):
            row = app.read_row(job)
        self.assertFalse(row['model_started'])
        self.assertEqual(row['status'], 'prepare_failed')

    def test_crashed_started_worker_is_failed_even_without_final_agent_report(self):
        job = app.make_jobs('crashed', ['dotenv-256'], 1)[0]
        run = self.make_row(job, worker_state='exited')
        (run / 'verification.json').unlink()
        agent = control.read_json(run / 'agent-run.json')
        agent['execution_status'] = 'started'
        control.write_json(run / 'agent-run.json', agent)
        control.write_json(control.worker_directory(self.root, job['run_id'], 'run') / 'process.json',
                           {'status': 'exited', 'returncode': -9})
        with patch.object(app.core, 'RUNS', self.root):
            self.assertEqual(app.read_row(job)['status'], 'failed')

    def test_legacy_subscription_cap_is_corrected_without_rewriting_evidence(self):
        job = app.make_jobs('legacy', ['dotenv-256'], 1)[0]
        run = self.make_row(job, worker_state='exited')
        original = (run / 'agent-run.json').read_bytes()
        with patch.object(app.core, 'RUNS', self.root):
            row = app.read_row(job)
        self.assertEqual(row['requested_max_output_tokens_per_call'], 8192)
        self.assertIsNone(row['max_output_tokens_per_call'])
        self.assertEqual(row['output_limit_status'], 'unsupported_subscription')
        self.assertEqual((run / 'agent-run.json').read_bytes(), original)

    def test_export_includes_control_evidence_but_excludes_raw_worker_log(self):
        jobs = app.make_jobs('safe-export', ['dotenv-256'], 1)
        for job in jobs:
            self.make_row(job)
            (control.worker_directory(self.root, job['run_id'], 'run') / 'worker.log').write_text('LOCAL ONLY')
        control.write_json(self.root / 'safe-export-comparison-plan.json', {'jobs': jobs})
        with patch.object(app.core, 'RUNS', self.root), patch.object(app, 'ROOT', self.root), redirect_stdout(io.StringIO()):
            output = app.export_results('safe-export')
        import zipfile
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(sum(n.endswith('process.json') for n in archive.namelist()), 2)
            self.assertFalse(any(n.endswith('worker.log') for n in archive.namelist()))

    def test_batch_continues_after_timeout_without_rerunning_same_attempt(self):
        def supervisor(command, **kwargs):
            job_id = command[command.index('--id') + 1]
            action = command[2]
            if action == 'prepare':
                return 0
            workflow = command[command.index('--workflow') + 1]
            job = {'run_id': job_id, 'task_id': 'dotenv-256', 'workflow': workflow}
            self.make_row(job, worker_state='timed_out' if workflow == 'oneshot' else 'exited')
            return 124 if workflow == 'oneshot' else 0
        with patch.object(app.core, 'RUNS', self.root), \
             patch.object(app.core, 'load_task', return_value={'id': 'dotenv-256'}), \
             patch.object(app.core, 'check_fixture'), patch.object(app.core, 'get_image_for_task'), \
             patch.object(control, 'supervise', side_effect=supervisor) as supervised, redirect_stdout(io.StringIO()):
            self.assertEqual(app.compare('batch', ['dotenv-256'], 1), 0)
        self.assertEqual(supervised.call_count, 4)
        summary = control.read_json(self.root / 'batch-comparison-summary.json')
        self.assertEqual([r['status'] for r in summary['rows']], ['timed_out', 'passed'])


class PinnedSDKTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'REPAIR_RUN_TIMEOUT_SECONDS': '300',
            'REPAIR_PREPARE_TIMEOUT_SECONDS': '120', 'REPAIR_LLM_TIMEOUT_SECONDS': '60',
            'REPAIR_LLM_ATTEMPTS': '2'})
        environment.start()
        self.addCleanup(environment.stop)

    def test_sdk_parameters_omit_subscription_cap_and_keep_api_configuration(self):
        from openhands.sdk import LLM
        from openhands.sdk.llm.options.responses_options import select_responses_options
        llm = LLM(model='openai/gpt-5.6-sol', api_key='offline-no-network')
        for subscription in (False, True):
            llm.is_subscription = subscription
            configured, budget = control.configure_llm(llm, 'subscription' if subscription else 'api', 8192)
            options = select_responses_options(configured, {}, include=None, store=False)
            self.assertEqual(configured.num_retries, 2)
            self.assertEqual(configured.timeout, 60)
            if subscription:
                self.assertNotIn('max_output_tokens', options)
                self.assertIsNone(budget['max_output_tokens_per_call'])
            else:
                self.assertEqual(options['max_output_tokens'], 8192)
                self.assertEqual(budget['output_limit_status'], 'sdk_configured')

    def test_sdk_attempt_limit_includes_initial_attempt(self):
        from openhands.sdk import LLM
        llm = LLM(model='openai/gpt-5.6-sol', api_key='offline-no-network')
        llm, _ = control.configure_llm(llm, 'api', 8192)
        calls = []
        @llm.retry_decorator(num_retries=llm.num_retries, retry_exceptions=(OSError,),
                             retry_min_wait=0, retry_max_wait=0, retry_multiplier=0)
        def failing_request():
            calls.append(1)
            raise OSError('offline simulated transport failure')
        with patch('openhands.sdk.llm.utils.retry_mixin.logger'), self.assertRaises(OSError):
            failing_request()
        self.assertEqual(len(calls), 2)

    def test_iterative_lifecycle_uses_limits_records_start_and_closes_conversation(self):
        from openhands.sdk import LLM
        llm = LLM(model='openai/gpt-5.6-sol', api_key='offline-no-network')
        llm.is_subscription = True
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / 'run'
            run.mkdir()
            (run / 'workspace').mkdir()
            control.write_json(run / 'manifest.json', {'task': {'id': 'offline-task',
                'issue': 'offline issue', 'allowed_files': ['source.py']},
                'baseline_commit': 'offline-base', 'image_id': 'offline-image'})
            operations = []
            class FakeSandbox:
                def __init__(self, *args): pass
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def export_sources(self): operations.append('export')
            class FakeConversation:
                def __init__(self, *, agent, callbacks, **kwargs):
                    self.state = SimpleNamespace(execution_status=SimpleNamespace(value='finished'))
                    self.callback = callbacks[0]
                    if agent.llm.timeout != 60 or agent.llm.num_retries != 2:
                        raise AssertionError('Agent did not retain configured limits')
                def send_message(self, text): pass
                def run(self):
                    operations.append('run')
                    self.callback(SimpleNamespace())
                def close(self): operations.append('close')
            def git(args, *a, **kw):
                return SimpleNamespace(stdout='' if 'status' in args else 'offline-base')
            with patch.object(app.core, 'get_image_for_task', return_value='offline-image'), \
                 patch.object(app.core, 'check_fixture'), patch.object(app.repair, 'command', side_effect=git), \
                 patch.object(app.repair, 'configured_llm', return_value=(llm, 'subscription')), \
                 patch.object(app.core, 'Sandbox', FakeSandbox), \
                 patch.object(app.core, 'make_agent', side_effect=lambda model, sandbox: SimpleNamespace(llm=model)), \
                 patch('openhands.sdk.Conversation', FakeConversation), \
                 patch.object(app.core, 'verify', return_value={'verification_status': 'passed'}):
                result = app.core.run_agent(run, max_output_tokens=8192)
            self.assertEqual(result['verification_status'], 'passed')
            self.assertEqual(operations, ['run', 'export', 'close'])
            report = control.read_json(run / 'agent-run.json')
            self.assertTrue(report['model_attempt_started'])
            self.assertIsNone(report['max_output_tokens_per_call'])


if __name__ == '__main__':
    unittest.main()
