"""Bounded CLI workers and honest SDK budget metadata (macOS/Linux).

Supervisor metadata excludes credentials, messages, contents and exception text.
Raw SDK output is kept in a local worker.log, excluded from review exports.
The supervisor deadline includes authentication, inference and verification.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}


def read_jsonl(path):
    """A concurrently appended final line may be incomplete; preserve earlier rows."""
    try:
        lines = Path(path).read_text().splitlines()
    except FileNotFoundError:
        return [], False
    rows, partial = [], False
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise ValueError('Invalid interior JSONL record') from None
            partial = True
    return rows, partial


@dataclass(frozen=True)
class Limits:
    run_seconds: int = 300
    prepare_seconds: int = 120
    request_seconds: int = 60
    sdk_attempts: int = 2

    @classmethod
    def from_env(cls):
        fields = {
            'run_seconds': ('REPAIR_RUN_TIMEOUT_SECONDS', 300, 30, 600),
            'prepare_seconds': ('REPAIR_PREPARE_TIMEOUT_SECONDS', 120, 30, 300),
            'request_seconds': ('REPAIR_LLM_TIMEOUT_SECONDS', 60, 5, 120),
            'sdk_attempts': ('REPAIR_LLM_ATTEMPTS', 2, 1, 3),
        }
        values = {}
        for field, (name, default, low, high) in fields.items():
            try:
                value = int(os.getenv(name, str(default)))
            except ValueError:
                raise ValueError(f'{name} must be an integer') from None
            if not low <= value <= high:
                raise ValueError(f'{name} must be between {low} and {high}')
            values[field] = value
        return cls(**values)


def active_directory():
    value = os.getenv('REPAIR_CONTROL_DIR')
    return Path(value) if value else None


def progress(stage, **metadata):
    directory = active_directory()
    if directory is None:
        return
    # Only the caller's structured event metadata is allowed here.
    allowed = {'operation', 'path', 'event_type', 'retry_after_attempt',
               'sdk_max_attempts', 'error_type', 'tool_count', 'model_attempt_started'}
    row = read_json(directory / 'progress.json')
    if stage != 'retry':
        for key in ('retry_after_attempt', 'sdk_max_attempts', 'error_type'):
            row.pop(key, None)
    row.update({k: v for k, v in metadata.items() if k in allowed})
    row.update(stage=stage, updated_at=now())
    write_json(directory / 'progress.json', row)


def register_container(name):
    directory = active_directory()
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'containers.jsonl').open('a') as stream:
            stream.write(json.dumps({'name': name}) + '\n')


def cleanup_containers(directory):
    records, partial = read_jsonl(directory / 'containers.jsonl')
    names = list(dict.fromkeys(row['name'] for row in records
                 if re.fullmatch(r'repair-task-[0-9a-f]{12}', row.get('name', ''))))
    if not names:
        return {'status': 'incomplete_registry' if partial else 'not_needed', 'names': []}
    try:
        subprocess.run(['docker', 'rm', '-f', *names], capture_output=True,
                       text=True, timeout=10, check=False)
        # Already-removed names are harmless. This read-only probe never removes other runs.
        inspection = subprocess.run(['docker', 'ps', '-a', '--format', '{{.Names}}'],
                                    capture_output=True, text=True, timeout=5, check=False)
        if inspection.returncode:
            return {'status': 'unconfirmed', 'names': names}
        remaining = sorted(set(names) & set(inspection.stdout.splitlines()))
        return {'status': 'unconfirmed' if remaining or partial else 'confirmed',
                'names': names, 'remaining': remaining}
    except (OSError, subprocess.SubprocessError) as exc:
        return {'status': 'unconfirmed', 'names': names, 'error_type': type(exc).__name__}


def configure_llm(llm, auth, requested_cap=None):
    limits = Limits.from_env()
    subscription = auth == 'subscription' or llm.is_subscription is True
    updates = {'timeout': limits.request_seconds, 'stream_idle_timeout': limits.request_seconds,
               # SDK 1.53 uses stop_after_attempt(num_retries): includes the initial attempt.
               'num_retries': limits.sdk_attempts, 'retry_min_wait': 1,
               'retry_max_wait': 3, 'retry_multiplier': 1.0}
    if requested_cap is not None:
        updates['max_output_tokens'] = None if subscription else requested_cap
    llm = llm.model_copy(update=updates)
    def retry_listener(attempt, maximum, error):
        progress('retry', retry_after_attempt=attempt, sdk_max_attempts=maximum,
                 error_type=type(error).__name__ if error else None)
    llm.retry_listener = retry_listener
    metadata = {
        'budget_policy_version': 2,
        'requested_max_output_tokens_per_call': requested_cap,
        'max_output_tokens_per_call': None if subscription else requested_cap,
        'output_limit_status': 'unsupported_subscription' if subscription else
                               'sdk_configured' if requested_cap is not None else 'not_requested',
        'output_limit_server_enforced': None,
        'request_timeout_seconds': limits.request_seconds,
        'sdk_max_attempts_per_request': limits.sdk_attempts,
        'sdk_retry_scope': 'SDK retry wrapper; not a count/guarantee of all HTTP requests',
        'wall_timeout_seconds': float(os.environ['REPAIR_WORKER_TIMEOUT_SECONDS'])
                                if active_directory() else None,
    }
    return llm, metadata


def budget_metadata(agent):
    """Normalize old records at report time without rewriting their original evidence."""
    if agent.get('budget_policy_version') == 2:
        return {k: agent.get(k) for k in (
            'requested_max_output_tokens_per_call', 'max_output_tokens_per_call',
            'output_limit_status', 'output_limit_server_enforced', 'request_timeout_seconds',
            'sdk_max_attempts_per_request', 'wall_timeout_seconds')}
    requested = agent.get('max_output_tokens_per_call')
    return {'requested_max_output_tokens_per_call': requested,
            'max_output_tokens_per_call': None,
            'output_limit_status': 'unsupported_subscription' if agent.get('auth') == 'subscription'
                                   else 'legacy_unverified',
            'output_limit_server_enforced': None, 'request_timeout_seconds': None,
            'sdk_max_attempts_per_request': None, 'wall_timeout_seconds': None}


def live_group_pids(group_id):
    """Use ps only to distinguish an empty/zombie-only group from a live EPERM.

    macOS can report EPERM when signalling a zombie-only group. A real denial
    against a live process must still fail visibly. No PID is signalled here.
    """
    result = subprocess.run(['ps', '-axo', 'pid=,pgid=,stat='], capture_output=True,
                            text=True, timeout=3, check=True)
    live = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        pid, group, state = fields[:3]
        if pid.isdigit() and group.isdigit() and int(group) == group_id and not state.startswith('Z'):
            live.add(int(pid))
    return live


def signal_worker_group(process, sig):
    # Reap the direct child before probing/signalling its group again. Otherwise
    # a terminated but unreaped worker can cause macOS killpg to return EPERM.
    process.poll()
    try:
        os.killpg(process.pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        if live_group_pids(process.pid):
            raise
        return False


def stop_process_group(process, grace=3):
    # The worker is a new POSIX session; descendants share its process group.
    active = signal_worker_group(process, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while active and time.monotonic() < deadline:
        active = signal_worker_group(process, 0)
        if not active:
            break
        time.sleep(0.05)
    # Kill remaining descendants even if the immediate worker already exited.
    if active:
        signal_worker_group(process, signal.SIGKILL)
    process.wait(timeout=5)


def supervise(command, *, cwd, directory, timeout, heartbeat=15, grace=3):
    """Run one trusted harness worker. Candidate source still executes only in Docker."""
    if os.name != 'posix':
        raise RuntimeError('Supervised workers currently require macOS or Linux')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'process.json').exists():
        raise ValueError('This worker already started; choose a fresh run ID')
    env = os.environ.copy()
    env.update(REPAIR_CONTROL_DIR=str(directory.resolve()),
               REPAIR_WORKER_TIMEOUT_SECONDS=str(timeout),
               OPENHANDS_SUPPRESS_BANNER='1', PYTHONUNBUFFERED='1')
    record = {'status': 'starting', 'started_at': now(), 'timeout_seconds': timeout,
              'model_attempt_started': False,
              'deadline_clock_policy': 'max_monotonic_and_utc_wall_v1'}
    write_json(directory / 'process.json', record)
    started, started_wall = time.monotonic(), time.time()
    process, interrupted = None, False
    def stop_worker(stop_grace):
        try:
            stop_process_group(process, stop_grace)
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            record.update(status='termination_failed', returncode=2,
                          termination_error_type=type(exc).__name__)
            return False
    try:
        # Raw SDK output can contain source/tool observations. Keep it local, off the review export.
        with (directory / 'worker.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            record.update(status='running', pid=process.pid)
            write_json(directory / 'process.json', record)
            last_heartbeat = float('-inf')
            while True:
                # Conservatively include suspend / forward wall-clock jumps. A
                # backward adjustment cannot extend the monotonic deadline.
                elapsed = max(time.monotonic() - started, time.time() - started_wall)
                # Deadline is authoritative even if a worker exits at the boundary.
                if elapsed >= timeout:
                    if stop_worker(grace):
                        record.update(status='timed_out', returncode=124)
                    break
                code = process.poll()
                if code is not None:
                    if stop_worker(0):
                        record.update(status='exited', returncode=code)
                    break
                if elapsed - last_heartbeat >= heartbeat:
                    state = read_json(directory / 'progress.json')
                    details = ' '.join(f'{k}={state[k]}' for k in (
                        'operation', 'tool_count', 'retry_after_attempt') if k in state)
                    print(f"  {directory.parent.name}/{directory.name}: {elapsed:.0f}/{timeout:g}s "
                          f"stage={state.get('stage', 'startup')} {details}", flush=True)
                    last_heartbeat = elapsed
                time.sleep(min(0.1, max(0.001, timeout - elapsed)))
    except KeyboardInterrupt:
        interrupted = True
        if process is None or stop_worker(grace):
            record.update(status='interrupted', returncode=130)
    except BaseException as exc:
        if process is not None:
            stop_worker(grace)
        if record['status'] != 'termination_failed':
            record.update(status='launch_error', returncode=2, error_type=type(exc).__name__)
        raise
    finally:
        state = read_json(directory / 'progress.json')
        cleanup_started = time.monotonic()
        cleanup = cleanup_containers(directory)
        record.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3),
                      model_attempt_started=state.get('model_attempt_started', False),
                      last_stage=state.get('stage'), cleanup=cleanup,
                      cleanup_seconds=round(time.monotonic() - cleanup_started, 3))
        record.update(clock_metadata(record))
        if record['cleanup']['status'] in {'unconfirmed', 'incomplete_registry'}:
            record['termination_status'] = record['status']
            record['status'], record['returncode'] = 'cleanup_failed', 2
        write_json(directory / 'process.json', record)
        print(f"  Worker {record['status']}: {record.get('returncode')} "
              f"({record['elapsed_seconds']}s); cleanup={record['cleanup']['status']}", flush=True)
        if record['cleanup']['status'] in {'unconfirmed', 'incomplete_registry'}:
            print('  Container cleanup unconfirmed; inspect the process record before another run.', flush=True)
        if record.get('termination_error_type'):
            print('  Worker termination unconfirmed; stop this batch and inspect process.json.', flush=True)
    if interrupted:
        raise KeyboardInterrupt
    return record.get('returncode', 2)


def clock_metadata(record):
    """Diagnostic only: suspend and clock adjustments cannot be distinguished here."""
    result = {'clock_wall_seconds': None, 'clock_gap_seconds': None,
              'clock_gap_detected': None}
    if not record.get('started_at') or not record.get('finished_at'):
        return result
    try:
        wall = (datetime.fromisoformat(record['finished_at']) -
                datetime.fromisoformat(record['started_at'])).total_seconds()
        elapsed = record.get('elapsed_seconds')
        gap = wall - elapsed if isinstance(elapsed, (int, float)) else None
        result.update(clock_wall_seconds=round(wall, 3),
                      clock_gap_seconds=round(gap, 3) if gap is not None else None,
                      clock_gap_detected=abs(gap) > 5 if gap is not None else None)
    except (TypeError, ValueError):
        pass
    return result


def worker_directory(runs, run_id, action):
    return Path(runs) / 'execution-control' / run_id / action
