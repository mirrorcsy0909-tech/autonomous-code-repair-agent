"""Phase 2A: check Docker isolation before connecting the repair agent.

Place this file beside repair.py. Run: uv run python check_sandbox.py
No model call, credentials, host mounts, or repair task are used.
First run downloads python:3.13-slim if needed. Reports go to runs/docker-check-*.json.
This is a prerequisite probe; it does not move the existing Agent into Docker.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

IMAGE = "python:3.13-slim"
PROBE = """
import json, os, platform
from pathlib import Path
p = Path('/tmp/repair-probe.txt')
p.write_text('container writes work')
assert p.read_text() == 'container writes work'
try:
    Path('/write-to-root.txt').write_text('must fail')
except OSError:
    root_write_blocked = True
else:
    raise RuntimeError('Root filesystem unexpectedly writable')
keys = ['LLM_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY']
assert not any(key in os.environ for key in keys)
assert not Path('/var/run/docker.sock').exists()
print(json.dumps({'python': platform.python_version(), 'platform': platform.system(),
                  'uid': os.getuid(), 'temporary_write': 'passed',
                  'root_write_blocked': root_write_blocked,
                  'credential_env_present': False, 'docker_socket_present': False}))
"""


def docker(args, *, timeout=30, check=True):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # Keep daemon addresses/auth data out of the saved error report.
        raise RuntimeError(f"docker {args[0]} failed (exit {result.returncode}). "
                           "Check Docker Desktop is running and image downloads are available.")
    return result


def create_args(name, image_id):
    return ['create', '--name', name, '--network', 'none', '--read-only',
            '--user', '65534:65534', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--pids-limit', '64',
            '--memory', '256m', '--cpus', '1',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777',
            image_id, 'python', '-c', PROBE]


def validate_config(info):
    host = info['HostConfig']
    checks = {
        'network_disabled': host['NetworkMode'] == 'none',
        'root_filesystem_readonly': host['ReadonlyRootfs'] is True,
        'nonroot_user': info['Config']['User'] == '65534:65534',
        'no_host_mounts': all(m.get('Type') == 'tmpfs' and m.get('Destination') == '/tmp'
                             for m in info.get('Mounts', [])),
        'capabilities_dropped': 'ALL' in host.get('CapDrop', []),
        'no_new_privileges': 'no-new-privileges:true' in host.get('SecurityOpt', []),
        'memory_limited': host['Memory'] == 256 * 1024 * 1024,
        'processes_limited': host['PidsLimit'] == 64,
        'cpu_limited': host['NanoCpus'] == 1_000_000_000,
    }
    if not all(checks.values()):
        raise RuntimeError('Container configuration check failed: ' +
                           ', '.join(k for k, v in checks.items() if not v))
    return checks


def probe():
    if not shutil.which('docker'):
        raise RuntimeError('Docker command not found. Install and start Docker Desktop, then retry.')
    docker(['info', '--format', '{{.ServerVersion}}'])
    name = 'repair-probe-' + uuid.uuid4().hex[:12]
    report = {'stage': '2A', 'status': 'started', 'model_called': False,
              'image_tag': IMAGE, 'timestamp': datetime.now(timezone.utc).isoformat()}
    report_dir = Path(__file__).resolve().parent / 'runs'
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f'docker-check-{name}.json'
    created = False
    try:
        image = docker(['image', 'inspect', IMAGE], check=False)
        if image.returncode:
            print('Downloading Python image for the first run...', flush=True)
            docker(['pull', IMAGE], timeout=300)
            image = docker(['image', 'inspect', IMAGE])
        metadata = json.loads(image.stdout)[0]
        image_id = metadata['Id']
        report.update(image_id=image_id, image_architecture=metadata['Architecture'])
        # Pin this run to the inspected image ID rather than using a mutable tag.
        docker(create_args(name, image_id))
        created = True
        config = json.loads(docker(['inspect', name]).stdout)[0]
        report['config_checks'] = validate_config(config)
        output = docker(['start', '-a', name], timeout=30)
        stopped = json.loads(docker(['inspect', name]).stdout)[0]
        if stopped['State']['ExitCode'] != 0:
            raise RuntimeError('Container Python probe did not exit successfully.')
        observation = json.loads(output.stdout.strip())
        if observation['platform'] != 'Linux' or observation['uid'] != 65534:
            raise RuntimeError('Python did not run in the expected Linux container user.')
        report['observation'] = observation
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error_type'] = type(exc).__name__
        raise
    finally:
        if created:
            try:
                cleanup = docker(['rm', '-f', name], check=False)
                report['cleanup'] = 'passed' if cleanup.returncode == 0 else 'failed'
            except Exception:
                report['cleanup'] = 'failed'
            if report['cleanup'] != 'passed':
                print(f'Cleanup failed. Remove only the probe: docker rm -f {name}', file=sys.stderr)
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        print(f'Report saved: runs/{report_path.name}')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' and report.get('cleanup') == 'passed' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(probe())
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        print(f'Check failed: {exc}', file=sys.stderr)
        raise SystemExit(2)
