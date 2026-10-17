"""Unified entry point for the existing, independently verified repair workflows.

Doctor, list and fixture checks use only the standard library and never log in.
Live commands delegate to the existing supervised CLI implementations.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
CATALOGS = {
    'itsdangerous': {'directory': 'repair_suite', 'script': 'suite_agent.py',
                     'build_script': 'real_task.py', 'dockerfile': 'Dockerfile.real',
                     'image': 'code-repair-python:itsdangerous126'},
    'dotenv': {'directory': 'comparison_tasks', 'script': 'comparison_agent.py',
               'build_script': 'comparison_agent.py', 'dockerfile': 'Dockerfile.comparison',
               'image': 'code-repair-python:dotenv-comparison-v1'},
    'sqlparse': {'directory': 'expanded_tasks', 'script': 'expanded_agent.py',
                 'build_script': 'expanded_agent.py', 'dockerfile': 'Dockerfile.expanded',
                 'image': 'code-repair-python:expanded-v1'},
}
TEST_MODULES = ('test_reliability', 'test_expanded_tasks', 'test_execution_control',
                'test_comparison_agent', 'test_suite_agent', 'test_project')
RUNTIME_FILES = ('repair.py', 'docker_agent.py', 'real_task.py', 'suite_agent.py',
                 'comparison_agent.py', 'expanded_agent.py', 'execution_control.py',
                 'run_diagnostics.py', 'patch_quality.py', 'pyproject.toml', 'uv.lock')


def checked_id(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('IDs must contain 1-64 letters, digits, underscores or hyphens.')
    return value


def selected_catalogs(name):
    return list(CATALOGS) if name == 'all' else [name]


def load_catalog(name):
    directory = ROOT / CATALOGS[name]['directory']
    ids = json.loads((directory / 'catalog.json').read_text())['tasks']
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate task IDs in catalog')
    tasks = []
    for task_id in ids:
        checked_id(task_id)
        task = json.loads((directory / task_id / 'task.json').read_text())
        if task.get('id') != task_id:
            raise ValueError('Task identity differs from its catalog')
        tasks.append(task)
    return tasks


def check_fixtures(name):
    """Hash trusted fixture data only; do not discover or execute its tests."""
    checked = 0
    for catalog_name in selected_catalogs(name):
        directory = ROOT / CATALOGS[catalog_name]['directory']
        for task in load_catalog(catalog_name):
            base = directory / task['id']
            fixture = base / 'fixture'
            expected = json.loads((base / 'fixture-sha256.json').read_text())
            for relative in expected:
                path = PurePosixPath(relative)
                if path.is_absolute() or '..' in path.parts or '\\' in relative:
                    raise ValueError('Invalid fixture path')
            actual = {}
            for path in fixture.rglob('*'):
                if set(path.relative_to(fixture).parts) & {'__pycache__', '.pytest_cache'}:
                    continue
                if path.is_symlink():
                    raise ValueError('Fixture symlinks are not supported')
                if path.is_file():
                    actual[path.relative_to(fixture).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            if expected != actual:
                raise ValueError(f"Fixture changed: {task['id']}")
            checked += 1
    return checked


def doctor(catalog, offline=False):
    checks = []
    checks.append(('Python 3.12-3.14', (3, 12) <= sys.version_info[:2] < (3, 15)))
    checks.append(('Git executable', shutil.which('git') is not None))
    for package in ('openhands-sdk', 'openhands-tools'):
        try:
            found = metadata.version(package)
        except metadata.PackageNotFoundError:
            found = None
        checks.append((f'{package} == 1.53.0', found == '1.53.0'))
    for package in ('python-dotenv', 'pytest'):
        try:
            metadata.version(package)
            installed = True
        except metadata.PackageNotFoundError:
            installed = False
        checks.append((package + ' installed', installed))
    checks.append(('Core files and lockfile', all((ROOT / name).is_file() for name in RUNTIME_FILES)))
    for name in selected_catalogs(catalog):
        checks.append((CATALOGS[name]['dockerfile'], (ROOT / CATALOGS[name]['dockerfile']).is_file()))
    try:
        count = check_fixtures(catalog)
        checks.append((f'Trusted fixtures ({count} tasks)', True))
    except (ValueError, OSError, KeyError):
        checks.append(('Trusted fixtures; restore modified/missing task data', False))
    if not offline:
        docker_ok = shutil.which('docker') is not None
        if docker_ok:
            try:
                docker_ok = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'],
                    capture_output=True, text=True, timeout=5, check=False).returncode == 0
            except (OSError, subprocess.SubprocessError):
                docker_ok = False
        checks.append(('Docker daemon reachable', docker_ok))
        if docker_ok:
            for name in selected_catalogs(catalog):
                image = CATALOGS[name]['image']
                try:
                    exists = subprocess.run(['docker', 'image', 'inspect', image],
                        capture_output=True, text=True, timeout=5, check=False).returncode == 0
                except (OSError, subprocess.SubprocessError):
                    exists = False
                checks.append((image, exists))
    for label, passed in checks:
        print(f"{'OK' if passed else 'FAIL'}  {label}")
    if offline:
        print('SKIP  Docker/image checks (--offline)')
    print('No model was called; credentials and .env contents were not read.')
    return 0 if all(passed for _, passed in checks) else 1


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='action', required=True)
    for action in ('doctor', 'list', 'check-fixtures', 'build', 'smoke', 'compare',
                   'batch', 'prepare', 'run', 'report', 'export'):
        sub = commands.add_parser(action)
        choices = list(CATALOGS) + (['all'] if action in {'doctor', 'list', 'check-fixtures', 'build', 'smoke'} else [])
        sub.add_argument('--catalog', choices=choices, default='sqlparse')
        if action == 'doctor':
            sub.add_argument('--offline', action='store_true')
        if action in {'compare', 'batch', 'report', 'export'}:
            sub.add_argument('--prefix', required=True, type=checked_id)
        if action in {'smoke', 'prepare', 'run'}:
            sub.add_argument('--id', required=True, type=checked_id)
        if action in {'compare', 'batch', 'smoke'}:
            sub.add_argument('--tasks', nargs='+')
        if action in {'prepare', 'run'}:
            sub.add_argument('--task', required=True)
            sub.add_argument('--workflow', choices=('oneshot', 'iterative'), default='iterative')
        if action == 'compare':
            sub.add_argument('--repeats', type=int, choices=range(1, 6), default=1)
        if action == 'batch':
            sub.add_argument('--include', nargs='*', default=[])
    commands.add_parser('check', help='Explicit harness tests; no model or Docker daemon')
    return result


def delegated_commands(args):
    """Pure routing plan: validate all selections before starting any subprocess."""
    if args.action == 'check':
        return [[sys.executable, '-m', 'unittest', *TEST_MODULES, '-v']]
    action, name = args.action, args.catalog
    if action == 'compare' and name == 'itsdangerous':
        raise ValueError('The ItsDangerous pilot is Agent-only; use batch --catalog itsdangerous.')
    if action == 'batch' and name != 'itsdangerous':
        raise ValueError('Use compare for paired dotenv/sqlparse workflows.')
    if action == 'export' and name == 'itsdangerous':
        raise ValueError('Use report for the ItsDangerous pilot; paired ZIP export supports dotenv/sqlparse.')
    if name == 'itsdangerous' and getattr(args, 'workflow', 'iterative') != 'iterative':
        raise ValueError('The ItsDangerous pilot supports only the iterative workflow.')
    if name == 'all' and getattr(args, 'tasks', None):
        raise ValueError('Select one catalog when supplying task IDs.')
    commands = []
    for catalog_name in selected_catalogs(name):
        catalog = CATALOGS[catalog_name]
        requested = getattr(args, 'tasks', None) or ([args.task] if hasattr(args, 'task') else [])
        if requested:
            known = {task['id'] for task in load_catalog(catalog_name)}
            if set(requested) - known or len(requested) != len(set(requested)):
                raise ValueError('Select unique task IDs from the chosen catalog.')
        script = catalog['build_script'] if action == 'build' else catalog['script']
        command = [sys.executable, str(ROOT / script), action]
        if hasattr(args, 'prefix'):
            command += ['--prefix', args.prefix]
        if hasattr(args, 'id'):
            run_id = checked_id(args.id + '-' + catalog_name) if name == 'all' else args.id
            command += ['--id', run_id]
        if getattr(args, 'tasks', None):
            command += ['--tasks', *args.tasks]
        if hasattr(args, 'task'):
            command += ['--task', args.task]
            if catalog_name != 'itsdangerous':
                command += ['--workflow', args.workflow]
        if action == 'compare':
            command += ['--repeats', str(args.repeats)]
        if action == 'batch' and args.include:
            command += ['--include', *(checked_id(value) for value in args.include)]
        commands.append(command)
    return commands


def main(argv=None):
    arguments = parser().parse_args(argv)
    if arguments.action == 'doctor':
        return doctor(arguments.catalog, arguments.offline)
    if arguments.action == 'check-fixtures':
        print(f'Fixture integrity OK: {check_fixtures(arguments.catalog)} tasks; no tests or model executed.')
        return 0
    if arguments.action == 'list':
        print('Catalog | Task | Kind | Baseline failures | Verification tests')
        for name in selected_catalogs(arguments.catalog):
            for task in load_catalog(name):
                print(f"{name} | {task['id']} | {task.get('task_kind', 'bug_fix')} | "
                      f"{task['baseline_failed']} | {task['verification_test_count']}")
        return 0
    commands = delegated_commands(arguments)
    env = os.environ.copy()
    if arguments.action == 'check':
        env.update(LITELLM_LOCAL_MODEL_COST_MAP='True', OPENHANDS_SUPPRESS_BANNER='1')
    for command in commands:
        code = subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
        if code:
            return code if code > 0 else 128 - code
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(f'Error: {type(error).__name__}. {error}', file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print('Interrupted; inspect saved records before choosing a new run ID.', file=sys.stderr)
        raise SystemExit(130)
