# spo-translate-video

Generates translated **subtitles (`.srt`)** for a video (YouTube, `.m3u8` stream, or local file), while keeping the **original audio** untouched.

> **Full specification:** see [`SPECIFICATIONS.md`](./SPECIFICATIONS.md) — the source of truth for this project (features, config, install, maintenance, troubleshooting). This README is just a quick-start.

## Quick start

```powershell
# 1. Install prerequisites: Python 3.10+, ffmpeg, Node.js LTS (see SPECIFICATIONS.md section 7)

# 2. Configure secrets
copy config.local.yaml.example config.local.yaml
# edit config.local.yaml and fill in your DeepL/OpenAI API keys

# 3. (Optional) Customize translation prompts for this project
copy config.prompt.example.yaml config.prompt.yaml
# edit config.prompt.yaml, mainly system_prompt_extended

# 4. Run (creates .venv and installs dependencies on first run)
.\spo-translate-video.bat "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Common commands

```powershell
# Download only, no transcription/translation
.\spo-dl-video.bat "https://www.youtube.com/watch?v=VIDEO_ID"

# Local file
.\spo-translate-video.bat "C:\path\to\video.mkv"

# Override languages
.\spo-translate-video.bat "C:\path\to\video.mp4" --source-lang en --target-lang fr

# Resume a failed run (skips whichever phases already succeeded: download, Whisper transcription, translation)
.\spo-translate-video.bat "https://www.youtube.com/watch?v=VIDEO_ID" --resume
```

## One-click from a YouTube page (Windows)

```powershell
.\install-protocol-handlers.ps1
```

Then add the two bookmarklets to your browser: the JavaScript code is in `SPECIFICATIONS.md` section 2.2, and the step-by-step browser instructions (Chrome/Edge/Firefox) are in section 7.3. This registers the `spodl:`/`spotr:` custom URL protocols for your user account (no admin rights needed, no background process).

To uninstall: `.\uninstall-protocol-handlers.ps1`

## Configuration files

| File | Purpose | Tracked by Git |
|---|---|---|
| `config.yaml` | Technical settings (default) | Yes |
| `config.local.yaml` | API keys/secrets | No (gitignored) |
| `config.prompt.yaml` | Translation prompts (`system_prompt`, `system_prompt_extended`) | Yes (not a secret) |

See `SPECIFICATIONS.md` sections 5 and 6 for details.

## Everything else

Options, pipeline details, chapter selection, resume/cache behavior, supported tools, maintenance/update commands, and troubleshooting are all documented in [`SPECIFICATIONS.md`](./SPECIFICATIONS.md).
