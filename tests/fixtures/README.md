# Fixtures de test

Ce dossier contient les ressources réelles utilisées par les tests d'intégration
(`tests/integration/`). Les vidéos (`videos/*`, hors `.gitkeep`) ne sont **pas versionnées
dans git** (voir `.gitignore`) car ce sont des binaires ; `test_chapters.txt` et
`test_urls.yaml` sont en revanche versionnés (petits fichiers texte, réutilisables tels quels).

## `videos/`

- `test.mkv` : courte vidéo locale avec chapitres embarqués, utilisée pour tester :
  - la détection de chapitres (`_ffprobe_chapters` dans `main.py`)
  - la sélection manuelle / automatique de chapitres (`_autoselect_chapters`, `_resolve_chapter_selection`, `--listchapters`, `--chapters`, `--autoselectchapters`)
- `test_chapters.txt` : fichier de chapitrage source (format ffmetadata `CHAPTERxx=`/`CHAPTERxxNAME=`)
  utilisé pour générer `test.mkv`. Sert de **référence dynamique** : les tests le parsent
  (`tests/conftest.py::expected_chapters`) pour comparer le résultat attendu sans le dupliquer en dur.
- `non_ascii_chapters.mkv` (+ `non_ascii_chapters_meta.txt`, source ffmetadata) : vidéo committée
  avec un titre de chapitre contenant un caractère non représentable en cp1252 (`U+2010`) et une
  piste audio silencieuse, utilisée par `tests/integration/test_chapter_encoding_bug.py` pour
  reproduire le bug d'encodage de `_ffprobe_chapters` (main.py) et de `AudioProcessor.extract_audio`
  (audio_processor.py) sans avoir à régénérer la vidéo à chaque exécution. Pour la régénérer si besoin :
  `ffmpeg -y -f lavfi -i color=c=black:s=64x64:d=2 -f lavfi -i anullsrc=r=16000:cl=mono:d=2 -i non_ascii_chapters_meta.txt -map_metadata 2 -map 0:v -map 1:a -c:v libx264 -c:a aac -shortest non_ascii_chapters.mkv`

Les chapitres doivent inclure des titres qui matchent les motifs par défaut de
`processing.chapter_autoselect_patterns` (`config.yaml`), ex. `Intro`, `MC1`, `MC2`, `Outro`,
ainsi que des titres qui ne matchent pas, pour vérifier l'exclusion.

## `test_urls.yaml`

Contient une URL YouTube réelle (vidéo courte et stable) utilisée par les tests d'intégration
du téléchargeur (`tests/integration/test_downloader.py`). **Ce fichier est versionné** (choix
du projet, pour éviter de devoir rechercher une URL à chaque nouvel environnement).

## Tests unitaires (`tests/unit/`)

N'ont besoin d'aucune ressource de ce dossier : ils testent des fonctions pures (parsing,
sélection de chapitres, détection de type de source, etc.) sans I/O réel.
