"""Paired one-shot / iterative workflows on two historical python-dotenv tasks."""
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
import zipfile

import repair
import execution_control as control
import run_diagnostics as diagnostics
import suite_agent as core

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'comparison_tasks'
IMAGE = 'code-repair-python:dotenv-comparison-v1'
DOCKERFILE = ROOT / 'Dockerfile.comparison'
OUTPUT_CAP = 8192
WORKFLOWS = ('oneshot', 'iterative')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON keys are not accepted.')
        result[key] = value
    return result


def parse_submission(text, allowed_files):
    text = text.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.*)\n```', text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    if len(text) > 150000:
        raise ValueError('Submission exceeds size limit.')
    obj = json.loads(text, object_pairs_hook=unique_object)
    if not isinstance(obj, dict) or set(obj) != {'files'} or not isinstance(obj['files'], dict):
        raise ValueError('Expected exactly one JSON object: {"files": {"relative/path.py": "full content"}}.')
    files = obj['files']
    if not files:
        raise ValueError('The model submitted no source files.')
    if set(files) - set(allowed_files):
        raise ValueError('Submission attempts to modify a non-allowlisted file.')
    if any(not isinstance(value, str) or len(value) > 30000 for value in files.values()):
        raise ValueError('Source content must be text within the existing 30000-character limit.')
    return files


def build_prompt(run_dir, task):
    fixture = core.check_fixture(task)
    context = {}
    for relative in task['context_files']:
        if '..' in Path(relative).parts or Path(relative).is_absolute():
            raise ValueError('Invalid trusted context path.')
        content = (fixture / relative).read_text()
        if len(content) > 30000:
            raise ValueError('Context file exceeds the repository read limit.')
        context[relative] = content
    baseline = (run_dir / 'baseline-tests.txt').read_text()
    prompt = (
        f"Repair this issue: {task['issue']}\nAllowed source files: {task['allowed_files']}.\n"
        "You have one response and no tools or test feedback after submitting. "
        "Use the supplied original source, selected public tests and baseline failure output. "
        "Make the smallest correct source change. Return ONLY JSON with exactly this shape: "
        '{"files": {"relative/source/path.py": "complete replacement file text"}}. '
        "Include only source files that need changes. Do not include tests or configuration, "
        "shell commands, a diff, or an explanation outside the JSON.\n"
        "Repository files (JSON mapping from relative paths to original content):\n"
        + json.dumps(context, ensure_ascii=False)
        + '\nPublic baseline test output:\n' + baseline
    )
    if len(prompt) > 160000:
        raise ValueError('One-shot context exceeds the documented input limit.')
    return prompt


def invoke_once(llm, messages):
    # The SDK owns subscription transport, token refresh and retries. This is one logical request.
    if llm.uses_responses_api():
        return llm.responses(messages=messages, tools=None, store=False, on_token=lambda token: None)
    return llm.completion(messages=messages, tools=None, on_token=lambda token: None)


def response_text(response):
    if response.message.tool_calls:
        raise ValueError('The one-shot workflow does not accept tool calls.')
    return ''.join(getattr(block, 'text', '') for block in response.message.content)


def oneshot(run_dir):
    manifest = repair.load_run(run_dir)
    task, repo = manifest['task'], run_dir / 'workspace'
    if (run_dir / 'agent-run.json').exists():
        raise ValueError('Run already started; choose a fresh ID.')
    image = core.get_image_for_task(task)
    if image != manifest['image_id']:
        raise RuntimeError('Image changed since prepare.')
    if repair.command(['git', 'status', '--porcelain'], repo).stdout.strip():
        raise RuntimeError('Workspace changed since prepare.')
    if repair.command(['git', 'rev-parse', 'HEAD'], repo).stdout.strip() != manifest['baseline_commit']:
        raise RuntimeError('Baseline commit changed since prepare.')
    prompt = build_prompt(run_dir, task)
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    if not os.getenv('OPENHANDS_SUBSCRIPTION_MODEL'):
        os.environ['OPENHANDS_SUBSCRIPTION_MODEL'] = 'gpt-5.6-sol'
    control.progress('auth')
    llm, auth = repair.configured_llm()
    llm, budget = control.configure_llm(llm, auth, OUTPUT_CAP)
    from openhands.sdk import Message, TextContent
    report = {'mode': 'live_baseline', 'workflow': 'oneshot', 'backend': 'docker',
              'task_id': task['id'], 'model': llm.model, 'auth': auth, 'image_id': image,
              'max_iterations': 1, 'model_attempt_started': False, **budget,
              'logical_llm_requests': 1, 'execution_status': 'started', 'stage': 'model',
              'diagnostics_policy_version': 1,
              'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
              'context_files': task['context_files'], 'harness': 'paired-workflows-v1'}
    control.write_json(run_dir / 'agent-run.json', report)
    started = time.perf_counter()
    try:
        report['model_attempt_started'] = True
        control.write_json(run_dir / 'agent-run.json', report)
        control.progress('model', model_attempt_started=True)
        result = invoke_once(llm, [Message(role='user', content=[TextContent(text=prompt)])])
        report['stage'] = 'parse'
        control.progress('parse')
        submission = response_text(result)
        (run_dir / 'model-submission.txt').write_text(submission)
        files = parse_submission(submission, task['allowed_files'])
        control.write_json(run_dir / 'submission.json', {'files': files})
        report['stage'] = 'apply'
        control.progress('apply')
        with core.Sandbox(repo, image, task['allowed_files'], run_dir / 'tool-calls.jsonl') as sandbox:
            for path, content in files.items():
                written = sandbox.request('write_file', path, content)
                if written['exit_code']:
                    raise RuntimeError('Container rejected a submitted source file.')
            sandbox.export_sources()
        report['stage'] = 'verify'
        verification = core.verify(run_dir, image)
        report.update(execution_status='finished', verification_status=verification['verification_status'], stage='complete')
        control.progress('complete')
        return verification
    except Exception as exc:
        report.update(execution_status='error', **diagnostics.exception_metadata(exc))
        raise
    finally:
        report['latency_seconds'] = round(time.perf_counter() - started, 3)
        try:
            report.update(core.usage_snapshot(llm))
        except Exception as metric_error:
            report['usage_error_type'] = type(metric_error).__name__
        control.write_json(run_dir / 'agent-run.json', report)


def read_row(job):
    run = core.RUNS / job['run_id']
    agent, verified = core.read_json(run / 'agent-run.json'), core.read_json(run / 'verification.json')
    started = agent.get('model_attempt_started', agent.get('mode') in {'live_agent', 'live_baseline'})
    worker = control.read_json(control.worker_directory(core.RUNS, job['run_id'], 'run') / 'process.json')
    prepared = control.read_json(control.worker_directory(core.RUNS, job['run_id'], 'prepare') / 'process.json')
    output = (run / 'verification-tests.txt').read_text() if (run / 'verification-tests.txt').is_file() else ''
    manifest = core.read_json(run / 'manifest.json')
    passed = (started and verified.get('verification_status') == 'passed' and agent.get('execution_status') != 'error'
              and core.complete_pass({'exit_code': verified.get('test_returncode'), 'stdout': output},
                                 verified.get('expected_test_count'),
                                 expected_deselected=verified.get('expected_deselected_count', 0)))
    if worker.get('status') in {'timed_out', 'interrupted', 'cleanup_failed', 'termination_failed'}:
        status = worker['status'] if started else 'model_not_started'
    elif passed:
        status = 'passed'
    elif started and (verified or agent.get('execution_status') == 'error' or
                      (worker.get('status') == 'exited' and worker.get('returncode') != 0)):
        status = 'failed'
    elif started:
        status = 'incomplete'
    elif (job.get('prepare_returncode') not in (None, 0) or
          prepared.get('status') in {'timed_out', 'interrupted', 'cleanup_failed', 'termination_failed', 'launch_error'}):
        status = 'prepare_failed'
    elif job.get('run_returncode') not in (None, 0):
        status = 'model_not_started'
    else:
        status = 'pending'
    calls_path = run / 'tool-calls.jsonl'
    calls, partial_trace = control.read_jsonl(calls_path)
    operations = [x for x in calls if x['operation'] in core.MODEL_OPERATIONS]
    row = {'run_id': job['run_id'], 'task_id': job['task_id'], 'workflow': job['workflow'],
           'repeat': job['repeat'], 'status': status, 'model_started': started,
           'task_kind': manifest.get('task', {}).get('task_kind'),
           'model': agent.get('model'), 'image_id': agent.get('image_id'),
           'base_commit': manifest.get('task', {}).get('base_commit') or verified.get('upstream_base_commit'),
           **control.budget_metadata(agent),
           'worker_status': worker.get('status'), 'prepare_worker_status': prepared.get('status'),
           'worker_wall_seconds': worker.get('elapsed_seconds'),
           'cleanup_status': worker.get('cleanup', {}).get('status'),
           'trace_incomplete': partial_trace, **core.review_fields(run, agent, worker),
           'latency_seconds': agent.get('latency_seconds'),
           'tests_passed': verified.get('test_counts', {}).get('passed'),
           'tests_deselected': verified.get('test_counts', {}).get('deselected'),
           'expected_deselected_count': verified.get('expected_deselected_count', 0),
           'source_write_operations': sum(x['operation'] == 'write_file' for x in operations),
           'model_tool_calls': len(operations) if job['workflow'] == 'iterative' else 0,
           'harness_apply_operations': len(operations) if job['workflow'] == 'oneshot' else 0,
           'failure_stage': (worker.get('last_stage') or agent.get('stage')) if status in {'failed', 'timed_out', 'interrupted', 'model_not_started'} else None,
           'failure_type': worker.get('status') if worker.get('status') in {'timed_out', 'interrupted', 'cleanup_failed', 'termination_failed'} else agent.get('error_type'), 'billed_cost': None,
           'sdk_estimated_cost_usd': agent.get('sdk_estimated_cost_usd')}
    usage = agent.get('sdk_token_usage') or {}
    row.update({k: usage.get(k) for k in ('prompt_tokens', 'completion_tokens', 'cache_read_tokens', 'reasoning_tokens')})
    return row


def make_jobs(prefix, task_ids, repeats):
    core.checked_id(prefix)
    if not task_ids or len(task_ids) != len(set(task_ids)) or not 1 <= repeats <= 5:
        raise ValueError('Select unique tasks and 1-5 repeats.')
    jobs = []
    for repeat in range(1, repeats + 1):
        for index, task_id in enumerate(task_ids):
            order = WORKFLOWS if (index + repeat - 1) % 2 == 0 else tuple(reversed(WORKFLOWS))
            for workflow in order:
                jobs.append({'task_id': task_id, 'workflow': workflow, 'repeat': repeat,
                    'run_id': core.checked_id(f'{prefix}-{task_id}-{workflow}-r{repeat}'),
                    'prepare_returncode': None, 'run_returncode': None})
    return jobs


def report(prefix, plan):
    rows = [read_row(job) for job in plan['jobs']]
    groups = {}
    for workflow in WORKFLOWS:
        started = [row for row in rows if row['workflow'] == workflow and row['model_started']]
        passed = [row for row in started if row['status'] == 'passed']
        times = [row['latency_seconds'] for row in started if isinstance(row['latency_seconds'], (int, float))]
        groups[workflow] = {'attempts_started': len(started), 'attempts_passed': len(passed),
            'observed_pass_fraction': len(passed) / len(started) if started else None,
            'median_latency_seconds': statistics.median(times) if times else None,
            'quality_review_required': sum(row['patch_quality_status'] == 'review_required' for row in started),
            'quality_unavailable': sum(row['patch_quality_status'] == 'not_available' for row in started)}
    paired = []
    for key in sorted({(row['task_id'], row['repeat']) for row in rows}):
        pair = {row['workflow']: row for row in rows if (row['task_id'], row['repeat']) == key}
        a, b = pair['oneshot'], pair['iterative']
        fields = ('model', 'image_id', 'base_commit')
        matched = a['model_started'] and b['model_started'] and all(a[f] == b[f] and a[f] is not None for f in fields)
        policy_matched = matched and all(a[f] == b[f] for f in (
            'requested_max_output_tokens_per_call', 'max_output_tokens_per_call', 'output_limit_status'))
        paired.append({'task_id': key[0], 'repeat': key[1],
                       'matched_task_model_image': matched, 'matched_output_limit_policy': policy_matched,
                       'matched_enforced_output_cap': False,
                       'matched_task_model_image_output_cap': False,
                       'oneshot': a['status'], 'iterative': b['status']})
    summary = {'scope': f"{len({row['task_id'] for row in rows})} historical tasks; paired exploratory workflow comparison",
        'rows': rows, 'by_workflow': groups, 'pairs': paired,
        'all_rows_complete': all(row['status'] not in ('pending', 'incomplete') for row in rows),
        'notes': ['Same task snapshot, public tests, model selection, image and independent verifier per pair.',
                  '8192 is the requested configuration, not a verified service-enforced cap. Subscription mode omits it.',
                  'max_output_tokens_per_call is null for subscription/legacy-unverified records; original records are preserved.',
                  'API sdk_configured means a SDK setting, not proof of service enforcement.',
                  'One-shot gets supplied original source and public tests/baseline, with one logical request and no tools.',
                  'Iterative uses repository tools and the configured iteration cap (default 20).',
                  'This is not an equal-total-token or equal-compute comparison; measure resource usage separately.',
                  'New workers default to 300s for the entire run, 60s per SDK request and 2 SDK attempts including the initial attempt.',
                  'SDK retry limits are not a count of all HTTP requests; a worker deadline also covers authentication and verification.',
                  'Legacy runs did not have these new limits. worker_wall_seconds covers setup/auth; latency_seconds retains its older scope.',
                  'No prompt retry after invalid one-shot output; no automatic rerun of timed-out experiments.',
                  'All started model attempts, including failures/incomplete runs, remain in their workflow denominator.',
                  'Repeated attempts are not extra unique tasks. Small historical samples may be memorized.',
                  'Any known marker-based exclusions are declared per task and their exact counts are verified.',
                  'SDK estimated cost is not actual billed cost. Missing usage stays null.',
                  'Quality flags are heuristic review hints, not a correctness score or a merge decision.',
                  'Derived quality files may be added for old patches; original model/test evidence is not rewritten.',
                  'A saved candidate is unverified and does not promote an errored or interrupted attempt.',
                  'New workers also enforce elapsed UTC wall time; large clock gaps may indicate suspend or clock adjustment.']}
    control.write_json(core.RUNS / f'{prefix}-comparison-summary.json', summary)
    with (core.RUNS / f'{prefix}-comparison-summary.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ['run_id'])
        writer.writeheader(); writer.writerows(rows)
    print('\nTask | Workflow | Repeat | Result | Seconds | Tests passed | Quality', flush=True)
    for row in rows:
        print(' | '.join(str(row[k]) for k in ('task_id', 'workflow', 'repeat', 'status', 'latency_seconds', 'tests_passed', 'patch_quality_status')), flush=True)
    for workflow, group in groups.items():
        print(f"{workflow}: {group['attempts_passed']}/{group['attempts_started']} started attempts passed", flush=True)
    print(f'Reports: runs/{prefix}-comparison-summary.json and .csv', flush=True)
    return summary


def compare(prefix, task_ids, repeats):
    jobs = make_jobs(prefix, task_ids, repeats)
    plan_file = core.RUNS / f'{prefix}-comparison-plan.json'
    if plan_file.exists() or any((core.RUNS / job['run_id']).exists() for job in jobs):
        raise ValueError('Prefix/run ID already exists; choose a fresh --prefix.')
    for task_id in task_ids:
        task = core.load_task(task_id)
        core.check_fixture(task)
        core.get_image_for_task(task)
    core.RUNS.mkdir(exist_ok=True)
    plan = {'created_at': datetime.now(timezone.utc).isoformat(), 'jobs': jobs,
            'requested_output_cap_per_call': OUTPUT_CAP, 'limits': control.asdict(control.Limits.from_env()),
            'budget_policy_version': 2, 'paired_order': 'alternating by task/repeat'}
    control.write_json(plan_file, plan)
    try:
        for index, job in enumerate(jobs, 1):
            print(f"\n[{index}/{len(jobs)}] Starting {job['run_id']}", flush=True)
            for action, field in [('prepare', 'prepare_returncode'), ('run', 'run_returncode')]:
                command = [sys.executable, str(Path(__file__).resolve()), action,
                           '--task', job['task_id'], '--workflow', job['workflow'], '--id', job['run_id'],
                           '--data-dir', str(DATA)]
                limits = control.Limits.from_env()
                job[field] = control.supervise(command + ['--worker'], cwd=ROOT,
                    directory=control.worker_directory(core.RUNS, job['run_id'], action),
                    timeout=limits.prepare_seconds if action == 'prepare' else limits.run_seconds)
                control.write_json(plan_file, plan)
                if action == 'prepare' and job[field] != 0:
                    break
            report(prefix, plan)
            worker = control.read_json(control.worker_directory(core.RUNS, job['run_id'], action) / 'process.json')
            if worker.get('status') in {'cleanup_failed', 'termination_failed'}:
                break
    finally:
        control.write_json(plan_file, plan)
        summary = report(prefix, plan)
    if any(row['status'] in ('prepare_failed', 'model_not_started', 'cleanup_failed', 'termination_failed') for row in summary['rows']):
        return 2
    return 0 if summary['all_rows_complete'] else 1


def export_results(prefix):
    core.checked_id(prefix)
    plan = core.read_json(core.RUNS / f'{prefix}-comparison-plan.json')
    if not plan:
        raise ValueError('Comparison plan not found; check --prefix.')
    report(prefix, plan)
    candidates = [core.RUNS / f'{prefix}-comparison-{suffix}' for suffix in (
        'plan.json', 'summary.json', 'summary.csv')]
    for job in plan['jobs']:
        core.checked_id(job['run_id'])
        candidates += [core.RUNS / job['run_id'] / name for name in (
            'agent-run.json', 'patch.diff', 'verification.json', 'verification-tests.txt',
            'baseline-tests.txt', 'tool-calls.jsonl', 'model-submission.txt', 'manifest.json',
            'events.jsonl', 'patch-quality.json', 'candidate.json', 'candidate-save.json',
            'candidate.diff', 'candidate-sources.zip')]
        for action in ('prepare', 'run'):
            candidates += [control.worker_directory(core.RUNS, job['run_id'], action) / name
                           for name in ('process.json', 'progress.json', 'containers.jsonl')]
    output = ROOT / f'{prefix}-review.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in candidates:
            if path.is_file():
                archive.write(path, path.relative_to(core.RUNS.parent))
    print(f'Exported: {output}')
    return output


def main():
    global DATA
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'list', 'smoke', 'compare', 'report', 'export', 'prepare', 'run'])
    parser.add_argument('--task')
    parser.add_argument('--tasks', nargs='+')
    parser.add_argument('--workflow', choices=WORKFLOWS, default='iterative')
    parser.add_argument('--id', default='dotenv-001')
    parser.add_argument('--prefix', default='compare-001')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--data-dir')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    limits = control.Limits.from_env()
    if args.data_dir:
        DATA = Path(args.data_dir).resolve()
    core.configure_task_directory(DATA)
    core.checked_id(args.id); core.checked_id(args.prefix)
    if args.action == 'build':
        with tempfile.TemporaryDirectory(prefix='comparison-image-') as tmp:
            shutil.copy2(DOCKERFILE, Path(tmp) / 'Dockerfile')
            subprocess.run(['docker', 'build', '-t', IMAGE, tmp], check=True)
        return 0
    if args.action == 'list':
        for task_id in core.catalog():
            task = core.load_task(task_id)
            print(f"{task_id}: {task['title']} | baseline {task['baseline_failed']} failed, "
                  f"{task['baseline_passed']} passed | verifier {task['verification_test_count']} tests")
        return 0
    if args.action == 'report':
        plan = core.read_json(core.RUNS / f'{args.prefix}-comparison-plan.json')
        if not plan:
            raise ValueError('Comparison plan not found; check --prefix.')
        report(args.prefix, plan)
        return 0
    if args.action == 'export':
        export_results(args.prefix)
        return 0
    if args.action == 'compare':
        return compare(args.prefix, args.tasks or core.catalog(), args.repeats)
    if args.action in {'prepare', 'run', 'smoke'} and args.task is None:
        args.task = (args.tasks or core.catalog())[0]
    if args.action in {'prepare', 'run', 'smoke'} and not args.worker:
        command = [sys.executable, str(Path(__file__).resolve()), args.action,
                   '--task', args.task, '--workflow', args.workflow, '--id', args.id,
                   '--data-dir', str(DATA), '--worker']
        if args.tasks:
            command += ['--tasks', *args.tasks]
        code = control.supervise(command, cwd=ROOT,
            directory=control.worker_directory(core.RUNS, args.id, args.action),
            timeout=limits.prepare_seconds if args.action == 'prepare' else limits.run_seconds)
        if args.action == 'smoke' and code == 0:
            print('SMOKE PASSED: selected baseline/reference checks; no model was called.')
        return code
    task = core.load_task(args.task)
    if args.action == 'smoke':
        tasks = args.tasks or core.catalog()
        core.smoke(core.get_image_for_task(core.load_task(tasks[0])), tasks)
        return 0
    run = core.RUNS / args.id
    if args.action == 'prepare':
        manifest = core.prepare(run, core.get_image_for_task(task), args.task)
        manifest['workflow'] = args.workflow
        control.write_json(run / 'manifest.json', manifest)
        print(f'Prepared {args.id} for {args.workflow}.')
        return 0
    manifest = repair.load_run(run)
    if manifest['task']['id'] != args.task or manifest.get('workflow') != args.workflow:
        raise ValueError('Task/workflow does not match the prepared run.')
    result = oneshot(run) if args.workflow == 'oneshot' else core.run_agent(run, max_output_tokens=OUTPUT_CAP)
    print(json.dumps(result, indent=2))
    return 0 if result['verification_status'] == 'passed' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print('Interrupted; inspect the saved comparison report before starting a new prefix.', file=sys.stderr)
        raise SystemExit(130)
