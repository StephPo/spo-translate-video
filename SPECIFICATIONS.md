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
  - ignorent les paramètres de playlist / tracking ;
  - **reconnaissent aussi les pages de tweet X/Twitter** (`x.com` ou `twitter.com`, `/<user>/status/<id>`) : dans ce cas, comme il n'y a pas d'identifiant vidéo court exploitable côté client (yt-dlp a besoin de l'URL complète du tweet, voir §3.1.1), le bookmarklet transmet l'**URL complète de la page**, encodée, plutôt qu'un simple ID.

**Code des deux bookmarklets** (à coller tel quel comme URL d'un favori) :

Bookmarklet "SPO Download" (`spodl:`, téléchargement seul) :

```
javascript:(function(){var u=location.href;var m=u.match(/[?&]v=([^&]+)/)||u.match(/youtu\.be\/([^?&/]+)/)||u.match(/\/shorts\/([^?&/]+)/);if(m){location.href='spodl:'+m[1];return;}if(/(?:^https?:\/\/)?(?:www\.)?(?:twitter\.com|x\.com)\/[^/]+\/status\/\d+/i.test(u)){location.href='spodl:'+encodeURIComponent(u);return;}alert('Aucun ID video YouTube ou tweet trouve dans cette page.');})();
```

Bookmarklet "SPO Translate" (`spotr:`, téléchargement + transcription + traduction) :

```
javascript:(function(){var u=location.href;var m=u.match(/[?&]v=([^&]+)/)||u.match(/youtu\.be\/([^?&/]+)/)||u.match(/\/shorts\/([^?&/]+)/);if(m){location.href='spotr:'+m[1];return;}if(/(?:^https?:\/\/)?(?:www\.)?(?:twitter\.com|x\.com)\/[^/]+\/status\/\d+/i.test(u)){location.href='spotr:'+encodeURIComponent(u);return;}alert('Aucun ID video YouTube ou tweet trouve dans cette page.');})();
```

Ces deux extraits produisent une navigation vers `spodl:VIDEO_ID`/`spotr:VIDEO_ID` (YouTube) ou `spodl:<URL encodée>`/`spotr:<URL encodée>` (tweet X/Twitter), conformément au mécanisme ci-dessus. Voir §7.3 pour la procédure d'ajout au navigateur. Le gestionnaire de protocole (`spo-protocol-handler.ps1`) détecte qu'une valeur reçue après `spodl:`/`spotr:` est déjà une URL complète (`https?://...`, une fois décodée) et la transmet telle quelle au pipeline (au lieu de la traiter comme un identifiant vidéo YouTube), qui applique ensuite sa détection automatique habituelle (§3.1) pour reconnaître qu'il s'agit d'un tweet.

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
| URL tweet X/Twitter (`x.com` ou `twitter.com`, `/status/<id>`) | Auto (regex) | yt-dlp |
| URL `.m3u8` (flux HLS) | Auto (regex) | ffmpeg (remux direct) |
| Fichier vidéo local (`mp4`, `avi`, `mov`, `mkv`, `webm`) | Auto (chemin existant sur disque) | — |

> La détection du type de source est **toujours automatique** ; il n'existe pas d'option pour la forcer manuellement (retiré volontairement : la détection auto est jugée fiable à 100 % dans l'usage réel du projet).

#### 3.1.1 URL de tweet X/Twitter avec plusieurs vidéos

Un tweet peut contenir plusieurs vidéos exploitables par yt-dlp : soit plusieurs pièces jointes vidéo natives sur le même tweet, soit la vidéo du tweet lui-même **et** celle d'un tweet cité/quoté. Comportement à l'entrée d'une URL de tweet :

- Le programme interroge d'abord yt-dlp (sans télécharger, `noplaylist=False`) pour lister les vidéos trouvées pour cette URL ; yt-dlp les représente comme les entrées d'une pseudo-playlist.
- **Une seule vidéo trouvée** : téléchargement automatique, sans interaction supplémentaire (comportement identique à YouTube/m3u8).
- **Plusieurs vidéos trouvées** : le programme affiche la liste des vidéos (auteur du tweet associé, titre/description si disponible) numérotée, puis **demande interactivement** à l'utilisateur de choisir laquelle télécharger (saisie du numéro). Seule la vidéo choisie est téléchargée et traitée (téléchargement seul, ou pipeline complet transcription/traduction selon `--download-only`) ; ce n'est pas un traitement en masse de toutes les vidéos du tweet.
  - **Important** : plusieurs pièces jointes natives sur un même tweet partagent exactement la même `webpage_url` (celle du tweet) — l'URL seule ne suffit donc pas à re-sélectionner la vidéo choisie. Le programme mémorise et réutilise la **position (1-based) de l'entrée dans la pseudo-playlist** (`playlist_index`, option yt-dlp `playlist_items`) pour le téléchargement effectif.
- Le téléchargement de la vidéo choisie réutilise la même mécanique que le téléchargement YouTube (yt-dlp, meilleure qualité tous conteneurs, remux `.mp4` si nécessaire — voir §3.3), yt-dlp gérant nativement l'extraction X/Twitter (manifest HLS maître avec pistes vidéo/audio séparées).

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
| `--listchapters` | `--lc` | Liste les chapitres et leur statut de correspondance, sans traiter. **Prévisualise toujours l'auto-sélection** (motifs `chapter_autoselect_patterns`), même sans `--autoselectchapters` — permet de vérifier rapidement ses regex sur une vidéo donnée. Une sélection manuelle `--chapters` fournie en même temps reste combinée (union) comme d'habitude |
| `--resume` | — | Reprend un run précédent en évitant de refaire les phases déjà terminées avec succès : ne retélécharge pas si le téléchargement avait déjà réussi, ne retranscrit pas si une transcription était déjà en cache, et continue la traduction à partir du dernier segment traduit |

> Options retirées volontairement par rapport à la version précédente : `--source-type` (détection auto systématique), `--output-basename` (le nom par défaut basé sur le titre/fichier source convient), `--no-progress` (l'affichage de progression est toujours utile, jugé non gênant).

### 3.3 Téléchargement (YouTube / m3u8)

- **YouTube** via `yt-dlp` :
  - **Sélecteur de format par défaut : la vraie meilleure qualité disponible, tous conteneurs confondus** (`bestvideo+bestaudio/best`), et non plus une sélection restreinte au MP4. C'est un changement de comportement volontaire par rapport à la version précédente, qui limitait la recherche au conteneur MP4 (`bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best`) et ratait donc les flux haute résolution disponibles uniquement en WebM/VP9/AV1 — cause racine du problème "téléchargement en 360p/480p alors qu'une meilleure qualité (1080p+) est disponible".
  - Si le résultat n'est pas déjà en `.mp4` (ex. conteneur WebM/MKV), **remux automatique vers `.mp4`** avec `ffmpeg` en copie de flux (pas de réencodage, donc pas de perte de qualité ni de temps de calcul significatif), pour garder un format de sortie homogène et compatible avec la plupart des lecteurs.
  - "Preflight" de qualité systématique avant téléchargement : interroge yt-dlp pour connaître la meilleure résolution disponible, sans télécharger. Ce calcul est fait à partir de **la liste complète des formats renvoyée par yt-dlp** (hauteur max parmi tous les formats vidéo listés), et non à partir du résultat d'un sélecteur de format (`bestvideo+bestaudio/best`) : si un défi de signature/n-challenge n'est que partiellement résolu, le sélecteur peut lui-même exclure certains formats haute résolution de ses candidats, ce qui ferait apparaître à tort une qualité dégradée comme "la meilleure disponible" et masquerait le problème.
  - **Information de qualité systématique** : à la fin de chaque téléchargement YouTube, le programme affiche toujours la résolution effectivement téléchargée et la meilleure résolution détectée comme disponible (ex. `Video quality: downloaded 1080p (best available: 1080p) — <url>`), que les deux correspondent ou non — pas seulement en cas de problème. **L'URL de la vidéo est systématiquement incluse dans les logs** : au début du preflight, au début du téléchargement, dans le message de qualité, et dans tout avertissement/erreur lié au téléchargement, pour permettre de retrouver facilement de quelle vidéo il s'agit dans un run avec plusieurs téléchargements ou dans les logs.
  - **Garde-fou obligatoire** : si le résultat effectivement téléchargé (`requested_downloads`) est inférieur à la meilleure résolution annoncée par le preflight (ex. un flux protégé/soumis à throttling n'était finalement pas accessible), le programme **avertit explicitement**, avant de continuer, avec :
    - la résolution effectivement téléchargée et celle qui était annoncée comme disponible (ex. `Téléchargé : 480p — Meilleure qualité disponible détectée : 1080p`) ;
    - la raison si elle est connue (ex. format restreint, nécessite authentification, échec de résolution de signature/n-challenge) ;
    - la commande à relancer manuellement pour forcer un format précis (ex. `yt-dlp -f <format_id> <url>`) pour investiguer/corriger soi-même ;
    - ne bloque pas le run (l'utilisateur peut vouloir continuer quand même), mais l'information doit être impossible à manquer : le message est encadré (bloc ASCII), comme les erreurs fatales.
  - **Cas particulier "SABR-only streaming"** (voir §4.2) : même si la qualité téléchargée correspond exactement à la "meilleure disponible" calculée par le preflight, un avertissement encadré dédié est affiché si yt-dlp signale que des formats ont été ignorés à cause du rollout SABR de YouTube — car dans ce cas la "meilleure disponible" elle-même peut être artificiellement basse (les formats concernés disparaissent des métadonnées, pas seulement du sélecteur).
  - **Retry automatique en cas de qualité dégradée, de rollout SABR détecté, OU d'échec pur et simple** : le rollout SABR de YouTube s'applique **par requête/session** (le message yt-dlp le précise explicitement : "for the current session"), pas de façon déterministe selon la configuration — la même URL, avec la même configuration, peut réussir en pleine qualité sur une tentative et être plafonnée sur la suivante. Ce n'est donc pas un problème corrigible une fois pour toutes côté configuration, mais un comportement probabiliste côté YouTube où chaque tentative est un nouveau tirage. Le programme retente automatiquement, dans le même run :
    - d'abord avec une liste étendue de clients de lecture (`VideoDownloader.FALLBACK_PLAYER_CLIENTS`, actuellement `["default", "tv", "ios", "web_safari", "mweb"]`, fusionnée avec `video.youtube_player_clients`) si la qualité est dégradée, qu'un avertissement SABR est présent, ou que la tentative précédente a échoué avec une erreur dure (ex. `ERROR: The downloaded file is empty`) ;
    - puis, si le problème persiste, en retentant **jusqu'à `video.youtube_quality_max_attempts` tentatives au total** (3 par défaut, configurable dans `config.yaml`) en réutilisant les clients de secours — chaque tentative étant un tirage indépendant, augmenter ce nombre augmente mécaniquement les chances d'obtenir la vraie meilleure qualité au sein d'un même run.
    - Le meilleur résultat parmi les tentatives ayant réussi (par résolution obtenue) est conservé ; les fichiers temporaires des tentatives non retenues sont supprimés. Le téléchargement n'échoue que si **toutes** les tentatives échouent.
  - **Commande yt-dlp équivalente systématiquement loguée** : avant chaque appel yt-dlp (preflight et téléchargement, y compris une éventuelle tentative de retry), le programme logue en `INFO` la ligne de commande `yt-dlp` équivalente aux options effectivement utilisées (format, `--extractor-args` pour le client de lecture, `--js-runtimes`, `--remote-components`, `-o`, etc.), pour permettre de reproduire exactement le même appel manuellement en cas de problème.
  - Nom de fichier final basé sur le **titre brut YouTube, assaini** (caractères interdits sous Windows `< > : " / \ | ? *` remplacés par `_`) — téléchargement initial dans le dossier temporaire sous un nom basé sur l'id vidéo (évite les soucis d'unicode/collision côté yt-dlp), puis renommage vers `<titre assaini>.mp4` lors du déplacement final.
  - **Le fichier vidéo et le fichier `.srt` partagent le même nom de base** (seule l'extension — et le suffixe de langue pour le `.srt` — diffère) : le nom de base utilisé pour les sous-titres est dérivé du nom réel du fichier vidéo téléchargé (déjà assaini), pas du titre brut, afin de garantir la cohérence même en cas de titre contenant des caractères interdits.
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
- **Fusion des sous-titres** : lorsque plusieurs chapitres sont sélectionnés, chaque chapitre est transcrit/traduit séparément (dans l'ordre des chapitres), mais les sous-titres résultants sont **fusionnés en un seul fichier `.srt`** (nommé comme pour un run classique, `<basename>.<langue>.srt`, sans suffixe `_chN`), avec renumérotation continue des indices de cues. Aucun fichier `.srt` par chapitre n'est conservé sur disque.
- **Mode test** (`--listchapters`) : liste les chapitres et indique lesquels seraient sélectionnés, sans rien extraire/transcrire/traduire — pour valider ses regex avant un run réel. Simule **toujours** l'auto-sélection par motifs, même sans `--autoselectchapters` (l'affichage seul n'a aucun effet destructif, donc pas besoin de le demander explicitement) ; une sélection manuelle `--chapters` fournie en parallèle reste prise en compte en union.
- Si aucun chapitre ne correspond (ou le fichier n'a pas de chapitres) alors qu'une sélection a été demandée : demande interactive (`y` = traduire le fichier entier, sinon arrêt).

### 3.6 Transcription (Whisper)

- Moteur : `openai-whisper` (local, pas d'API payante), avec `torch` pour l'inférence.
- Modèle configurable (`tiny` à `large`, y compris `turbo`).
- Accélération GPU optionnelle (CUDA), avec repli CPU automatique si indisponible.
- Horodatage par segment (`start`, `end`, texte).
- Filtrage par seuil de confiance heuristique : Whisper ne fournit pas de score de confiance calibré unique (0-1), mais expose par segment `avg_logprob` (log-probabilité moyenne du décodage) et `no_speech_prob` (probabilité d'absence de parole, utile contre les hallucinations sur silence/bruit) ; l'heuristique combine ces deux signaux natifs avec la longueur du texte et la proportion de caractères spéciaux.
- Longueur de segment maximale configurable.

### 3.7 Traduction

Voir §5 pour le détail des fournisseurs. Points communs :

- Segmentation identique à celle produite par Whisper (1 segment audio = 1 ligne/cue de sous-titre).
- **Retry avec backoff exponentiel + jitter** en cas d'erreur transitoire ou de rate limit (HTTP 429), configurable (`max_retries`, `initial_delay_seconds`, `max_delay_seconds`, `backoff_multiplier`, `jitter_ratio`), avec override possible par service. Pour le SDK OpenAI, le retry interne du client (`max_retries`, 2 par défaut) est explicitement désactivé (`max_retries=0`) afin que ce backoff custom soit la seule source de retry (évite d'empiler deux mécanismes de retry indépendants).
- **Fail-fast** : si la traduction échoue définitivement (retries épuisés), le programme s'arrête immédiatement et sauvegarde un **cache JSON** (`<base>.<lang>.cache.json`) contenant la transcription complète (`starts`/`ends`/`originals`) ainsi que les segments déjà traduits (éventuellement une liste vide si l'échec survient dès le premier segment), à côté du dossier de sortie des sous-titres.
- **Reprise (`--resume`) — les 3 phases sont reprises indépendamment**, chacune n'étant refaite que si elle n'a pas déjà réussi lors d'un run précédent :
  1. **Téléchargement** (YouTube/m3u8) : le chemin du fichier vidéo téléchargé avec succès est mémorisé dans un cache JSON persistant (`download_cache_<hash>.json`, à la racine de `video.temp_directory`, donc hors du répertoire temporaire par run qui est nettoyé à chaque exécution). Si ce fichier existe encore sur disque, `--resume` le réutilise directement sans relancer `yt-dlp`/`ffmpeg`.
  2. **Transcription** : dès qu'une transcription Whisper a réussi, elle est présente dans le cache JSON de traduction (`starts`/`ends`/`originals`), y compris si la traduction échoue immédiatement après (segments traduits vides). `--resume` détecte la présence d'une transcription en cache (indépendamment du nombre de segments déjà traduits) et **ne refait jamais la transcription** dans ce cas — l'étape la plus coûteuse en temps.
  3. **Traduction** : reprend au dernier segment traduit avec succès (potentiellement l'index 0 si aucun segment n'avait encore réussi).
  - Le cache de téléchargement est supprimé automatiquement à la fin d'un run complet réussi (ou d'un `--download-only` réussi) ; le cache de traduction est supprimé dès que tous les segments d'une unité de traduction (vidéo entière ou chapitre) sont traduits avec succès.

### 3.8 Écriture des sous-titres

- Format `.srt` (horodatage `HH:MM:SS,mmm`).
- Nom de fichier : `<base>.<langue_cible>.srt`.
- Emplacement :
  - Téléchargements (YouTube/m3u8) : dossier `output.video_download_directory`.
  - Fichiers locaux : à côté du fichier vidéo source.
  - Dans les deux cas, `--dest` permet d'overrider pour un run donné.
- **Sauvegarde de la transcription originale** : en plus du `.srt` traduit, un fichier de backup contenant la transcription dans la langue source (avant traduction), avec les mêmes horodatages, est écrit à côté dans le même dossier, au format `<base>.<langue_source>.bak` (même format `.srt` en interne, juste une extension `.bak`). Exemple : pour `toto.mp4` transcrit en japonais et traduit en français, on obtient `toto.fr.srt` et `toto.jp.bak`. Ce fichier permet de retrouver le texte original si besoin (relecture, retraduction manuelle, etc.) sans avoir à retranscrire la vidéo. En cas de sélection de chapitres (§3.6), c'est la transcription originale fusionnée (tous les chapitres sélectionnés, renumérotée en continu) qui est sauvegardée dans un unique `.bak`, de la même façon que le `.srt` traduit fusionné.

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
- **Commande d'invocation systématiquement loguée en première ligne** : dès le démarrage (juste après l'initialisation du logger), le programme logue la commande `.bat` équivalente permettant de reproduire exactement ce run (ex. `Command: C:\...\spo-translate-video.bat https://www.youtube.com/watch?v=XXXX --asc`), reconstruite à partir de `sys.argv` (`main._describe_invocation_command`). `spo-translate-video.bat` et `spo-dl-video.bat` transmettent leurs arguments tels quels à `python main.py`, donc cette reconstruction est fiable quel que soit le point d'entrée réellement utilisé (bookmarklet/protocole, `.bat` direct, ou `python main.py` direct).
- Sortie colorée dans le terminal (vert/jaune/rouge) quand le terminal le supporte (détection ANSI, y compris activation du mode VT100 sous Windows).
- Affichage de progression (désactivable via `--no-progress`).
- Bloc d'erreur visuellement distinct en cas d'échec fatal (bordures, message clair) pour rester lisible même dans une fenêtre qui va rester ouverte.
- **Copie des logs dans un fichier**, en plus de l'affichage console habituel (inchangé) : contrôlé par `processing.log_to_file` (`true` par défaut) et `processing.log_file_path` (défaut `./temp/logs/last_run.log`).
  - Le fichier est **écrasé au début de chaque run** (pas d'accumulation entre runs) — il ne contient donc toujours que les logs du run le plus récent, jusqu'au démarrage du run suivant.
  - Le chemin du fichier de log est loggué dès l'initialisation (juste avant la commande d'invocation), pour le retrouver facilement.
  - Les **exceptions non interceptées** (traceback Python en fin de run, ex. `RuntimeError: Download failed: ...`) sont également écrites dans ce fichier (`sys.excepthook`), en plus de leur affichage console habituel (inchangé, sur stderr) — utile pour transmettre le fichier de log complet en cas de bug plutôt que copier-coller la console.
  - **Codes couleur ANSI filtrés dans le fichier** : la sortie console reste colorée normalement (vert/jaune/rouge, inchangé), mais le fichier de log (destiné à être relu/collé tel quel) ne contient jamais les séquences d'échappement ANSI brutes (`main._StripAnsiFormatter`, appliqué uniquement au handler fichier).

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

**Cache du script de résolution de challenge scopé au run** : le composant distant (`ejs:github`) télécharge et met en cache localement un script de résolution. Si ce cache (normalement partagé entre tous les runs, dans le dossier de cache par défaut de l'utilisateur) contient une version incompatible avec celle attendue par la version de yt-dlp installée, cela peut provoquer un échec dur du téléchargement (`Challenge solver lib script version X is not supported ...` suivi de `ERROR: The downloaded file is empty`) que yt-dlp n'invalide pas toujours à temps lors de la même extraction. **Fix** : le cache yt-dlp (`cachedir`) est scopé au dossier temporaire du run en cours (`<temp_directory>/run_<horodatage>_<pid>/.yt-dlp-cache`) plutôt que d'utiliser le dossier de cache global — chaque run démarre avec un cache vide et cohérent, tout en évitant les téléchargements redondants du script entre les différents appels yt-dlp **au sein d'un même run**. Le cache est supprimé en fin de run avec le reste du dossier temporaire (§3.10).

### 4.2 Rollout YouTube "SABR-only streaming" et `youtube_player_clients`

YouTube déploie progressivement, par client de lecture (`web`, `android_vr`, etc.) et par session, un mode de streaming "SABR" qui ne fournit plus d'URL de téléchargement direct (`https`) pour les formats haute résolution de ce client — seul un flux protocolaire SABR (non supporté en téléchargement direct par yt-dlp) reste disponible. Quand cela touche le(s) client(s) utilisé(s) par yt-dlp pour une vidéo donnée :

- yt-dlp logue un avertissement du type `Some <client> client https formats have been skipped as they are missing a URL. YouTube may have enabled the SABR-only streaming experiment...` (voir [yt-dlp#12482](https://github.com/yt-dlp/yt-dlp/issues/12482)).
- Les formats haute résolution de ce client disparaissent **entièrement** de la liste de formats retournée par yt-dlp — le calcul de "meilleure qualité disponible" (§3.3) ne peut donc pas les détecter, car ils ne sont pas juste ignorés par le sélecteur, ils sont absents des métadonnées elles-mêmes.
- Dans ce cas, le programme détecte l'avertissement SABR de yt-dlp et affiche un **avertissement encadré dédié**, même si la qualité téléchargée correspond à la "meilleure disponible" calculée — car cette dernière peut elle-même être artificiellement basse.
- **Contournement, activé par défaut** : `config.yaml` configure `video.youtube_player_clients: ["default", "tv"]` — le client `tv` n'était pas touché par le rollout SABR au moment de la rédaction. Le client `tv` seul peut renvoyer une erreur DRM sur certaines vidéos/sessions ; le combiner avec `default` permet de retomber sur les formats `default` dans ce cas, `tv` n'apportant alors que les formats haute résolution manquants.
- Si le problème persiste malgré ce réglage : mettre à jour yt-dlp (`pip install -U yt-dlp`), les correctifs par client étant publiés fréquemment, ou essayer d'autres combinaisons de clients (`ios`, `web_safari`, `mweb`...).

```yaml
video:
  youtube_player_clients: ["default", "tv"]  # vide = comportement par défaut de yt-dlp
```

---

## 5. Traduction : fournisseurs supportés

Deux fournisseurs actifs, choisis via `translation.service` dans `config.yaml` :

### 5.1 DeepL

- `translation.service: "deepl"`
- Plan : `translation.deepl.plan: "free"` (défaut) ou `"pro"`.
- Clé API : `config.local.yaml` (recommandé), variable d'environnement `DEEPL_API_KEY`, ou `translation.api_keys.deepl`.
- Traduction segment par segment, sans awareness contextuelle avancée (pas de prompt personnalisable — DeepL n'expose pas ce mécanisme).
- Qualité généralement excellente et rapide pour la plupart des paires de langues.

### 5.2 OpenAI (LLM, avec prompts personnalisables) — recommandé par défaut

- `translation.service: "openai"`
- Modèle configurable : `translation.openai.model` (ex. `"gpt-4o"`, `"gpt-4o-mini"`).
- `translation.openai.batch_size` : nombre de segments envoyés par requête (regroupement pour limiter les appels et donner plus de contexte au modèle).
- Clé API : `config.local.yaml` (recommandé), variable d'environnement `OPENAI_API_KEY`, ou `translation.api_keys.openai`.
- Meilleur pour les lignes riches en contexte (jeux de mots, tonalité, argot, références culturelles), plus lent et plus coûteux que DeepL mais choisi comme fournisseur par défaut pour sa meilleure qualité contextuelle.
- Seul fournisseur exposant un système de **prompts personnalisables** (voir §5.3).
- Alignement segments ↔ traductions : les segments d'un batch sont envoyés sous forme de liste numérotée ; la réponse est découpée ligne par ligne pour ré-associer chaque traduction à son segment d'origine. Si le modèle ne respecte pas le format 1 ligne = 1 segment (ex. fusion de segments courts/fragmentés en une phrase plus naturelle), le batch entier est retraduit segment par segment (`batch_size` effectif de 1 pour ce batch), avec un warning loggé indiquant le nombre de lignes reçu vs. attendu. Cela évite qu'un texte non traduit (langue source) ne se retrouve silencieusement dans le sous-titre final.

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

#### Bibliothèque de prompts sauvegardés (`saved_prompts`)

`config.prompt.yaml` peut contenir une clé optionnelle `saved_prompts` (mapping nommé, ex. `saved_prompts.bish_concert`) servant de **bibliothèque personnelle de prompts `system_prompt_extended` passés/mis de côté**, pour pouvoir les réutiliser sur un projet futur sans les perdre. Cette clé n'est **jamais lue par le code** (seul le champ exact `system_prompt_extended` est envoyé au modèle) — c'est un espace de stockage texte pur, géré manuellement par l'utilisateur : pour réactiver un prompt sauvegardé, il faut recopier son contenu dans `system_prompt_extended`. Avant d'écraser `system_prompt_extended` pour un nouveau projet, l'utilisateur est libre d'archiver son contenu actuel sous une nouvelle entrée de `saved_prompts`.

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
- `video` — chemin ffmpeg, encodage (non utilisé pour la génération de sous-titres seule, conservé pour référence), dossier temporaire, `youtube_js_runtime`, `youtube_remote_components`, `youtube_player_clients` (voir §4.2).
- `processing` — parallélisme, niveau de log, copie des logs dans un fichier (`log_to_file`/`log_file_path`, voir §3.11), patterns d'auto-sélection de chapitres, nettoyage des fichiers temporaires.
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

Ensuite, **`yt-dlp` spécifiquement est revérifié et mis à jour automatiquement au plus une fois tous les 7 jours** (indépendamment du reste des dépendances, figées après la première installation — voir §8) : YouTube change fréquemment ses protections, et une installation de `yt-dlp` figée plusieurs mois peut significativement dégrader la fiabilité des téléchargements.

### 7.3 Configurer le déclenchement navigateur (optionnel)

```powershell
cd C:\Dev\CascadeProjects\spo-translate-video
.\install-protocol-handlers.ps1
```

Puis créer 2 favoris dans le navigateur avec le code JavaScript fourni en §2.2 :

**Chrome / Edge :**
1. Afficher la barre de favoris si elle est masquée (`Ctrl+Maj+B`).
2. Clic droit sur la barre de favoris → "Ajouter une page..." (ou clic droit → "Ajouter un favori").
3. Dans le champ *Nom*, mettre par exemple `SPO Download` (ou `SPO Translate`).
4. Dans le champ *URL*, coller intégralement le code `javascript:(function(){...})();` correspondant (§2.2), en une seule ligne.
5. Enregistrer. Répéter pour le second bookmarklet.

**Firefox :**
1. Afficher la barre personnelle (`Ctrl+Maj+B`).
2. Clic droit sur la barre → "Nouveau signet...".
3. *Nom* : `SPO Download` (ou `SPO Translate`). *URL/Emplacement* : coller le code `javascript:...`.
4. Enregistrer. Répéter pour le second bookmarklet.

**Test :** ouvrir une vidéo YouTube (`https://www.youtube.com/watch?v=...`), cliquer sur le bookmarklet `SPO Download`. Le navigateur demande de confirmer l'ouverture de l'application externe associée à `spodl:` → accepter. Une fenêtre de commande doit s'ouvrir et rester ouverte pendant le téléchargement (voir §9 en cas de fenêtre qui se referme aussitôt).

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
| **yt-dlp** | YouTube change fréquemment ses protections anti-bot ; les mainteneurs de yt-dlp publient des correctifs très rapidement | **Automatique** : `spo-translate-video.bat` revérifie et met à jour `yt-dlp` tout seul au plus une fois tous les 7 jours (marqueur `.venv\yt_dlp_last_update.marker`), en plus de l'installation initiale figée par `deps_installed.marker`. Forcer une vérification immédiate : supprimer `.venv\yt_dlp_last_update.marker` | `.\.venv\Scripts\python -m pip install --upgrade yt-dlp` puis vérifier avec `.\.venv\Scripts\python -m yt_dlp --version` |
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
| `WARNING: ... Some <client> client https formats have been skipped as they are missing a URL. YouTube may have enabled the SABR-only streaming experiment...` / qualité téléchargée décevante malgré un avertissement absent ou correspondant | Rollout YouTube "SABR-only streaming" affectant le(s) client(s) de lecture utilisé(s) par yt-dlp pour cette vidéo (voir §4.2) | Configurer `video.youtube_player_clients` (ex. `["default", "tv"]`) et/ou mettre à jour yt-dlp (`pip install -U yt-dlp`) |
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

## 10bis. Tests

Framework : **pytest** (`requirements-dev.txt`, config dans `pytest.ini`).

Organisation en deux catégories, séparées par dossier :

| Dossier | Contenu | Exécution |
|---|---|---|
| `tests/unit/` | Fonctions pures, sans I/O (parsing de sélection de chapitres, détection de type de source, etc.) | Toujours exécutés (`pytest`) |
| `tests/integration/` | Nécessitent des ressources réelles : vidéo locale, ffmpeg, réseau YouTube, API de traduction | Marqués `@pytest.mark.integration`, **exclus par défaut** (`addopts = -m "not integration"`), lancés explicitement via `pytest -m integration` |

Ressources de test réelles (voir `tests/fixtures/README.md`) :
- `tests/fixtures/videos/test.mkv` : courte vidéo locale avec chapitres embarqués (non versionnée, binaire, à déposer manuellement).
- `tests/fixtures/videos/test_chapters.txt` : fichier de chapitrage source (format ffmetadata) ayant servi à générer `test.mkv` ; versionné, utilisé comme référence dynamique pour calculer le résultat attendu des tests de chapitres (`tests/conftest.py::expected_chapters`), sans dupliquer les valeurs en dur dans le code de test.
- `tests/fixtures/videos/non_ascii_chapters.mkv` (+ `non_ascii_chapters_meta.txt`) : vidéo committée avec un titre de chapitre non représentable en cp1252, pour reproduire de façon reproductible et sans coût de régénération le bug d'encodage de `_ffprobe_chapters` (voir historique des décisions).
- `tests/fixtures/test_urls.yaml` : URL(s) YouTube de test ; **versionné** (choix du projet, pour éviter d'avoir à en rechercher une à chaque nouvel environnement).

Les fixtures pytest correspondantes (`tests/conftest.py`) font un `pytest.skip()` explicite si la ressource n'est pas encore fournie, plutôt que d'échouer.

**Règle sur les vidéos de test générées par l'agent** : lorsqu'un test a besoin d'une courte vidéo synthétique (ex. générée via `ffmpeg -f lavfi ...` pour reproduire un bug précis), cette vidéo doit être **générée une seule fois puis committée** dans `tests/fixtures/videos/` (avec son éventuel fichier source, ex. métadonnées ffmetadata, pour permettre de la régénérer si besoin) — jamais régénérée dynamiquement à chaque exécution du test (`tmp_path` + appel `ffmpeg`), afin d'éviter le coût d'exécution répété et de garder les tests rapides et déterministes.

---

## 11. Historique des décisions

- **[Cette version]** Ajout de la clé optionnelle `saved_prompts` dans `config.prompt.yaml` : bibliothèque de prompts `system_prompt_extended` archivés (non lue par le code), pour permettre de conserver et réutiliser plus tard les prompts d'anciens projets sans les perdre ni les confondre avec le prompt actif.
- **[Cette version]** Correction bug : avec le fournisseur OpenAI, quand le modèle ne renvoyait pas exactement une ligne par segment du batch (fusion de segments courts/fragmentés), les segments en trop retombaient silencieusement sur le texte source non traduit dans le `.srt` final. Le batch concerné est maintenant retraduit segment par segment avec un warning loggé.
- **[Cette version]** Changement du fournisseur de traduction par défaut : `translation.service` passe de `"deepl"` à `"openai"` dans `config.yaml`.
- **[Cette version]** Suppression du code de doublage/TTS (jamais fonctionnel, non branché) du périmètre du projet.
- **[Cette version]** Retrait des fournisseurs de traduction `google_translate` et `azure` du périmètre actif (non recommandés / non implémentés).
- **[Cette version]** Introduction du champ `system_prompt_extended` comme mécanisme officiel de personnalisation par projet, avec nouveau `system_prompt` par défaut générique.
- **[Cette version]** Formalisation des exigences de fiabilité du déclenchement navigateur (protocole d'URL personnalisé).
- **[Cette version]** Retrait des options CLI `--source-type`, `--output-basename` et `--no-progress` (détection auto systématique, nom de fichier par défaut satisfaisant, progression toujours affichée).
- **[Cette version]** Extraction de `system_prompt` / `system_prompt_extended` dans un fichier dédié `config.prompt.yaml`, distinct de `config.yaml`, car modifié beaucoup plus fréquemment que le reste de la configuration.
- **[Cette version]** Changement du sélecteur de format YouTube par défaut : vraie meilleure qualité tous conteneurs (`bestvideo+bestaudio/best`) + remux `.mp4` si nécessaire, au lieu d'une sélection restreinte au MP4 qui pouvait dégrader la résolution téléchargée. Ajout d'un avertissement obligatoire si la qualité effectivement obtenue reste inférieure à la meilleure disponible.
- **[Cette version]** Renforcement des exigences de diagnostic pour le bug connu du déclenchement navigateur (fenêtre qui se ferme sans exécuter) : `try`/`catch` global, log systématique, affichage de la commande exacte tentée pour permettre de la rejouer manuellement.
- **[Cette version]** Mise en place de l'infrastructure de tests (pytest) : séparation `tests/unit/` (sans I/O, toujours exécutés) et `tests/integration/` (ressources réelles — vidéo locale avec chapitres, URL YouTube — exclus par défaut, lancés via `pytest -m integration`).
- **[Cette version]** Implémentation des premiers tests : unitaires sur la sélection de chapitres (`_parse_chapter_selection`, `_autoselect_chapters`, `_resolve_chapter_selection`) et la détection de source (`detect_source_type`) ; intégration sur `_ffprobe_chapters`/sélection auto avec une vraie vidéo fixture (`test.mkv` + `test_chapters.txt` versionné comme référence dynamique) et sur le téléchargement YouTube réel. Choix de versionner `test_urls.yaml` (petit fichier texte) plutôt que de l'exclure du dépôt.
- **[Cette version]** Correction de `--resume` pour qu'il reprenne réellement les 3 phases (téléchargement, transcription, traduction) indépendamment : (1) ajout d'un cache JSON persistant pour le téléchargement (`download_cache_<hash>.json`) afin de ne pas retélécharger la vidéo si elle a déjà été récupérée avec succès ; (2) correction d'un bug où la condition de reprise de la traduction (`cache.get("segments")`) était considérée fausse — et déclenchait donc une re-transcription Whisper inutile — dès que la liste des segments déjà traduits était vide (ex. échec dès le premier segment), alors que la transcription elle-même (`originals`/`starts`/`ends`) était bien présente en cache. Couvert par de nouveaux tests unitaires (`tests/unit/test_resume.py`), sur les 3 phases indépendamment (reprise du téléchargement, non re-transcription si déjà en cache — y compris avec `segments` vide —, et reprise de la traduction au bon index).
- **[Cette version]** Correction de `VideoDownloader._build_js_runtime` (`video_downloader.py`) : l'option `js_runtimes` passée à yt-dlp doit être un dict `{runtime: {config}}` et non une liste — bug détecté par le nouveau test d'intégration `test_download_from_youtube_succeeds`.
- **[Cette version]** Correction d'un bug d'encodage dans `_ffprobe_chapters` (main.py) : `subprocess.run(..., text=True)` sans `encoding="utf-8"` explicite décodait la sortie JSON d'ffprobe avec l'encodage de la locale Windows (ex. cp1252), provoquant un `UnicodeDecodeError` silencieux et une perte de chapitres dès qu'un titre contenait un caractère non représentable dans cette locale (ex. japonais). Fix : `encoding="utf-8"` explicite passé à `subprocess.run`. Détecté et vérifié par `tests/integration/test_chapter_encoding_bug.py` (vidéo fixture committée `non_ascii_chapters.mkv`).
- **[Cette version]** Correction : le fichier vidéo YouTube était nommé d'après l'id de la vidéo (`%(id)s.%(ext)s`) au lieu du titre, contrairement à la spec §3.3, et le `.srt` était nommé d'après le titre brut (non assaini), ce qui pouvait produire des noms de vidéo et de sous-titres différents, voire un échec d'écriture du `.srt` si le titre contenait des caractères interdits sous Windows. Fix : le fichier vidéo est désormais renommé d'après le titre assaini (`VideoDownloader._sanitize_filename`) lors du déplacement final, et `main.py` dérive systématiquement le nom de base des sous-titres du nom réel du fichier vidéo téléchargé (`Path(video_path).stem`), garantissant qu'ils partagent toujours le même nom de base. Ajout de tests unitaires (`tests/unit/test_video_downloader.py`).
- **[Cette version]** Correction du "preflight" de qualité YouTube (§3.3) : la meilleure résolution disponible est désormais calculée à partir de la hauteur maximale de la liste brute des formats (`VideoDownloader._max_available_height`), et non plus du résultat du sélecteur `bestvideo+bestaudio/best`, qui pouvait sous-estimer silencieusement la qualité réellement disponible. Ajout d'un message d'information systématique (qualité téléchargée vs. meilleure disponible, à chaque téléchargement) et passage de l'avertissement de qualité dégradée à un bloc ASCII encadré.
- **[Cette version]** Détection du rollout YouTube "SABR-only streaming" (§4.2, [yt-dlp#12482](https://github.com/yt-dlp/yt-dlp/issues/12482)), qui peut faire disparaître des formats haute résolution des métadonnées elles-mêmes (pas seulement du sélecteur) et donc rendre le garde-fou de qualité aveugle au problème. Contournement par défaut via `video.youtube_player_clients: ["default", "tv"]` dans `config.yaml`.
- **[Cette version]** Le rollout SABR s'appliquant par requête/session (confirmé en conditions réelles : mêmes URL/config, résultats différents d'une tentative à l'autre), `download_from_youtube` retente automatiquement avec une liste de clients de lecture étendue (`VideoDownloader.FALLBACK_PLAYER_CLIENTS`) sur qualité dégradée, avertissement SABR, **ou échec dur d'une tentative** (ex. `ERROR: The downloaded file is empty`), jusqu'à `video.youtube_quality_max_attempts` tentatives au total (3 par défaut, chaque tentative étant un tirage indépendant côté YouTube) ; le meilleur résultat obtenu est conservé.
- **[Cette version]** Le cache yt-dlp (`cachedir`) est scopé au dossier temporaire du run en cours plutôt qu'au dossier de cache global de l'utilisateur, pour éviter qu'un script de résolution de challenge (`ejs:github`) mis en cache dans une version incompatible avec le yt-dlp installé ne cause un échec dur (`Challenge solver lib script version ... is not supported`).
- **[Cette version]** Journalisation systématique (§3.11) de la commande d'invocation complète permettant de reproduire un run (`main._describe_invocation_command`, sous forme `<chemin>\spo-translate-video.bat <arguments>`), et de la commande `yt-dlp` équivalente avant chaque appel (preflight, téléchargement, retry), pour faciliter la reproduction manuelle d'un problème.
- **[Cette version]** `spo-translate-video.bat` vérifie et met à jour `yt-dlp` automatiquement au plus une fois tous les 7 jours (§7.2/§8, marqueur `.venv\yt_dlp_last_update.marker`), indépendamment du reste des dépendances figées après l'installation initiale : une installation figée peut prendre plusieurs mois de retard et dégrader la fiabilité des téléchargements YouTube.
- **[Cette version]** Ajout du code JavaScript effectif des deux bookmarklets (`spodl:`/`spotr:`) en §2.2, ainsi que de la procédure pas-à-pas d'ajout aux favoris (Chrome/Edge/Firefox) en §7.3 — auparavant seulement décrits sans être fournis.
- **[Cette version]** Même correctif (`encoding="utf-8"` explicite) appliqué par cohérence aux autres appels `subprocess.run(..., text=True)` manipulant des flux ffmpeg pouvant contenir des caractères non-ASCII (chemins/titres) : extraction audio (`audio_processor.py`), remux mp4 et téléchargement m3u8 (`video_downloader.py`). Bug confirmé en conditions réelles (titre de vidéo avec caractères japonais) sur l'extraction audio, même symptôme que celui déjà corrigé pour `_ffprobe_chapters`.
- **[Cette version]** Ajout de `test_extract_audio_handles_non_cp1252_chapter_title` (`tests/integration/test_chapter_encoding_bug.py`), couvrant le cas réel `AudioProcessor.extract_audio` avec titres de chapitres non-ASCII. Ajout d'une piste audio silencieuse à la fixture `non_ascii_chapters.mkv` pour permettre ce test (chapitres inchangés).
- **[Cette version]** Formalisation de la règle : toute vidéo de test synthétique générée par l'agent (ffmpeg) doit être committée dans `tests/fixtures/videos/`, jamais régénérée à chaque exécution (`tmp_path` + appel ffmpeg à chaque run), pour éviter le coût de génération répété.
- **[Cette version]** Correction d'un échec de téléchargement observé avec X/Twitter (`VideoDownloader._attempt_youtube_download`) : pour certains formats (notamment X/Twitter), yt-dlp laisse `ext` non résolu (`"NA"`) dans les métadonnées de format, ce qui produit un chemin de fichier incorrect (ex. `<id>.NA`) alors que le fichier réellement fusionné par `ffmpeg`/yt-dlp existe sous une autre extension (ex. `.mp4`) — provoquant un échec du remux (`No such file or directory`) alors que le téléchargement avait en réalité réussi. Fix : si le chemin dérivé n'existe pas sur disque, recherche du fichier réellement téléchargé dans le dossier temporaire par préfixe d'id vidéo.
- **[Cette version]** Correction de la racine du bug ci-dessus pour le cas réel qui le déclenchait : un tweet avec **plusieurs pièces jointes vidéo natives** (pas seulement le cas "tweet cité") expose des entrées yt-dlp partageant toutes la même `webpage_url`, donc retélécharger via cette seule URL après sélection ré-ambiguïsait la vidéo et cassait la résolution de `ext`/`formats` (§3.1.1). Fix : `VideoDownloader.list_twitter_videos` renvoie désormais aussi la position (1-based) de chaque entrée dans la pseudo-playlist yt-dlp (`playlist_index`), et `download_from_youtube`/`_attempt_youtube_download`/`preflight_best_height` acceptent ce `playlist_index` pour cibler précisément l'entrée choisie via l'option yt-dlp `playlist_items`, en dépaquetant l'entrée unique résultante (`VideoDownloader._unwrap_single_entry`) pour retrouver des métadonnées de format correctes.
- **[Cette version]** Filtrage des codes couleur ANSI dans le fichier de log (`main._StripAnsiFormatter`), pour qu'il reste lisible tel quel une fois collé/partagé, sans toucher à l'affichage console coloré habituel.
- **[Cette version]** Ajout de la copie des logs dans un fichier (`processing.log_to_file`/`processing.log_file_path`, §3.11), en plus de l'affichage console inchangé : fichier écrasé au début de chaque run (pas d'accumulation), et capture des exceptions non interceptées via `sys.excepthook` en plus de leur affichage console habituel.
- **[Cette version]** Ajout de la détection automatique des URL de tweet X/Twitter (`/status/<id>`) comme nouvelle source d'entrée (§3.1/§3.1.1) : téléchargement direct si une seule vidéo est trouvée par yt-dlp pour ce tweet, sinon affichage d'une liste numérotée (vidéo du tweet + vidéo(s) de tweet(s) cité(s)) et sélection interactive de la vidéo à traiter.
- **[Cette version]** `--listchapters` prévisualise désormais **toujours** l'auto-sélection par motifs (`chapter_autoselect_patterns`), même sans `--autoselectchapters` : usage attendu de `--listchapters` étant justement de vérifier rapidement ses regex sur une vidéo donnée, exiger `--asc` en plus était une friction inutile. Nouvelle fonction `_listchapters_selection_preview` (main.py), couverte par `tests/unit/test_chapter_selection.py`.
- **[Cette version]** Lorsque plusieurs chapitres sont sélectionnés (`--chapters`/`--autoselectchapters`), les sous-titres de chaque chapitre sont désormais **fusionnés en un unique fichier `.srt`** au lieu de produire un fichier séparé par chapitre (`_chN`). Extraction de la logique de transcription/traduction dans `_translate_range` (retourne des `SubtitleCue` sans écrire de fichier), réutilisée par `_translate_and_write` (cas mono-fichier) et par la boucle de sélection de chapitres dans `main()` (concatène puis renumérote les cues avant un unique `write_srt`).
- **[Cette version]** Correction bug : les bookmarklets (§2.2) ne reconnaissaient que les URL YouTube et affichaient "Aucun ID vidéo YouTube trouvé dans cette page." sur une page de tweet X/Twitter, alors que ce type de source est supporté par le pipeline (§3.1) depuis son ajout — support jamais répercuté dans les bookmarklets. Fix : les bookmarklets reconnaissent désormais aussi les pages `x.com`/`twitter.com` `/status/<id>` et transmettent l'URL complète encodée (`spodl:`/`spotr:` + `encodeURIComponent(location.href)`) plutôt qu'un ID ; `spo-protocol-handler.ps1` (`ConvertTo-YouTubeUrl`) décode désormais la valeur reçue avant de vérifier si c'est déjà une URL complète.
- **[Cette version]** Ajout de la sauvegarde de la transcription originale (langue source) à côté du `.srt` traduit, au format `<base>.<langue_source>.bak` (§3.8), pour conserver le texte source sans avoir à retranscrire. `_translate_range` retourne désormais `(cues, original_cues)` ; nouvelle fonction `_write_original_backup` (main.py), appelée par `_translate_and_write` et par la boucle de sélection de chapitres (backup fusionné, comme le `.srt`). Tests dans `tests/unit/test_resume.py`.
