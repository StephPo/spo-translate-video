"""Tests d'intégration : extraction et sélection de chapitres sur une vraie
vidéo locale (tests/fixtures/videos/test.mkv), avec résultat attendu déduit
dynamiquement de tests/fixtures/videos/test_chapters.txt.

Nécessite ffprobe accessible (voir config.yaml: video.ffmpeg_path).
"""
import re

import pytest

from main import _autoselect_chapters, _ffprobe_chapters, _resolve_chapter_selection

pytestmark = pytest.mark.integration


def test_ffprobe_chapters_matches_fixture_metadata(sample_chapters_video, expected_chapters):
    chapters = _ffprobe_chapters(str(sample_chapters_video), "ffmpeg")
    assert len(chapters) == len(expected_chapters)

    titles = [(c.get("tags") or {}).get("title") for c in chapters]
    expected_titles = [c["title"] for c in expected_chapters]
    assert titles == expected_titles

    for chapter, expected in zip(chapters, expected_chapters):
        assert float(chapter["start_time"]) == pytest.approx(expected["start"], abs=0.05)


def test_autoselect_chapters_matches_config_patterns(sample_chapters_video, expected_chapters, chapter_autoselect_patterns):
    chapters = _ffprobe_chapters(str(sample_chapters_video), "ffmpeg")
    selected_idx = _autoselect_chapters(chapters, chapter_autoselect_patterns)

    expected_idx = [
        i for i, ch in enumerate(expected_chapters)
        if any(re.search(p, ch["title"], re.IGNORECASE) for p in chapter_autoselect_patterns)
    ]
    assert selected_idx == expected_idx

    selected_titles = [expected_chapters[i]["title"] for i in selected_idx]
    assert selected_titles == ["Intro", "MC1", "MC2", "Outro"]


def test_resolve_chapter_selection_union_manual_and_auto(sample_chapters_video, expected_chapters, chapter_autoselect_patterns):
    chapters = _ffprobe_chapters(str(sample_chapters_video), "ffmpeg")
    auto_idx = _autoselect_chapters(chapters, chapter_autoselect_patterns)

    # Sélection manuelle du chapitre 2 (1-based) = "The Beginning" (idx 1),
    # normalement non couvert par l'auto-sélection.
    combined = _resolve_chapter_selection(chapters, manual_spec="2", autoselect=True, patterns=chapter_autoselect_patterns)

    assert combined == sorted(set(auto_idx) | {1})
