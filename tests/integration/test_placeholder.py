"""Ancien squelette de démonstration.

Remplacé par des tests réels : voir tests/integration/test_chapters.py
(chapitres sur test.mkv) et tests/integration/test_downloader.py (URL YouTube).
"""
import pytest

from pathlib import Path

pytestmark = pytest.mark.integration


def test_fixtures_directory_exists():
    assert (Path(__file__).resolve().parent.parent / "fixtures").is_dir()
