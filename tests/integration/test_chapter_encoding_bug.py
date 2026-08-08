"""Reproduction d'un bug réel (corrigé) : plusieurs appels
`subprocess.run(cmd, capture_output=True, text=True)` (main.py, audio_processor.py,
video_downloader.py) ne précisaient pas `encoding="utf-8"`. Sur un poste Windows
dont l'encodage de la console/locale n'est pas UTF-8 (ex: cp1252), la sortie
d'ffprobe/ffmpeg (toujours en UTF-8, qui peut contenir des titres de chapitres
non-ASCII, ex. japonais) provoquait un `UnicodeDecodeError` dans le thread
lecteur interne de `subprocess`.

Rencontré en conditions réelles avec une vidéo locale dont les titres de
chapitres contiennent des caractères non représentables en cp1252 :

    UnicodeDecodeError: 'charmap' codec can't decode byte 0x90 in position ...

D'abord détecté sur `_ffprobe_chapters` (main.py), puis retrouvé sur
`AudioProcessor.extract_audio` (audio_processor.py) : ffmpeg liste les chapitres
de la vidéo d'entrée sur sa sortie (stderr), ce qui déclenche le même bug dès
qu'on extrait l'audio d'une vidéo aux titres de chapitres non-ASCII.

Vidéo de test committée : tests/fixtures/videos/non_ascii_chapters.mkv, dont le
chapitre contient U+2010 (HYPHEN) — encodage UTF-8 `\\xe2\\x80\\x90`, qui
contient l'octet 0x90, non défini en cp1252 — reproduisant fidèlement le bug.
Contient aussi une piste audio (silencieuse) pour permettre de tester
l'extraction audio. Générée depuis tests/fixtures/videos/non_ascii_chapters_meta.txt
(fichier ffmetadata source, également committé, permettant de régénérer la
vidéo si besoin — voir tests/fixtures/README.md).

Statut actuel : ces deux tests PASSENT (bug corrigé via `encoding="utf-8"`
explicite sur les appels `subprocess.run` concernés).
"""
from pathlib import Path

import pytest

from audio_processor import AudioProcessor
from main import _ffprobe_chapters

pytestmark = pytest.mark.integration

# U+2010 (HYPHEN) encode en UTF-8 sur l'octet 0x90 en 2e position (\xe2\x80\x90),
# qui est une position non definie de la table cp1252 -> UnicodeDecodeError.
NON_CP1252_TITLE = "Test\u2010Chapter"


@pytest.fixture
def video_with_non_ascii_chapter_title(videos_dir):
    video_path = videos_dir / "non_ascii_chapters.mkv"
    if not video_path.exists():
        pytest.skip(f"Fixture vidéo manquante: {video_path} (voir tests/fixtures/README.md)")
    return video_path


def test_ffprobe_chapters_handles_non_cp1252_title(video_with_non_ascii_chapter_title):
    """Le titre du chapitre est récupéré correctement, quel que soit
    l'encodage de la locale Windows (nécessite `encoding="utf-8"` explicite
    dans `_ffprobe_chapters`, sans quoi UnicodeDecodeError silencieux)."""
    chapters = _ffprobe_chapters(str(video_with_non_ascii_chapter_title), "ffmpeg")

    assert len(chapters) == 1
    assert chapters[0]["tags"]["title"] == NON_CP1252_TITLE


def test_extract_audio_handles_non_cp1252_chapter_title(video_with_non_ascii_chapter_title, tmp_path):
    """L'extraction audio (ffmpeg) doit réussir même si la vidéo source a des
    titres de chapitres non représentables en cp1252 : ffmpeg les affiche dans
    la description du fichier d'entrée (stderr), ce qui déclenchait le même
    UnicodeDecodeError que pour `_ffprobe_chapters` (nécessite `encoding="utf-8"`
    explicite dans `AudioProcessor.extract_audio`)."""
    processor = AudioProcessor({"video": {"temp_directory": str(tmp_path)}, "audio": {}})

    result = processor.extract_audio(str(video_with_non_ascii_chapter_title))

    assert result.success, result.error
    assert Path(result.audio_path).exists()
