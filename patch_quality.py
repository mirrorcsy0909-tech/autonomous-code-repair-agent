"""Static review hints, separate from tests and never a proof of correctness.

Replay patches only as file data in a temporary directory. No candidate code is
imported, evaluated or executed by this module.
"""
from __future__ import annotations

import ast
import copy
import difflib
import hashlib
import io
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import tokenize

POLICY_VERSION = 1


def source_features(text):
    tree = ast.parse(text)
    docs, functions = {}, {}
    def walk(node, parent=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = parent + '.' + child.name if parent else child.name
                doc = ast.get_docstring(child, clean=False)
                if doc is not None:
                    docs[name] = len(doc.splitlines())
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    clone = copy.deepcopy(child)
                    class RemoveDocs(ast.NodeTransformer):
                        def generic_visit(self, item):
                            item = super().generic_visit(item)
                            if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                                if ast.get_docstring(item, clean=False) is not None:
                                    item.body = item.body[1:]
                            return item
                    functions[name] = ast.dump(RemoveDocs().visit(clone), include_attributes=False)
                walk(child, name)
            else:
                walk(child, parent)
    walk(tree)
    comments = [token.string for token in tokenize.generate_tokens(io.StringIO(text).readline)
                if token.type == tokenize.COMMENT]
    return docs, functions, comments


def analyze_sources(originals, candidates):
    rows, flags = [], set()
    for path in sorted(candidates):
        original, content = originals[path], candidates[path]
        plus = minus = 0
        for tag, i, j, k, l in difflib.SequenceMatcher(
                a=original.splitlines(), b=content.splitlines(), autojunk=False).get_opcodes():
            if tag != 'equal':
                minus += j - i
                plus += l - k
        row = {'path': path, 'added_lines': plus, 'deleted_lines': minus}
        try:
            before, after = source_features(original), source_features(content)
            removed_docs = sum(max(0, lines - after[0].get(name, 0)) for name, lines in before[0].items())
            removed_comments = max(0, len(before[2]) - len(after[2]))
            functions = sorted(name for name in set(before[1]) | set(after[1])
                               if before[1].get(name) != after[1].get(name))
            row.update(removed_docstring_lines=removed_docs, net_removed_comment_lines=removed_comments,
                       changed_functions=functions)
            if removed_docs >= 10:
                flags.add('substantial_documentation_removal')
            if removed_comments >= 5:
                flags.add('substantial_comment_removal')
            if len(functions) >= 8:
                flags.add('many_functions_changed')
        except (SyntaxError, tokenize.TokenError, IndentationError):
            flags.add('python_static_parse_failed')
            row['static_parse_status'] = 'failed'
        if plus + minus > 120:
            flags.add('large_patch')
        rows.append(row)
    return {'policy_version': POLICY_VERSION,
            'status': 'review_required' if flags else 'no_heuristic_flags',
            'flags': sorted(flags), 'files': rows,
            'added_lines': sum(x['added_lines'] for x in rows),
            'deleted_lines': sum(x['deleted_lines'] for x in rows),
            'meaning': 'Heuristic review hints only. No flags does not mean merge-ready; tests are reported separately.',
            'thresholds': {'changed_lines_per_file': 120, 'removed_docstring_lines_per_file': 10,
                           'net_removed_comment_lines_per_file': 5, 'changed_functions_per_file': 8}}


def analyze_patch(fixture, patch_text, allowed_files):
    """Static git replay with a whitelist; no repository hooks or candidate tests."""
    def git(args, cwd=None):
        return subprocess.run(['git', 'apply', *args], input=patch_text, capture_output=True,
                              text=True, timeout=10, cwd=cwd, check=True)
    raw = git(['--numstat', '-z', '-']).stdout
    paths = []
    for record in raw.split('\0'):
        if not record:
            continue
        added, deleted, path = record.split('\t', 2)
        rel = PurePosixPath(path)
        if (not added.isdigit() or not deleted.isdigit() or path not in allowed_files
                or rel.is_absolute() or '..' in rel.parts or str(rel) != path or '\\' in path):
            raise ValueError('Quality review requires an allowlisted text source patch')
        paths.append(path)
    if not paths:
        return {'policy_version': POLICY_VERSION, 'status': 'not_available', 'reason': 'no_patch'}
    originals, candidates = {}, {}
    with tempfile.TemporaryDirectory(prefix='static-quality-') as tmp:
        root = Path(tmp)
        for path in paths:
            source = fixture / path
            if source.is_symlink() or source.stat().st_size > 200000:
                raise ValueError('Invalid original source')
            originals[path] = source.read_text()
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, root / path)
        git(['--whitespace=nowarn', '-'], cwd=root)
        for path in paths:
            target = root / path
            if target.is_symlink() or target.stat().st_size > 200000:
                raise ValueError('Invalid candidate source')
            candidates[path] = target.read_text()
    return analyze_sources(originals, candidates)


def patch_fingerprint(patch_text):
    return hashlib.sha256(patch_text.encode('utf-8')).hexdigest()
