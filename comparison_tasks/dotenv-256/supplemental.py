"""Harness-authored variable expansion checks for the historical precedence rule."""
import io
import os
import pytest
from dotenv import dotenv_values, load_dotenv


@pytest.mark.parametrize("file_value", ["from-file", "", "你好", "a/b"])
@pytest.mark.parametrize("env_value", ["from-env", "", "other"])
def test_file_binding_wins_for_expansion(monkeypatch, file_value, env_value):
    monkeypatch.setenv("BASE", env_value)
    result = dotenv_values(stream=io.StringIO(f'BASE="{file_value}"\nRESULT=${{BASE}}/tail\n'))
    assert result == {"BASE": file_value, "RESULT": file_value + "/tail"}


@pytest.mark.parametrize("file_value", ["from-file", "", "你好"])
def test_environment_fallback_is_still_used(monkeypatch, file_value):
    monkeypatch.setenv("BASE", file_value)
    assert dotenv_values(stream=io.StringIO("RESULT=${BASE}/tail\n")) == {"RESULT": file_value + "/tail"}


@pytest.mark.parametrize("override", [True, False])
def test_loading_keeps_existing_assignment_policy(monkeypatch, override):
    monkeypatch.setenv("BASE", "from-env")
    monkeypatch.delenv("RESULT", raising=False)
    assert load_dotenv(stream=io.StringIO("BASE=from-file\nRESULT=${BASE}/tail\n"), override=override)
    assert os.environ["BASE"] == ("from-file" if override else "from-env")
    # This historical fix predates the later override-aware interpolation API.
    assert os.environ["RESULT"] == "from-file/tail"


def test_disabled_interpolation_preserves_literal(monkeypatch):
    monkeypatch.setenv("BASE", "env")
    assert dotenv_values(stream=io.StringIO("BASE=file\nRESULT=${BASE}\n"), interpolate=False) == {
        "BASE": "file", "RESULT": "${BASE}"}


def test_self_reference_uses_prior_environment_value(monkeypatch):
    monkeypatch.setenv("BASE", "env")
    assert dotenv_values(stream=io.StringIO("BASE=${BASE}/tail\n")) == {"BASE": "env/tail"}


def test_missing_value_and_default(monkeypatch):
    monkeypatch.delenv("ABSENT", raising=False)
    assert dotenv_values(stream=io.StringIO("A=${ABSENT}\nB=${ABSENT:-fallback}\n")) == {
        "A": "", "B": "fallback"}
