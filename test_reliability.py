"""Failure-path contracts; no model, Docker daemon or candidate execution."""
from contextlib import redirect_stdout
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
os.environ.setdefault('OPENHANDS_SUPPRESS_BANNER', '1')

import comparison_agent as app
import execution_control as control
import patch_quality as quality
import run_diagnostics as diagnostics


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def sandbox(self, original=b'x = 1\n', content=b'x = 2\n'):
        repo = self.root / 'workspace'
        repo.mkdir(exist_ok=True)
        (repo / 'source.py').write_bytes(original)
        initial = {'source.py': {'sha256': hashlib.sha256(original).hexdigest()},
                   '.env': {'sha256': 'private-unchanged'}}
        files = {'source.py': {'sha256': hashlib.sha256(content).hexdigest(),
                              'base64': base64.b64encode(content).decode()},
                 '.env': {'sha256': 'private-unchanged'}}
        return SimpleNamespace(repo=repo, initial=initial, allowed_files=['source.py'],
                               request=Mock(return_value={'files': files})), files

    def test_pinned_sdk_exception_unwraps_without_exporting_secrets(self):
        from openhands.sdk.conversation.exceptions import ConversationRunError
        import httpx
        inner = httpx.ReadTimeout('Bearer private-token https://private.example/?key=secret')
        wrapped = ConversationRunError('offline', inner)
        record = diagnostics.exception_metadata(wrapped)
        self.assertEqual(record['error_chain_types'], ['ConversationRunError', 'ReadTimeout'])
        self.assertEqual(record['root_error_type'], 'ReadTimeout')
        self.assertEqual(record['failure_category'], 'request_or_operation_timeout')
        self.assertNotIn('private', json.dumps(record))
        self.assertNotIn('secret', json.dumps(record))

    def test_error_cycles_are_bounded_and_unknown_class_names_are_not_exported(self):
        Custom = type('private_credential_value', (RuntimeError,), {})
        error = Custom('secret')
        error.original_exception = error
        record = diagnostics.exception_metadata(error)
        self.assertEqual(record['error_chain_types'], ['UnknownError'])
        self.assertNotIn('private', json.dumps(record))

    def test_checkpoint_is_source_only_and_does_not_modify_host_workspace(self):
        box, _ = self.sandbox()
        result = diagnostics.checkpoint(self.root, box)
        self.assertEqual(result['status'], 'saved')
        self.assertEqual((box.repo / 'source.py').read_text(), 'x = 1\n')
        with zipfile.ZipFile(self.root / 'candidate-sources.zip') as z:
            self.assertEqual(z.namelist(), ['source.py'])
            self.assertEqual(z.read('source.py'), b'x = 2\n')
        self.assertFalse(diagnostics.candidate_metadata(self.root)['candidate_verified'])
        self.assertFalse((self.root / 'verification.json').exists())

    def test_successful_write_triggers_checkpoint_before_next_model_turn(self):
        box, files = self.sandbox()
        backend = app.core.Sandbox(box.repo, 'offline', ['source.py'], self.root / 'tool-calls.jsonl')
        backend.initial = box.initial
        with patch.object(app.core.BaseSandbox, 'request', side_effect=[
                {'exit_code': 0}, {'files': files}]):
            backend.request('write_file', 'source.py', 'x = 2\n')
        self.assertEqual(diagnostics.candidate_metadata(self.root)['candidate_status'], 'saved')

    def test_out_of_scope_container_changes_are_never_saved_as_candidate(self):
        box, files = self.sandbox()
        files['tests/test_original.py'] = {'sha256': 'changed'}
        result = diagnostics.checkpoint(self.root, box)
        self.assertEqual(result['status'], 'save_failed')
        self.assertFalse((self.root / 'candidate-sources.zip').exists())

    def test_source_hash_mismatch_is_rejected(self):
        box, files = self.sandbox()
        files['source.py']['sha256'] = 'incorrect'
        self.assertEqual(diagnostics.checkpoint(self.root, box)['status'], 'save_failed')

    def test_interrupted_multi_file_save_is_detectable(self):
        box, _ = self.sandbox()
        diagnostics.checkpoint(self.root, box)
        (self.root / 'candidate.diff').write_text('interrupted replacement')
        self.assertEqual(diagnostics.candidate_metadata(self.root)['candidate_status'], 'checkpoint_incomplete')

    def test_reverted_candidate_is_not_reported_as_current_patch(self):
        box, _ = self.sandbox()
        diagnostics.checkpoint(self.root, box)
        box.request.return_value = {'files': box.initial}
        self.assertEqual(diagnostics.checkpoint(self.root, box)['status'], 'no_patch')
        self.assertEqual(diagnostics.candidate_metadata(self.root)['candidate_status'], 'no_patch')

    def test_checkpoint_patch_replays_sources_without_final_newline(self):
        box, _ = self.sandbox(original=b'x = 1', content=b'x = 2')
        diagnostics.checkpoint(self.root, box)
        result = subprocess.run(['git', 'apply', '--check', '-'], cwd=box.repo,
            input=(self.root / 'candidate.diff').read_bytes(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_secondary_disk_failure_does_not_mask_original_operation(self):
        box, _ = self.sandbox()
        with patch.object(control, 'write_json', side_effect=OSError('disk-full-secret')):
            self.assertEqual(diagnostics.checkpoint(self.root, box)['status'], 'metadata_save_failed')

    def test_conversation_failure_saves_candidate_and_does_not_invoke_verifier(self):
        from openhands.sdk import LLM
        from openhands.sdk.conversation.exceptions import ConversationRunError
        from openhands.sdk.event.conversation_error import ConversationErrorEvent
        import httpx
        run = self.root / 'run'
        run.mkdir()
        (run / 'workspace').mkdir()
        control.write_json(run / 'manifest.json', {'task': {'id': 'offline-task',
            'issue': 'offline', 'allowed_files': ['source.py']},
            'baseline_commit': 'offline-base', 'image_id': 'offline-image'})
        llm = LLM(model='openai/gpt-5.6-sol', api_key='offline-no-network')
        llm.is_subscription = True
        close_calls = []
        error = ConversationRunError('offline', httpx.ReadTimeout('private-token'))
        class FakeConversation:
            def __init__(self, callbacks, **kwargs): self.callback = callbacks[0]
            def send_message(self, text): pass
            def run(self):
                self.callback(ConversationErrorEvent(source='environment', code='ReadTimeout', detail='private-token'))
                raise error
            def close(self):
                close_calls.append(1)
                raise OSError('secondary-close-error')
        class FakeSandbox:
            def __init__(self, *args): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch.object(app.core, 'get_image_for_task', return_value='offline-image'), \
             patch.object(app.core, 'check_fixture'), \
             patch.object(app.repair, 'command', side_effect=lambda args, *a, **kw:
                          SimpleNamespace(stdout='' if 'status' in args else 'offline-base')), \
             patch.object(app.repair, 'configured_llm', return_value=(llm, 'subscription')), \
             patch.object(app.core, 'make_agent', side_effect=lambda model, box: SimpleNamespace(llm=model)), \
             patch.object(app.core, 'Sandbox', FakeSandbox), \
             patch('openhands.sdk.Conversation', FakeConversation), \
             patch.object(diagnostics, 'checkpoint', return_value={'status': 'saved'}) as saved, \
             patch.object(app.core, 'verify') as verify, self.assertRaises(ConversationRunError) as caught:
            app.core.run_agent(run)
        self.assertIs(caught.exception, error)
        saved.assert_called_once()
        verify.assert_not_called()
        self.assertEqual(close_calls, [1])
        report = control.read_json(run / 'agent-run.json')
        self.assertEqual(report['root_error_type'], 'ReadTimeout')
        self.assertEqual(report['conversation_error_code'], 'ReadTimeout')
        self.assertEqual(report['execution_status'], 'error')
        self.assertEqual(report['close_error_type'], 'OSError')
        self.assertNotIn('private-token', (run / 'agent-run.json').read_text())
        self.assertNotIn('private-token', (run / 'events.jsonl').read_text())

    def test_checkpoint_survives_hard_worker_deadline(self):
        worker_dir = self.root / 'worker'
        run = self.root / 'saved-run'
        script = '''import base64,hashlib,time
from pathlib import Path
from types import SimpleNamespace
import run_diagnostics as d
root=Path(RUN_PATH); root.mkdir(); repo=root/'workspace'; repo.mkdir()
old=b'x = 1\\n'; new=b'x = 2\\n'; (repo/'source.py').write_bytes(old)
box=SimpleNamespace(repo=repo,allowed_files=['source.py'],
 initial={'source.py':{'sha256':hashlib.sha256(old).hexdigest()}},
 request=lambda *a,**kw: {'files':{'source.py':{'sha256':hashlib.sha256(new).hexdigest(),
 'base64':base64.b64encode(new).decode()}}})
d.checkpoint(root,box)
print('saved',flush=True)
time.sleep(30)
'''.replace('RUN_PATH', repr(str(run)))
        with redirect_stdout(io.StringIO()):
            code = control.supervise([sys.executable, '-c', script], cwd=Path(__file__).parent,
                directory=worker_dir, timeout=3, grace=0.05)
        self.assertEqual(code, 124)
        self.assertEqual(diagnostics.candidate_metadata(run)['candidate_status'], 'saved')
        self.assertFalse((run / 'verification.json').exists())

    def test_large_documentation_deletion_is_a_review_hint_not_a_test_failure(self):
        old = 'def f():\n    """' + '\n'.join('documentation line' for _ in range(20)) + '\n    """\n    return 1\n'
        report = quality.analyze_sources({'source.py': old}, {'source.py': 'def f():\n    return 2\n'})
        self.assertEqual(report['status'], 'review_required')
        self.assertIn('substantial_documentation_removal', report['flags'])
        self.assertNotIn('verification_status', report)

    def test_static_quality_review_does_not_execute_source(self):
        marker = self.root / 'must-not-exist'
        source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
        result = quality.analyze_sources({'source.py': ''}, {'source.py': source})
        self.assertEqual(result['status'], 'no_heuristic_flags')
        self.assertFalse(marker.exists())

    def test_quality_analysis_failure_cannot_change_verification_evidence(self):
        (self.root / 'patch.diff').write_text('unreadable patch without manifest')
        evidence = {'verification_status': 'passed', 'test_returncode': 0}
        control.write_json(self.root / 'verification.json', evidence)
        before = (self.root / 'verification.json').read_bytes()
        self.assertEqual(app.core.quality_for_run(self.root)['status'], 'not_available')
        self.assertEqual((self.root / 'verification.json').read_bytes(), before)

    def test_old_review_without_manifest_can_use_verified_catalog_base(self):
        with patch.object(app.core, 'DATA', app.DATA):
            task = app.core.load_task('dotenv-256')
            fixture = app.core.check_fixture(task)
            (self.root / 'patch.diff').write_text((app.DATA / task['id'] / 'reference.patch').read_text())
            control.write_json(self.root / 'verification.json', {'task_id': task['id'],
                'upstream_base_commit': task['base_commit'], 'verification_status': 'passed'})
            before = (self.root / 'verification.json').read_bytes()
            result = app.core.quality_for_run(self.root)
        self.assertIn(result['status'], {'no_heuristic_flags', 'review_required'})
        self.assertFalse((self.root / 'manifest.json').exists())
        self.assertEqual((self.root / 'verification.json').read_bytes(), before)

    def test_quality_rejects_patch_outside_source_whitelist(self):
        text = '--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-secret\n+other\n'
        with self.assertRaises(ValueError):
            quality.analyze_patch(self.root, text, ['source.py'])

    def test_clock_gap_is_diagnostic_and_does_not_claim_sleep(self):
        result = control.clock_metadata({'started_at': '2026-10-17T00:00:00+00:00',
            'finished_at': '2026-10-17T01:00:00+00:00', 'elapsed_seconds': 60})
        self.assertEqual(result['clock_gap_seconds'], 3540)
        self.assertTrue(result['clock_gap_detected'])
        self.assertNotIn('sleep', result)

    def test_wall_clock_gap_cannot_extend_worker_deadline(self):
        process = Mock(pid=99999)
        process.poll.return_value = None
        with patch.object(control.subprocess, 'Popen', return_value=process), \
             patch.object(control, 'stop_process_group'), \
             patch.object(control.time, 'monotonic', return_value=100), \
             patch.object(control.time, 'time', side_effect=[1000, 1400]), \
             redirect_stdout(io.StringIO()):
            code = control.supervise(['never-executed'], cwd=self.root,
                directory=self.root / 'clock-worker', timeout=300)
        self.assertEqual(code, 124)

    def test_failed_attempt_with_saved_candidate_and_pass_artifact_stays_failed(self):
        job = app.make_jobs('candidate-error', ['dotenv-256'], 1)[1]
        run = self.root / job['run_id']
        run.mkdir()
        control.write_json(run / 'agent-run.json', {'mode': 'live_agent', 'model_attempt_started': True,
            'execution_status': 'error', 'error_type': 'ConversationRunError', 'root_error_type': 'ReadTimeout'})
        control.write_json(run / 'verification.json', {'verification_status': 'passed',
            'test_returncode': 0, 'expected_test_count': 77, 'test_counts': {'passed': 77}})
        (run / 'verification-tests.txt').write_text('77 passed in 0.1s\n')
        control.write_json(run / 'candidate.json', {'status': 'saved', 'verified': False})
        with patch.object(app.core, 'RUNS', self.root):
            row = app.read_row(job)
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['root_error_type'], 'ReadTimeout')

    def test_review_export_contains_candidates_and_safe_errors_but_no_raw_logs(self):
        jobs = app.make_jobs('review-export', ['dotenv-256'], 1)
        control.write_json(self.root / 'review-export-comparison-plan.json', {'jobs': jobs})
        for job in jobs:
            run = self.root / job['run_id']
            run.mkdir()
            box, _ = self.sandbox()
            diagnostics.checkpoint(run, box)
            control.write_json(run / 'agent-run.json', {'mode': 'live_agent', 'execution_status': 'error',
                **diagnostics.exception_metadata(TimeoutError('private-value'))})
            (run / 'events.jsonl').write_text('{"event_type":"ConversationErrorEvent","error_code":"TimeoutError"}\n')
            (run / '.env').write_text('private-value')
            wd = control.worker_directory(self.root, job['run_id'], 'run')
            wd.mkdir(parents=True)
            (wd / 'worker.log').write_text('private-value')
        with patch.object(app.core, 'RUNS', self.root), patch.object(app, 'ROOT', self.root), \
             redirect_stdout(io.StringIO()):
            out = app.export_results('review-export')
        with zipfile.ZipFile(out) as z:
            self.assertEqual(sum(n.endswith('candidate-sources.zip') for n in z.namelist()), 2)
            self.assertFalse(any(n.endswith(('.env', 'worker.log')) for n in z.namelist()))
            for n in z.namelist():
                if n.endswith(('.json', '.jsonl')):
                    self.assertNotIn(b'private-value', z.read(n))


if __name__ == '__main__':
    unittest.main()
