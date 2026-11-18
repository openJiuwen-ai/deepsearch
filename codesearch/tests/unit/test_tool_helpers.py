"""Tests for coder-tool helpers (platform-aware command splitting, COPY path quoting)."""

from pathlib import Path

from openjiuwen_codesearch.algorithm.coder_tools.run_bash import split_command
from openjiuwen_search_base.codegraph.backends.ladybug import _copy_path_literal


def test_split_command_posix_mode(monkeypatch):
    monkeypatch.setattr("openjiuwen_codesearch.algorithm.coder_tools.run_bash.os.name", "posix")
    assert split_command("pytest tests/ -k 'a or b'") == ["pytest", "tests/", "-k", "a or b"]


def test_split_command_keeps_windows_paths(monkeypatch):
    monkeypatch.setattr("openjiuwen_codesearch.algorithm.coder_tools.run_bash.os.name", "nt")
    # POSIX 分词会把反斜杠当转义：C:\tmp\a.txt -> C:tmpa.txt
    assert split_command(r"type C:\tmp\a.txt") == ["type", r"C:\tmp\a.txt"]


def test_split_command_strips_quotes_on_windows(monkeypatch):
    monkeypatch.setattr("openjiuwen_codesearch.algorithm.coder_tools.run_bash.os.name", "nt")
    assert split_command('python "my script.py" run') == ["python", "my script.py", "run"]


def test_copy_path_literal_uses_forward_slashes():
    assert _copy_path_literal(Path(r"C:\tmp\dir\part.csv")) == "C:/tmp/dir/part.csv"


def test_copy_path_literal_escapes_double_quote():
    assert _copy_path_literal(Path('/tmp/we"ird.csv')) == '/tmp/we""ird.csv'
