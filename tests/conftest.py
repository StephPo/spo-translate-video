import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
VIDEOS_DIR = FIXTURES_DIR / "videos"

# Permet aux tests d'importer les modules du projet (main.py, video_downloader.py, ...)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def videos_dir() -> Path:
    return VIDEOS_DIR


@pytest.fixture
def sample_chapters_video() -> Path:
    """Chemin vers la vidéo locale de test avec chapitres.

    Skip le test si le fichier n'a pas encore été déposé par l'utilisateur.
    """
    path = VIDEOS_DIR / "test.mkv"
    if not path.exists():
        pytest.skip(f"Fixture vidéo manquante: {path} (voir tests/fixtures/README.md)")
    return path


def _parse_ffmetadata_timestamp(value: str) -> float:
    """Convertit 'HH:MM:SS.mmm' en secondes (float)."""
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_ffmetadata_chapters(path: Path) -> List[Dict[str, Any]]:
    """Parse un fichier de chapitrage au format ffmetadata simplifié
    (CHAPTERxx=timestamp / CHAPTERxxNAME=titre), tel que produit par
    l'utilisateur pour tests/fixtures/videos/test_chapters.txt.
    """
    chapters: Dict[int, Dict[str, Any]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        m = re.match(r"^CHAPTER(\d+)(NAME)?$", key.strip(), re.IGNORECASE)
        if not m:
            continue
        idx = int(m.group(1))
        entry = chapters.setdefault(idx, {})
        if m.group(2):
            entry["title"] = value.strip()
        else:
            entry["start"] = _parse_ffmetadata_timestamp(value.strip())
    return [chapters[i] for i in sorted(chapters)]


@pytest.fixture
def expected_chapters() -> List[Dict[str, Any]]:
    """Résultat attendu (titre + heure de début) déduit dynamiquement de
    tests/fixtures/videos/test_chapters.txt (fichier de chapitrage source
    utilisé pour générer test.mkv).
    """
    path = VIDEOS_DIR / "test_chapters.txt"
    if not path.exists():
        pytest.skip(f"Fichier de chapitrage manquant: {path} (voir tests/fixtures/README.md)")
    return _parse_ffmetadata_chapters(path)


@pytest.fixture
def chapter_autoselect_patterns() -> List[str]:
    """Patterns regex `processing.chapter_autoselect_patterns` lus depuis le
    vrai config.yaml du projet (pas une valeur en dur dans le test)."""
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    patterns = cfg.get("processing", {}).get("chapter_autoselect_patterns")
    if not patterns:
        pytest.skip("processing.chapter_autoselect_patterns absent de config.yaml")
    return patterns


@pytest.fixture
def test_youtube_url() -> str:
    """URL YouTube de test, lue depuis tests/fixtures/test_urls.yaml.

    Skip le test si le fichier n'existe pas encore.
    """
    urls_path = FIXTURES_DIR / "test_urls.yaml"
    if not urls_path.exists():
        pytest.skip(f"Fichier de config manquant: {urls_path} (copier test_urls.yaml.example)")
    with open(urls_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    url = data.get("youtube_url")
    if not url or "XXXXXXXXXXX" in url:
        pytest.skip("test_urls.yaml: youtube_url non renseignée")
    return url
