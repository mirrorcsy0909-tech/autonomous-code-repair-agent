"""Phase 1: prepare a task, run OpenHands, and independently verify its patch."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
TASK = ROOT / "tasks" / "001-partial-batch.json"


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def command(args: list[str], cwd: Path, *, timeout: int = 60,
            check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, text=True, input=input_text,
                            capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"Command failed: {args[0]} (exit {result.returncode})")
    return result


def test_repo(repo: Path) -> dict:
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("PYTHONPATH", None)
    try:
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                                cwd=repo, env=env, text=True, capture_output=True, timeout=60)
        return {"returncode": result.returncode,
                "output": result.stdout + result.stderr, "timed_out": False}
    except subprocess.TimeoutExpired:
        return {"returncode": None, "output": "Verification exceeded 60 seconds.",
                "timed_out": True}


def initialize_git(repo: Path) -> str:
    command(["git", "init", "-q"], repo)
    command(["git", "add", "."], repo)
    command(["git", "-c", "user.name=Repair Fixture", "-c",
             "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
             "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Known broken baseline"], repo)
    return command(["git", "rev-parse", "HEAD"], repo).stdout.strip()


def prepare(run_dir: Path) -> dict:
    if run_dir.exists():
        raise ValueError(f"Run already exists: {run_dir.name}. Choose a new --id.")
    task = json.loads(TASK.read_text())
    run_dir.mkdir(parents=True)
    repo = run_dir / "workspace"
    shutil.copytree(ROOT / "fixtures" / task["fixture"], repo,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    baseline_commit = initialize_git(repo)
    baseline = test_repo(repo)
    (run_dir / "baseline-tests.txt").write_text(baseline["output"])
    if baseline["returncode"] != 1 or "3 failed, 3 passed" not in baseline["output"]:
        raise RuntimeError("Baseline did not reproduce the expected 3 failures. Read baseline-tests.txt.")
    manifest = {"task": task, "baseline_commit": baseline_commit,
                "baseline_returncode": baseline["returncode"],
                "created_at": datetime.now(timezone.utc).isoformat()}
    write_json(run_dir / "manifest.json", manifest)
    return manifest


def load_run(run_dir: Path) -> dict:
    manifest = run_dir / "manifest.json"
    if not manifest.is_file():
        raise ValueError(f"Run {run_dir.name} is not prepared. Use prepare first.")
    return json.loads(manifest.read_text())


def verify(run_dir: Path) -> dict:
    manifest = load_run(run_dir)
    task = manifest["task"]
    repo = run_dir / "workspace"
    base = manifest["baseline_commit"]
    # Compare against the stored original commit, even if the agent commits a fix.
    common = ["git", "diff", "--no-ext-diff", "--no-textconv", "--no-renames"]
    changed = command(common + ["--name-only", base], repo).stdout.splitlines()
    patch = command(common + ["--binary", base], repo).stdout
    (run_dir / "patch.diff").write_text(patch)
    untracked = command(["git", "ls-files", "--others", "--exclude-standard"], repo).stdout.splitlines()
    result = {"task_id": task["id"], "verification_status": "failed",
              "changed_files": changed, "untracked_files": untracked,
              "baseline_returncode": manifest["baseline_returncode"]}
    if not changed:
        result["reason"] = "no_patch"
    elif set(changed) - set(task["allowed_files"]) or untracked:
        result["reason"] = "patch_scope_violation"
    else:
        # Replay onto an untouched fixture; never trust the agent's test files.
        with tempfile.TemporaryDirectory(prefix="repair-verify-") as tmp:
            fresh = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / task["fixture"], fresh,
                            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            initialize_git(fresh)
            applied = command(["git", "apply", "--whitespace=error"], fresh,
                              check=False, input_text=patch)
            if applied.returncode:
                result["reason"] = "patch_apply_failed"
            else:
                shutil.copy2(ROOT / "evaluator" / "test_contract.py", fresh / "tests")
                tests = test_repo(fresh)
                (run_dir / "verification-tests.txt").write_text(tests["output"])
                result["test_returncode"] = tests["returncode"]
                complete = re.search(r"\b75 passed\b", tests["output"]) is not None
                if tests["returncode"] == 0 and complete:
                    result["verification_status"] = "passed"
                    result["reason"] = "public_and_supplemental_tests_passed"
                else:
                    result["reason"] = ("test_timeout" if tests["timed_out"] else
                                        "incomplete_test_run" if tests["returncode"] == 0 else "tests_failed")
    write_json(run_dir / "verification.json", result)
    return result


def build_agent(llm):
    from openhands.sdk import Agent, Tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool
    return Agent(llm=llm, tools=[Tool(name=TerminalTool.name),
                                Tool(name=FileEditorTool.name),
                                Tool(name=TaskTrackerTool.name)])


def configured_llm():
    from openhands.sdk import LLM
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    auth = os.getenv("REPAIR_AUTH", "subscription")
    if auth == "subscription":
        model = os.getenv("OPENHANDS_SUBSCRIPTION_MODEL", "gpt-5.2-codex")
        return LLM.subscription_login(vendor="openai", model=model), auth
    if auth == "api":
        model, key = os.getenv("LLM_MODEL"), os.getenv("LLM_API_KEY")
        if not model or not key:
            raise ValueError("API mode requires LLM_MODEL and LLM_API_KEY in your local .env.")
        return LLM(model=model, api_key=key, base_url=os.getenv("LLM_BASE_URL") or None), auth
    raise ValueError("REPAIR_AUTH must be subscription or api.")


def run_agent(run_dir: Path) -> dict:
    manifest = load_run(run_dir)
    if (run_dir / "agent-run.json").exists():
        raise ValueError("This run already started. Prepare a new --id for a comparable fresh run.")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    max_iterations = int(os.getenv("REPAIR_MAX_ITERATIONS", "20"))
    if not 1 <= max_iterations <= 100:
        raise ValueError("REPAIR_MAX_ITERATIONS must be between 1 and 100.")
    from openhands.sdk import Conversation
    llm, auth = configured_llm()
    agent = build_agent(llm)
    events_file = run_dir / "events.jsonl"

    def on_event(event):
        # Log event metadata, not API keys, prompts, tool output, or model reasoning.
        row = {"timestamp": datetime.now(timezone.utc).isoformat(),
               "event_type": type(event).__name__}
        with events_file.open("a") as stream:
            stream.write(json.dumps(row) + "\n")

    report = {"auth": auth, "model": llm.model, "max_iterations": max_iterations,
              "execution_status": "started", "mode": "live_agent"}
    write_json(run_dir / "agent-run.json", report)
    conversation = None
    started = time.perf_counter()
    try:
        conversation = Conversation(agent=agent, workspace=run_dir / "workspace",
                                    callbacks=[on_event], max_iteration_per_run=max_iterations)
        task = manifest["task"]
        prompt = (
            "You are repairing the Python repository in the current working directory.\n"
            f"Issue: {task['issue']}\n"
            f"Only modify these source files: {task['allowed_files']}.\n"
            "First inspect the repository and run the existing tests to reproduce the issue. "
            "Then make the smallest correct fix and rerun the tests. Do not change tests, "
            "configuration, or Git history. Do not access paths outside this repository, "
            "install packages, or access the network. These are task instructions, not a sandbox. "
            f"Use {sys.executable} -m pytest -q for tests. "
            "Finish by explaining the root cause, changes, and observed test results."
        )
        conversation.send_message(prompt)
        conversation.run()
        report["execution_status"] = str(conversation.state.execution_status.value)
    except Exception as exc:
        report["execution_status"] = "error"
        report["error_type"] = type(exc).__name__
        raise
    finally:
        report["latency_seconds"] = round(time.perf_counter() - started, 3)
        write_json(run_dir / "agent-run.json", report)
        if conversation is not None:
            conversation.close()
    return verify(run_dir)


def doctor() -> None:
    if not shutil.which("git"):
        raise RuntimeError("Git is required.")
    sdk, tools = version("openhands-sdk"), version("openhands-tools")
    if sdk != tools or sdk != "1.53.0":
        raise RuntimeError("Run uv sync to install the matched 1.53.0 SDK/tools pair.")
    from openhands.sdk import LLM, Conversation
    # Construct without contacting a provider or reading real credentials.
    build_agent(LLM(model="openai/gpt-5.5", api_key="configuration-check-only"))
    print(f"Python {sys.version.split()[0]}; Git OK; SDK/tools {sdk}; Agent imports/config OK.")
    print("Doctor does not authenticate or call the model.")


def smoke() -> None:
    # A deterministic harness self-test, NOT an agent repair or benchmark result.
    with tempfile.TemporaryDirectory(prefix="repair-smoke-") as tmp:
        run_dir = Path(tmp) / "self-test"
        prepare(run_dir)
        assert verify(run_dir)["reason"] == "no_patch"
        source = run_dir / "workspace" / "batching.py"
        source.write_text(source.read_text().replace("len(items) - batch_size + 1", "len(items)"))
        result = verify(run_dir)
        assert result["verification_status"] == "passed", result
        print("Harness self-test passed: 3 baseline failures -> known manual patch -> 75 tests passed.")
        print("No LLM was called. This is not an agent success result.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["doctor", "smoke", "prepare", "run", "verify"])
    parser.add_argument("--id", default="run-001", help="A new ID for each independent attempt")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.id):
        parser.error("--id must contain only letters, digits, underscores, and hyphens")
    run_dir = ROOT / "runs" / args.id
    if args.action == "doctor":
        doctor()
    elif args.action == "smoke":
        smoke()
    elif args.action == "prepare":
        prepare(run_dir)
        print(f"Prepared {args.id}: 3 failed, 3 passed (the intended broken baseline).")
    else:
        result = run_agent(run_dir) if args.action == "run" else verify(run_dir)
        print(json.dumps(result, indent=2))
        return 0 if result["verification_status"] == "passed" else 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
