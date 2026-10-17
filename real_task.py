"""Historical ItsDangerous issue #126, using the existing Docker-only repo tool.

Commands: build, smoke, prepare --id real-001, run --id real-001, verify --id real-001.
Requires the existing repair.py and the corrected Phase 2B docker_agent.py.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

import repair
from docker_agent import Sandbox, cli, make_agent

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'real_tasks'
IMAGE = 'code-repair-python:itsdangerous126'


def get_image():
    if not shutil.which('docker'):
        raise RuntimeError('Docker is missing. Start Docker Desktop first.')
    cli(['info', '--format', '{{.ServerVersion}}'])
    result = cli(['image', 'inspect', IMAGE], check=False)
    if result.returncode:
        raise RuntimeError('Build the real-task image first: uv run python real_task.py build')
    return json.loads(result.stdout)[0]['Id']


def counts(output):
    # Read only the final pytest summary, rather than matching arbitrary earlier text.
    lines = [line for line in output.strip().splitlines() if line.strip()]
    summary = lines[-1] if lines else ''
    return {name: int(match.group(1)) if (match := re.search(r'\b(\d+) ' + name + r'\b', summary)) else 0
            for name in ('passed', 'failed', 'skipped', 'deselected', 'errors')}


def complete_pass(tests, expected):
    return tests['exit_code'] == 0 and counts(tests.get('stdout', '')) == {
        'passed': expected, 'failed': 0, 'skipped': 0, 'deselected': 0, 'errors': 0}


def prepare(run_dir, image):
    if run_dir.exists():
        raise ValueError('Run already exists. Choose a fresh --id.')
    task = json.loads((DATA / 'task.json').read_text())
    run_dir.mkdir(parents=True)
    repo = run_dir / 'workspace'
    shutil.copytree(DATA / 'fixtures' / task['fixture'], repo,
                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    base = repair.initialize_git(repo)
    with Sandbox(repo, image, task['allowed_files']) as box:
        baseline = box.request('run_tests')
    (run_dir / 'baseline-tests.txt').write_text(baseline.get('stdout', '') + baseline.get('stderr', ''))
    expected = {'failed': task['baseline_failed'], 'passed': task['baseline_passed'],
                'skipped': 0, 'deselected': 0, 'errors': 0}
    if baseline['exit_code'] != 1 or counts(baseline.get('stdout', '')) != expected:
        raise RuntimeError('Unexpected baseline; read baseline-tests.txt before calling the model.')
    if 'TestTimestampSigner::test_future_age' not in baseline['stdout']:
        raise RuntimeError('The expected upstream regression did not fail.')
    manifest = {'task': task, 'baseline_commit': base, 'baseline_returncode': baseline['exit_code'],
                'image_id': image, 'created_at': datetime.now(timezone.utc).isoformat()}
    repair.write_json(run_dir / 'manifest.json', manifest)
    return manifest


def verify(run_dir, image):
    manifest = repair.load_run(run_dir)
    repo, task = run_dir / 'workspace', manifest['task']
    common = ['git', 'diff', '--no-ext-diff', '--no-textconv', '--no-renames']
    changed = repair.command(common + ['--name-only', manifest['baseline_commit']], repo).stdout.splitlines()
    patch = repair.command(common + ['--binary', manifest['baseline_commit']], repo).stdout
    untracked = repair.command(['git', 'ls-files', '--others', '--exclude-standard'], repo).stdout.splitlines()
    (run_dir / 'patch.diff').write_text(patch)
    result = {'task_id': task['id'], 'verification_status': 'failed', 'changed_files': changed,
              'untracked_files': untracked, 'baseline_returncode': manifest['baseline_returncode'],
              'verification_backend': 'docker', 'image_id': image,
              'expected_test_count': task['verification_test_count'], 'issue_url': task['issue_url'],
              'upstream_base_commit': task['base_commit']}
    if not changed:
        result['reason'] = 'no_patch'
    elif set(changed) - set(task['allowed_files']) or untracked:
        result['reason'] = 'patch_scope_violation'
    else:
        with tempfile.TemporaryDirectory(prefix='real-task-verify-') as tmp:
            fresh = Path(tmp) / 'repo'
            # Neither the model's workspace nor its test files are reused.
            shutil.copytree(DATA / 'fixtures' / task['fixture'], fresh,
                            ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
            repair.initialize_git(fresh)
            applied = repair.command(['git', 'apply', '--whitespace=error'], fresh,
                                     check=False, input_text=patch)
            if applied.returncode:
                result['reason'] = 'patch_apply_failed'
            else:
                shutil.copy2(DATA / 'evaluator' / task['supplemental_test'], fresh / 'tests')
                with Sandbox(fresh, image, task['allowed_files']) as verifier:
                    tests = verifier.request('run_tests')
                (run_dir / 'verification-tests.txt').write_text(tests.get('stdout', '') + tests.get('stderr', ''))
                passed = complete_pass(tests, task['verification_test_count'])
                result.update(test_returncode=tests['exit_code'], test_counts=counts(tests.get('stdout', '')),
                              verification_status='passed' if passed else 'failed',
                              reason='upstream_and_supplemental_tests_passed' if passed else 'tests_failed_or_incomplete')
    repair.write_json(run_dir / 'verification.json', result)
    return result


def run_agent(run_dir):
    manifest = repair.load_run(run_dir)
    if (run_dir / 'agent-run.json').exists():
        raise ValueError('This run already started. Prepare a new --id.')
    image = get_image()
    if image != manifest['image_id']:
        raise RuntimeError('Image changed since prepare; prepare a new run with the current image.')
    repo = run_dir / 'workspace'
    if repair.command(['git', 'status', '--porcelain'], repo).stdout.strip():
        raise RuntimeError('Workspace changed since prepare. Use a fresh run for a comparable attempt.')
    if repair.command(['git', 'rev-parse', 'HEAD'], repo).stdout.strip() != manifest['baseline_commit']:
        raise RuntimeError('Baseline Git commit changed. Prepare a new run.')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    if not os.getenv('OPENHANDS_SUBSCRIPTION_MODEL'):
        os.environ['OPENHANDS_SUBSCRIPTION_MODEL'] = 'gpt-5.6-sol'
    iterations = int(os.getenv('REPAIR_MAX_ITERATIONS', '20'))
    if not 1 <= iterations <= 100:
        raise ValueError('REPAIR_MAX_ITERATIONS must be between 1 and 100.')
    llm, auth = repair.configured_llm()
    from openhands.sdk import Conversation
    task = manifest['task']
    report = {'mode': 'live_agent', 'backend': 'docker', 'model': llm.model, 'auth': auth,
              'task_id': task['id'], 'image_id': image, 'max_iterations': iterations,
              'execution_status': 'started'}
    repair.write_json(run_dir / 'agent-run.json', report)
    started = time.perf_counter()
    try:
        with Sandbox(repo, image, task['allowed_files'], run_dir / 'tool-calls.jsonl') as sandbox:
            def on_event(event):
                with (run_dir / 'events.jsonl').open('a') as out:
                    out.write(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(),
                                          'event_type': type(event).__name__}) + '\n')
            with tempfile.TemporaryDirectory(prefix='real-task-control-') as control:
                conversation = Conversation(agent=make_agent(llm, sandbox), workspace=control,
                                            callbacks=[on_event], max_iteration_per_run=iterations)
                try:
                    # Only symptom text and source allowlist; reference patch, PR and supplemental tests stay on host.
                    conversation.send_message(f"Repair this issue: {task['issue']}\n"
                        f"Allowed source files: {task['allowed_files']}. Use sandbox_repo to inspect "
                        "the repository and run_tests to reproduce the failure first. Make the smallest "
                        "correct source change using write_file (complete replacement text), then rerun "
                        "the tests. Do not modify tests or configuration. Explain the root cause, "
                        "changes, and observed results.")
                    conversation.run()
                    report['execution_status'] = conversation.state.execution_status.value
                    sandbox.export_sources()
                finally:
                    conversation.close()
        result = verify(run_dir, image)
        report['verification_status'] = result['verification_status']
        return result
    except Exception as exc:
        report.update(execution_status='error', error_type=type(exc).__name__)
        raise
    finally:
        report['latency_seconds'] = round(time.perf_counter() - started, 3)
        repair.write_json(run_dir / 'agent-run.json', report)


def smoke(image):
    with tempfile.TemporaryDirectory(prefix='real-task-smoke-') as tmp:
        run = Path(tmp) / 'run'
        prepare(run, image)
        assert verify(run, image)['reason'] == 'no_patch'
        # Trusted upstream patch is an environment/evaluator check, not an Agent attempt.
        patch = (DATA / 'evaluator' / 'upstream-source.patch').read_text()
        repair.command(['git', 'apply', '--whitespace=error'], run / 'workspace', input_text=patch)
        result = verify(run, image)
        print(json.dumps(result, indent=2))
        if result['verification_status'] != 'passed':
            print((run / 'verification-tests.txt').read_text())
            raise RuntimeError('Upstream reference patch failed the real-task smoke check.')
    print('Real-task smoke PASSED: baseline 1 failed / 414 passed; reference patch 468 passed.')
    print('No LLM was called. This is an environment/evaluator check, not an Agent repair result.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'smoke', 'prepare', 'run', 'verify'])
    parser.add_argument('--id', default='real-001')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.id):
        parser.error('Invalid run ID')
    if args.action == 'build':
        if not shutil.which('docker'):
            raise RuntimeError('Docker is missing.')
        with tempfile.TemporaryDirectory(prefix='real-task-build-') as tmp:
            shutil.copy2(ROOT / 'Dockerfile.real', Path(tmp) / 'Dockerfile')
            subprocess.run(['docker', 'build', '-t', IMAGE, tmp], check=True)
        return 0
    image = get_image()
    if args.action == 'smoke':
        smoke(image)
        return 0
    run = ROOT / 'runs' / args.id
    if args.action == 'prepare':
        prepare(run, image)
        print(f'Prepared {args.id}: real historical issue reproduced (1 failed, 414 passed).')
        return 0
    result = run_agent(run) if args.action == 'run' else verify(run, image)
    print(json.dumps(result, indent=2))
    return 0 if result['verification_status'] == 'passed' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Error: {exc}', file=__import__('sys').stderr)
        raise SystemExit(2)
