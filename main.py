#!/usr/bin/env python3
"""CLI orchestrator for spo-translate-video. See SPECIFICATIONS.md."""

import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from audio_processor import AudioProcessor
from speech_recognizer import SpeechRecognizerFactory
from subtitle_writer import SubtitleCue, build_cues, write_srt
from translator import TranslatorFactory
from video_downloader import VideoDownloader, detect_source_type


class _HelpOnErrorArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"error: {message}\n", file=sys.stderr)
        self.print_help(sys.stderr)
        raise SystemExit(2)


# --------------------------------------------------------------------------
# Console helpers (color, VT100 support on Windows)
# --------------------------------------------------------------------------

_VT_ENABLED: Optional[bool] = None


def _supports_ansi() -> bool:
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    global _VT_ENABLED
    if _VT_ENABLED is not None:
        return _VT_ENABLED
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        if handle in (0, -1):
            _VT_ENABLED = False
            return _VT_ENABLED
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            _VT_ENABLED = False
            return _VT_ENABLED
        _VT_ENABLED = kernel32.SetConsoleMode(handle, mode.value | 0x0004) != 0
        return _VT_ENABLED
    except Exception:
        _VT_ENABLED = False
        return _VT_ENABLED


def _green(text: str) -> str:
    return f"\x1b[92m{text}\x1b[0m" if _supports_ansi() else text


def _yellow(text: str) -> str:
    return f"\x1b[93m{text}\x1b[0m" if _supports_ansi() else text


def _red(text: str) -> str:
    return f"\x1b[91m{text}\x1b[0m" if _supports_ansi() else text


def _print_fatal_error_block(message: str) -> None:
    border = "!" * 72
    print("\n" + border, file=sys.stderr)
    print(_red("! TRANSLATION FAILED"), file=sys.stderr)
    print(border, file=sys.stderr)
    print(message.rstrip() + "\n", file=sys.stderr)
    print(border + "\n", file=sys.stderr)


# --------------------------------------------------------------------------
# Config loading (config.yaml + config.local.yaml + config.prompt.yaml)
# --------------------------------------------------------------------------

def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(config_path: str, config_local_path: str, prompt_path: str = "config.prompt.yaml") -> Dict[str, Any]:
    cfg = _load_yaml(config_path)

    local_p = Path(config_local_path)
    if local_p.exists():
        cfg = _deep_merge(cfg, _load_yaml(str(local_p)))

    prompt_p = Path(prompt_path)
    custom_prompts = cfg.setdefault("translation", {}).setdefault("custom_prompts", {})
    if prompt_p.exists():
        prompt_cfg = _load_yaml(str(prompt_p))
        custom_prompts["system_prompt"] = prompt_cfg.get("system_prompt", "")
        custom_prompts["system_prompt_extended"] = prompt_cfg.get("system_prompt_extended", "")
    else:
        custom_prompts.setdefault("system_prompt", "")
        custom_prompts.setdefault("system_prompt_extended", "")

    return cfg


def _setup_logging(config: Dict[str, Any]) -> logging.Logger:
    log_level = config.get("processing", {}).get("log_level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("spo_translate_video")


def _describe_invocation_command() -> str:
    """Best-effort reconstruction of the command line to reproduce this exact run.

    `spo-translate-video.bat` forwards its arguments unchanged to `python main.py` (and
    `spo-dl-video.bat` is equivalent, just with `--download-only` appended), so reconstructing
    the `.bat` invocation from `sys.argv` is accurate regardless of which entry point (bookmarklet,
    protocol handler, or direct `.bat` call) was actually used to start this run.
    """
    bat_path = Path(__file__).resolve().parent / "spo-translate-video.bat"

    def _quote(arg: str) -> str:
        if arg == "" or any(c in arg for c in (" ", "\t", '"')):
            return '"' + arg.replace('"', '\\"') + '"'
        return arg

    args_str = " ".join(_quote(a) for a in sys.argv[1:])
    return f"{bat_path} {args_str}".rstrip()


# --------------------------------------------------------------------------
# Temp directory management (per-run, with pruning of old runs)
# --------------------------------------------------------------------------

def _create_run_temp_dir(config: Dict[str, Any], logger: logging.Logger) -> Path:
    base_temp_dir = Path(config.get("video", {}).get("temp_directory", "./temp"))
    base_temp_dir.mkdir(parents=True, exist_ok=True)

    keep_days = int(config.get("processing", {}).get("temp_keep_days", 2) or 2)
    if keep_days > 0:
        cutoff = time.time() - keep_days * 86400
        for p in base_temp_dir.glob("run_*"):
            try:
                if p.is_dir() and p.stat().st_mtime < cutoff:
                    import shutil

                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                continue

    run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    run_temp_dir = base_temp_dir / run_id
    run_temp_dir.mkdir(parents=True, exist_ok=True)
    config.setdefault("_runtime", {})["run_temp_dir"] = str(run_temp_dir)
    config.setdefault("video", {})["temp_directory"] = str(run_temp_dir)
    logger.info(f"Using per-run temp directory: {run_temp_dir}")
    return run_temp_dir


def _cleanup_run_temp_dir(run_temp_dir: Path, logger: logging.Logger) -> None:
    try:
        import shutil

        shutil.rmtree(run_temp_dir, ignore_errors=True)
        logger.info(f"Cleaned per-run temp directory: {run_temp_dir}")
    except Exception as e:
        logger.warning(f"Failed to clean per-run temp directory '{run_temp_dir}': {e}")


# --------------------------------------------------------------------------
# Cache (resume support)
# --------------------------------------------------------------------------

def _download_cache_path(base_temp_dir: Path, source: str) -> Path:
    import hashlib

    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
    return base_temp_dir / f"download_cache_{digest}.json"


def _cache_file_path(subtitles_dir: Path, base: str, target_lang: str) -> Path:
    safe_base = (base or "subtitles").strip() or "subtitles"
    safe_lang = (target_lang or "").strip().lower() or "target"
    return subtitles_dir / f"{safe_base}.{safe_lang}.cache.json"


def _load_cache(cache_path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not cache_path.exists():
            return None
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(cache_path: Path, data: Dict[str, Any]) -> None:
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_path)


# --------------------------------------------------------------------------
# Overwrite handling
# --------------------------------------------------------------------------

def _resolve_output_path(desired_path: Path, overwrite_decision: Dict[str, bool]) -> Path:
    if not desired_path.exists():
        return desired_path

    if "overwrite" not in overwrite_decision:
        ans = input(f"Output file already exists ({desired_path}). Overwrite? (y/n): ").strip().lower()
        overwrite_decision["overwrite"] = ans == "y"

    if overwrite_decision["overwrite"]:
        return desired_path

    for i in range(1, 101):
        candidate = desired_path.with_name(f"{desired_path.stem}_{i}{desired_path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a free filename for {desired_path} (tried up to _100)")


# --------------------------------------------------------------------------
# Chapters (local files only)
# --------------------------------------------------------------------------

def _ffprobe_chapters(video_path: str, ffmpeg_path: str) -> List[Dict[str, Any]]:
    ffprobe_path = ffmpeg_path.replace("ffmpeg", "ffprobe") if "ffmpeg" in ffmpeg_path else "ffprobe"
    cmd = [ffprobe_path, "-v", "quiet", "-print_format", "json", "-show_chapters", video_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
        return data.get("chapters", [])
    except Exception:
        return []


def _parse_chapter_selection(spec: str, total: int) -> List[int]:
    """Parse a 1-based selection like '2,5-6' into a sorted list of 0-based indices."""
    selected = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            for i in range(int(a), int(b) + 1):
                if 1 <= i <= total:
                    selected.add(i - 1)
        else:
            i = int(part)
            if 1 <= i <= total:
                selected.add(i - 1)
    return sorted(selected)


def _autoselect_chapters(chapters: List[Dict[str, Any]], patterns: List[str]) -> List[int]:
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    selected = []
    for idx, ch in enumerate(chapters):
        title = str((ch.get("tags") or {}).get("title") or "")
        if any(p.search(title) for p in compiled):
            selected.append(idx)
    return selected


def _resolve_chapter_selection(chapters: List[Dict[str, Any]], manual_spec: Optional[str], autoselect: bool,
                                patterns: List[str]) -> List[int]:
    selected = set()
    if manual_spec:
        selected.update(_parse_chapter_selection(manual_spec, len(chapters)))
    if autoselect:
        selected.update(_autoselect_chapters(chapters, patterns))
    return sorted(selected)


def _listchapters_selection_preview(chapters: List[Dict[str, Any]], manual_spec: Optional[str],
                                     autoselectchapters: bool, patterns: List[str]) -> List[int]:
    """Selection preview used by --listchapters: always includes the auto-selected
    chapters (matching processing.chapter_autoselect_patterns), regardless of whether
    --autoselectchapters was passed, so users can quickly browse a video's chapters and
    validate their regex patterns in a single command. Any manual --chapters selection
    is still combined in, as usual."""
    return _resolve_chapter_selection(chapters, manual_spec, True, patterns)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def _prepare_input(
    source: str, config: Dict[str, Any], logger: logging.Logger,
    *, resume: bool = False, download_cache_path: Optional[Path] = None,
) -> Tuple[str, str, Optional[str], Optional[str]]:
    """Return (video_path, title_for_output, quality_info, quality_warning)."""
    source_type = detect_source_type(source)
    logger.info(f"Detected source type: {source_type}")

    if source_type == "local":
        p = Path(source)
        return str(p), p.stem, None, None

    if resume and download_cache_path is not None:
        cached = _load_cache(download_cache_path)
        cached_path = cached.get("video_path") if cached else None
        if cached_path and Path(cached_path).exists():
            logger.info(f"Resuming: reusing previously downloaded video (skipping download): {cached_path}")
            return cached_path, cached.get("title") or Path(cached_path).stem, None, None

    downloader = VideoDownloader(config)
    if source_type == "youtube":
        result = downloader.download_from_youtube(source)
    else:
        result = downloader.download_from_m3u8(source)

    if not result.success:
        raise RuntimeError(f"Download failed: {result.error}")

    title = result.title or Path(result.video_path).stem
    if download_cache_path is not None:
        _save_cache(download_cache_path, {"video_path": result.video_path, "title": title})
    return result.video_path, title, result.quality_info, result.quality_warning


def _translate_range(
    *, config: Dict[str, Any], logger: logging.Logger, video_path: str, output_basename: str,
    subtitles_dir: Path, source_lang: str, target_lang: str, resume: bool,
    time_range: Optional[Tuple[float, float]] = None,
) -> List[SubtitleCue]:
    """Transcribe and translate a (portion of a) video, returning subtitle cues
    without writing them to disk. `output_basename` is only used for the cache
    file name."""
    audio_processor = AudioProcessor(config)
    recognizer = SpeechRecognizerFactory.create_recognizer(config)
    translator = TranslatorFactory.create_translator(config)

    cache_path = _cache_file_path(subtitles_dir, output_basename, target_lang)
    cache = _load_cache(cache_path) if resume else None

    if cache is not None and cache.get("originals") is not None:
        logger.info(
            f"Resuming from cache: {cache_path} "
            f"({len(cache.get('segments') or [])} segments already translated, "
            "skipping audio extraction + transcription)"
        )
        starts = cache["starts"]
        ends = cache["ends"]
        originals = cache["originals"]
        translated = cache.get("segments") or []
        start_index = len(translated)
    else:
        start_time, end_time = time_range if time_range else (None, None)
        audio_result = audio_processor.extract_audio(video_path, start_time, end_time)
        if not audio_result.success:
            raise RuntimeError(f"Audio extraction failed: {audio_result.error}")

        transcription = recognizer.transcribe(audio_result.audio_path, language=source_lang)
        if not transcription.success:
            raise RuntimeError(f"Transcription failed: {transcription.error}")

        offset = start_time or 0.0
        starts = [seg.start_time + offset for seg in transcription.segments]
        ends = [seg.end_time + offset for seg in transcription.segments]
        originals = [seg.text for seg in transcription.segments]
        translated = []
        start_index = 0

    remaining = originals[start_index:]
    if remaining:
        try:
            result = translator.translate_segments(remaining, source_lang, target_lang)
        except Exception as e:
            _save_cache(cache_path, {"starts": starts, "ends": ends, "originals": originals, "segments": translated})
            _print_fatal_error_block(
                f"{e}\n\nProgress saved to: {cache_path}\nRe-run with --resume to continue without re-running transcription."
            )
            raise SystemExit(1)

        if not result.success:
            translated.extend(seg.translated_text for seg in (result.segments or []))
            _save_cache(cache_path, {"starts": starts, "ends": ends, "originals": originals, "segments": translated})
            _print_fatal_error_block(
                f"{result.error}\n\nProgress saved to: {cache_path}\nRe-run with --resume to continue without re-running transcription."
            )
            raise SystemExit(1)

        translated.extend(seg.translated_text for seg in result.segments)

    cues = build_cues(starts, ends, translated)
    cache_path.unlink(missing_ok=True)
    return cues


def _translate_and_write(
    *, config: Dict[str, Any], logger: logging.Logger, video_path: str, output_basename: str,
    subtitles_dir: Path, source_lang: str, target_lang: str, resume: bool,
    time_range: Optional[Tuple[float, float]] = None,
) -> Path:
    cues = _translate_range(
        config=config, logger=logger, video_path=video_path, output_basename=output_basename,
        subtitles_dir=subtitles_dir, source_lang=source_lang, target_lang=target_lang,
        resume=resume, time_range=time_range,
    )
    desired_path = subtitles_dir / f"{output_basename}.{target_lang}.srt"
    output_path = _resolve_output_path(desired_path, config.setdefault("_runtime", {}))
    write_srt(cues, output_path)
    logger.info(_green(f"Subtitles written: {output_path}"))
    return output_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = _HelpOnErrorArgumentParser(
        description="Generate translated subtitles for a video (YouTube, .m3u8, or local file).",
        add_help=False,
    )
    parser.add_argument("input", help="YouTube URL, .m3u8 URL, or local video file path")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--config-local", default="config.local.yaml", help="Path to local secrets override")
    parser.add_argument("--h", action="help", help="Show this help message and exit")
    parser.add_argument("--dest", default=None, help="Override destination folder for this run")
    parser.add_argument("--source-lang", default=None, help="Override source language (transcription), e.g. ja, en")
    parser.add_argument("--target-lang", default=None, help="Override target language, e.g. fr")
    parser.add_argument("--info", action="store_true", help="Download/prepare the input and print info, then exit")
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen and exit")
    parser.add_argument("--download-only", "--d", action="store_true", help="Only download/prepare the input, then exit")
    parser.add_argument("--chapters", "--c", default=None, help="1-based chapter selection for local files, e.g. 1,3-5")
    parser.add_argument("--autoselectchapters", "--asc", action="store_true", help="Auto-select chapters by regex patterns in config")
    parser.add_argument("--listchapters", "--lc", action="store_true", help="List chapters and matching status, without translating")
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume a previous failed run: skip re-downloading if already downloaded, skip "
        "re-transcribing if a transcription was already cached, and continue translation from "
        "the last translated segment",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    config = load_config(args.config, args.config_local)
    logger = _setup_logging(config)
    logger.info(f"Command: {_describe_invocation_command()}")

    source_lang = args.source_lang or config.get("translation", {}).get("source_language", "ja")
    target_lang = args.target_lang or config.get("translation", {}).get("target_language", "fr")

    if args.dry_run:
        print(f"Would process: {args.input}")
        print(f"  source_type (auto-detected): {detect_source_type(args.input)}")
        print(f"  source_lang={source_lang} target_lang={target_lang}")
        print(f"  download_only={args.download_only}")
        return 0

    base_temp_dir = Path(config.get("video", {}).get("temp_directory", "./temp"))
    run_temp_dir = _create_run_temp_dir(config, logger)
    try:
        source_type = detect_source_type(args.input)
        download_cache_path = (
            _download_cache_path(base_temp_dir, args.input) if source_type != "local" else None
        )

        # --listchapters: inspect without extracting/transcribing/translating.
        if args.listchapters:
            if source_type != "local":
                print("--listchapters only applies to local files.")
                return 1
            ffmpeg_path = str(config.get("video", {}).get("ffmpeg_path") or "ffmpeg")
            chapters = _ffprobe_chapters(args.input, ffmpeg_path)
            if not chapters:
                print("No chapters found in this file.")
                return 0
            patterns = config.get("processing", {}).get("chapter_autoselect_patterns", [])
            selected = set(_listchapters_selection_preview(chapters, args.chapters, args.autoselectchapters, patterns))
            for idx, ch in enumerate(chapters):
                title = str((ch.get("tags") or {}).get("title") or "")
                mark = "[x]" if idx in selected else "[ ]"
                print(f"{mark} {idx + 1}. {title} ({ch.get('start_time')} - {ch.get('end_time')})")
            return 0

        video_path, title, quality_info, quality_warning = _prepare_input(
            args.input, config, logger, resume=args.resume, download_cache_path=download_cache_path,
        )
        if quality_info:
            print(_green(quality_info))
        if quality_warning:
            print(_yellow(f"\n{quality_warning}\n"))

        if args.info:
            print(f"Title: {title}")
            print(f"Path: {video_path}")
            return 0

        if args.download_only:
            logger.info(_green(f"Download complete: {video_path}"))
            if download_cache_path is not None:
                download_cache_path.unlink(missing_ok=True)
            return 0

        output_cfg = config.get("output", {})
        if args.dest:
            subtitles_dir = Path(args.dest)
        elif source_type == "local":
            subtitles_dir = Path(video_path).parent
        else:
            subtitles_dir = Path(output_cfg.get("video_download_directory") or output_cfg.get("output_directory") or "./output")
        subtitles_dir.mkdir(parents=True, exist_ok=True)

        output_basename = Path(video_path).stem
        config.setdefault("_runtime", {}).update({"video_title": title, "video_filename": Path(video_path).name})

        # Chapter selection (local files only).
        if (args.chapters or args.autoselectchapters) and source_type == "local":
            ffmpeg_path = str(config.get("video", {}).get("ffmpeg_path") or "ffmpeg")
            chapters = _ffprobe_chapters(video_path, ffmpeg_path)
            patterns = config.get("processing", {}).get("chapter_autoselect_patterns", [])
            selected_idx = _resolve_chapter_selection(chapters, args.chapters, args.autoselectchapters, patterns)

            if not selected_idx:
                ans = input("No chapters matched the requested selection. Translate the whole file instead? (y/n): ").strip().lower()
                if ans != "y":
                    return 1
                selected_idx = None

            if selected_idx:
                all_cues: List[SubtitleCue] = []
                for i in selected_idx:
                    ch = chapters[i]
                    time_range = (float(ch["start_time"]), float(ch["end_time"]))
                    basename = f"{output_basename}_ch{i + 1}"
                    chapter_cues = _translate_range(
                        config=config, logger=logger, video_path=video_path, output_basename=basename,
                        subtitles_dir=subtitles_dir, source_lang=source_lang, target_lang=target_lang,
                        resume=args.resume, time_range=time_range,
                    )
                    all_cues.extend(chapter_cues)

                for idx, cue in enumerate(all_cues, start=1):
                    cue.index = idx

                desired_path = subtitles_dir / f"{output_basename}.{target_lang}.srt"
                output_path = _resolve_output_path(desired_path, config.setdefault("_runtime", {}))
                write_srt(all_cues, output_path)
                logger.info(_green(f"Subtitles written: {output_path}"))
                if download_cache_path is not None:
                    download_cache_path.unlink(missing_ok=True)
                return 0

        _translate_and_write(
            config=config, logger=logger, video_path=video_path, output_basename=output_basename,
            subtitles_dir=subtitles_dir, source_lang=source_lang, target_lang=target_lang, resume=args.resume,
        )
        if download_cache_path is not None:
            download_cache_path.unlink(missing_ok=True)
        return 0
    finally:
        _cleanup_run_temp_dir(run_temp_dir, logger)


if __name__ == "__main__":
    sys.exit(main())
