"""Phase 2B: model on host, repository operations and verification in Docker.

Copy this file and Dockerfile.repair beside repair.py.
Commands: build | smoke | run --id docker-001
The existing repair.py and .env stay unchanged. Only the trusted fixture is supported.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from typing import Literal
import uuid

import repair

ROOT = Path(__file__).resolve().parent
IMAGE = 'code-repair-python:phase2'
IGNORED = {'.git', '__pycache__', '.pytest_cache'}

# Run the seed writer inside the live container so its tmpfs is visible.
# docker cp's archive API is unsuitable for this tmpfs workspace.
SEED = r'''
import pathlib, sys, tarfile
root = pathlib.Path('/workspace')
with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as archive:
    for member in archive:
        relative = pathlib.PurePosixPath(member.name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid seed archive path')
        target = root / relative
        if not target.resolve().is_relative_to(root):
            raise ValueError('Seed path outside workspace')
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile() and member.size <= 200000:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
        else:
            raise ValueError('Only small regular files/directories are supported')
print('Task files seeded into container workspace.')
'''

# This program runs INSIDE Docker. No model-generated command runs on the host.
OPS = r'''
import base64, hashlib, json, os, pathlib, subprocess, sys
root = pathlib.Path('/workspace')
request = json.loads(sys.argv[1])
def path_for(name):
    p = pathlib.PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts:
        raise ValueError('Use a relative repository path without ..')
    result = root / name
    for ancestor in [result, *result.parents]:
        if ancestor == root: break
        if ancestor.is_symlink(): raise ValueError('Symlinks are not supported')
    if not result.resolve().is_relative_to(root): raise ValueError('Path outside workspace')
    return result
try:
    op = request['operation']
    if op == 'list_files' or op == 'snapshot':
        files = {}
        for p in sorted(root.rglob('*')):
            rel = p.relative_to(root)
            if set(rel.parts) & {'.git', '__pycache__', '.pytest_cache'}: continue
            if p.is_symlink(): raise ValueError('Unexpected symlink')
            if p.is_file():
                data = p.read_bytes()
                if len(data) > 200000: raise ValueError('File exceeds fixture size limit')
                files[str(rel)] = {'sha256': hashlib.sha256(data).hexdigest()}
                if op == 'snapshot' and str(rel) in request.get('export', []):
                    files[str(rel)]['base64'] = base64.b64encode(data).decode()
        response = {'exit_code': 0, 'stdout': '\n'.join(files)} if op == 'list_files' else {'files': files}
    elif op == 'read_file':
        data = path_for(request['path']).read_text()
        if len(data) > 30000: raise ValueError('Read exceeds fixture size limit')
        response = {'exit_code': 0, 'stdout': data}
    elif op == 'write_file':
        if request['path'] not in request['allowed_files']:
            raise ValueError('Only task allowlisted source files may be edited')
        text = request['content']
        if len(text) > 30000: raise ValueError('Write exceeds fixture size limit')
        path_for(request['path']).write_text(text)
        response = {'exit_code': 0, 'stdout': 'Source file updated; run tests to check it.'}
    elif op == 'run_tests':
        try:
            r = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'],
                               cwd=root, capture_output=True, text=True, timeout=20)
            output = r.stdout + r.stderr
            response = {'exit_code': r.returncode, 'stdout': output[:4000] + output[-4000:] if len(output)>8000 else output}
        except subprocess.TimeoutExpired:
            response = {'exit_code': 124, 'stdout': 'Tests exceeded the 20-second timeout.'}
    else:
        raise ValueError('Unsupported operation')
except Exception as exc:
    response = {'exit_code': 2, 'stdout': '', 'stderr': str(exc)}
print(json.dumps(response))
'''


def cli(args, *, input_bytes=None, timeout=45, check=True):
    r = subprocess.run(['docker', *args], input=input_bytes, capture_output=True, timeout=timeout)
    if check and r.returncode:
        detail = r.stderr.decode(errors='replace').strip()[:2000]
        raise RuntimeError(f'docker {args[0]} failed (exit {r.returncode}).\n{detail}')
    return r


def source_files(repo):
    return {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob('*')
            if p.is_file() and not (set(p.relative_to(repo).parts) & IGNORED)}


def archive_files(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        directories = {str(parent) for name in files for parent in PurePosixPath(name).parents
                       if str(parent) != '.'}
        for directory in sorted(directories, key=lambda x: (len(PurePosixPath(x).parts), x)):
            info = tarfile.TarInfo(directory)
            info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o755, 65534, 65534
            archive.addfile(info)
        for name, data in files.items():
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Invalid fixture path')
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.uid, info.gid = len(data), 0o644, 65534, 65534
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


class Sandbox:
    def __init__(self, repo, image_id, allowed_files, log=None):
        self.repo, self.image_id, self.allowed_files, self.log = repo, image_id, allowed_files, log
        self.name = 'repair-task-' + uuid.uuid4().hex[:12]
        self.closed = False

    def __enter__(self):
        try:
            cli(['create', '--name', self.name, '--network', 'none', '--read-only',
                 '--user', '65534:65534', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                 '--pids-limit', '64', '--memory', '512m', '--cpus', '1',
                 '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777',
                 '--tmpfs', '/workspace:rw,nosuid,nodev,noexec,size=32m,mode=1777,uid=65534,gid=65534',
                 self.image_id])
            cli(['start', self.name])
            # Seed through stdin as the same nonroot user that edits the files.
            cli(['exec', '-i', '--workdir', '/workspace', self.name, 'python', '-I', '-c', SEED],
                input_bytes=archive_files(source_files(self.repo)))
            self.initial = self.request('snapshot')['files']
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        r = cli(['rm', '-f', self.name], check=False)
        if r.returncode:
            probe = cli(['inspect', self.name], check=False)
            if b'No such' not in probe.stderr:
                raise RuntimeError(f'Container cleanup failed. Run: docker rm -f {self.name}')
        self.closed = True

    def __exit__(self, *args):
        self.close()

    def request(self, operation, path='', content='', export=None):
        payload = {'operation': operation, 'path': path, 'content': content,
                   'allowed_files': self.allowed_files, 'export': export or []}
        started = time.perf_counter()
        try:
            r = cli(['exec', '--workdir', '/workspace', self.name, 'python', '-I', '-c', OPS,
                     json.dumps(payload)], timeout=30)
            result = json.loads(r.stdout)
            if operation == 'snapshot' and 'files' not in result:
                raise RuntimeError('Container snapshot failed.')
            return result
        except subprocess.TimeoutExpired:
            # Kill the whole container if a tool process does not return; no host fallback.
            self.close()
            raise RuntimeError('Container tool timed out; container was removed.')
        finally:
            if self.log:
                with self.log.open('a') as out:
                    out.write(json.dumps({'operation': operation, 'path': path,
                                          'latency_seconds': round(time.perf_counter()-started, 3)}) + '\n')

    def export_sources(self):
        files = self.request('snapshot', export=self.allowed_files)['files']
        changed = {p for p in set(files) | set(self.initial)
                   if files.get(p, {}).get('sha256') != self.initial.get(p, {}).get('sha256')}
        if not changed:
            raise RuntimeError('Agent produced no source patch.')
        if changed - set(self.allowed_files):
            raise RuntimeError('Patch scope violation: tests/config or other files changed.')
        if any(p not in files for p in self.allowed_files):
            raise RuntimeError('Source deletion is not supported by this fixture.')
        for p in changed:
            data = base64.b64decode(files[p]['base64'], validate=True)
            (self.repo / p).write_bytes(data)


def image_id():
    if not shutil.which('docker'):
        raise RuntimeError('Docker is missing. Install/start Docker Desktop first.')
    cli(['info', '--format', '{{.ServerVersion}}'])
    r = cli(['image', 'inspect', IMAGE], check=False)
    if r.returncode:
        raise RuntimeError('Build the test image first: uv run python docker_agent.py build')
    return json.loads(r.stdout)[0]['Id']


def verify_docker(run_dir, image):
    manifest = repair.load_run(run_dir)
    repo, task = run_dir / 'workspace', manifest['task']
    base = manifest['baseline_commit']
    common = ['git', 'diff', '--no-ext-diff', '--no-textconv', '--no-renames']
    changed = repair.command(common + ['--name-only', base], repo).stdout.splitlines()
    patch = repair.command(common + ['--binary', base], repo).stdout
    (run_dir / 'patch.diff').write_text(patch)
    result = {'task_id': task['id'], 'verification_status': 'failed', 'changed_files': changed,
              'baseline_returncode': manifest['baseline_returncode'], 'verification_backend': 'docker',
              'image_id': image}
    if not changed or set(changed) - set(task['allowed_files']):
        result['reason'] = 'no_patch' if not changed else 'patch_scope_violation'
    else:
        with tempfile.TemporaryDirectory(prefix='repair-clean-') as tmp:
            fresh = Path(tmp) / 'repo'
            shutil.copytree(ROOT / 'fixtures' / task['fixture'], fresh,
                            ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
            repair.initialize_git(fresh)
            repair.command(['git', 'apply', '--whitespace=error'], fresh, input_text=patch)
            shutil.copy2(ROOT / 'evaluator' / 'test_contract.py', fresh / 'tests')
            # Candidate code is tested in a NEW container, never executed on the Mac.
            with Sandbox(fresh, image, task['allowed_files']) as verifier:
                tests = verifier.request('run_tests')
            (run_dir / 'verification-tests.txt').write_text(tests.get('stdout', '') + tests.get('stderr', ''))
            passed = tests['exit_code'] == 0 and re.search(r'\b75 passed\b', tests['stdout']) is not None
            result.update(test_returncode=tests['exit_code'],
                          verification_status='passed' if passed else 'failed',
                          reason='public_and_supplemental_tests_passed' if passed else 'tests_failed')
    repair.write_json(run_dir / 'verification.json', result)
    return result


def make_agent(llm, sandbox):
    from openhands.sdk import Action, Agent, Observation, TextContent, Tool, ToolDefinition
    from openhands.sdk.tool import ToolExecutor, register_tool
    from pydantic import Field

    class RepoAction(Action):
        operation: Literal['list_files', 'read_file', 'write_file', 'run_tests']
        path: str = Field(default='', description='Relative repository file path')
        content: str = Field(default='', description='Complete replacement text for write_file')

    class RepoObservation(Observation):
        exit_code: int
        stdout: str = ''
        stderr: str = ''

        @property
        def to_llm_content(self):
            return [TextContent(text=json.dumps({'exit_code': self.exit_code,
                                               'stdout': self.stdout, 'stderr': self.stderr}))]

    class RepoExecutor(ToolExecutor[RepoAction, RepoObservation]):
        def __call__(self, action, conversation=None):
            return RepoObservation(**sandbox.request(action.operation, action.path, action.content))

    class SandboxRepoTool(ToolDefinition[RepoAction, RepoObservation]):
        @classmethod
        def create(cls, conv_state):
            return [cls(description='Operate on the Python repository inside Docker. List/read files; '
                                    'replace allowlisted source files; run pytest. Paths are relative. '
                                    'No shell commands, installation, or external network access.',
                        action_type=RepoAction, observation_type=RepoObservation, executor=RepoExecutor())]

    register_tool('sandbox_repo', SandboxRepoTool)
    # No local TerminalTool or FileEditorTool: every repository operation uses the container.
    return Agent(llm=llm, tools=[Tool(name='sandbox_repo')])


def run_agent(run_dir):
    manifest = repair.load_run(run_dir)
    if (run_dir / 'agent-run.json').exists():
        raise ValueError('This run already started. Prepare a new --id.')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    if not os.getenv('OPENHANDS_SUBSCRIPTION_MODEL'):
        os.environ['OPENHANDS_SUBSCRIPTION_MODEL'] = 'gpt-5.6-sol'
    iterations = int(os.getenv('REPAIR_MAX_ITERATIONS', '20'))
    if not 1 <= iterations <= 100:
        raise ValueError('REPAIR_MAX_ITERATIONS must be between 1 and 100.')
    image = image_id()
    llm, auth = repair.configured_llm()
    from openhands.sdk import Conversation
    report = {'mode': 'live_agent', 'backend': 'docker', 'model': llm.model, 'auth': auth,
              'image_id': image, 'max_iterations': iterations, 'execution_status': 'started'}
    repair.write_json(run_dir / 'agent-run.json', report)
    started, conversation = time.perf_counter(), None
    try:
        with Sandbox(run_dir / 'workspace', image, manifest['task']['allowed_files'],
                     run_dir / 'tool-calls.jsonl') as sandbox:
            agent = make_agent(llm, sandbox)
            def on_event(event):
                with (run_dir / 'events.jsonl').open('a') as out:
                    out.write(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(),
                                          'event_type': type(event).__name__}) + '\n')
            with tempfile.TemporaryDirectory(prefix='repair-control-') as control:
                conversation = Conversation(agent=agent, workspace=control, callbacks=[on_event],
                                            max_iteration_per_run=iterations)
                task = manifest['task']
                conversation.send_message(f"Repair this issue: {task['issue']}\n"
                    f"Allowed source files: {task['allowed_files']}. Use sandbox_repo to inspect the files "
                    "and reproduce the failing tests first. Then make the smallest correct source change "
                    "using write_file, supplying the complete replacement file, and rerun tests. "
                    "Do not modify tests or configuration. Explain the root cause and observed results.")
                try:
                    conversation.run()
                    report['execution_status'] = conversation.state.execution_status.value
                    sandbox.export_sources()
                finally:
                    conversation.close()
                    conversation = None
        result = verify_docker(run_dir, image)
        report['verification_status'] = result['verification_status']
        return result
    except Exception as exc:
        report['execution_status'], report['error_type'] = 'error', type(exc).__name__
        raise
    finally:
        report['latency_seconds'] = round(time.perf_counter() - started, 3)
        repair.write_json(run_dir / 'agent-run.json', report)


def smoke():
    image = image_id()
    with tempfile.TemporaryDirectory(prefix='repair-docker-smoke-') as tmp:
        run = Path(tmp) / 'run'
        repair.prepare(run)
        manifest = repair.load_run(run)
        with Sandbox(run / 'workspace', image, manifest['task']['allowed_files']) as box:
            baseline = box.request('run_tests')
            assert baseline['exit_code'] == 1 and '3 failed, 3 passed' in baseline['stdout'], baseline
            old = box.request('read_file', 'batching.py')['stdout']
            box.request('write_file', 'batching.py', old.replace('len(items) - batch_size + 1', 'len(items)'))
            assert box.request('run_tests')['exit_code'] == 0
            box.export_sources()
        assert verify_docker(run, image)['verification_status'] == 'passed'
    print('Docker harness smoke PASSED: baseline failure, known manual patch, 75 tests in a fresh container.')
    print('No LLM was called. This is not an Agent repair result.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'smoke', 'run'])
    parser.add_argument('--id', default='docker-001')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.id):
        parser.error('Invalid run ID')
    if args.action == 'build':
        if not shutil.which('docker'): raise RuntimeError('Docker is missing.')
        # Send only the Dockerfile to the build daemon, never the project's .env.
        with tempfile.TemporaryDirectory(prefix='repair-image-build-') as tmp:
            shutil.copy2(ROOT / 'Dockerfile.repair', Path(tmp) / 'Dockerfile')
            r = subprocess.run(['docker', 'build', '-t', IMAGE, tmp], check=True)
            return r.returncode
    if args.action == 'smoke':
        smoke()
        return 0
    result = run_agent(ROOT / 'runs' / args.id)
    print(json.dumps(result, indent=2))
    return 0 if result['verification_status'] == 'passed' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Error: {exc}', file=__import__('sys').stderr)
        raise SystemExit(2)
