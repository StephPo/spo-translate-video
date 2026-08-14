"""Tests unitaires (sans I/O) pour les utilitaires CLI de main.py."""
import logging
from pathlib import Path

from main import _describe_invocation_command, _setup_logging


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


# --------------------------------------------------------------------------
# _setup_logging: log file creation / overwrite (see SPECIFICATIONS.md section 3.11)
# --------------------------------------------------------------------------

def _reset_logging():
    """`_setup_logging` uses `logging.basicConfig(..., force=True)`, which replaces the root
    logger's handlers; close them first so file handles aren't leaked across tests."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)


def test_setup_logging_creates_log_file(tmp_path):
    log_path = tmp_path / "logs" / "run.log"
    config = {"processing": {"log_to_file": True, "log_file_path": str(log_path)}}
    try:
        logger = _setup_logging(config)
        logger.info("hello from test")
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "hello from test" in content
        assert f"Log file: {log_path}" in content
    finally:
        _reset_logging()


def test_setup_logging_overwrites_previous_run_log(tmp_path):
    log_path = tmp_path / "logs" / "run.log"
    config = {"processing": {"log_to_file": True, "log_file_path": str(log_path)}}
    try:
        logger1 = _setup_logging(config)
        logger1.info("first run message")
        _reset_logging()

        logger2 = _setup_logging(config)
        logger2.info("second run message")
        for handler in logging.getLogger().handlers:
            handler.flush()

        content = log_path.read_text(encoding="utf-8")
        assert "second run message" in content
        assert "first run message" not in content
    finally:
        _reset_logging()


def test_setup_logging_strips_ansi_codes_from_file(tmp_path, capsys):
    log_path = tmp_path / "logs" / "run.log"
    config = {"processing": {"log_to_file": True, "log_file_path": str(log_path)}}
    try:
        logger = _setup_logging(config)
        colored_message = "\x1b[92mDownload complete: video.mp4\x1b[0m"
        logger.info(colored_message)
        for handler in logging.getLogger().handlers:
            handler.flush()

        content = log_path.read_text(encoding="utf-8")
        assert "Download complete: video.mp4" in content
        assert "\x1b[" not in content

        console_out = capsys.readouterr().out
        assert colored_message in console_out
    finally:
        _reset_logging()


def test_setup_logging_disabled_does_not_create_file(tmp_path):
    log_path = tmp_path / "logs" / "run.log"
    config = {"processing": {"log_to_file": False, "log_file_path": str(log_path)}}
    try:
        _setup_logging(config)
        assert not log_path.exists()
    finally:
        _reset_logging()
