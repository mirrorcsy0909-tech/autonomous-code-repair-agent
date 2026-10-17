from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repair import prepare, verify


def test_correct_patch_replays_and_passes(tmp_path):
    run = tmp_path / "correct"
    prepare(run)
    source = run / "workspace" / "batching.py"
    source.write_text(source.read_text().replace("len(items) - batch_size + 1", "len(items)"))
    assert verify(run)["verification_status"] == "passed"
    assert "75 passed" in (run / "verification-tests.txt").read_text()


def test_modified_tests_are_rejected(tmp_path):
    run = tmp_path / "tamper"
    prepare(run)
    (run / "workspace" / "tests" / "test_batching.py").write_text("def test_fake(): assert True\n")
    assert verify(run)["reason"] == "patch_scope_violation"


def test_incorrect_source_patch_fails(tmp_path):
    run = tmp_path / "incorrect"
    prepare(run)
    (run / "workspace" / "batching.py").write_text("def make_batches(items, batch_size): return []\n")
    assert verify(run)["reason"] == "tests_failed"


def test_no_patch_and_untracked_files_are_rejected(tmp_path):
    run = tmp_path / "extra"
    prepare(run)
    assert verify(run)["reason"] == "no_patch"
    source = run / "workspace" / "batching.py"
    source.write_text(source.read_text().replace("len(items) - batch_size + 1", "len(items)"))
    (run / "workspace" / "extra.py").write_text("pass\n")
    assert verify(run)["reason"] == "patch_scope_violation"


def test_early_zero_exit_is_not_a_pass(tmp_path):
    run = tmp_path / "early-exit"
    prepare(run)
    (run / "workspace" / "batching.py").write_text("import os\nos._exit(0)\n")
    assert verify(run)["reason"] == "incomplete_test_run"
