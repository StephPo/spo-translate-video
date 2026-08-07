# Spécifications — Sous-titrage automatique de vidéos (spo-translate-video)

Ce document est la **source de vérité** du projet. Il doit permettre de régénérer l'application de zéro sans se référer au code existant. Toute évolution du projet doit d'abord être répercutée ici, puis implémentée.

---

## 1. Vue d'ensemble

### 1.1 Objectif

Générer des **sous-titres traduits (`.srt`)** pour une vidéo (YouTube, flux `.m3u8`, ou fichier local), **en conservant la piste audio originale**. Le programme ne modifie jamais l'audio ni la vidéo : il ne fait que produire un fichier de sous-titres à côté de la vidéo.

Pipeline complet :

```
Source vidéo (YouTube / .m3u8 / fichier local)
        │
        ▼
  Téléchargement / préparation du fichier vidéo
        │
        ▼
  Extraction de l'audio (ffmpeg)
        │
        ▼
  Transcription audio → texte horodaté (Whisper)
        │
        ▼
  Traduction du texte (DeepL ou LLM/OpenAI)
        │
        ▼
  Écriture des sous-titres (.srt)
```

### 1.2 Non-objectifs (explicitement hors périmètre)

- **Pas de doublage / synthèse vocale (TTS)**. Aucune resynthèse audio dans la langue cible n'est prévue. (Une ancienne tentative de ce type existait dans le code historique et a été abandonnée : elle n'est pas reprise dans ces specs.)
- **Pas de réencodage vidéo** (pas d'incrustation "hardcoded" des sous-titres dans la vidéo — les `.srt` restent des fichiers externes, chargés par le lecteur vidéo).
- **Pas de serveur web permanent** ni de processus qui tourne en arrière-plan.
- **Windows uniquement.** Pas d'objectif de portabilité Linux/macOS dans cette version.

### 1.3 Langage et stack

- **Python 3.10+** — conservé pour la continuité avec l'écosystème existant (Whisper, yt-dlp, SDK OpenAI, tous matures et bien maintenus en Python). C'est le choix le plus pratique à maintenir vu les dépendances incontournables (Whisper notamment n'a pas d'équivalent aussi simple hors Python).
- Scripts `.bat` / `.ps1` pour l'expérience Windows (lancement, installation du protocole navigateur).

---

## 2. Modes de fonctionnement

Un seul code base sert les deux modes : le mode "navigateur" ne fait qu'invoquer les mêmes scripts `.bat` / le même `main.py` que le mode CLI. Aucune logique métier n'est dupliquée.

### 2.1 Mode ligne de commande (CLI)

Point d'entrée : `main.py`, invoqué via un wrapper `.bat` qui :

1. Se place dans le dossier du projet.
2. Détecte un interpréteur Python (`py` puis `python`).
3. Crée un environnement virtuel `.venv` s'il n'existe pas.
4. Installe les dépendances (`requirements.txt`) une seule fois (marqueur de fichier pour éviter de le refaire à chaque run).
5. Exécute `main.py` en transmettant tous les arguments reçus.

Deux wrappers dédiés :

- `spo-translate-video.bat <source>` — pipeline complet (téléchargement + transcription + traduction).
- `spo-dl-video.bat <source>` — équivalent à `spo-translate-video.bat <source> --download-only`.

Voir §3 pour la liste complète des options CLI.

### 2.2 Mode navigateur (déclenchement en un clic depuis YouTube)

**Contraintes fixées par l'utilisateur :**
- Minimum de clics depuis une page YouTube ouverte dans le navigateur.
- Facile à réinstaller sur un nouveau poste Windows.
- **Aucun serveur ni processus en arrière-plan** ne doit tourner en permanence.
- Même code que le CLI (pas de logique dupliquée) : le mode navigateur ne fait que traduire l'URL de la page en une invocation du `.bat` existant.

**Mécanisme retenu : protocole d'URL personnalisé Windows + bookmarklet.**

```
Clic sur un bookmarklet dans la barre de favoris (sur youtube.com)
        │  (extrait le videoId depuis l'URL de la page)
        ▼
Navigation vers spodl:VIDEO_ID  ou  spotr:VIDEO_ID
        │  (le navigateur demande confirmation d'ouvrir une appli externe)
        ▼
Windows résout le protocole via le Registre (HKEY_CURRENT_USER, par utilisateur, sans droits admin)
        │
        ▼
spo-protocol-handler.bat → spo-protocol-handler.ps1 -Mode dl|tr -Raw "<valeur après le :>"
        │  (normalise en URL YouTube complète, log horodaté dans protocol-handler.log)
        ▼
Lance une fenêtre cmd (via Windows Terminal si dispo, sinon cmd.exe), avec /k (fenêtre qui reste ouverte)
        │
        ▼
spo-dl-video.bat "<URL>"   ou   spo-translate-video.bat "<URL>"
        │
        ▼
Même pipeline que le mode CLI (§2.1)
```

- Installation en un script PowerShell, **par utilisateur, sans droits admin** : `install-protocol-handlers.ps1`. Désinstallation : `uninstall-protocol-handlers.ps1`.
- Deux protocoles distincts :
  - `spodl:` → téléchargement seul.
  - `spotr:` → téléchargement + transcription + traduction.
- Deux bookmarklets JavaScript (fournis dans le guide d'installation) à ajouter comme favoris dans le navigateur. Ils :
  - fonctionnent sur l'onglet courant ;
  - extraient l'identifiant vidéo YouTube (`watch?v=`, `youtu.be/`, `/shorts/`) ;
  - ignorent les paramètres de playlist / tracking.

**Exigence de fiabilité (corrige le bug connu "l'invite de commande s'ouvre, reste quelques instants, puis se ferme sans rien exécuter") :**

Ce bug est actuellement **connu mais non résolu** : la cause exacte (erreur silencieuse dans `spo-protocol-handler.ps1`, association de protocole mal enregistrée, échec avant l'ouverture de la fenêtre `cmd`, etc.) n'a pas encore été diagnostiquée avec certitude. Tant qu'il n'est pas corrigé à la racine, le programme doit **au minimum garantir que l'utilisateur est informé sur le moment**, avec assez d'informations pour relancer, débugger, ou corriger lui-même :

- Le gestionnaire de protocole (`.ps1`) doit **envelopper l'intégralité de son exécution dans un `try`/`catch` global**, dès la première ligne, pour ne jamais échouer avant d'avoir pu logger ou afficher quoi que ce soit (y compris les erreurs "impensables" : politique d'exécution PowerShell, argument `Raw` mal formé, etc.).
- Il doit **toujours** :
  - écrire une ligne de log horodatée à chaque étape clé (réception, normalisation URL, lancement du runner) dans `protocol-handler.log`, **même en cas de succès**, pas seulement en debug ;
  - lancer la fenêtre de commande avec une option qui la garde ouverte après exécution (`cmd /k`, jamais `/c`), **y compris en cas d'erreur**, et la garder ouverte même si une exception survient avant ce point (fenêtre de secours minimaliste type `cmd /k echo <erreur>`) ;
  - en cas d'erreur (URL vide, runner introuvable, échec de lancement, exception imprévue), afficher **dans la fenêtre elle-même** (pas seulement dans le log) :
    - le message d'erreur précis ;
    - le chemin complet du fichier `protocol-handler.log` à consulter ;
    - les étapes de diagnostic suggérées (ex. "relancer `install-protocol-handlers.ps1`", "vérifier que Node.js/ffmpeg sont dans le PATH", "vérifier la clé de registre `HKCU\Software\Classes\spodl`") ;
    - la commande exacte qui a été tentée (runner + URL), pour pouvoir la copier-coller et la rejouer manuellement en CLI classique afin d'isoler si le problème vient du protocole ou du pipeline lui-même.
- Le `.bat` runner ne doit jamais fermer la fenêtre automatiquement en cas d'erreur (`exit /b` doit être précédé d'un `pause` si le code de sortie est non nul, sauf si lancé depuis un terminal interactif qui reste ouvert par défaut).
- Objectif de diagnostic à terme : consigner dans `protocol-handler.log` suffisamment de contexte (heure exacte, PID, ligne de commande complète reçue de Windows) pour qu'une occurrence future du bug soit reproductible et corrigeable définitivement — cette correction définitive reste un travail futur, pas garanti par cette version des specs.

**Alternative envisagée et écartée** : une page HTML locale exécutant du JavaScript pur pourrait sembler plus simple, mais elle ne peut pas lancer un process Windows (accès disque/exécutable) sans un serveur local ou une extension navigateur — ce qui violerait la contrainte "pas de process en arrière-plan". Le protocole d'URL personnalisé reste donc la solution la plus adaptée : zéro process permanent, un seul clic, réinstallation triviale (`.ps1` idempotent), et il réutilise le CLI tel quel.

---

## 3. Fonctionnalités

### 3.1 Sources d'entrée supportées

| Source | Détection | Outil |
|---|---|---|
| URL YouTube (vidéo, short, `youtu.be`) | Auto (regex) | yt-dlp |
| URL `.m3u8` (flux HLS) | Auto (regex) | ffmpeg (remux direct) |
| Fichier vidéo local (`mp4`, `avi`, `mov`, `mkv`, `webm`) | Auto (chemin existant sur disque) | — |

> La détection du type de source est **toujours automatique** ; il n'existe pas d'option pour la forcer manuellement (retiré volontairement : la détection auto est jugée fiable à 100 % dans l'usage réel du projet).

### 3.2 Options de la ligne de commande

| Option | Alias | Description |
|---|---|---|
| `input` | — | URL YouTube, URL `.m3u8`, ou chemin de fichier local (positionnel, obligatoire) |
| `--config` | — | Chemin vers `config.yaml` (défaut : `config.yaml`) |
| `--config-local` | — | Chemin vers l'override local de secrets (défaut : `config.local.yaml`) |
| `--dest` | — | Dossier de destination (override pour ce run) |
| `--source-lang` | — | Override de la langue source (transcription) |
| `--target-lang` | — | Override de la langue cible (traduction) |
| `--info` | — | Télécharge/prépare puis affiche des infos, sans transcrire/traduire |
| `--dry-run` | — | Affiche ce qui serait fait, sans rien exécuter |
| `--download-only` | `--d` | Télécharge/prépare uniquement, puis s'arrête |
| `--chapters` | `--c` | Sélection manuelle de chapitres, 1-based, ex. `"2,5-6"` (fichiers locaux) |
| `--autoselectchapters` | `--asc` | Sélection automatique de chapitres par regex (config) |
| `--listchapters` | `--lc` | Liste les chapitres et leur statut de correspondance, sans traiter |
| `--resume` | — | Reprend un run précédent depuis le dernier segment traduit (cache) |

> Options retirées volontairement par rapport à la version précédente : `--source-type` (détection auto systématique), `--output-basename` (le nom par défaut basé sur le titre/fichier source convient), `--no-progress` (l'affichage de progression est toujours utile, jugé non gênant).

### 3.3 Téléchargement (YouTube / m3u8)

- **YouTube** via `yt-dlp` :
  - **Sélecteur de format par défaut : la vraie meilleure qualité disponible, tous conteneurs confondus** (`bestvideo+bestaudio/best`), et non plus une sélection restreinte au MP4. C'est un changement de comportement volontaire par rapport à la version précédente, qui limitait la recherche au conteneur MP4 (`bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best`) et ratait donc les flux haute résolution disponibles uniquement en WebM/VP9/AV1 — cause racine du problème "téléchargement en 360p/480p alors qu'une meilleure qualité (1080p+) est disponible".
  - Si le résultat n'est pas déjà en `.mp4` (ex. conteneur WebM/MKV), **remux automatique vers `.mp4`** avec `ffmpeg` en copie de flux (pas de réencodage, donc pas de perte de qualité ni de temps de calcul significatif), pour garder un format de sortie homogène et compatible avec la plupart des lecteurs.
  - "Preflight" de qualité systématique avant téléchargement : interroge yt-dlp pour connaître la meilleure résolution disponible (`best_overall_height`), sans télécharger.
  - **Garde-fou obligatoire** : si, malgré le sélecteur "vraie meilleure qualité", le résultat effectivement téléchargé (`requested_downloads`) est inférieur à la meilleure résolution annoncée par le preflight (ex. un flux protégé/soumis à throttling n'était finalement pas accessible), le programme **avertit explicitement**, avant de continuer, avec :
    - la résolution effectivement téléchargée et celle qui était annoncée comme disponible (ex. `Téléchargé : 480p — Meilleure qualité disponible détectée : 1080p`) ;
    - la raison si elle est connue (ex. format restreint, nécessite authentification) ;
    - la commande à relancer manuellement pour forcer un format précis (ex. `yt-dlp -f <format_id> <url>`) pour investiguer/corriger soi-même ;
    - ne bloque pas le run (l'utilisateur peut vouloir continuer quand même), mais l'information doit être impossible à manquer (bloc encadré, comme les erreurs fatales).
  - Nom de fichier basé sur le titre YouTube, assaini pour Windows (`restrictfilenames`).
  - Téléchargement dans un dossier temporaire d'abord (pour ne pas polluer le dossier de sortie avec les fichiers annexes de yt-dlp comme `.info.json`), puis déplacement final.
  - Gestion du **runtime JavaScript requis par YouTube** pour résoudre les défis d'extraction (voir §4).
  - Gestion des **composants distants de contournement** (`remote_components`, ex. `ejs:github`).
  - Détection et avertissement explicite si yt-dlp signale l'absence de runtime JS ou un échec de résolution de challenge (signature/n-challenge).
- **`.m3u8`** : téléchargement/remux direct via `ffmpeg` (pas de transcodage, copie des flux) vers un `.mp4`.
- **Fichier local** : utilisé tel quel, aucun téléchargement.

### 3.4 Extraction audio

- Extraction via `ffmpeg` en `.wav`, mono, taux d'échantillonnage configurable (16 kHz par défaut — format attendu par Whisper).
- Normalisation de volume optionnelle.
- Extraction possible d'un segment précis (utilisé pour la sélection de chapitres).

### 3.5 Sélection de chapitres (fichiers locaux)

- Lecture des chapitres embarqués dans le conteneur (ex. `.mkv`).
- **Sélection manuelle** par numéro (1-based), avec plages (`"2,5-6"`).
- **Sélection automatique** par motifs regex insensibles à la casse (`processing.chapter_autoselect_patterns` dans la config), ex. chapitres commençant par `MC`, ou nommés `Intro`/`Outro`.
- **Combinaison** : manuel + automatique = union des deux sélections.
- **Mode test** (`--listchapters`) : liste les chapitres et indique lesquels seraient sélectionnés, sans rien extraire/transcrire/traduire — pour valider ses regex avant un run réel.
- Si aucun chapitre ne correspond (ou le fichier n'a pas de chapitres) alors qu'une sélection a été demandée : demande interactive (`y` = traduire le fichier entier, sinon arrêt).

### 3.6 Transcription (Whisper)

- Moteur : `openai-whisper` (local, pas d'API payante), avec `torch` pour l'inférence.
- Modèle configurable (`tiny` à `large`, y compris `turbo`).
- Accélération GPU optionnelle (CUDA), avec repli CPU automatique si indisponible.
- Horodatage par segment (`start`, `end`, texte).
- Filtrage par seuil de confiance heuristique (Whisper ne fournit pas de score de confiance natif fiable — une heuristique basée sur la longueur du texte et la proportion de caractères spéciaux est utilisée).
- Longueur de segment maximale configurable.

### 3.7 Traduction

Voir §5 pour le détail des fournisseurs. Points communs :

- Segmentation identique à celle produite par Whisper (1 segment audio = 1 ligne/cue de sous-titre).
- **Retry avec backoff exponentiel + jitter** en cas d'erreur transitoire ou de rate limit (HTTP 429), configurable (`max_retries`, `initial_delay_seconds`, `max_delay_seconds`, `backoff_multiplier`, `jitter_ratio`), avec override possible par service.
- **Fail-fast** : si la traduction échoue définitivement (retries épuisés), le programme s'arrête immédiatement et sauvegarde un **cache JSON** (`<base>.<lang>.cache.json`) contenant les segments déjà traduits, à côté du dossier de sortie des sous-titres.
- **Reprise (`--resume`)** : relit le cache et continue la traduction à partir du dernier segment traduit, **sans refaire la transcription Whisper** (étape la plus coûteuse en temps).

### 3.8 Écriture des sous-titres

- Format `.srt` (horodatage `HH:MM:SS,mmm`).
- Nom de fichier : `<base>.<langue_cible>.srt`.
- Emplacement :
  - Téléchargements (YouTube/m3u8) : dossier `output.video_download_directory`.
  - Fichiers locaux : à côté du fichier vidéo source.
  - Dans les deux cas, `--dest` permet d'overrider pour un run donné.

### 3.9 Gestion des fichiers existants

- Si le fichier de sortie existe déjà : demande interactive unique par run `Overwrite? (y/n)`.
  - `y` → écrase.
  - `n` → crée un nouveau nom avec suffixe `_1`, `_2`, ... jusqu'à `_100`.

### 3.10 Fichiers temporaires

- Chaque run utilise un sous-dossier temporaire dédié et horodaté (`run_<timestamp>_<pid>`) sous `video.temp_directory`.
- Purge automatique des anciens dossiers de run (configurable en nombre de jours à conserver) au démarrage.
- Suppression du dossier temporaire du run en fin d'exécution.

### 3.11 Journalisation et affichage

- Logs structurés (niveau configurable : DEBUG/INFO/WARNING/ERROR).
- Sortie colorée dans le terminal (vert/jaune/rouge) quand le terminal le supporte (détection ANSI, y compris activation du mode VT100 sous Windows).
- Affichage de progression (désactivable via `--no-progress`).
- Bloc d'erreur visuellement distinct en cas d'échec fatal (bordures, message clair) pour rester lisible même dans une fenêtre qui va rester ouverte.

---

## 4. Outils techniques utilisés

| Outil | Rôle | Type |
|---|---|---|
| **Python 3.10+** | Langage d'implémentation | Runtime |
| **yt-dlp** | Téléchargement YouTube | Bibliothèque Python (PyPI) |
| **ffmpeg / ffprobe** | Extraction audio, remux `.m3u8`, inspection média | Binaire externe (PATH) |
| **openai-whisper** + **torch** | Transcription audio → texte | Bibliothèque Python (modèle exécuté localement) |
| **PyYAML** | Lecture de `config.yaml` | Bibliothèque Python |
| **requests** | Appels HTTP (DeepL) | Bibliothèque Python |
| **openai** (SDK officiel) | Appels à l'API OpenAI | Bibliothèque Python |
| **Node.js** (LTS) | Runtime JavaScript requis par yt-dlp pour résoudre les défis anti-bot de YouTube | Binaire externe (PATH ou chemin configuré) |

### 4.1 Pourquoi Node.js est nécessaire

YouTube exige désormais que yt-dlp exécute du JavaScript pour résoudre des "challenges" d'extraction. yt-dlp n'active automatiquement que **Deno**. Sous Windows, la solution la plus simple est d'installer **Node.js LTS** et de configurer :

```yaml
video:
  youtube_js_runtime:
    runtime: "node"
    path: ""          # laisser vide si node.exe est dans le PATH
  youtube_remote_components:
    enable: true
    components:
      - "ejs:github"  # télécharge les scripts de résolution de challenge de yt-dlp
```

Si yt-dlp logue `WARNING: [youtube] No supported JavaScript runtime could be found...`, le programme affiche un avertissement explicite invitant à installer Node.js ou à corriger `video.youtube_js_runtime`.

---

## 5. Traduction : fournisseurs supportés

Deux fournisseurs actifs, choisis via `translation.service` dans `config.yaml` :

### 5.1 DeepL (recommandé par défaut)

- `translation.service: "deepl"`
- Plan : `translation.deepl.plan: "free"` (défaut) ou `"pro"`.
- Clé API : `config.local.yaml` (recommandé), variable d'environnement `DEEPL_API_KEY`, ou `translation.api_keys.deepl`.
- Traduction segment par segment, sans awareness contextuelle avancée (pas de prompt personnalisable — DeepL n'expose pas ce mécanisme).
- Qualité généralement excellente et rapide pour la plupart des paires de langues ; **le choix par défaut recommandé**.

### 5.2 OpenAI (LLM, avec prompts personnalisables)

- `translation.service: "openai"`
- Modèle configurable : `translation.openai.model` (ex. `"gpt-4o"`, `"gpt-4o-mini"`).
- `translation.openai.batch_size` : nombre de segments envoyés par requête (regroupement pour limiter les appels et donner plus de contexte au modèle).
- Clé API : `config.local.yaml` (recommandé), variable d'environnement `OPENAI_API_KEY`, ou `translation.api_keys.openai`.
- Meilleur pour les lignes riches en contexte (jeux de mots, tonalité, argot, références culturelles), mais plus lent et plus coûteux que DeepL.
- Seul fournisseur exposant un système de **prompts personnalisables** (voir §5.3).

### 5.3 Système de prompts

Trois champs, tous supportant les *placeholders* suivants (remplis automatiquement) :

- `{text}` — le segment à traduire.
- `{source_language}` / `{target_language}` — codes de langue (ex. `ja`, `fr`).
- `{source_language_name}` / `{target_language_name}` — noms lisibles (ex. `Japanese`, `French`).
- `{video_title}` / `{video_filename}` — contexte de la vidéo en cours.

| Champ | Rôle | Emplacement |
|---|---|---|
| `system_prompt` | Instructions **générales et stables**, valables pour tous les projets. Défini une fois, rarement modifié. | `config.prompt.yaml` |
| `system_prompt_extended` | Instructions **spécifiques au run/à la série/au streamer en cours**, à personnaliser librement à chaque projet de traduction (contexte du contenu, ton particulier, glossaire, façon de gérer le chat, etc.). Concaténé après `system_prompt`. Modifié fréquemment. | `config.prompt.yaml` |
| `user_prompt_template` | Gabarit du message utilisateur envoyé au modèle (par défaut `"{text}"`, généralement à ne pas modifier). | `config.yaml` (reste avec les autres réglages techniques d'OpenAI, car il n'est quasiment jamais modifié) |

#### Fichier dédié `config.prompt.yaml`

Ces deux champs sont **volontairement extraits de `config.yaml` dans un fichier séparé, `config.prompt.yaml`**, à la racine du projet, car `system_prompt_extended` est modifié régulièrement (à chaque nouveau projet/série de vidéos) : l'isoler évite de polluer/risquer de casser la configuration technique en l'éditant souvent, et rend le diff plus lisible (un fichier = uniquement du texte de prompt, pas de YAML technique autour).

- Chargé par `main.py` en plus de `config.yaml`/`config.local.yaml`, puis injecté dans `translation.custom_prompts.system_prompt` / `system_prompt_extended` en mémoire (le reste du pipeline ne voit pas la différence).
- **Suivi par Git** par défaut (ce n'est pas un secret) — sauf si l'utilisateur y met un jour des informations sensibles, auquel cas il peut être ajouté au `.gitignore` comme `config.local.yaml`.
- `translation.custom_prompts.enable` reste dans `config.yaml` (interrupteur technique).
- Si `config.prompt.yaml` est absent, un jeu de valeurs par défaut est utilisé (voir ci-dessous), pour que le projet fonctionne dès l'installation sans configuration supplémentaire.

Contenu de `config.prompt.yaml` :

```yaml
# Instructions générales de traduction de sous-titres — à garder stables.
# Modifie ce champ seulement si tu veux changer la méthode de traduction
# pour TOUS tes projets.
system_prompt: >
  You are a professional subtitle translator specialized in {source_language_name} to
  {target_language_name} translation for video content.
  Context: video title "{video_title}" (file: {video_filename}).

  Core rules:
  - Translate meaning and intent, not word-for-word; produce natural, idiomatic
    {target_language_name} that a native speaker would actually say.
  - Preserve tone, register (formal/casual), emotion, and speaker attitude.
  - Keep each line short and readable at subtitle reading speed; split or
    simplify long sentences rather than translating them literally in full.
  - Preserve proper nouns, names, and recurring terminology consistently
    across segments.
  - Omit filler words/sounds that carry no meaning (hesitations, verbal tics);
    keep them only if they convey real emotion or comedic timing.
  - If a segment is inaudible, unclear, or nonsensical due to transcription
    errors, output "[inaudible]" rather than guessing or inventing content.
  - Only add a short clarifying note in parentheses when a cultural reference
    or wordplay would otherwise be incomprehensible; never add general
    translator commentary.
  - Never summarize, skip, merge unrelated segments, or add content that
    is not present in the source.
  - Output only the translated line for the given segment — no explanations,
    no labels, no quotation marks around the text.

# Instructions spécifiques à CE projet/cette série de vidéos.
# À modifier librement et aussi souvent que nécessaire. Exemples :
#
# - Contexte du contenu : "This is a gaming stream by X, casual tone, lots of
#   in-game jargon in {source_language_name}; keep game terms in English."
# - Glossaire : "Translate the running joke 'XYZ' consistently as 'ABC'."
# - Speakers multiples : "If a chat message appears, prefix it with [Chat]."
# - Ton spécifique : "The host is sarcastic; preserve sarcasm, don't soften it."
#
# Laisser vide ("") si system_prompt seul suffit.
system_prompt_extended: ""
```

`user_prompt_template` reste dans `config.yaml`, sous `translation.custom_prompts.user_prompt_template` :

```yaml
translation:
  custom_prompts:
    enable: true
    user_prompt_template: "{text}"
```

> Ce `system_prompt` doit être **branché dans le code** pour tous les fournisseurs LLM (aujourd'hui, `system_prompt_extended` existe déjà dans `config.yaml` mais n'est pas encore concaténé par le traducteur OpenAI — à corriger dans la ré-implémentation : le message système final envoyé au modèle doit être `system_prompt + "\n\n" + system_prompt_extended` si ce dernier est non vide).

### 5.4 Extensibilité

L'architecture doit permettre d'ajouter facilement un nouveau fournisseur de traduction basé sur un LLM (ex. Anthropic Claude, Google Gemini) en implémentant une interface commune (`translate(text, source_lang, target_lang)` / `translate_segments(...)`), sans toucher au reste du pipeline. Les fournisseurs `google_translate` (basique, sans contexte) et `azure` (jamais implémenté) ne sont **pas repris** dans cette version des specs — à réintroduire seulement si un besoin concret apparaît.

---

## 6. Configuration

### 6.1 `config.yaml` (suivi par Git, pas de secrets)

Structure (sections principales) :

- `output` — dossiers de sortie, format vidéo/sous-titres, `video_download_directory`.
- `translation` — service actif, langues source/cible, retry, clés API (vides ici), `custom_prompts` (§5.3), options par fournisseur (`deepl.plan`, `openai.model`/`batch_size`).
- `input` — type de source par défaut, formats locaux supportés.
- `audio` — format d'extraction, sample rate, canaux, normalisation.
- `speech_recognition` — moteur (`whisper`), modèle, seuil de confiance, longueur max de segment.
- `video` — chemin ffmpeg, encodage (non utilisé pour la génération de sous-titres seule, conservé pour référence), dossier temporaire, `youtube_js_runtime`, `youtube_remote_components`.
- `processing` — parallélisme, niveau de log, patterns d'auto-sélection de chapitres, nettoyage des fichiers temporaires.
- `advanced` — GPU, timeouts, retries génériques.
- `quality_control` — validations optionnelles (longueur min/max de texte, durée min audio...).

### 6.2 `config.local.yaml` (ignoré par Git — secrets)

- Même structure que `config.yaml`, fusionné par-dessus (deep merge) au chargement.
- Contient les clés API réelles (`translation.api_keys.deepl`, `translation.api_keys.openai`).
- Alternative : variables d'environnement `DEEPL_API_KEY`, `OPENAI_API_KEY`.

### 6.3 `config.prompt.yaml` (suivi par Git — pas un secret, mais modifié souvent)

- Fichier dédié, séparé de `config.yaml`, contenant uniquement `system_prompt` et `system_prompt_extended` (voir §5.3).
- Chargé automatiquement par `main.py` s'il existe ; sinon, des valeurs par défaut intégrées au programme sont utilisées (le projet fonctionne sans que ce fichier existe).
- C'est le fichier que l'utilisateur modifie **le plus fréquemment**, généralement uniquement `system_prompt_extended`, à chaque nouveau projet de traduction.
- Le dépôt fournit un `config.prompt.example.yaml` (suivi par Git, avec le `system_prompt` par défaut proposé en §5.3 et `system_prompt_extended: ""`) à copier en `config.prompt.yaml` lors de l'installation — même logique que `config.yaml` → `config.local.yaml`, mais ici `config.prompt.yaml` peut ensuite lui aussi être suivi par Git si l'utilisateur le souhaite (ce n'est pas un secret).

---

## 7. Guide d'installation (Windows)

### 7.1 Prérequis à installer

| Outil | Comment vérifier s'il est installé | Comment l'installer |
|---|---|---|
| **Python 3.10+** | `py --version` ou `python --version` dans un terminal | Télécharger depuis [python.org/downloads](https://www.python.org/downloads/) (cocher "Add python.exe to PATH" à l'installation), ou `winget install Python.Python.3.12` |
| **ffmpeg** (avec `ffprobe`) | `ffmpeg -version` dans un terminal | `winget install Gyan.FFmpeg` (recommandé), ou télécharger un build depuis [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/) et ajouter le dossier `bin` au `PATH` système |
| **Node.js LTS** | `node --version` dans un terminal | `winget install OpenJS.NodeJS.LTS`, ou télécharger depuis [nodejs.org](https://nodejs.org/) (version LTS) |
| **Git** (optionnel, pour cloner le repo) | `git --version` | `winget install Git.Git` |

### 7.2 Installation du projet

```powershell
# 1. Cloner ou copier le projet
cd C:\Dev\CascadeProjects
git clone <url-du-repo> spo-translate-video
cd spo-translate-video

# 2. Configurer les secrets (clés API)
copy config.yaml config.local.yaml
# Éditer config.local.yaml et renseigner translation.api_keys.deepl / openai

# 3. (Optionnel) Personnaliser les prompts de traduction pour ce projet
copy config.prompt.example.yaml config.prompt.yaml
# Éditer config.prompt.yaml, notamment system_prompt_extended

# 4. Premier lancement : crée automatiquement le venv et installe les dépendances
.\spo-translate-video.bat --info "https://www.youtube.com/watch?v=XXXXXXXXXXX"
```

Le premier lancement du `.bat` :
- crée `.venv` ;
- installe `requirements.txt` (télécharge notamment `torch` et `openai-whisper`, qui peuvent prendre plusieurs minutes) ;
- exécute la commande demandée.

### 7.3 Configurer le déclenchement navigateur (optionnel)

```powershell
cd C:\Dev\CascadeProjects\spo-translate-video
.\install-protocol-handlers.ps1
```

Puis créer 2 favoris dans le navigateur (voir §2.2) avec les bookmarklets fournis dans le README/ce document.

Pour désinstaller :

```powershell
.\uninstall-protocol-handlers.ps1
```

### 7.4 Vérification de l'installation

```powershell
.\.venv\Scripts\python.exe -m yt_dlp --version
.\.venv\Scripts\python.exe -c "import whisper, torch; print(torch.cuda.is_available())"
ffmpeg -version
node --version
```

---

## 8. Maintenance — outils à mettre à jour régulièrement

| Outil | Pourquoi le mettre à jour | Fréquence recommandée | Commande |
|---|---|---|---|
| **yt-dlp** | YouTube change fréquemment ses protections anti-bot ; les mainteneurs de yt-dlp publient des correctifs très rapidement | Dès qu'un téléchargement YouTube échoue, sinon ~1x/mois | `.\.venv\Scripts\python -m pip install --upgrade yt-dlp` puis vérifier avec `.\.venv\Scripts\python -m yt_dlp --version` |
| **openai-whisper** | Corrections de bugs, nouveaux modèles | Occasionnel (peu de changements cassants) | `.\.venv\Scripts\python -m pip install --upgrade openai-whisper` |
| **torch** | Rare, sauf changement de version CUDA ou de GPU | Seulement si nécessaire (peut être un gros téléchargement) | `.\.venv\Scripts\python -m pip install --upgrade torch` (vérifier compatibilité CUDA si GPU) |
| **openai (SDK)** | Nouveaux modèles, corrections d'API | ~1x/mois ou en cas d'erreur d'API | `.\.venv\Scripts\python -m pip install --upgrade openai` |
| **ffmpeg** | Corrections de bugs, nouveaux codecs | Occasionnel | `winget upgrade Gyan.FFmpeg` |
| **Node.js** | Sécurité, compatibilité avec les scripts de résolution de challenge yt-dlp | Suivre les releases LTS | `winget upgrade OpenJS.NodeJS.LTS` |
| **Toutes les dépendances Python** | Rattraper les correctifs de sécurité/bugs en une fois | Vérification périodique | `.\.venv\Scripts\python -m pip list --outdated` puis mettre à jour ce qui est pertinent |

> **Astuce** : après une mise à jour de dépendance, supprimer le marqueur `.venv\deps_installed.marker` force le `.bat` à relancer une installation complète depuis `requirements.txt` au prochain run.

---

## 9. Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| `WARNING: [youtube] No supported JavaScript runtime could be found` | Node.js absent ou mal configuré | Installer Node.js LTS, vérifier `video.youtube_js_runtime` dans `config.yaml` |
| Avertissement sur le "remote component challenge solver script" | `youtube_remote_components` désactivé | Activer `youtube_remote_components.enable: true` avec `components: ["ejs:github"]` |
| `HTTP 429 Too Many Requests` (DeepL/OpenAI) | Rate limiting de l'API | Le programme retry automatiquement avec backoff ; ajuster `translation.retry.*` si besoin |
| La traduction s'arrête en plein milieu | Échec définitif après épuisement des retries (fail-fast volontaire) | Relancer avec `--resume` pour reprendre depuis le cache, sans refaire la transcription |
| "Le fichier n'a pas de chapitres correspondants" | Regex `chapter_autoselect_patterns` ne matche rien, ou fichier sans chapitres embarqués | Utiliser `--listchapters` pour tester les patterns avant un run réel |
| Vidéo YouTube téléchargée dans une qualité inférieure à celle disponible (ex. 480p alors que le 1080p+ existe) | Cause historique corrigée par défaut (voir §3.3) : sélecteur restreint au MP4. Peut néanmoins encore se produire si le flux haute résolution est temporairement inaccessible/throttlé | Vérifier l'avertissement affiché en fin de téléchargement (résolution obtenue vs. meilleure disponible) ; relancer, ou forcer un format précis avec `yt-dlp -f <format_id> <url>` en diagnostic manuel |
| L'invite de commande du bookmarklet s'ouvre, reste quelques instants, puis se ferme sans rien exécuter | Bug connu, cause exacte non encore isolée (erreur silencieuse dans `spo-protocol-handler.ps1`, association de protocole, ou échec avant ouverture de fenêtre) | Consulter `protocol-handler.log` (l'exigence de fiabilité de §2.2 impose qu'une trace y soit toujours écrite) ; en cas d'absence totale de log, le problème se situe avant l'exécution du `.ps1` (association de protocole) → relancer `install-protocol-handlers.ps1` ; sinon, rejouer manuellement la commande loggée (runner + URL) dans un terminal classique pour isoler si le souci vient du protocole ou du pipeline |

---

## 10. Architecture cible (modules)

Découpage en modules indépendants, chacun testable isolément :

| Module | Responsabilité |
|---|---|
| `main` (CLI/orchestrateur) | Parsing des arguments, chargement de config, orchestration du pipeline, logs, cache/resume, gestion des chapitres, overwrite |
| `downloader` | Téléchargement YouTube (yt-dlp) et `.m3u8` (ffmpeg), détection de source, sélection de format/qualité |
| `audio` | Extraction audio (ffmpeg), extraction de segments par plage temporelle |
| `transcription` | Whisper : chargement du modèle, transcription, segmentation horodatée |
| `translation` | Interface commune + implémentations DeepL / OpenAI, gestion des prompts, retry/backoff |
| `subtitles` | Construction des cues et écriture du `.srt` |
| `config` | Chargement/fusion `config.yaml` + `config.local.yaml` + `config.prompt.yaml`, valeurs par défaut |

Aucun module de doublage/TTS ni de réencodage vidéo n'est prévu dans cette architecture.

---

## 11. Historique des décisions

- **[Cette version]** Suppression du code de doublage/TTS (jamais fonctionnel, non branché) du périmètre du projet.
- **[Cette version]** Retrait des fournisseurs de traduction `google_translate` et `azure` du périmètre actif (non recommandés / non implémentés).
- **[Cette version]** Introduction du champ `system_prompt_extended` comme mécanisme officiel de personnalisation par projet, avec nouveau `system_prompt` par défaut générique.
- **[Cette version]** Formalisation des exigences de fiabilité du déclenchement navigateur (protocole d'URL personnalisé).
- **[Cette version]** Retrait des options CLI `--source-type`, `--output-basename` et `--no-progress` (détection auto systématique, nom de fichier par défaut satisfaisant, progression toujours affichée).
- **[Cette version]** Extraction de `system_prompt` / `system_prompt_extended` dans un fichier dédié `config.prompt.yaml`, distinct de `config.yaml`, car modifié beaucoup plus fréquemment que le reste de la configuration.
- **[Cette version]** Changement du sélecteur de format YouTube par défaut : vraie meilleure qualité tous conteneurs (`bestvideo+bestaudio/best`) + remux `.mp4` si nécessaire, au lieu d'une sélection restreinte au MP4 qui pouvait dégrader la résolution téléchargée. Ajout d'un avertissement obligatoire si la qualité effectivement obtenue reste inférieure à la meilleure disponible.
- **[Cette version]** Renforcement des exigences de diagnostic pour le bug connu du déclenchement navigateur (fenêtre qui se ferme sans exécuter) : `try`/`catch` global, log systématique, affichage de la commande exacte tentée pour permettre de la rejouer manuellement.
