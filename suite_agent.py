"""Three historical tasks, sequential batch execution, and auditable summaries.

Requires the existing repair.py, docker_agent.py, real_task.py and real-task image.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

import repair
import execution_control as control
import run_diagnostics as diagnostics
import patch_quality
import real_task as single
from docker_agent import Sandbox as BaseSandbox, cli, make_agent

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'repair_suite'
RUNS = ROOT / 'runs'
MODEL_OPERATIONS = {'list_files', 'read_file', 'write_file', 'run_tests'}


def configure_task_directory(path):
    """Configure this CLI process before starting runs; no concurrent reconfiguration."""
    global DATA
    DATA = Path(path).resolve()


def get_image_for_task(task):
    tag = task.get('image_tag')
    if not tag:
        return single.get_image()
    cli(['info', '--format', '{{.ServerVersion}}'])
    result = cli(['image', 'inspect', tag], check=False)
    if result.returncode:
        raise RuntimeError('Missing task image. Build it first: ' + task.get(
            'image_build_command', 'uv run python comparison_agent.py build'))
    return json.loads(result.stdout)[0]['Id']


def usage_snapshot(llm):
    """SDK-reported usage and estimate; never an actual subscription bill."""
    snapshot = llm.metrics.get_snapshot().model_dump(mode='json')
    usage = snapshot.get('accumulated_token_usage')
    return {'sdk_token_usage': {k: usage[k] for k in (
        'prompt_tokens', 'completion_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens'
    ) if k in usage} if usage else None,
        'sdk_estimated_cost_usd': snapshot.get('accumulated_cost') if usage else None,
        'billed_cost': None}


def complete_pass(tests, expected, *, expected_deselected=0):
    """Require the declared number of passes and exact known marker exclusions."""
    return tests['exit_code'] == 0 and single.counts(tests.get('stdout', '')) == {
        'passed': expected, 'failed': 0, 'skipped': 0,
        'deselected': expected_deselected, 'errors': 0}


def checked_id(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('IDs must use 1-64 letters, numbers, underscores or hyphens.')
    return value


def catalog():
    return json.loads((DATA / 'catalog.json').read_text())['tasks']


def load_task(task_id):
    if task_id not in catalog():
        raise ValueError('Unknown task. Run: uv run python suite_agent.py list')
    return json.loads((DATA / task_id / 'task.json').read_text())


def check_fixture(task):
    fixture = DATA / task['id'] / 'fixture'
    expected = json.loads((DATA / task['id'] / 'fixture-sha256.json').read_text())
    actual = {str(p.relative_to(fixture)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in fixture.rglob('*') if p.is_file()
              and not set(p.relative_to(fixture).parts) & {'__pycache__', '.pytest_cache'}}
    if actual != expected:
        raise RuntimeError(f"Task fixture changed: {task['id']}. Restore it from the package.")
    return fixture


class Sandbox(BaseSandbox):
    """Log tool success/failure, including missing-file reads, without file contents."""
    def __init__(self, repo, image_id, allowed_files, log=None):
        super().__init__(repo, image_id, allowed_files, log=None)
        self.trace_log = log
        self.tool_count = 0
        control.register_container(self.name)

    def request(self, operation, path='', content='', export=None):
        started, result, error = time.perf_counter(), None, None
        control.progress("tool", operation=operation, path=path)
        try:
            result = super().request(operation, path, content, export)
            if (operation == 'write_file' and result.get('exit_code') == 0
                    and self.trace_log is not None):
                diagnostics.checkpoint(self.trace_log.parent, self)
            return result
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            if operation in MODEL_OPERATIONS:
                self.tool_count += 1
            control.progress("tool_complete", operation=operation,
                             **({"tool_count": self.tool_count} if self.trace_log else {}))
            if self.trace_log:
                row = {'operation': operation, 'path': path,
                       'latency_seconds': round(time.perf_counter() - started, 3),
                       'exit_code': result.get('exit_code') if result else None,
                       'status': 'error' if error or result is None else
                                 'nonzero' if result.get('exit_code', 0) else 'ok'}
                if error:
                    row['error_type'] = error
                with self.trace_log.open('a') as stream:
                    stream.write(json.dumps(row) + '\n')



def quality_for_run(run_dir):
    """Add derived review hints without changing historical model/test evidence."""
    try:
        path = run_dir / 'patch.diff'
        if not path.is_file() or not path.stat().st_size:
            return {'status': 'not_available', 'reason': 'no_final_patch'}
        if path.stat().st_size > 2000000:
            return {'status': 'not_available', 'reason': 'patch_size_limit'}
        patch_text = path.read_text()
        manifest = control.read_json(run_dir / 'manifest.json')
        task = manifest.get('task')
        if not task:
            # Early review ZIPs omitted manifests. Reconstruct only from a known
            # catalog task whose exact upstream base is recorded in verification.
            verification = control.read_json(run_dir / 'verification.json')
            task = load_task(verification['task_id'])
            if verification.get('upstream_base_commit') != task['base_commit']:
                raise ValueError('Historical source base is not confirmed')
        fixture = check_fixture(task)
        fingerprint = patch_quality.patch_fingerprint(patch_text)
        previous = control.read_json(run_dir / 'patch-quality.json')
        if (previous.get('patch_sha256') == fingerprint
                and previous.get('policy_version') == patch_quality.POLICY_VERSION
                and previous.get('base_commit') == task.get('base_commit')):
            return previous
        result = patch_quality.analyze_patch(fixture, patch_text, task['allowed_files'])
        result.update(patch_sha256=fingerprint, base_commit=task.get('base_commit'))
        control.write_json(run_dir / 'patch-quality.json', result)
        return result
    except Exception as exc:
        # Review hints are observational; failure to derive them cannot change
        # a test result or hide the primary model/verifier exception.
        return {'status': 'not_available', 'error_type': diagnostics.safe_error_type(exc)}


def review_fields(run_dir, agent, worker):
    quality = quality_for_run(run_dir)
    return {'patch_quality_status': quality.get('status'),
            'patch_quality_flags': ';'.join(quality.get('flags', [])),
            'patch_added_lines': quality.get('added_lines'),
            'patch_deleted_lines': quality.get('deleted_lines'),
            'root_error_type': agent.get('root_error_type'),
            'error_chain_types': ' > '.join(agent.get('error_chain_types', [])),
            'conversation_error_code': agent.get('conversation_error_code'),
            'failure_category': agent.get('failure_category'),
            **diagnostics.candidate_metadata(run_dir), **control.clock_metadata(worker)}


def prepare(run_dir, image, task_id):
    if run_dir.exists():
        raise ValueError('Run already exists. Choose a fresh --id.')
    control.progress("prepare")
    task = load_task(task_id)
    fixture = check_fixture(task)
    run_dir.mkdir(parents=True)
    repo = run_dir / 'workspace'
    shutil.copytree(fixture, repo, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    base = repair.initialize_git(repo)
    with Sandbox(repo, image, task['allowed_files']) as box:
        baseline = box.request('run_tests')
    output = baseline.get('stdout', '') + baseline.get('stderr', '')
    (run_dir / 'baseline-tests.txt').write_text(output)
    expected = {'failed': task['baseline_failed'], 'passed': task['baseline_passed'],
                'skipped': 0, 'deselected': task.get('baseline_deselected', 0), 'errors': 0}
    if baseline['exit_code'] != 1 or single.counts(output) != expected:
        raise RuntimeError('Unexpected baseline; inspect baseline-tests.txt before calling the model.')
    if any(marker not in output for marker in task['regression_markers']):
        raise RuntimeError('The expected regression tests did not fail.')
    manifest = {'task': task, 'baseline_commit': base, 'baseline_returncode': baseline['exit_code'],
                'image_id': image, 'created_at': datetime.now(timezone.utc).isoformat(),
                'harness': task.get('harness_id', 'three-task-pilot-v1')}
    control.write_json(run_dir / 'manifest.json', manifest)
    return manifest


def verify(run_dir, image):
    control.progress("verify")
    manifest = repair.load_run(run_dir)
    task, repo = manifest['task'], run_dir / 'workspace'
    if image != manifest['image_id']:
        raise RuntimeError('Image differs from prepare; use the original image or prepare a fresh run.')
    fixture = check_fixture(task)
    common = ['git', 'diff', '--no-ext-diff', '--no-textconv', '--no-renames']
    changed = repair.command(common + ['--name-only', manifest['baseline_commit']], repo).stdout.splitlines()
    patch = repair.command(common + ['--binary', manifest['baseline_commit']], repo).stdout
    untracked = repair.command(['git', 'ls-files', '--others', '--exclude-standard'], repo).stdout.splitlines()
    (run_dir / 'patch.diff').write_text(patch)
    result = {'task_id': task['id'], 'verification_status': 'failed', 'changed_files': changed,
              'untracked_files': untracked, 'baseline_returncode': manifest['baseline_returncode'],
              'verification_backend': 'docker', 'image_id': image,
              'expected_test_count': task['verification_test_count'],
              'expected_deselected_count': task.get('verification_deselected', 0), 'issue_url': task['issue_url'],
              'upstream_base_commit': task['base_commit']}
    if not changed:
        result['reason'] = 'no_patch'
    elif set(changed) - set(task['allowed_files']) or untracked:
        result['reason'] = 'patch_scope_violation'
    else:
        with tempfile.TemporaryDirectory(prefix='suite-verify-') as tmp:
            fresh = Path(tmp) / 'repo'
            shutil.copytree(fixture, fresh, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
            repair.initialize_git(fresh)
            applied = repair.command(['git', 'apply', '--whitespace=error'], fresh,
                                     check=False, input_text=patch)
            if applied.returncode:
                result['reason'] = 'patch_apply_failed'
            else:
                shutil.copy2(DATA / task['id'] / 'supplemental.py', fresh / 'tests/test_supplemental.py')
                with Sandbox(fresh, image, task['allowed_files']) as box:
                    tests = box.request('run_tests')
                output = tests.get('stdout', '') + tests.get('stderr', '')
                (run_dir / 'verification-tests.txt').write_text(output)
                passed = complete_pass(tests, task['verification_test_count'],
                                       expected_deselected=task.get('verification_deselected', 0))
                result.update(test_returncode=tests['exit_code'], test_counts=single.counts(output),
                              verification_status='passed' if passed else 'failed',
                              reason='public_and_supplemental_tests_passed' if passed else 'tests_failed_or_incomplete')
    control.write_json(run_dir / 'verification.json', result)
    quality_for_run(run_dir)
    return result


def run_agent(run_dir, *, max_output_tokens=None):
    manifest = repair.load_run(run_dir)
    if (run_dir / 'agent-run.json').exists():
        raise ValueError('Run already started. Use a fresh --id.')
    image = get_image_for_task(manifest['task'])
    if image != manifest['image_id']:
        raise RuntimeError('Image changed since prepare. Prepare a fresh run.')
    task, repo = manifest['task'], run_dir / 'workspace'
    check_fixture(task)
    if repair.command(['git', 'status', '--porcelain'], repo).stdout.strip():
        raise RuntimeError('Workspace changed since prepare. Prepare a fresh run.')
    if repair.command(['git', 'rev-parse', 'HEAD'], repo).stdout.strip() != manifest['baseline_commit']:
        raise RuntimeError('Baseline commit changed. Prepare a fresh run.')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    if not os.getenv('OPENHANDS_SUBSCRIPTION_MODEL'):
        os.environ['OPENHANDS_SUBSCRIPTION_MODEL'] = 'gpt-5.6-sol'
    iterations = int(os.getenv('REPAIR_MAX_ITERATIONS', '20'))
    if not 1 <= iterations <= 100:
        raise ValueError('REPAIR_MAX_ITERATIONS must be between 1 and 100.')
    control.progress("auth")
    llm, auth = repair.configured_llm()
    llm, budget = control.configure_llm(llm, auth, max_output_tokens)
    from openhands.sdk import Conversation
    report = {'mode': 'live_agent', 'backend': 'docker', 'task_id': task['id'],
              'model': llm.model, 'auth': auth, 'image_id': image, 'max_iterations': iterations,
              'execution_status': 'started', 'harness': task.get('harness_id', 'three-task-pilot-v1'),
              'workflow': 'iterative', 'model_attempt_started': False,
              'diagnostics_policy_version': 1, 'stage': 'model', **budget}
    control.write_json(run_dir / 'agent-run.json', report)
    started = time.perf_counter()
    try:
        with Sandbox(repo, image, task['allowed_files'], run_dir / 'tool-calls.jsonl') as sandbox:
            agent = make_agent(llm, sandbox)
            llm = agent.llm
            def on_event(event):
                control.progress('agent_event', event_type=type(event).__name__)
                row = {'timestamp': datetime.now(timezone.utc).isoformat(),
                       'event_type': type(event).__name__}
                if type(event).__name__ == 'ConversationErrorEvent':
                    row['error_code'] = diagnostics.safe_error_type(event.code)
                    report['conversation_error_code'] = row['error_code']
                    report['root_error_type'] = row['error_code']
                    report['failure_category'] = diagnostics.category_for_types([row['error_code']])
                    control.write_json(run_dir / 'agent-run.json', report)
                with (run_dir / 'events.jsonl').open('a') as stream:
                    stream.write(json.dumps(row) + '\n')
            with tempfile.TemporaryDirectory(prefix='suite-control-') as control_workspace:
                conversation = Conversation(agent=agent, workspace=control_workspace,
                                            callbacks=[on_event], max_iteration_per_run=iterations)
                try:
                    conversation.send_message(f"Repair this issue: {task['issue']}\n"
                        f"Allowed source files: {task['allowed_files']}. Use sandbox_repo to inspect "
                        "the repository and run_tests to reproduce the failure first. Make the smallest "
                        "correct source change using write_file (complete replacement text), then rerun "
                        "the tests. Do not modify tests or configuration. Explain the root cause, "
                        "changes, and observed results.")
                    report['model_attempt_started'] = True
                    control.write_json(run_dir / 'agent-run.json', report)
                    control.progress('model', model_attempt_started=True)
                    conversation.run()
                    report['execution_status'] = conversation.state.execution_status.value
                    report['stage'] = 'export'
                    sandbox.export_sources()
                except BaseException:
                    diagnostics.checkpoint(run_dir, sandbox)
                    try:
                        conversation.close()
                    except Exception as close_error:
                        report['close_error_type'] = diagnostics.safe_error_type(close_error)
                    raise
                else:
                    conversation.close()
        report['stage'] = 'verify'
        result = verify(run_dir, image)
        report['stage'] = 'complete'
        report['verification_status'] = result['verification_status']
        control.progress('complete')
        return result
    except Exception as exc:
        report.update(execution_status='error', **diagnostics.exception_metadata(exc))
        raise
    finally:
        report.update(diagnostics.candidate_metadata(run_dir))
        report['latency_seconds'] = round(time.perf_counter() - started, 3)
        try:
            report.update(usage_snapshot(llm))
        except Exception as metric_error:
            report['usage_error_type'] = type(metric_error).__name__
        control.write_json(run_dir / 'agent-run.json', report)


def smoke(image, task_ids):
    for task_id in task_ids:
        task = load_task(task_id)
        with tempfile.TemporaryDirectory(prefix='suite-smoke-') as tmp:
            run = Path(tmp) / 'run'
            prepare(run, image, task_id)
            if verify(run, image)['reason'] != 'no_patch':
                raise RuntimeError('No-patch control failed.')
            repair.command(['git', 'apply', '--whitespace=error'], run / 'workspace',
                           input_text=(DATA / task_id / 'reference.patch').read_text())
            result = verify(run, image)
            if result['verification_status'] != 'passed':
                print((run / 'verification-tests.txt').read_text())
                raise RuntimeError(f'Reference patch failed: {task_id}')
        print(f"SMOKE PASSED {task_id}: baseline {task['baseline_failed']} failed / "
              f"{task['baseline_passed']} passed; reference {task['verification_test_count']} passed; "
              f"declared deselections {task.get('verification_deselected', 0)}.", flush=True)
    print('All selected smoke checks PASSED. No model was called; these are not Agent results.')


def read_json(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def summarize_run(run_id, job=None):
    checked_id(run_id)
    run = RUNS / run_id
    agent, verify_result = read_json(run / 'agent-run.json'), read_json(run / 'verification.json')
    task_id = agent.get('task_id') or verify_result.get('task_id') or (job or {}).get('task_id')
    live = agent.get('model_attempt_started', agent.get('mode') == 'live_agent')
    worker = control.read_json(control.worker_directory(RUNS, run_id, 'run') / 'process.json')
    status = 'pending'
    if live and worker.get('status') in {'timed_out', 'interrupted', 'cleanup_failed', 'termination_failed'}:
        status = worker['status']
    elif not live and worker.get('status') in {'timed_out', 'interrupted', 'cleanup_failed', 'termination_failed', 'launch_error'}:
        status = 'agent_not_started'
    elif live:
        output = (run / 'verification-tests.txt').read_text() if (run / 'verification-tests.txt').is_file() else ''
        complete = complete_pass({'exit_code': verify_result.get('test_returncode'), 'stdout': output},
                                 verify_result.get('expected_test_count'),
                                 expected_deselected=verify_result.get('expected_deselected_count', 0))
        if verify_result.get('verification_status') == 'passed' and complete and agent.get('execution_status') != 'error':
            status = 'passed'
        elif verify_result or agent.get('execution_status') == 'error':
            status = 'failed'
        else:
            status = 'incomplete'
    elif job and job.get('prepare_returncode') not in (None, 0):
        status = 'prepare_failed'
    elif agent:
        status = 'not_live_agent'
    elif job and job.get('agent_returncode') not in (None, 0):
        status = 'agent_not_started'
    calls_file = run / 'tool-calls.jsonl'
    calls, partial_trace = control.read_jsonl(calls_file)
    model_calls = [x for x in calls if x.get('operation') in MODEL_OPERATIONS]
    errors_known = bool(model_calls) and all('status' in x for x in model_calls)
    return {'run_id': run_id, 'task_id': task_id, 'status': status, 'live_agent_started': live,
            'model': agent.get('model'), 'image_id': agent.get('image_id'),
            'max_iterations': agent.get('max_iterations'), 'latency_seconds': agent.get('latency_seconds'),
            'model_tool_calls': len(model_calls),
            'write_calls': sum(x['operation'] == 'write_file' for x in model_calls),
            'test_calls': sum(x['operation'] == 'run_tests' for x in model_calls),
            'tool_errors': sum(x['status'] == 'error' or
                               (x['operation'] != 'run_tests' and x.get('exit_code', 0) != 0)
                               for x in model_calls) if errors_known else None,
            'tests_passed': verify_result.get('test_counts', {}).get('passed'),
            'reason': verify_result.get('reason') or agent.get('error_type'),
            'trace_incomplete': partial_trace, **control.budget_metadata(agent),
            **review_fields(run, agent, worker),
            'worker_status': worker.get('status'),
            'token_count': (agent['sdk_token_usage'].get('prompt_tokens', 0) +
                            agent['sdk_token_usage'].get('completion_tokens', 0))
                           if agent.get('sdk_token_usage') else None,
            'sdk_estimated_cost_usd': agent.get('sdk_estimated_cost_usd'), 'billed_cost': None}


def write_report(prefix, suite):
    jobs = suite['jobs']
    rows = [summarize_run(x['run_id'], x) for x in jobs]
    rows += [summarize_run(run_id) for run_id in suite.get('include', [])]
    live = [x for x in rows if x['live_agent_started']]
    passed = [x for x in live if x['status'] == 'passed']
    complete = all(x['status'] not in {'pending', 'incomplete'} for x in rows)
    groups = {(x['model'], x['image_id'], x['max_iterations']) for x in live}
    latencies = [x['latency_seconds'] for x in live if isinstance(x['latency_seconds'], (int, float))]
    report = {'scope': '3-task, single-repository historical pilot; not a general benchmark',
              'rows': rows, 'planned_or_included_rows': len(rows), 'live_attempts_started': len(live),
              'successful_live_attempts': len(passed),
              'observed_success_fraction': len(passed) / len(live) if live else None,
              'all_rows_complete': complete, 'same_model_image_iteration_limit': len(groups) <= 1,
              'unique_tasks_started': len({x['task_id'] for x in live}),
              'median_latency_seconds': statistics.median(latencies) if latencies else None,
              'notes': ['All started live attempts remain in the denominator, including errors and incomplete attempts.',
                        'Preparation failures are reported separately and have not started a live Agent.',
                        'Repeated attempts are not extra unique tasks. Smoke checks are excluded.',
                        'Token counts appear only where SDK usage was recorded; actual billed cost is unknown.',
                        'Legacy tool logs lack outcomes, so tool_errors may be unknown.',
                        'Quality flags are heuristic review hints, separate from test results.',
                        'Saved candidates are unverified and never promote failed attempts to success.',
                        'Clock gaps suggest suspend or clock adjustment; they do not prove sleep.',
                        'Latency includes Agent execution, cleanup and independent verification, excluding prepare.']}
    RUNS.mkdir(exist_ok=True)
    control.write_json(RUNS / f'{prefix}-summary.json', report)
    with (RUNS / f'{prefix}-summary.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ['run_id'])
        writer.writeheader()
        writer.writerows(rows)
    print('\nRun | Task | Result | Seconds | Tool calls | Tests passed', flush=True)
    for row in rows:
        print(' | '.join(str(row[k]) for k in ['run_id', 'task_id', 'status', 'latency_seconds', 'model_tool_calls', 'tests_passed']), flush=True)
    print(f"Live attempts passed: {len(passed)}/{len(live)}; unique tasks: {report['unique_tasks_started']}; complete: {complete}", flush=True)
    print(f'Reports: runs/{prefix}-summary.json and runs/{prefix}-summary.csv', flush=True)
    return report


def batch(prefix, task_ids, include):
    checked_id(prefix)
    if not task_ids or len(set(task_ids)) != len(task_ids) or len(set(include)) != len(include):
        raise ValueError('Select unique task IDs and included run IDs.')
    jobs = [{'task_id': task_id, 'run_id': checked_id(f'{prefix}-{task_id}'),
             'prepare_returncode': None, 'agent_returncode': None} for task_id in task_ids]
    for task_id in task_ids:
        check_fixture(load_task(task_id))
    for run_id in include:
        checked_id(run_id)
        if not (RUNS / run_id / 'agent-run.json').is_file():
            raise ValueError(f'Included run does not exist: {run_id}')
    suite_file = RUNS / f'{prefix}-suite.json'
    if suite_file.exists() or any((RUNS / x['run_id']).exists() for x in jobs):
        raise ValueError('Batch prefix or run IDs already exist. Choose a fresh --prefix.')
    if set(include) & {x['run_id'] for x in jobs}:
        raise ValueError('A run cannot be both included and newly scheduled.')
    for task_id in task_ids:
        get_image_for_task(load_task(task_id))
    suite = {'created_at': datetime.now(timezone.utc).isoformat(), 'jobs': jobs, 'include': include}
    RUNS.mkdir(exist_ok=True)
    control.write_json(suite_file, suite)
    try:
        for job in jobs:
            print(f"\nStarting {job['run_id']}", flush=True)
            for action, key in [('prepare', 'prepare_returncode'), ('run', 'agent_returncode')]:
                args = [sys.executable, str(Path(__file__).resolve()), action,
                        '--task', job['task_id'], '--id', job['run_id'], '--data-dir', str(DATA)]
                limits = control.Limits.from_env()
                job[key] = control.supervise(args + ['--worker'], cwd=ROOT,
                    directory=control.worker_directory(RUNS, job['run_id'], action),
                    timeout=limits.prepare_seconds if action == 'prepare' else limits.run_seconds)
                control.write_json(suite_file, suite)
                if action == 'prepare' and job[key] != 0:
                    break
            write_report(prefix, suite)
            worker = control.read_json(control.worker_directory(RUNS, job['run_id'], action) / 'process.json')
            if worker.get('status') in {'cleanup_failed', 'termination_failed'}:
                break
    finally:
        control.write_json(suite_file, suite)
        report = write_report(prefix, suite)
    return 0 if report['all_rows_complete'] and all(x['status'] == 'passed' for x in report['rows']) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'smoke', 'prepare', 'run', 'verify', 'batch', 'report'])
    parser.add_argument('--task', default='itsdangerous-126')
    parser.add_argument('--tasks', nargs='+')
    parser.add_argument('--id', default='suite-001')
    parser.add_argument('--prefix', default='pilot-001')
    parser.add_argument('--include', nargs='*', default=[])
    parser.add_argument('--data-dir')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    limits = control.Limits.from_env()
    if args.data_dir:
        configure_task_directory(args.data_dir)
    checked_id(args.id)
    checked_id(args.prefix)
    if args.action == 'list':
        for task_id in catalog():
            task = load_task(task_id)
            print(f"{task_id}: {task['title']} | baseline {task['baseline_failed']} failed, "
                  f"{task['baseline_passed']} passed | verifier {task['verification_test_count']} tests")
        return 0
    if args.action == 'report':
        suite = read_json(RUNS / f'{args.prefix}-suite.json')
        if not suite:
            raise ValueError('Batch record not found. Check --prefix.')
        write_report(args.prefix, suite)
        return 0
    if args.action == 'batch':
        return batch(args.prefix, args.tasks or catalog(), args.include)
    if args.action in {'prepare', 'run', 'smoke'} and not args.worker:
        command = [sys.executable, str(Path(__file__).resolve()), args.action,
                   '--task', args.task, '--id', args.id, '--data-dir', str(DATA), '--worker']
        if args.tasks:
            command += ['--tasks', *args.tasks]
        code = control.supervise(command, cwd=ROOT,
            directory=control.worker_directory(RUNS, args.id, args.action),
            timeout=limits.prepare_seconds if args.action == 'prepare' else limits.run_seconds)
        if args.action == 'smoke' and code == 0:
            print('SMOKE PASSED: selected baseline/reference checks; no model was called.')
        return code
    image_task = load_task((args.tasks or catalog())[0]) if args.action == 'smoke' else (
        load_task(args.task) if args.action == 'prepare' else repair.load_run(RUNS / args.id)['task'])
    image = get_image_for_task(image_task)
    if args.action == 'smoke':
        smoke(image, args.tasks or catalog())
        return 0
    run = RUNS / args.id
    if args.action == 'prepare':
        task = prepare(run, image, args.task)['task']
        print(f"Prepared {args.id}: {task['baseline_failed']} failed, {task['baseline_passed']} passed.")
        return 0
    if repair.load_run(run)['task']['id'] != args.task:
        raise ValueError('--task does not match the prepared run.')
    result = run_agent(run) if args.action == 'run' else verify(run, image)
    print(json.dumps(result, indent=2))
    return 0 if result['verification_status'] == 'passed' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print('Interrupted. Use report to inspect saved progress before starting a new batch.', file=sys.stderr)
        raise SystemExit(130)
