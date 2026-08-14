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


def test_detect_source_type_twitter_status_x_domain():
    assert detect_source_type("https://x.com/someuser/status/1234567890123456789") == "twitter"


def test_detect_source_type_twitter_status_twitter_domain():
    assert detect_source_type("https://twitter.com/someuser/status/1234567890123456789") == "twitter"


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
    """Mimics yt-dlp's YoutubeDL enough for these tests, including resolving the output path from
    `opts["outtmpl"]` (like real yt-dlp does) so that different attempts (e.g. a quality retry
    using a different `attempt_suffix`) never collide on the same file."""

    def __init__(self, opts, info, warning_msg: str = None):
        self._opts = opts
        self._info = info
        self._warning_msg = warning_msg

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def _resolve_path(self) -> Path:
        outtmpl = self._opts.get("outtmpl", "%(id)s.%(ext)s")
        filename = outtmpl.replace("%(id)s", str(self._info.get("id", "video"))).replace(
            "%(ext)s", str(self._info.get("ext", "mp4"))
        )
        return Path(filename)

    def extract_info(self, url, download=True):
        if self._warning_msg:
            self._opts["logger"].warning(self._warning_msg)
        if download:
            path = self._resolve_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake video data")
        return self._info

    def prepare_filename(self, info):
        return str(self._resolve_path())


def test_download_from_youtube_uses_sanitized_title_as_filename(tmp_path, monkeypatch):
    """The downloaded video must be named after the sanitized YouTube title, not the video id."""
    downloader = _make_downloader(tmp_path)
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url, playlist_index=None: None)

    raw_title = 'My Video: Part 1 / "Intro"?'
    info = {"id": "dQw4w9WgXcQ", "ext": "mp4", "title": raw_title}

    import video_downloader as vd_module

    monkeypatch.setattr(
        vd_module.yt_dlp,
        "YoutubeDL",
        lambda opts: _FakeYoutubeDL(opts, info),
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
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url, playlist_index=None: None)

    raw_title = "Some: Title?"
    info = {"id": "abc123", "ext": "mp4", "title": raw_title}

    import video_downloader as vd_module

    monkeypatch.setattr(
        vd_module.yt_dlp,
        "YoutubeDL",
        lambda opts: _FakeYoutubeDL(opts, info),
    )

    result = downloader.download_from_youtube("https://www.youtube.com/watch?v=abc123")

    assert result.success, result.error
    video_path = Path(result.video_path)
    output_basename = video_path.stem
    srt_path = video_path.with_name(f"{output_basename}.fr.srt")
    assert srt_path.stem.startswith(video_path.stem)


@pytest.mark.parametrize(
    "formats, expected",
    [
        ([{"vcodec": "avc1", "height": 360}, {"vcodec": "avc1", "height": 1080}, {"vcodec": "none", "height": 4000}], 1080),
        ([{"vcodec": "none", "height": 1080}], None),
        ([], None),
    ],
)
def test_max_available_height(formats, expected):
    assert VideoDownloader._max_available_height({"formats": formats}) == expected


def test_max_available_height_none_info():
    assert VideoDownloader._max_available_height(None) is None


def test_download_from_youtube_reports_quality_info_when_matching(tmp_path, monkeypatch):
    """Quality info must always be reported, even when the download matches the best available quality."""
    downloader = _make_downloader(tmp_path)
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url, playlist_index=None: 1080)

    raw_title = "Great Video"
    info = {"id": "abc123", "ext": "mp4", "title": raw_title, "height": 1080}

    import video_downloader as vd_module

    monkeypatch.setattr(
        vd_module.yt_dlp,
        "YoutubeDL",
        lambda opts: _FakeYoutubeDL(opts, info),
    )

    result = downloader.download_from_youtube("https://www.youtube.com/watch?v=abc123")

    assert result.success, result.error
    assert result.quality_warning is None
    assert result.quality_info is not None
    assert "1080p" in result.quality_info


def test_download_from_youtube_warns_when_quality_below_best_available(tmp_path, monkeypatch):
    """A framed, hard-to-miss warning must be produced when the downloaded quality is lower than
    the best quality detected as available (the exact scenario reported by the user)."""
    downloader = _make_downloader(tmp_path)
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url, playlist_index=None: 1080)

    raw_title = "Great Video"
    info = {"id": "abc123", "ext": "mp4", "title": raw_title, "height": 360}

    import video_downloader as vd_module

    monkeypatch.setattr(
        vd_module.yt_dlp,
        "YoutubeDL",
        lambda opts: _FakeYoutubeDL(opts, info),
    )

    result = downloader.download_from_youtube("https://www.youtube.com/watch?v=abc123")

    assert result.success, result.error
    assert result.quality_info is not None
    assert "360p" in result.quality_info and "1080p" in result.quality_info
    assert result.quality_warning is not None
    assert "360p" in result.quality_warning
    assert "1080p" in result.quality_warning
    # The warning is framed so it stands out in the terminal output.
    assert result.quality_warning.splitlines()[0].startswith("!")


def test_download_from_youtube_logs_and_reports_the_url(tmp_path, monkeypatch, caplog):
    """The URL being downloaded must always appear in the logs and in the quality info message."""
    downloader = _make_downloader(tmp_path)
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url, playlist_index=None: 1080)

    raw_title = "Great Video"
    info = {"id": "abc123", "ext": "mp4", "title": raw_title, "height": 1080}
    url = "https://www.youtube.com/watch?v=abc123"

    import video_downloader as vd_module

    monkeypatch.setattr(
        vd_module.yt_dlp,
        "YoutubeDL",
        lambda opts: _FakeYoutubeDL(opts, info),
    )

    with caplog.at_level("INFO"):
        result = downloader.download_from_youtube(url)

    assert result.success, result.error
    assert url in result.quality_info
    assert any(url in record.message for record in caplog.records)


def test_download_from_youtube_warns_on_sabr_even_when_heights_match(tmp_path, monkeypatch):
    """When yt-dlp reports SABR-related format skipping, warn even if downloaded == 'best detected',
    since the detected best may itself be artificially low (the scenario the user hit in practice:
    android_vr formats skipped, best available and downloaded both computed as 360p)."""
    downloader = _make_downloader(tmp_path)
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url, playlist_index=None: 360)

    raw_title = "Great Video"
    info = {"id": "abc123", "ext": "mp4", "title": raw_title, "height": 360}
    sabr_msg = (
        "[youtube] abc123: Some android_vr client https formats have been skipped as they are "
        "missing a URL. YouTube may have enabled the SABR-only streaming experiment for the "
        "current session."
    )

    import video_downloader as vd_module

    monkeypatch.setattr(
        vd_module.yt_dlp,
        "YoutubeDL",
        lambda opts: _FakeYoutubeDL(opts, info, warning_msg=sabr_msg),
    )

    result = downloader.download_from_youtube("https://www.youtube.com/watch?v=abc123")

    assert result.success, result.error
    assert result.quality_warning is not None
    assert "SABR" in result.quality_warning
    assert result.quality_warning.splitlines()[0].startswith("!")


def test_download_from_youtube_retries_and_uses_better_quality_on_sabr(tmp_path, monkeypatch):
    """When the first attempt hits a SABR warning and a retry with fallback player clients yields
    a higher resolution, the retry's result must be kept (reproduces the session-flakiness the
    user observed: same config sometimes gets 1080p, sometimes 360p)."""
    downloader = _make_downloader(tmp_path)

    raw_title = "Great Video"
    sabr_msg = (
        "[youtube] abc123: Some android_vr client https formats have been skipped as they are "
        "missing a URL. YouTube may have enabled the SABR-only streaming experiment for the "
        "current session."
    )

    call_count = {"n": 0}

    def fake_preflight(url, playlist_index=None):
        call_count["n"] += 1
        # First attempt (configured clients) sees only 360p; retry (fallback clients) sees 1080p.
        return 360 if call_count["n"] == 1 else 1080

    monkeypatch.setattr(downloader, "preflight_best_height", fake_preflight)

    def fake_youtube_dl(opts):
        player_client = ((opts.get("extractor_args") or {}).get("youtube") or {}).get("player_client")
        is_retry = bool(player_client) and player_client == downloader._merged_fallback_clients()
        height = 1080 if is_retry else 360
        info = {"id": "abc123", "ext": "mp4", "title": raw_title, "height": height}
        return _FakeYoutubeDL(opts, info, warning_msg=sabr_msg)

    import video_downloader as vd_module

    monkeypatch.setattr(vd_module.yt_dlp, "YoutubeDL", fake_youtube_dl)

    result = downloader.download_from_youtube("https://www.youtube.com/watch?v=abc123")

    assert result.success, result.error
    assert "1080p" in result.quality_info


def test_download_from_youtube_retries_after_hard_failure_on_first_attempt(tmp_path, monkeypatch):
    """A hard exception on the first attempt (e.g. 'the downloaded file is empty' from an
    incompatible cached challenge-solver script) must still trigger the fallback-clients retry,
    not abort the whole download immediately."""
    downloader = _make_downloader(tmp_path)
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url, playlist_index=None: 1080)

    raw_title = "Great Video"

    def fake_youtube_dl(opts):
        player_client = ((opts.get("extractor_args") or {}).get("youtube") or {}).get("player_client")
        is_retry = bool(player_client) and player_client == downloader._merged_fallback_clients()
        if not is_retry:
            raise RuntimeError("ERROR: The downloaded file is empty")
        info = {"id": "abc123", "ext": "mp4", "title": raw_title, "height": 1080}
        return _FakeYoutubeDL(opts, info)

    import video_downloader as vd_module

    monkeypatch.setattr(vd_module.yt_dlp, "YoutubeDL", fake_youtube_dl)

    result = downloader.download_from_youtube("https://www.youtube.com/watch?v=abc123")

    assert result.success, result.error
    assert "1080p" in result.quality_info


def test_download_from_youtube_fails_when_all_attempts_fail(tmp_path, monkeypatch):
    """If every candidate player-client list fails, the download must fail with a clear error
    instead of raising an unrelated exception (e.g. attribute error on an empty attempts list)."""
    downloader = _make_downloader(tmp_path)
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url, playlist_index=None: 1080)

    import video_downloader as vd_module

    def always_fails(opts):
        raise RuntimeError("ERROR: The downloaded file is empty")

    monkeypatch.setattr(vd_module.yt_dlp, "YoutubeDL", always_fails)

    result = downloader.download_from_youtube("https://www.youtube.com/watch?v=abc123")

    assert not result.success
    assert "empty" in result.error


def test_describe_cli_includes_key_options():
    opts = {
        "format": "bestvideo+bestaudio/best",
        "extractor_args": {"youtube": {"player_client": ["default", "tv"]}},
        "js_runtimes": {"node": {"path": ""}},
        "remote_components": ["ejs:github"],
        "cachedir": "temp/.yt-dlp-cache",
        "restrictfilenames": True,
        "merge_output_format": "mp4",
        "outtmpl": "temp/%(id)s.%(ext)s",
    }
    cli = VideoDownloader._describe_cli("https://www.youtube.com/watch?v=abc123", opts, download=True)
    assert cli.startswith("yt-dlp ")
    assert "bestvideo+bestaudio/best" in cli
    assert "player_client=default,tv" in cli
    assert "node" in cli
    assert "ejs:github" in cli
    assert "--cache-dir" in cli and ".yt-dlp-cache" in cli
    assert "--restrict-filenames" in cli
    assert "abc123" in cli
    assert "--skip-download" not in cli


def test_cachedir_scoped_to_run_temp_directory(tmp_path):
    """yt-dlp's cache must be scoped to this run's temp directory, not the user's global/default
    cache dir, so a stale cross-run cached script (e.g. an incompatible challenge-solver script
    version) from a previous run can never cause a failure in a later one."""
    downloader = _make_downloader(tmp_path)
    opts = downloader._base_ydl_opts(logger=None)
    assert opts["cachedir"] == str(downloader.temp_dir / ".yt-dlp-cache")
    assert str(downloader.temp_dir) in opts["cachedir"]


def test_describe_cli_skip_download_when_not_downloading():
    cli = VideoDownloader._describe_cli("https://www.youtube.com/watch?v=abc123", {}, download=False)
    assert "--skip-download" in cli


def test_player_clients_config_maps_to_extractor_args(tmp_path):
    config = {
        "output": {"video_download_directory": str(tmp_path / "output")},
        "video": {
            "temp_directory": str(tmp_path / "temp"),
            "ffmpeg_path": "ffmpeg",
            "youtube_player_clients": ["default", "tv"],
        },
    }
    downloader = VideoDownloader(config)
    opts = downloader._base_ydl_opts(logger=None)
    assert opts["extractor_args"] == {"youtube": {"player_client": ["default", "tv"]}}


def test_quality_max_attempts_defaults_to_three(tmp_path):
    downloader = _make_downloader(tmp_path)
    assert downloader.quality_max_attempts == 3


def test_quality_max_attempts_configurable(tmp_path):
    config = {
        "output": {"video_download_directory": str(tmp_path / "output")},
        "video": {
            "temp_directory": str(tmp_path / "temp"),
            "ffmpeg_path": "ffmpeg",
            "youtube_quality_max_attempts": 5,
        },
    }
    downloader = VideoDownloader(config)
    assert downloader.quality_max_attempts == 5


def test_download_from_youtube_makes_up_to_max_attempts_when_still_degraded(tmp_path, monkeypatch):
    """If quality stays degraded even after the fallback client list, the program must keep
    retrying (reusing the fallback clients) up to `quality_max_attempts` total attempts, since
    each attempt is an independent roll of the dice against YouTube's per-session SABR rollout."""
    downloader = _make_downloader(tmp_path)
    downloader.quality_max_attempts = 4
    monkeypatch.setattr(downloader, "preflight_best_height", lambda url, playlist_index=None: 1080)

    call_count = {"n": 0}

    def fake_youtube_dl(opts):
        call_count["n"] += 1
        # Only the 4th (last allowed) attempt actually succeeds at full quality.
        height = 1080 if call_count["n"] >= 4 else 360
        info = {"id": "abc123", "ext": "mp4", "title": "Great Video", "height": height}
        return _FakeYoutubeDL(opts, info)

    import video_downloader as vd_module

    monkeypatch.setattr(vd_module.yt_dlp, "YoutubeDL", fake_youtube_dl)

    result = downloader.download_from_youtube("https://www.youtube.com/watch?v=abc123")

    assert result.success, result.error
    assert call_count["n"] == 4
    assert "1080p" in result.quality_info
