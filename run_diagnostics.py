"""Bounded, content-free failure metadata and source-only candidate checkpoints.

Candidate bytes are saved as data. They are never imported, evaluated or tested
on the host, and a saved candidate does not change an attempt's success status.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import io
import os
from pathlib import PurePosixPath
import tempfile
import zipfile

import execution_control as control


# Never copy exception messages, response bodies, URLs, headers or event detail.
# An unknown provider-defined class/code is deliberately collapsed to UnknownError.
ERROR_TYPES = frozenset('''Exception RuntimeError ValueError TypeError KeyError
AttributeError OSError IOError FileNotFoundError PermissionError TimeoutError
ConnectionError ConnectionResetError BrokenPipeError JSONDecodeError UnicodeDecodeError
ReadTimeout ConnectTimeout WriteTimeout PoolTimeout APITimeoutError Timeout
ConnectError ReadError WriteError RemoteProtocolError LocalProtocolError
NetworkError TransportError HTTPError HTTPStatusError RequestError
APIError APIConnectionError InternalServerError RateLimitError AuthenticationError
PermissionDeniedError BadRequestError NotFoundError UnprocessableEntityError
ServiceUnavailableError ContextWindowExceededError ContentPolicyViolationError
LLMAuthenticationError LLMNoResponseError LLMResponseError LLMContextWindowExceedError
ConversationRunError WebSocketConnectionError MaxIterationsReached MaxBudgetReached
CalledProcessError TimeoutExpired KeyboardInterrupt CancelledError UnknownError'''.split())


def safe_error_type(value):
    name = value if isinstance(value, str) else type(value).__name__
    return name if name in ERROR_TYPES else 'UnknownError'


def exception_metadata(error):
    """Follow the pinned SDK's original_exception and Python cause chain safely."""
    chain, seen, pending = [], set(), [error]
    while pending and len(chain) < 8:
        current = pending.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(safe_error_type(current))
        for nested in (getattr(current, 'original_exception', None), current.__cause__):
            if isinstance(nested, BaseException):
                pending.append(nested)
        if not pending and not current.__suppress_context__ and current.__context__:
            pending.append(current.__context__)
    root = chain[-1] if chain else 'UnknownError'
    return {'error_type': chain[0] if chain else root, 'error_chain_types': chain,
            'root_error_type': root, 'failure_category': category_for_types(chain)}


def category_for_types(chain):
    if any('Timeout' in name or name == 'TimeoutExpired' for name in chain):
        category = 'request_or_operation_timeout'
    elif any(name in {'AuthenticationError', 'PermissionDeniedError', 'LLMAuthenticationError'} for name in chain):
        category = 'authentication_or_permission_error'
    elif 'RateLimitError' in chain:
        category = 'rate_limit_error'
    elif any(name in {'MaxIterationsReached', 'MaxBudgetReached', 'ContextWindowExceededError'} for name in chain):
        category = 'model_or_iteration_limit'
    elif any(name in {'APIConnectionError', 'ConnectError', 'ReadError', 'WriteError',
                      'RemoteProtocolError', 'NetworkError', 'TransportError', 'ConnectionError'} for name in chain):
        category = 'transport_error'
    else:
        category = 'execution_error_unclassified'
    return category


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        from pathlib import Path
        Path(name).unlink(missing_ok=True)


def _diff(path, original, content):
    lines = difflib.unified_diff(original.decode('utf-8').splitlines(keepends=True),
                                 content.decode('utf-8').splitlines(keepends=True),
                                 fromfile='a/' + path, tofile='b/' + path)
    return ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
                   for line in lines)


def save_candidate(run_dir, sandbox):
    """Validate the whole container snapshot, then save only changed source bytes."""
    files = sandbox.request('snapshot', export=sandbox.allowed_files)['files']
    changed = sorted(p for p in set(files) | set(sandbox.initial)
                     if files.get(p, {}).get('sha256') != sandbox.initial.get(p, {}).get('sha256'))
    if set(changed) - set(sandbox.allowed_files):
        raise ValueError('Candidate scope violation')
    if any(p not in files for p in sandbox.allowed_files):
        raise ValueError('Candidate source deletion')
    if not changed:
        result = {'status': 'no_patch', 'saved_at': control.now(), 'verified': False}
        control.write_json(run_dir / 'candidate.json', result)
        return result
    data, patch = {}, ''
    for path in changed:
        rel = PurePosixPath(path)
        if rel.is_absolute() or '..' in rel.parts or str(rel) != path or '\\' in path:
            raise ValueError('Invalid source path')
        content = base64.b64decode(files[path]['base64'], validate=True)
        if len(content) > 200000 or hashlib.sha256(content).hexdigest() != files[path]['sha256']:
            raise ValueError('Invalid candidate content')
        original = (sandbox.repo / path).read_bytes()
        if hashlib.sha256(original).hexdigest() != sandbox.initial[path]['sha256']:
            raise ValueError('Host baseline changed during Agent execution')
        data[path] = content
        patch += _diff(path, original, content)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for path, content in data.items():
            z.writestr(path, content)
    patch_bytes, zip_bytes = patch.encode('utf-8'), archive.getvalue()
    atomic_bytes(run_dir / 'candidate.diff', patch_bytes)
    atomic_bytes(run_dir / 'candidate-sources.zip', zip_bytes)
    result = {'status': 'saved', 'saved_at': control.now(), 'verified': False,
              'changed_files': changed, 'patch_sha256': hashlib.sha256(patch_bytes).hexdigest(),
              'archive_sha256': hashlib.sha256(zip_bytes).hexdigest(),
              'source_sha256': {p: hashlib.sha256(b).hexdigest() for p, b in data.items()},
              'meaning': 'Last completed source checkpoint; not a final submission or a verified repair.'}
    # Metadata is committed last; hashes detect interrupted multi-file writes.
    control.write_json(run_dir / 'candidate.json', result)
    return result


def checkpoint(run_dir, sandbox):
    """Do not replace a model/tool failure with a secondary checkpoint failure."""
    try:
        result = save_candidate(run_dir, sandbox)
        outcome = {'status': result['status'], 'updated_at': control.now()}
    except Exception as error:
        outcome = {'status': 'save_failed', 'updated_at': control.now(),
                   **exception_metadata(error)}
    try:
        control.write_json(run_dir / 'candidate-save.json', outcome)
    except OSError:
        # Disk errors must not hide the original tool/model exception.
        outcome['status'] = 'metadata_save_failed'
    return outcome


def candidate_metadata(run_dir):
    record = control.read_json(run_dir / 'candidate.json')
    latest = control.read_json(run_dir / 'candidate-save.json')
    status = record.get('status', 'not_recorded')
    if status == 'saved':
        for name, key in [('candidate.diff', 'patch_sha256'),
                          ('candidate-sources.zip', 'archive_sha256')]:
            path = run_dir / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record.get(key):
                status = 'checkpoint_incomplete'
                break
    return {'candidate_status': status,
            'candidate_last_save_status': latest.get('status'),
            'candidate_verified': False if record else None}
