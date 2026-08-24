"""Tests unitaires (sans appel reseau reel) pour translator.py, en particulier
la logique d'alignement segments <-> traductions du fournisseur OpenAI."""
import sys
import types
from types import SimpleNamespace

import pytest

from translator import OpenAITranslator


def _fake_openai_module(responses):
    """Construit un faux module `openai` dont le client renvoie successivement
    les contenus de `responses` (un par appel a chat.completions.create)."""
    calls = []

    class _FakeCompletions:
        def create(self, model, messages):
            calls.append(messages[-1]["content"])
            content = responses[len(calls) - 1]
            message = SimpleNamespace(content=content)
            choice = SimpleNamespace(message=message)
            return SimpleNamespace(choices=[choice])

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, api_key=None, max_retries=0):
            self.chat = _FakeChat()

    fake_module = types.ModuleType("openai")
    fake_module.OpenAI = _FakeOpenAI
    return fake_module, calls


def _make_translator(monkeypatch, responses, batch_size=10):
    fake_module, calls = _fake_openai_module(responses)
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    config = {
        "translation": {
            "api_keys": {"openai": "fake-key"},
            "openai": {"model": "gpt-4o", "batch_size": batch_size},
        }
    }
    return OpenAITranslator(config), calls


def test_translate_segments_aligned_batch(monkeypatch):
    """Cas nominal : le modele renvoie exactement une ligne par segment."""
    segments = [f"seg{i}" for i in range(1, 6)]
    response = "\n".join(f"{i}. T{i}" for i in range(1, 6))
    translator, calls = _make_translator(monkeypatch, [response])

    result = translator.translate_segments(segments, "ja", "fr")

    assert result.success
    assert [s.translated_text for s in result.segments] == [f"T{i}" for i in range(1, 6)]
    assert len(calls) == 1


def test_translate_segments_retries_on_line_count_mismatch(monkeypatch):
    """Si le modele fusionne des segments (moins de lignes que de segments dans
    le batch), le batch doit etre retraduit segment par segment plutot que de
    laisser les segments en trop retomber sur le texte source non traduit."""
    segments = [f"seg{i}" for i in range(1, 6)]
    # Premiere reponse (batch complet) : seulement 2 lignes pour 5 segments.
    batch_response = "1. T1-T2 fusionnes\n2. autre chose"
    # Reponses de secours (un segment a la fois, dans l'ordre).
    single_responses = [f"1. T{i}" for i in range(1, 6)]
    translator, calls = _make_translator(monkeypatch, [batch_response] + single_responses)

    result = translator.translate_segments(segments, "ja", "fr")

    assert result.success
    # Aucun segment ne doit rester non traduit (retombee sur le texte source).
    assert [s.translated_text for s in result.segments] == [f"T{i}" for i in range(1, 6)]
    for original, seg in zip(segments, result.segments):
        assert seg.translated_text != original
    # 1 appel batch + 5 appels de secours individuels.
    assert len(calls) == 6


def test_translate_segments_single_segment_no_retry(monkeypatch):
    """Un batch d'un seul segment n'a pas besoin de logique de retry speciale."""
    translator, calls = _make_translator(monkeypatch, ["1. Translated"], batch_size=1)

    result = translator.translate_segments(["only"], "ja", "fr")

    assert result.success
    assert result.segments[0].translated_text == "Translated"
    assert len(calls) == 1
