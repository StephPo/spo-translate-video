"""Tests unitaires (sans I/O) des fonctions de sélection de chapitres de main.py."""
import pytest

from main import (
    _autoselect_chapters,
    _listchapters_selection_preview,
    _parse_chapter_selection,
    _resolve_chapter_selection,
)


def _ch(title):
    return {"tags": {"title": title}}


SAMPLE_CHAPTERS = [
    _ch("Intro"),            # 0
    _ch("The Beginning"),    # 1
    _ch("MC1"),               # 2
    _ch("The Continuation"), # 3
    _ch("MC2"),               # 4
    _ch("Outro"),             # 5
]

DEFAULT_PATTERNS = ["^MC", "^Intro$", "^Outro$"]


def test_parse_chapter_selection_single_indices():
    assert _parse_chapter_selection("2,4", 6) == [1, 3]


def test_parse_chapter_selection_range():
    assert _parse_chapter_selection("2,5-6", 6) == [1, 4, 5]


def test_parse_chapter_selection_out_of_bounds_ignored():
    assert _parse_chapter_selection("0,7,3", 6) == [2]


def test_parse_chapter_selection_empty_spec():
    assert _parse_chapter_selection("", 6) == []


def test_autoselect_chapters_matches_default_patterns():
    assert _autoselect_chapters(SAMPLE_CHAPTERS, DEFAULT_PATTERNS) == [0, 2, 4, 5]


def test_autoselect_chapters_case_insensitive():
    chapters = [_ch("intro"), _ch("outro")]
    assert _autoselect_chapters(chapters, DEFAULT_PATTERNS) == [0, 1]


def test_autoselect_chapters_no_match():
    chapters = [_ch("The Beginning"), _ch("The Continuation")]
    assert _autoselect_chapters(chapters, DEFAULT_PATTERNS) == []


def test_autoselect_chapters_missing_title_tag():
    chapters = [{"tags": {}}, _ch("MC1")]
    assert _autoselect_chapters(chapters, DEFAULT_PATTERNS) == [1]


def test_resolve_chapter_selection_manual_only():
    selected = _resolve_chapter_selection(SAMPLE_CHAPTERS, manual_spec="2,4", autoselect=False, patterns=DEFAULT_PATTERNS)
    assert selected == [1, 3]


def test_resolve_chapter_selection_autoselect_only():
    selected = _resolve_chapter_selection(SAMPLE_CHAPTERS, manual_spec=None, autoselect=True, patterns=DEFAULT_PATTERNS)
    assert selected == [0, 2, 4, 5]


def test_resolve_chapter_selection_union_of_manual_and_auto():
    # manuel = chapitre 2 ("The Beginning", idx 1), auto = Intro/MC1/MC2/Outro (idx 0,2,4,5)
    selected = _resolve_chapter_selection(SAMPLE_CHAPTERS, manual_spec="2", autoselect=True, patterns=DEFAULT_PATTERNS)
    assert selected == [0, 1, 2, 4, 5]


def test_resolve_chapter_selection_none_requested():
    selected = _resolve_chapter_selection(SAMPLE_CHAPTERS, manual_spec=None, autoselect=False, patterns=DEFAULT_PATTERNS)
    assert selected == []


def test_listchapters_preview_autoselects_by_default_even_without_flag():
    """--listchapters doit toujours prévisualiser l'auto-sélection (pour vérifier
    rapidement les regex), même si --autoselectchapters n'est pas passé."""
    selected = _listchapters_selection_preview(
        SAMPLE_CHAPTERS, manual_spec=None, autoselectchapters=False, patterns=DEFAULT_PATTERNS,
    )
    assert selected == [0, 2, 4, 5]


def test_listchapters_preview_combines_manual_selection():
    selected = _listchapters_selection_preview(
        SAMPLE_CHAPTERS, manual_spec="2", autoselectchapters=False, patterns=DEFAULT_PATTERNS,
    )
    assert selected == [0, 1, 2, 4, 5]
