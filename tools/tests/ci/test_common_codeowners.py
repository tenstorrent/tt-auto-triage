"""Tests for tools.ci.common.codeowners."""

from __future__ import annotations

from tools.ci.common.codeowners import codeowners_match, parse_codeowners, parse_codeowners_logins


SAMPLE_CODEOWNERS = """\
# Top-level
* @org/default-team @alice

# Tests
tests/ @bob @org/qa-team

# Models
models/*.py @carol
"""


def test_parse_codeowners_keep_teams(tmp_path):
    path = tmp_path / "CODEOWNERS"
    path.write_text(SAMPLE_CODEOWNERS)
    rules = parse_codeowners(path, keep_teams=True)
    assert len(rules) == 3
    assert rules[0] == ("*", ["org/default-team", "alice"])
    assert rules[1] == ("tests/", ["bob", "org/qa-team"])


def test_parse_codeowners_drop_teams(tmp_path):
    path = tmp_path / "CODEOWNERS"
    path.write_text(SAMPLE_CODEOWNERS)
    rules = parse_codeowners(path, keep_teams=False)
    assert len(rules) == 3
    assert rules[0] == ("*", ["alice"])
    assert rules[1] == ("tests/", ["bob"])
    assert rules[2] == ("models/*.py", ["carol"])


def test_parse_codeowners_missing_file(tmp_path):
    assert parse_codeowners(tmp_path / "nope") == []


def test_codeowners_match_glob():
    assert codeowners_match("tests/foo.py", "tests/")
    assert codeowners_match("models/bar.py", "models/*.py")


def test_codeowners_match_basename():
    assert codeowners_match("deeply/nested/Makefile", "Makefile")


def test_codeowners_match_no_match():
    assert not codeowners_match("src/main.py", "tests/")


def test_parse_codeowners_logins(tmp_path):
    path = tmp_path / "CODEOWNERS"
    path.write_text(SAMPLE_CODEOWNERS)
    logins = parse_codeowners_logins(path)
    assert "alice" in logins
    assert "bob" in logins
    assert "carol" in logins
    assert all("/" not in l for l in logins)


def test_parse_codeowners_logins_deduplicates(tmp_path):
    path = tmp_path / "CODEOWNERS"
    path.write_text("* @Alice\ntests/ @alice\n")
    logins = parse_codeowners_logins(path)
    assert len(logins) == 1
