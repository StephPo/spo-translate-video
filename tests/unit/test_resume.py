"""Tests unitaires (sans I/O rÃ©el) pour le comportement de `--resume` dans main.py :
reprise indÃ©pendante des 3 phases (tÃ©lÃ©chargement, transcription, traduction).
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from speech_recognizer import TranscriptionResult, TranscriptionSegment
from translator import TranslationResult, TranslationSegment
from video_downloader import DownloadResult


# --------------------------------------------------------------------------
# Helpers / fakes
# --------------------------------------------------------------------------

class _FakeAudioProcessor:
    def __init__(self, config):
        self.calls = []

    def extract_audio(self, video_path, start_time=None, end_time=None):
        self.calls.append((video_path, start_time, end_time))
        return SimpleNamespace(success=True, audio_path="fake_audio.wav", error=None)


class _FakeRecognizer:
    def __init__(self, segments):
        self._segments = segments
        self.calls = 0

    def transcribe(self, audio_path, language="ja"):
        self.calls += 1
        return TranscriptionResult(success=True, segments=self._segments)


class _FakeTranslator:
    def __init__(self):
        self.received_segments = None

    def translate_segments(self, segments, source_lang, target_lang):
        self.received_segments = list(segments)
        translated = [TranslationSegment(original_text=s, translated_text=s.upper()) for s in segments]
        return TranslationResult(success=True, segments=translated)


def _install_fakes(monkeypatch, *, audio_processor=None, recognizer=None, translator=None):
    audio_processor = audio_processor or _FakeAudioProcessor(None)
    recognizer = recognizer or _FakeRecognizer([])
    translator = translator or _FakeTranslator()

    monkeypatch.setattr(main, "AudioProcessor", lambda config: audio_processor)
    monkeypatch.setattr(
        main.SpeechRecognizerFactory, "create_recognizer", staticmethod(lambda config: recognizer)
    )
    monkeypatch.setattr(
        main.TranslatorFactory, "create_translator", staticmethod(lambda config: translator)
    )
    return audio_processor, recognizer, translator


# --------------------------------------------------------------------------
# Phase 2 + 3: transcription / translation resume (_translate_range)
# --------------------------------------------------------------------------

def test_translate_range_without_resume_runs_full_pipeline(tmp_path, monkeypatch):
    segments = [TranscriptionSegment(start_time=0.0, end_time=1.0, text="hello", confidence=1.0)]
    audio_processor, recognizer, translator = _install_fakes(
        monkeypatch, recognizer=_FakeRecognizer(segments)
    )

    cues = main._translate_range(
        config={}, logger=main.logging.getLogger("test"), video_path="video.mp4",
        output_basename="video", subtitles_dir=tmp_path, source_lang="ja", target_lang="fr",
        resume=False,
    )

    assert len(audio_processor.calls) == 1
    assert recognizer.calls == 1
    assert translator.received_segments == ["hello"]
    assert [c.text for c in cues] == ["HELLO"]


def test_translate_range_resumes_from_partial_translation_cache(tmp_path, monkeypatch):
    """Regression: some segments already translated before a failure -- must resume translation
    without re-running audio extraction / transcription."""
    audio_processor, recognizer, translator = _install_fakes(monkeypatch)

    cache_path = main._cache_file_path(tmp_path, "video", "fr")
    main._save_cache(cache_path, {
        "starts": [0.0, 1.0], "ends": [1.0, 2.0],
        "originals": ["bonjour", "monde"], "segments": ["HELLO"],
    })

    cues = main._translate_range(
        config={}, logger=main.logging.getLogger("test"), video_path="video.mp4",
        output_basename="video", subtitles_dir=tmp_path, source_lang="ja", target_lang="fr",
        resume=True,
    )

    # Audio extraction and transcription must be skipped entirely.
    assert audio_processor.calls == []
    assert recognizer.calls == 0
    # Only the remaining (not-yet-translated) segment is sent to the translator.
    assert translator.received_segments == ["monde"]
    assert [c.text for c in cues] == ["HELLO", "MONDE"]
    # Cache is cleared once every segment is translated.
    assert not cache_path.exists()


def test_translate_range_resumes_with_empty_segments_cache_does_not_retranscribe(tmp_path, monkeypatch):
    """Regression test for the bug where a failure on the very first segment (empty `segments`
    list, e.g. right after a wrong API key) made `--resume` fall back to re-running audio
    extraction + Whisper transcription instead of reusing the already-cached transcription."""
    audio_processor, recognizer, translator = _install_fakes(monkeypatch)

    cache_path = main._cache_file_path(tmp_path, "video", "fr")
    main._save_cache(cache_path, {
        "starts": [0.0], "ends": [1.0], "originals": ["bonjour"], "segments": [],
    })

    cues = main._translate_range(
        config={}, logger=main.logging.getLogger("test"), video_path="video.mp4",
        output_basename="video", subtitles_dir=tmp_path, source_lang="ja", target_lang="fr",
        resume=True,
    )

    assert audio_processor.calls == []
    assert recognizer.calls == 0
    assert translator.received_segments == ["bonjour"]
    assert [c.text for c in cues] == ["BONJOUR"]


def test_translate_range_resume_ignores_stale_cache_when_flag_not_set(tmp_path, monkeypatch):
    """Without --resume, an existing cache file must be ignored (full pipeline re-run)."""
    segments = [TranscriptionSegment(start_time=0.0, end_time=1.0, text="hello", confidence=1.0)]
    audio_processor, recognizer, translator = _install_fakes(
        monkeypatch, recognizer=_FakeRecognizer(segments)
    )

    cache_path = main._cache_file_path(tmp_path, "video", "fr")
    main._save_cache(cache_path, {
        "starts": [0.0], "ends": [1.0], "originals": ["bonjour"], "segments": [],
    })

    main._translate_range(
        config={}, logger=main.logging.getLogger("test"), video_path="video.mp4",
        output_basename="video", subtitles_dir=tmp_path, source_lang="ja", target_lang="fr",
        resume=False,
    )

    assert len(audio_processor.calls) == 1
    assert recognizer.calls == 1
    assert translator.received_segments == ["hello"]


def test_translate_range_saves_cache_on_translation_failure(tmp_path, monkeypatch):
    """When translation fails outright, progress (transcription + whatever translated so far)
    must be persisted so a subsequent --resume can pick it up."""
    segments = [TranscriptionSegment(start_time=0.0, end_time=1.0, text="hello", confidence=1.0)]

    class _FailingTranslator:
        def translate_segments(self, segments, source_lang, target_lang):
            raise RuntimeError("401 Unauthorized: invalid API key")

    audio_processor, recognizer, translator = _install_fakes(
        monkeypatch, recognizer=_FakeRecognizer(segments), translator=_FailingTranslator()
    )

    with pytest.raises(SystemExit):
        main._translate_range(
            config={}, logger=main.logging.getLogger("test"), video_path="video.mp4",
            output_basename="video", subtitles_dir=tmp_path, source_lang="ja", target_lang="fr",
            resume=False,
        )

    cache_path = main._cache_file_path(tmp_path, "video", "fr")
    assert cache_path.exists()
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["originals"] == ["hello"]
    assert cached["segments"] == []


# --------------------------------------------------------------------------
# Phase 1: download resume (_prepare_input)
# --------------------------------------------------------------------------

def test_prepare_input_local_source_ignores_download_cache(tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"data")

    path, title, quality_info, quality_warning = main._prepare_input(
        str(video_path), {}, main.logging.getLogger("test"),
        resume=True, download_cache_path=tmp_path / "download_cache.json",
    )

    assert path == str(video_path)
    assert title == "video"


def test_prepare_input_resume_reuses_cached_download_when_file_still_exists(tmp_path, monkeypatch):
    video_path = tmp_path / "My Video.mp4"
    video_path.write_bytes(b"data")

    download_cache_path = tmp_path / "download_cache.json"
    main._save_cache(download_cache_path, {"video_path": str(video_path), "title": "My Video"})

    def _fail_downloader(config):
        raise AssertionError("VideoDownloader should not be instantiated when resuming from cache")

    monkeypatch.setattr(main, "VideoDownloader", _fail_downloader)

    path, title, quality_info, quality_warning = main._prepare_input(
        "https://www.youtube.com/watch?v=abc123", {}, main.logging.getLogger("test"),
        resume=True, download_cache_path=download_cache_path,
    )

    assert path == str(video_path)
    assert title == "My Video"
    assert quality_info is None
    assert quality_warning is None


def test_prepare_input_resume_redownloads_when_cached_file_missing(tmp_path, monkeypatch):
    missing_path = tmp_path / "gone.mp4"  # never created
    download_cache_path = tmp_path / "download_cache.json"
    main._save_cache(download_cache_path, {"video_path": str(missing_path), "title": "Gone"})

    downloaded_path = tmp_path / "output" / "Fresh Video.mp4"
    downloaded_path.parent.mkdir(parents=True, exist_ok=True)
    downloaded_path.write_bytes(b"data")

    class _FakeDownloader:
        def __init__(self, config):
            pass

        def download_from_youtube(self, url):
            return DownloadResult(success=True, video_path=str(downloaded_path), title="Fresh Video")

    monkeypatch.setattr(main, "VideoDownloader", _FakeDownloader)

    path, title, quality_info, quality_warning = main._prepare_input(
        "https://www.youtube.com/watch?v=abc123", {}, main.logging.getLogger("test"),
        resume=True, download_cache_path=download_cache_path,
    )

    assert path == str(downloaded_path)
    assert title == "Fresh Video"
    # The cache is refreshed with the new download.
    cached = json.loads(download_cache_path.read_text(encoding="utf-8"))
    assert cached["video_path"] == str(downloaded_path)


def test_prepare_input_without_resume_flag_always_downloads(tmp_path, monkeypatch):
    """Even if a download cache exists, it must be ignored unless --resume is passed."""
    video_path = tmp_path / "My Video.mp4"
    video_path.write_bytes(b"data")

    download_cache_path = tmp_path / "download_cache.json"
    main._save_cache(download_cache_path, {"video_path": str(video_path), "title": "My Video"})

    called = {"count": 0}

    class _FakeDownloader:
        def __init__(self, config):
            pass

        def download_from_youtube(self, url):
            called["count"] += 1
            return DownloadResult(success=True, video_path=str(video_path), title="My Video")

    monkeypatch.setattr(main, "VideoDownloader", _FakeDownloader)

    main._prepare_input(
        "https://www.youtube.com/watch?v=abc123", {}, main.logging.getLogger("test"),
        resume=False, download_cache_path=download_cache_path,
    )

    assert called["count"] == 1


def test_download_cache_path_is_stable_and_source_specific(tmp_path):
    p1 = main._download_cache_path(tmp_path, "https://www.youtube.com/watch?v=abc123")
    p2 = main._download_cache_path(tmp_path, "https://www.youtube.com/watch?v=abc123")
    p3 = main._download_cache_path(tmp_path, "https://www.youtube.com/watch?v=xyz789")

    assert p1 == p2
    assert p1 != p3
    assert p1.parent == tmp_path

