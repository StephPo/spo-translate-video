import tempfile
from pathlib import Path

import pytest

from video_downloader import VideoDownloader, detect_source_type


def test_detect_source_type_youtube_watch():
    assert detect_source_type("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"


def test_detect_source_type_youtube_short_url():
    assert detect_source_type("https://youtu.be/dQw4w9WgXcQ") == "youtube"


def test_detect_source_type_youtube_shorts():
    assert detect_source_type("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "youtube"


def test_detect_source_type_m3u8():
    assert detect_source_type("https://example.com/stream/playlist.m3u8") == "m3u8"


def test_detect_source_type_local_file():
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        assert detect_source_type(str(tmp_path)) == "local"
    finally:
        tmp_path.unlink(missing_ok=True)


def test_detect_source_type_unresolvable_raises():
    with pytest.raises(ValueError):
        detect_source_type("not-a-real-path-or-url")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Normal Title", "Normal Title"),
        ('Bad: Name / With <Chars> "Quoted" | Pipe ? * \\', "Bad_ Name _ With _Chars_ _Quoted_ _ Pipe _ _ _"),
        ("   ", "video"),
        ("", "video"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert VideoDownloader._sanitize_filename(raw) == expected


def _make_downloader(tmp_path: Path) -> VideoDownloader:
    config = {
        "output": {"video_download_directory": str(tmp_path / "output")},
        "video": {"temp_directory": str(tmp_path / "temp"), "ffmpeg_path": "ffmpeg"},
    }
    return VideoDownloader(config)


class _FakeYoutubeDL:
    def __init__(self, opts, info, temp_video_path: Path):
        self._opts = opts
        self._info = info
        self._temp_video_path = temp_video_path

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download=True):
        if download:
            self._temp_video_path.parent.mkdir(parents=True, exist_ok=True)
            self._temp_video_path.write_bytes(b"fake video data")
        return self._info

    def prepare_filename(self, info):
        return str(self._temp_video_path)


def test_download_from_youtube_uses_sanitized_title_as_filename(tmp_path, monkeypatch):
    """The downloaded video must be named after the sanitized YouTube title, not the video id."""
    downloader = _make_downloader(tmp_path)
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url: None)

    raw_title = 'My Video: Part 1 / "Intro"?'
    temp_video_path = downloader.temp_dir / "dQw4w9WgXcQ.mp4"
    info = {"id": "dQw4w9WgXcQ", "ext": "mp4", "title": raw_title}

    import video_downloader as vd_module

    monkeypatch.setattr(
        vd_module.yt_dlp,
        "YoutubeDL",
        lambda opts: _FakeYoutubeDL(opts, info, temp_video_path),
    )

    result = downloader.download_from_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result.success, result.error
    expected_name = VideoDownloader._sanitize_filename(raw_title) + ".mp4"
    assert Path(result.video_path).name == expected_name
    assert Path(result.video_path).exists()
    assert result.title == raw_title


def test_download_from_youtube_output_basename_matches_video_and_would_produce_matching_srt(tmp_path, monkeypatch):
    """Video and subtitle files must share the same basename (except extension)."""
    downloader = _make_downloader(tmp_path)
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url: None)

    raw_title = "Some: Title?"
    temp_video_path = downloader.temp_dir / "abc123.mp4"
    info = {"id": "abc123", "ext": "mp4", "title": raw_title}

    import video_downloader as vd_module

    monkeypatch.setattr(
        vd_module.yt_dlp,
        "YoutubeDL",
        lambda opts: _FakeYoutubeDL(opts, info, temp_video_path),
    )

    result = downloader.download_from_youtube("https://www.youtube.com/watch?v=abc123")

    assert result.success, result.error
    video_path = Path(result.video_path)
    output_basename = video_path.stem
    srt_path = video_path.with_name(f"{output_basename}.fr.srt")
    assert srt_path.stem.startswith(video_path.stem)
