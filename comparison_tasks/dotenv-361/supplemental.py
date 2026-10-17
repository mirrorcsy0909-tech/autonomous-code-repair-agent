"""Harness-authored append/replace preservation checks."""
import pytest
from dotenv import dotenv_values, set_key


@pytest.mark.parametrize("before", ["A=one", "A=one\n", "A=one\n\n", "# comment", "A=one\n# tail", ""])
@pytest.mark.parametrize("export", [True, False])
def test_append_preserves_existing_text_and_separates_entry(tmp_path, before, export):
    path = tmp_path / ".env"
    path.write_text(before)
    assert set_key(str(path), "NEW", "value", export=export) == (True, "NEW", "value")
    separator = "\n" if before and not before.endswith("\n") else ""
    entry = ("export " if export else "") + "NEW='value'\n"
    assert path.read_text() == before + separator + entry
    assert dotenv_values(str(path))["NEW"] == "value"


@pytest.mark.parametrize("before,expected", [
    ("A=old", "A='new'\n"),
    ("A=old\nB=keep", "A='new'\nB=keep"),
    ("# heading\nA=old", "# heading\nA='new'\n"),
    ("A=old\n\n", "A='new'\n\n"),
])
def test_replacing_does_not_append_a_duplicate(tmp_path, before, expected):
    path = tmp_path / ".env"
    path.write_text(before)
    set_key(str(path), "A", "new")
    assert path.read_text() == expected


@pytest.mark.parametrize("quote_mode,value,entry", [
    ("never", "simple", "NEW=simple\n"),
    ("auto", "simple", "NEW=simple\n"),
    ("always", "simple", "NEW='simple'\n"),
    ("always", "", "NEW=''\n"),
    ("always", "你好", "NEW='你好'\n"),
])
def test_quote_modes_when_newline_is_missing(tmp_path, quote_mode, value, entry):
    path = tmp_path / ".env"
    path.write_text("A=one")
    set_key(str(path), "NEW", value, quote_mode=quote_mode)
    assert path.read_text() == "A=one\n" + entry
