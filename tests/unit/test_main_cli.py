"""Tests unitaires (sans I/O) pour les utilitaires CLI de main.py."""
from pathlib import Path

from main import _describe_invocation_command


def test_describe_invocation_command_simple_args(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "https://www.youtube.com/watch?v=rmtjVPmnn-Q", "--asc"],
    )
    cmd = _describe_invocation_command()
    expected_bat = Path(__file__).resolve().parent.parent.parent / "spo-translate-video.bat"
    assert cmd == f'{expected_bat} https://www.youtube.com/watch?v=rmtjVPmnn-Q --asc'


def test_describe_invocation_command_quotes_args_with_spaces(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "C:\\path with spaces\\video.mkv", "--dest", "C:\\some output"],
    )
    cmd = _describe_invocation_command()
    assert '"C:\\path with spaces\\video.mkv"' in cmd
    assert '"C:\\some output"' in cmd


def test_describe_invocation_command_no_args(monkeypatch):
    monkeypatch.setattr("sys.argv", ["main.py"])
    cmd = _describe_invocation_command()
    assert cmd.endswith("spo-translate-video.bat")
