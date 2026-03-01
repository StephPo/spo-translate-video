# Translate subtitles (YouTube or local video)

This project generates **subtitles (.srt)** for **videos**, while keeping the **original audio**.

## What it does

- Download video from YouTube, download from an `.m3u8` URL, or use a local file
- Extract audio
- Transcribe speech to text (Whisper)
- Translate to target language (DeepL or OpenAI)
- Write subtitles as `<subtitles_directory>/<name>.<target_lang>.srt`

## Requirements

- Python 3.10+
- FFmpeg installed and available in PATH (includes `ffprobe`)

## Install

```bash
pip install -r requirements.txt
```

## Windows quick runner (.bat)

If you want to run the project without manually activating the virtual environment each time, use:

```bat
spo-translate-video.bat <same arguments as main.py>
```

What it does:

- Creates `.venv` if it doesn't exist
- Installs dependencies once (from `requirements.txt`)
- Runs `main.py` and passes through all arguments

## Browser integration (Windows): one-click from YouTube

You can trigger the scripts directly from a YouTube page using a Windows custom URL protocol handler.

This gives a mouse-only workflow:

- Click a bookmarklet (or a link)
- Confirm the browser prompt to open an external application (if shown)
- A Windows Terminal window opens and runs the script

### Install protocol handlers (per user, no admin)

Run this once in PowerShell:

```powershell
./install-protocol-handlers.ps1
```

It installs:

- `spodl:` -> runs `spo-dl-video.bat` (download-only)
- `spotr:` -> runs `spo-translate-video.bat` (download + translate)

To uninstall:

```powershell
./uninstall-protocol-handlers.ps1
```

### Bookmarklets (recommended)

Create 2 bookmarks in Chrome and set their URL to the JavaScript below.

They:

- work on the current tab
- remove playlist/list noise (like `list=` / `index=`)
- remove all other query params (keeps only `v=`)

#### Download only

```text
javascript:(()=>{try{const u=new URL(location.href);const v=u.searchParams.get('v');if(!v){alert('Not a YouTube watch page');return;}location.href='spodl:'+encodeURIComponent(v);}catch(e){alert('Error: '+e);}})();
```

#### Download + translate

```text
javascript:(()=>{try{const u=new URL(location.href);const v=u.searchParams.get('v');if(!v){alert('Not a YouTube watch page');return;}location.href='spotr:'+encodeURIComponent(v);}catch(e){alert('Error: '+e);}})();
```

## Configure

Edit `config.yaml`.

### Secrets (recommended)

Create a `config.local.yaml` next to `config.yaml` and put secrets there. This file is ignored by Git via `.gitignore`.

Example:

```yaml
translation:
  api_keys:
    deepl: "YOUR_DEEPL_KEY"
    openai: "YOUR_OPENAI_KEY"
```

### DeepL (recommended default)

- Set:
  - `translation.service: "deepl"`
- DeepL plan:
  - `translation.deepl.plan: "free"` (default) or `"pro"`
- Provide a key via `config.local.yaml` (recommended), or via environment variable:

```bash
set DEEPL_API_KEY=your_key
```

(or put it in `config.yaml` under `translation.api_keys.deepl`)

### OpenAI

- Set:
  - `translation.service: "openai"`
  - `translation.openai.model: "gpt-4o-mini"` (or whichever you prefer)
- Provide a key via `config.local.yaml` (recommended), or via environment variable:

```bash
set OPENAI_API_KEY=your_key
```

### Custom prompt placeholders (OpenAI)

If you use `translation.service: "openai"`, you can customize prompts via `translation.custom_prompts`.

Both `system_prompt` and `user_prompt_template` support placeholders:

- `{text}` (the current segment text being translated; the app fills it automatically)
- `{source_language}` (language code, e.g. `ja`)
- `{target_language}` (language code, e.g. `fr`)
- `{source_language_name}` (human name, e.g. `Japanese`)
- `{target_language_name}` (human name, e.g. `French`)

You can use either the language *codes* (`{source_language}`, `{target_language}`) or the human-readable language *names* (`{source_language_name}`, `{target_language_name}`), depending on what you prefer in the prompt. You don’t need to include both.

## Run

### From YouTube

```bash
python main.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Download only (no transcription / no translation)

Use `--download-only` (alias: `--d`) to only download/prepare the input video and then exit.

```bash
python main.py "https://www.youtube.com/watch?v=VIDEO_ID" --download-only
```

The downloaded `.mp4` filename is based on the YouTube title (sanitized for Windows).

### Destination folder override

By default, destination folders come from `config.yaml`:

- `output.video_download_directory`

Subtitle output rules (when `--dest` is not set):

- Downloads (YouTube / m3u8): subtitles are written next to the downloaded video, i.e. `output.video_download_directory`
- Local files: subtitles are written next to the source video file

Use `--dest` to override those destinations for a single run:

```bash
python main.py "https://www.youtube.com/watch?v=VIDEO_ID" --dest "C:\\Temp"
```

### Local file

```bash
python main.py "C:\path\to\video.mp4"
```

### From an .m3u8 URL

```bash
python main.py "https://example.com/path/stream.m3u8"
```

## Chapter selection (local files)

If your local file contains embedded chapters, you can translate only selected chapters.

### Select chapters by number (1-based)

```bash
python main.py "C:\path\to\video.mkv" --source-type local --chapters "2,5-6"
```

### Auto-select chapters by chapter title

```bash
python main.py "C:\path\to\video.mkv" --source-type local --autoselectchapters
```

Auto-select uses `processing.chapter_autoselect_patterns` from `config.yaml` (regex, case-insensitive). Default patterns include chapters starting with `MC`, and chapters named `Intro`/`Outro`.

### Test patterns (no extraction / no translation)

To verify that your `chapter_autoselect_patterns` match the chapters you expect, you can list chapters and matching status without doing any audio extraction / transcription / translation:

```bash
python main.py "C:\path\to\video.mkv" --autoselectchapters --listchapters
```

You can also list chapters and show which ones would be selected by a manual chapter selection:

```bash
python main.py "C:\path\to\video.mkv" --chapters "2,5-6" --listchapters
```

### Combine manual + auto selection

You can combine `--chapters` and `--autoselectchapters`. The program will take the **union** (all chapters that match either selection).

## Resume / recovery mode

If translation fails mid-run (for example due to rate limiting), the program will **stop immediately** (fail-fast) and save a cache file next to the subtitles output directory.

Re-run with `--resume` to continue translating from the last completed segment without re-running Whisper/transcription:

```bash
python main.py "https://www.youtube.com/watch?v=VIDEO_ID" --resume
```

```bash
python main.py "C:\path\to\video.mkv" --source-type local --chapters "2,5-6" --autoselectchapters
```

If you request chapter selection but the file has no chapters (or nothing matches), the program will ask whether to translate the whole file (`y`) or stop.

## Output

- Downloaded videos: `output.video_download_directory`

Subtitles are generated:

- For downloads: in `output.video_download_directory`
- For local files: in the source video directory

Example output subtitle:

- `<output_folder>/<video_basename>.fr.srt`

## Temp files and cleanup

- Intermediate files are stored in `video.temp_directory`.
- If `processing.clean_temp_on_start: true`, the temp directory is cleaned at the start of each run.
- YouTube sidecar metadata files (like `.info.json`) are written to the temp folder and cleaned on the next run.

## Overwrite behavior

If an output file already exists, the program will ask once per run:

- `Overwrite? (y/n)`

If you answer `y`, it overwrites.
If you answer `n`, it creates a new filename using `_1`, `_2`, ... up to `_100`.

## Notes

- Translation quality: **DeepL** tends to be the best default
- OpenAI can be better for context-heavy lines, but is generally slower/costlier.

## DeepL rate limiting (HTTP 429)

If DeepL (or OpenAI) returns a rate limit / transient error (e.g. `HTTP 429 Too Many Requests`), the program will retry the request with **bounded exponential backoff** (it will not retry forever).

You can tune the retry behavior in `config.yaml` under `translation.retry`:

- `max_retries`
- `initial_delay_seconds`
- `max_delay_seconds`
- `backoff_multiplier`
- `jitter_ratio`

You can override these per service (optional) under `translation.deepl` or `translation.openai`.

## Language settings

- Transcription language uses `translation.source_language`
- Translation target uses `translation.target_language`

You can override them per run with CLI flags:

```bash
python main.py "C:\path\to\video.mp4" --source-lang en --target-lang fr
```
