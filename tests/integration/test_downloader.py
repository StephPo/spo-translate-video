"""Tests d'intégration : accès réseau réel à YouTube via video_downloader.
URL configurée dans tests/fixtures/test_urls.yaml.
"""
import pytest

from video_downloader import VideoDownloader, detect_source_type

pytestmark = pytest.mark.integration


def test_detect_source_type_on_real_url(test_youtube_url):
    assert detect_source_type(test_youtube_url) == "youtube"


def test_download_from_youtube_succeeds(test_youtube_url, tmp_path):
    config = {
        "output": {"video_download_directory": str(tmp_path)},
        "video": {
            "ffmpeg_path": "",
            "youtube_js_runtime": {"runtime": "node", "path": ""},
            "youtube_remote_components": {"enable": True, "components": ["ejs:github"]},
        },
    }
    downloader = VideoDownloader(config)
    result = downloader.download_from_youtube(test_youtube_url)

    assert result.success, result.error
    assert result.video_path
    from pathlib import Path
    assert Path(result.video_path).exists()
