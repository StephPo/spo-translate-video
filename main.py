#!/usr/bin/env python3

import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from audio_processor import AudioProcessor
from speech_recognizer import SpeechRecognizerFactory
from subtitle_writer import build_cues, write_srt
from translator import TranslatorFactory
from video_downloader import VideoDownloader


class _HelpOnErrorArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"error: {message}\n", file=sys.stderr)
        self.print_help(sys.stderr)
        raise SystemExit(2)


@dataclass
class Progress:
    stage: str
    percent: float
    message: str


def _setup_logging(config: Dict[str, Any]) -> logging.Logger:
    log_level = config.get("processing", {}).get("log_level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("video_subtitles")


def _cache_file_path(subtitles_dir: Path, base: str, target_lang: str) -> Path:
    safe_base = (base or "subtitles").strip() or "subtitles"
    safe_lang = (target_lang or "").strip().lower() or "target"
    return subtitles_dir / f"{safe_base}.{safe_lang}.cache.json"


def _format_invocation(argv: Optional[list[str]] = None) -> str:
    args = argv if argv is not None else sys.argv
    return " ".join(shlex.quote(str(a)) for a in args)


def _fmt_quality(obj: Any) -> str:
    if not isinstance(obj, dict):
        return "n/a"
    try:
        h = int(obj.get("height") or 0)
    except Exception:
        h = 0
    ext = str(obj.get("ext") or "").strip() or "?"
    vcodec = str(obj.get("vcodec") or "").strip() or "?"
    if h <= 0 and ext == "?" and vcodec == "?":
        return "n/a"
    if h > 0:
        return f"{h}p {ext} {vcodec}".strip()
    return f"{ext} {vcodec}".strip()


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


def _print_fatal_error_block(message: str) -> None:
    border = "!" * 72
    print("\n" + border, file=sys.stderr)
    print(_red("! TRANSLATION FAILED"), file=sys.stderr)
    print(border, file=sys.stderr)
    print(message.rstrip() + "\n", file=sys.stderr)
    print(border + "\n", file=sys.stderr)


def _supports_ansi() -> bool:
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    return _enable_windows_vt_processing()


_VT_ENABLED: Optional[bool] = None


def _enable_windows_vt_processing() -> bool:
    global _VT_ENABLED
    if _VT_ENABLED is not None:
        return _VT_ENABLED

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if handle == 0 or handle == -1:
            _VT_ENABLED = False
            return _VT_ENABLED

        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            _VT_ENABLED = False
            return _VT_ENABLED

        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if kernel32.SetConsoleMode(handle, new_mode) == 0:
            _VT_ENABLED = False
            return _VT_ENABLED

        _VT_ENABLED = True
        return _VT_ENABLED
    except Exception:
        _VT_ENABLED = False
        return _VT_ENABLED


def _green(text: str) -> str:
    if not _supports_ansi():
        return text
    return f"\x1b[92m{text}\x1b[0m"


def _yellow(text: str) -> str:
    if not _supports_ansi():
        return text
    return f"\x1b[93m{text}\x1b[0m"


def _red(text: str) -> str:
    if not _supports_ansi():
        return text
    return f"\x1b[91m{text}\x1b[0m"


def _load_config(config_path: str) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _load_config_with_local(config_path: str, local_config_path: Optional[str]) -> Dict[str, Any]:
    cfg = _load_config(config_path)
    if local_config_path:
        p = Path(local_config_path)
        if p.exists():
            local_cfg = _load_config(str(p))
            if isinstance(cfg, dict) and isinstance(local_cfg, dict):
                cfg = _deep_merge(cfg, local_cfg)
    return cfg


def _clean_temp_directory(config: Dict[str, Any], logger: logging.Logger):
    if not config.get("processing", {}).get("clean_temp_on_start", False):
        return

    temp_dir = Path(config.get("video", {}).get("temp_directory", "./temp"))
    if not temp_dir.exists():
        return

    try:
        deleted = 0
        for p in temp_dir.glob("*"):
            if p.is_file():
                p.unlink()
                deleted += 1
            elif p.is_dir():
                import shutil

                shutil.rmtree(p)
                deleted += 1
        logger.info(f"Cleaned temp directory on start: {temp_dir} ({deleted} entries)")
    except Exception as e:
        logger.warning(f"Failed to clean temp directory '{temp_dir}': {e}")


def _timed_input(prompt: str, config: Optional[Dict[str, Any]] = None) -> str:
    t0 = time.perf_counter()
    try:
        return input(prompt)
    finally:
        if isinstance(config, dict):
            rt = config.setdefault("_runtime", {})
            rt["user_wait_seconds"] = float(rt.get("user_wait_seconds") or 0.0) + (time.perf_counter() - t0)


def _get_overwrite_decision(config: Dict[str, Any]) -> bool:
    rt = config.setdefault("_runtime", {})
    if "overwrite_existing" in rt:
        return bool(rt["overwrite_existing"])

    ans = _timed_input("Output file already exists. Overwrite? (y/n): ", config).strip().lower()
    overwrite = ans == "y"
    rt["overwrite_existing"] = overwrite
    return overwrite


def _resolve_existing_destination(config: Dict[str, Any], dest: Path) -> Path:
    if not dest.exists():
        return dest

    if _get_overwrite_decision(config):
        return dest

    stem = dest.stem
    suffix = dest.suffix
    for i in range(1, 101):
        candidate = dest.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Unable to find available filename for {dest} (up to _100)")


def _parse_chapter_selection(selection: str) -> set:
    selected = set()
    for part in (selection or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a.strip())
            b = int(b.strip())
            if a <= 0 or b <= 0:
                raise ValueError("Chapter numbers must be >= 1")
            if b < a:
                a, b = b, a
            for n in range(a, b + 1):
                selected.add(n)
        else:
            n = int(part)
            if n <= 0:
                raise ValueError("Chapter numbers must be >= 1")
            selected.add(n)
    return selected


def _compute_autoselect_matches(config: Dict[str, Any], chapters: list) -> list:
    patterns = config.get("processing", {}).get("chapter_autoselect_patterns", [])
    if not isinstance(patterns, list):
        patterns = []
    regs = []
    for p in patterns:
        try:
            regs.append(re.compile(str(p), re.IGNORECASE))
        except re.error:
            continue

    out = []
    for c in chapters:
        title = c.get("title") or ""
        out.append(any(r.search(title) for r in regs))
    return out


def _fmt_ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _read_chapters_ffprobe(video_path: Path, config: Dict[str, Any]) -> list:
    ffprobe_path = str(config.get("video", {}).get("ffprobe_path") or "ffprobe")
    cmd = [
        ffprobe_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_chapters",
        "-i",
        str(video_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip())
    data = json.loads(proc.stdout or "{}")
    chapters = data.get("chapters") or []
    out = []
    for i, ch in enumerate(chapters, start=1):
        start = float(ch.get("start_time") or 0.0)
        end = float(ch.get("end_time") or 0.0)
        title = ((ch.get("tags") or {}).get("title") or "").strip()
        out.append({"index": i, "start": start, "end": end, "title": title})
    return out


def _select_chapters(
    config: Dict[str, Any],
    chapters: list,
    selection: Optional[str],
    autoselect: bool,
) -> Optional[list]:
    if not chapters:
        return None

    if not selection and not autoselect:
        return None

    selected_numbers = _parse_chapter_selection(selection) if selection else set()
    matches = _compute_autoselect_matches(config, chapters) if autoselect else [False] * len(chapters)

    selected = []
    for i, c in enumerate(chapters):
        if c["index"] in selected_numbers or matches[i]:
            selected.append(c)

    if not selected:
        ans = _timed_input("No chapters matched selection. Translate the whole file instead? (y/n): ", config).strip().lower()
        if ans == "y":
            return None
        raise RuntimeError("No chapters matched selection")

    return selected


def _print_progress(p: Progress):
    bar_len = 40
    filled = int(bar_len * max(0.0, min(100.0, p.percent)) / 100.0)
    bar = "#" * filled + "-" * (bar_len - filled)
    print(f"\r{p.stage:16s} |{bar}| {p.percent:6.1f}%  {p.message:60s}", end="", flush=True)
    if p.percent >= 100:
        print()


def generate_french_subtitles(
    config: Dict[str, Any],
    input_source: str,
    source_type: str,
    output_basename: Optional[str] = None,
    show_progress: bool = True,
    chapters: Optional[str] = None,
    autoselect_chapters: bool = False,
    list_chapters: bool = False,
    source_lang: Optional[str] = None,
    target_lang: Optional[str] = None,
    download_only: bool = False,
    info_only: bool = False,
    dry_run: bool = False,
    resume: bool = False,
) -> Dict[str, Any]:
    logger = logging.getLogger("video_subtitles")

    timings_seconds: Dict[str, float] = {}
    t0_total = time.perf_counter()

    def progress(stage: str, percent: float, message: str):
        if show_progress:
            _print_progress(Progress(stage=stage, percent=percent, message=message))

    downloader = VideoDownloader(config)

    invocation = str(config.get("_runtime", {}).get("invocation") or "").strip()
    if invocation:
        print(f"Command: {invocation}")

    _clean_temp_directory(config, logger)

    out_dir = Path(config.get("output", {}).get("output_directory", "./output"))
    out_dir.mkdir(parents=True, exist_ok=True)

    def compute_subtitles_dir(effective_source_type: str, resolved_video_path: Optional[Path]) -> Path:
        dest_override = bool(config.get("_runtime", {}).get("dest_override"))
        if effective_source_type == "local" and not dest_override:
            if resolved_video_path is not None:
                return resolved_video_path.parent
            return Path(input_source).resolve().parent
        return Path(config.get("output", {}).get("video_download_directory", str(out_dir)))

    progress("input", 0, "Loading video...")
    if dry_run:
        if source_type == "auto":
            if input_source.startswith(("http://", "https://")):
                source_type = "m3u8" if input_source.lower().split("?", 1)[0].endswith(".m3u8") else "youtube"
            else:
                source_type = "local"

        if source_lang:
            config.setdefault("translation", {})["source_language"] = source_lang
        if target_lang:
            config.setdefault("translation", {})["target_language"] = target_lang

        tgt = str(config.get("translation", {}).get("target_language", "fr")).strip().lower() or "fr"
        base = output_basename
        if not base:
            if source_type == "local":
                base = Path(input_source).stem
            elif source_type == "m3u8":
                base = "hls_video"
            else:
                base = "youtube_video"

        planned_subtitle = compute_subtitles_dir(source_type, None) / f"{base}.{tgt}.srt"

        print("\nDry run:")
        print(f"- Source type: {source_type}")
        print(f"- Input: {input_source}")
        if source_type == "local":
            print(f"- Local file exists: {Path(input_source).exists()}")
        elif source_type == "youtube":
            print(f"- Video will be downloaded to: {config.get('output', {}).get('video_download_directory', config.get('output', {}).get('output_directory', './output'))}")
        elif source_type == "m3u8":
            print(f"- Stream will be downloaded/remuxed to: {config.get('output', {}).get('video_download_directory', config.get('output', {}).get('output_directory', './output'))}")
        print(f"- Planned subtitles: {planned_subtitle}")
        if chapters or autoselect_chapters:
            print(f"- Chapter selection requested: chapters={chapters or ''} autoselect={autoselect_chapters}")
        print("- No download/transcription/translation performed")

        return {
            "success": True,
            "video_path": "",
            "subtitle_path": "",
            "title": "",
            "segments": 0,
        }

    if source_type == "auto":
        if input_source.startswith(("http://", "https://")):
            source_type = "m3u8" if input_source.lower().split("?", 1)[0].endswith(".m3u8") else "youtube"
        else:
            source_type = "local"

    if source_lang:
        config.setdefault("translation", {})["source_language"] = source_lang
    if target_lang:
        config.setdefault("translation", {})["target_language"] = target_lang

    quality_info: Dict[str, Any] = {}
    if source_type == "youtube":
        t0_meta = time.perf_counter()
        preflight = downloader.preflight_youtube_quality(input_source)
        timings_seconds["metadata"] = time.perf_counter() - t0_meta
        if isinstance(preflight, dict) and not preflight.get("error"):
            best_overall = preflight.get("best_overall") or {}
            best_mp4 = preflight.get("best_mp4") or {}
            quality_info = {
                "downgraded": bool(preflight.get("is_downgraded")),
                "best_available": {
                    "height": int(preflight.get("best_overall_height") or 0),
                    "ext": str(best_overall.get("ext") or ""),
                    "vcodec": str(best_overall.get("vcodec") or ""),
                },
                "best_mp4": {
                    "height": int(preflight.get("best_mp4_height") or 0),
                    "ext": str(best_mp4.get("ext") or ""),
                    "vcodec": str(best_mp4.get("vcodec") or ""),
                },
            }

            best_av = quality_info.get("best_available") or {}
            best_mp4_info = quality_info.get("best_mp4") or {}
            if best_av or best_mp4_info:
                print("\nVideo quality:")
                if best_mp4_info:
                    print(f"- Downloading: {_fmt_quality(best_mp4_info)}")
                if best_av:
                    print(f"- Best available: {_fmt_quality(best_av)}")
                print("\n")            

            if quality_info.get("downgraded"):
                msg = (
                    "Best available quality is higher than what this program will download (MP4-only).\n"
                    f"- Best available: {quality_info['best_available']['height']}p {quality_info['best_available']['ext']} {quality_info['best_available']['vcodec']}\n"
                    f"- Will download:  {quality_info['best_mp4']['height']}p {quality_info['best_mp4']['ext'] or 'mp4'} {quality_info['best_mp4']['vcodec']}\n"
                    "Continue anyway? (y/n): "
                )
                ans = _timed_input("\n" + _yellow("WARNING") + "\n" + msg, config).strip().lower()
                if ans != "y":
                    timings_seconds["user_wait"] = float(config.get("_runtime", {}).get("user_wait_seconds") or 0.0)
                    timings_seconds["total"] = time.perf_counter() - t0_total
                    return {
                        "success": False,
                        "error": "Aborted by user (not downloading lower-quality MP4)",
                        "timings_seconds": timings_seconds,
                        "quality_info": quality_info,
                    }

    t0_download = time.perf_counter()
    if source_type == "youtube":
        video_result = downloader.download_from_youtube(input_source)
        timings_seconds["download"] = time.perf_counter() - t0_download
    elif source_type == "m3u8":
        video_result = downloader.download_from_m3u8(input_source)
        timings_seconds["download"] = time.perf_counter() - t0_download
    elif source_type == "local":
        video_result = downloader.process_local_file(input_source)
    else:
        return {"success": False, "error": f"Unsupported source type: {source_type}"}

    if not video_result.success or not video_result.video_path:
        timings_seconds["user_wait"] = float(config.get("_runtime", {}).get("user_wait_seconds") or 0.0)
        return {
            "success": False,
            "error": video_result.error or "Failed to load video",
            "timings_seconds": timings_seconds,
            "quality_info": quality_info,
        }

    video_path = Path(video_result.video_path)
    title = (video_result.metadata or {}).get("title") or video_path.stem
    progress("input", 100, f"Video ready: {title}")

    if source_type == "youtube":
        md = video_result.metadata or {}
        downloaded_snapshot = {
            "height": int(md.get("downloaded_video_height") or 0),
            "ext": str(md.get("downloaded_video_ext") or ""),
            "vcodec": str(md.get("downloaded_video_vcodec") or ""),
        }
        quality_info.setdefault("downloaded", {})
        quality_info["downloaded"] = downloaded_snapshot

        downloaded_height = int(downloaded_snapshot.get("height") or 0)
        best_available = quality_info.get("best_available") or {}
        best_available_height = int(best_available.get("height") or 0)

        if best_available_height:
            is_same_height = downloaded_height == best_available_height
            same_codec = downloaded_snapshot.get("vcodec") == best_available.get("vcodec")
            same_ext = downloaded_snapshot.get("ext") == best_available.get("ext")
            quality_info["downgraded"] = downloaded_height < best_available_height or (
                is_same_height and not (same_codec and same_ext)
            )
        else:
            quality_info["downgraded"] = False

        if downloaded_snapshot.get("ext") == "mp4":
            best_mp4 = quality_info.get("best_mp4") or {}
            best_mp4_height = int(best_mp4.get("height") or 0)
            if downloaded_height >= best_mp4_height:
                quality_info["best_mp4"] = dict(downloaded_snapshot)

    subtitles_dir = compute_subtitles_dir(source_type, video_path)
    subtitles_dir.mkdir(parents=True, exist_ok=True)

    if download_only or info_only:
        if info_only:
            tgt = str(config.get("translation", {}).get("target_language", "fr")).strip().lower() or "fr"
            base = output_basename or video_path.stem
            planned_subtitle = subtitles_dir / f"{base}.{tgt}.srt"

            print("\nInfo:")
            print(f"- Source type: {source_type}")
            print(f"- Input: {input_source}")
            print(f"- Video path: {video_path}")
            print(f"- Title: {title}")
            print(f"- Planned subtitles: {planned_subtitle}")

            if source_type == "local":
                try:
                    chs = _read_chapters_ffprobe(video_path, config)
                    if chs:
                        print(f"- Chapters found: {len(chs)}")
                    else:
                        print("- Chapters found: 0")

                    if chs and (chapters or autoselect_chapters):
                        matches = _compute_autoselect_matches(config, chs) if autoselect_chapters else [False] * len(chs)
                        selected = _select_chapters(config, chs, chapters, autoselect_chapters)
                        selected_idx = {c["index"] for c in (selected or [])}
                        print("\nChapters:")
                        for i, c in enumerate(chs):
                            tags = []
                            if c["index"] in selected_idx:
                                tags.append("SELECTED")
                            if matches[i]:
                                tags.append("MATCH")
                            mark = f"[{'&'.join(tags)}]" if tags else ""
                            print(
                                f"{c['index']:>3d}  { _fmt_ts(c['start']) } -> { _fmt_ts(c['end']) }  {mark:10s}  {c.get('title','')}"
                            )
                except Exception as e:
                    return {"success": False, "error": f"Failed to read chapters: {e}"}

        timings_seconds["total"] = time.perf_counter() - t0_total
        timings_seconds["user_wait"] = float(config.get("_runtime", {}).get("user_wait_seconds") or 0.0)
        return {
            "success": True,
            "video_path": str(video_path),
            "subtitle_path": "",
            "title": title,
            "segments": 0,
            "timings_seconds": timings_seconds,
            "quality_info": quality_info,
        }

    transcribe_lang = config.get("translation", {}).get("source_language", "ja")

    if list_chapters:
        if source_type != "local":
            return {"success": False, "error": "--listchapters is only supported for local files", "timings_seconds": timings_seconds}
        try:
            chs = _read_chapters_ffprobe(video_path, config)
            if not chs:
                print("No chapters found")
                timings_seconds["total"] = time.perf_counter() - t0_total
                return {
                    "success": True,
                    "video_path": str(video_path),
                    "subtitle_path": "",
                    "title": title,
                    "segments": 0,
                    "timings_seconds": timings_seconds,
                }

            selected_numbers = _parse_chapter_selection(chapters) if chapters else None
            matches = _compute_autoselect_matches(config, chs) if autoselect_chapters else [False] * len(chs)

            print("\nChapters:")
            for i, c in enumerate(chs):
                tags = []
                if selected_numbers is not None and c["index"] in selected_numbers:
                    tags.append("SELECTED")
                if autoselect_chapters and matches[i]:
                    tags.append("MATCH")
                mark = f"[{'&'.join(tags)}]" if tags else ""
                print(
                    f"{c['index']:>3d}  { _fmt_ts(c['start']) } -> { _fmt_ts(c['end']) }  {mark:10s}  {c.get('title','')}"
                )

            timings_seconds["total"] = time.perf_counter() - t0_total
            return {
                "success": True,
                "video_path": str(video_path),
                "subtitle_path": "",
                "title": title,
                "segments": 0,
                "timings_seconds": timings_seconds,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list chapters: {e}", "timings_seconds": timings_seconds}

    base = output_basename or video_path.stem
    tgt = str(config.get("translation", {}).get("target_language", "fr")).strip().lower() or "fr"
    cache_path = _cache_file_path(subtitles_dir, base, tgt)

    cache = _load_cache(cache_path) if resume else None

    cached_video_path = None
    cached_segments = None
    cached_translated_texts = None
    if cache:
        cached_video_path = cache.get("video_path")
        cached_segments = cache.get("segments")
        cached_translated_texts = cache.get("translated_texts")

    use_cached_transcription = (
        isinstance(cached_video_path, str)
        and str(Path(cached_video_path)) == str(video_path)
        and isinstance(cached_segments, list)
        and len(cached_segments) > 0
    )

    all_segments = []
    if use_cached_transcription:
        try:
            for s in cached_segments:
                all_segments.append(
                    type("_Seg", (), {})()
                )
                all_segments[-1].start_time = float(s.get("start_time"))
                all_segments[-1].end_time = float(s.get("end_time"))
                all_segments[-1].text = str(s.get("text") or "")
        except Exception:
            all_segments = []
            use_cached_transcription = False

    audio_processor = AudioProcessor(config)
    recognizer = None
    translator = None

    selected_chapters = None
    if source_type == "local" and (chapters or autoselect_chapters):
        try:
            chs = _read_chapters_ffprobe(video_path, config)
            selected_chapters = _select_chapters(config, chs, chapters, autoselect_chapters)
            if selected_chapters is not None:
                logger.info(f"Selected {len(selected_chapters)} chapters for transcription")
        except Exception as e:
            return {"success": False, "error": f"Chapter selection failed: {e}", "timings_seconds": timings_seconds}

    max_seg = config.get("speech_recognition", {}).get("max_segment_length", 30)
    t0_transcribe = time.perf_counter()
    if not use_cached_transcription:
        recognizer = SpeechRecognizerFactory.create_recognizer(config)
        progress("transcribe", 0, "Transcribing Japanese...")

        if selected_chapters is None:
            progress("audio", 0, "Extracting audio...")
            audio_result = audio_processor.extract_audio_from_video(str(video_path))
            if not audio_result.success or not audio_result.audio_path:
                return {"success": False, "error": audio_result.error or "Failed to extract audio"}
            progress("audio", 100, "Audio extracted")

            seg_result = audio_processor.segment_audio(audio_result.audio_path, max_seg)
            if seg_result.success and seg_result.segments:
                for i, seg in enumerate(seg_result.segments):
                    progress("transcribe", (i / max(1, len(seg_result.segments))) * 100, f"Segment {i+1}/{len(seg_result.segments)}")
                    r = recognizer.transcribe(seg.audio_path, language=transcribe_lang)
                    if r.success and r.segments:
                        for s in r.segments:
                            s.start_time += seg.start_time
                            s.end_time += seg.start_time
                        all_segments.extend(r.segments)
            else:
                r = recognizer.transcribe(audio_result.audio_path, language=transcribe_lang)
                if not r.success or not r.segments:
                    return {"success": False, "error": r.error or "Transcription failed"}
                all_segments = r.segments
        else:
            for ci, ch in enumerate(selected_chapters):
                ch_index = ch["index"]
                ch_start = float(ch["start"])
                ch_end = float(ch["end"])
                ch_title = ch.get("title") or f"Chapter {ch_index}"
                progress("audio", (ci / max(1, len(selected_chapters))) * 100, f"Extracting chapter {ch_index}: {ch_title}")
                ar = audio_processor.extract_audio_segment_from_video(str(video_path), ch_start, ch_end, tag=f"ch{ch_index:03d}")
                if not ar.success or not ar.audio_path:
                    return {"success": False, "error": ar.error or f"Failed to extract chapter {ch_index} audio"}

                seg_result = audio_processor.segment_audio(ar.audio_path, max_seg)
                if seg_result.success and seg_result.segments:
                    for i, seg in enumerate(seg_result.segments):
                        progress(
                            "transcribe",
                            100.0 * (ci + (i / max(1, len(seg_result.segments)))) / max(1, len(selected_chapters)),
                            f"Chapter {ch_index} segment {i+1}/{len(seg_result.segments)}",
                        )
                        r = recognizer.transcribe(seg.audio_path, language=transcribe_lang)
                        if r.success and r.segments:
                            for s in r.segments:
                                s.start_time += ch_start + seg.start_time
                                s.end_time += ch_start + seg.start_time
                            all_segments.extend(r.segments)
                else:
                    r = recognizer.transcribe(ar.audio_path, language=transcribe_lang)
                    if r.success and r.segments:
                        for s in r.segments:
                            s.start_time += ch_start
                            s.end_time += ch_start
                        all_segments.extend(r.segments)

    if not use_cached_transcription:
        progress("transcribe", 100, f"Transcribed {len(all_segments)} segments")
        timings_seconds["transcription"] = time.perf_counter() - t0_transcribe
    else:
        timings_seconds["transcription"] = 0.0

    if not all_segments:
        return {
            "success": False,
            "error": "Transcription produced 0 segments. Check Whisper configuration and logs.",
            "timings_seconds": timings_seconds,
        }

    if not use_cached_transcription:
        _save_cache(
            cache_path,
            {
                "video_path": str(video_path),
                "segments": [
                    {"start_time": float(s.start_time), "end_time": float(s.end_time), "text": str(s.text)}
                    for s in all_segments
                ],
                "translated_texts": [],
            },
        )

    translator = TranslatorFactory.create_translator(config)

    progress("translate", 0, "Translating...")
    source_lang_eff = config.get("translation", {}).get("source_language", "ja")
    target_lang_eff = config.get("translation", {}).get("target_language", "fr")

    t0_translate = time.perf_counter()

    translated_texts = []
    if (
        resume
        and use_cached_transcription
        and isinstance(cached_translated_texts, list)
        and len(cached_translated_texts) <= len(all_segments)
    ):
        translated_texts = [str(x) for x in cached_translated_texts]

    for i, seg in enumerate(all_segments):
        if i < len(translated_texts):
            continue

        progress("translate", 100.0 * (i / max(1, len(all_segments))), f"Segment {i+1}/{len(all_segments)}")
        try:
            r = translator.translate(seg.text, source_lang_eff, target_lang_eff)
        except Exception as e:
            r = None
            err = str(e)
        else:
            err = r.error if r and not r.success else None

        if not r or not r.success or not r.segments:
            _save_cache(
                cache_path,
                {
                    "video_path": str(video_path),
                    "segments": [
                        {"start_time": float(s.start_time), "end_time": float(s.end_time), "text": str(s.text)}
                        for s in all_segments
                    ],
                    "translated_texts": translated_texts,
                    "failed_at_index": i,
                    "failed_text": seg.text,
                    "error": err or "Translation failed",
                },
            )
            _print_fatal_error_block(
                "\n".join(
                    [
                        f"Failed to translate segment {i+1}/{len(all_segments)} after retries.",
                        f"Service: {config.get('translation', {}).get('service', '')}",
                        f"Error: {err or 'Translation failed'}",
                        f"Cache saved: {cache_path}",
                        "To resume from this point, re-run with: --resume",
                    ]
                )
            )
            timings_seconds["translation"] = time.perf_counter() - t0_translate
            timings_seconds["user_wait"] = float(config.get("_runtime", {}).get("user_wait_seconds") or 0.0)
            timings_seconds["total"] = time.perf_counter() - t0_total
            return {"success": False, "error": err or "Translation failed", "timings_seconds": timings_seconds}

        translated_texts.append(r.segments[0].translated_text)
        _save_cache(
            cache_path,
            {
                "video_path": str(video_path),
                "segments": [
                    {"start_time": float(s.start_time), "end_time": float(s.end_time), "text": str(s.text)}
                    for s in all_segments
                ],
                "translated_texts": translated_texts,
            },
        )

    progress("translate", 100, f"Translated {len(translated_texts)} segments")
    timings_seconds["translation"] = time.perf_counter() - t0_translate

    starts = [s.start_time for s in all_segments]
    ends = [s.end_time for s in all_segments]
    fr_texts = translated_texts

    cues = build_cues(starts, ends, fr_texts)

    subtitle_path = _resolve_existing_destination(config, subtitles_dir / f"{base}.{tgt}.srt")
    if subtitle_path.exists() and _get_overwrite_decision(config):
        subtitle_path.unlink()

    progress("subtitles", 0, "Writing SRT...")
    write_srt(cues, subtitle_path)
    progress("subtitles", 100, f"Wrote {subtitle_path.name}")

    if not resume:
        try:
            if cache_path.exists():
                cache_path.unlink()
        except Exception:
            pass

    timings_seconds["total"] = time.perf_counter() - t0_total
    timings_seconds["user_wait"] = float(config.get("_runtime", {}).get("user_wait_seconds") or 0.0)

    return {
        "success": True,
        "video_path": str(video_path),
        "subtitle_path": str(subtitle_path),
        "title": title,
        "segments": len(cues),
        "timings_seconds": timings_seconds,
        "quality_info": quality_info,
    }


def main():
    parser = _HelpOnErrorArgumentParser(description="Generate French subtitles for Japanese videos (keeps original audio)")
    parser.add_argument("input", help="YouTube URL or local video file path")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--config-local", default="config.local.yaml", help="Optional local override config (e.g. API keys)")
    parser.add_argument("--source-type", choices=["youtube", "m3u8", "local", "auto"], default="auto")
    parser.add_argument("--output-basename", default=None, help="Base filename for subtitle output (no extension)")
    parser.add_argument(
        "--dest",
        default=None,
        help="Destination folder for downloads/subtitles for this run (overrides video_download_directory)",
    )
    parser.add_argument("--h", action="help", help="Show this help message and exit")
    parser.add_argument("--source-lang", default=None, help="Override source language (also used for transcription), e.g. ja, en")
    parser.add_argument("--target-lang", default=None, help="Override target language, e.g. fr")
    parser.add_argument("--info", action="store_true", help="Download/prepare the input and print info (no transcription/translation)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen and exit (no download/transcription/translation)")
    parser.add_argument("--download-only", "--d", action="store_true", help="Only download/prepare the input video then exit")
    parser.add_argument("--chapters", "--c", default=None, help="1-based chapter selection for local files, e.g. 1,3-5")
    parser.add_argument("--autoselectchapters", "--asc", action="store_true", help="Auto-select chapters by regex patterns in config")
    parser.add_argument("--listchapters", "--lc", action="store_true", help="List chapters and matching status (for testing patterns) without translating")
    parser.add_argument("--resume", action="store_true", help="Resume a previous run from the last translated segment (uses cache)")
    parser.add_argument("--no-progress", action="store_true", help="Disable progress display")

    args = parser.parse_args()

    if args.source_type in ["local", "auto"] and not args.input.startswith(("http://", "https://")):
        if not Path(args.input).exists():
            print(f"File not found: {args.input}")
            sys.exit(1)

    config = _load_config_with_local(args.config, args.config_local)
    _setup_logging(config)

    runtime_bucket = config.setdefault("_runtime", {})
    runtime_bucket["invocation"] = os.environ.get("SPO_LAUNCH_CMD") or _format_invocation()

    if (
        not args.dest
        and args.source_type in ["local", "auto"]
        and not args.input.startswith(("http://", "https://"))
    ):
        src_dir = str(Path(args.input).resolve().parent)
        config.setdefault("output", {})["output_directory"] = src_dir

    if args.dest:
        dest = str(Path(args.dest).expanduser())
        config.setdefault("_runtime", {})["dest_override"] = True
        config.setdefault("output", {})["output_directory"] = dest
        config.setdefault("output", {})["video_download_directory"] = dest

    res = generate_french_subtitles(
        config=config,
        input_source=args.input,
        source_type=args.source_type,
        output_basename=args.output_basename,
        show_progress=not args.no_progress,
        chapters=args.chapters,
        autoselect_chapters=args.autoselectchapters,
        list_chapters=args.listchapters,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        download_only=args.download_only,
        info_only=args.info,
        dry_run=args.dry_run,
        resume=args.resume,
    )

    print("\n" + "=" * 60)
    if res.get("success"):
        q = res.get("quality_info") or {}
        downgraded = bool(isinstance(q, dict) and q.get("downgraded"))
        if downgraded:
            print(_yellow("WARNING"))
            print("Downloaded video is not the best available quality (MP4-only selection).")
        else:
            print(_green("SUCCESS"))
        print(f"Video: {res['video_path']}")
        print(f"Subtitles: {res['subtitle_path']}")
        print(f"Segments: {res['segments']}")

        invocation = str(config.get("_runtime", {}).get("invocation") or "").strip()
        if isinstance(q, dict) and q:
            downloaded = q.get("downloaded") or {}
            best_av = q.get("best_available") or {}
            best_mp4 = q.get("best_mp4") or {}

            if invocation:
                print(f"\nCommand: {invocation}")
            print("\nVideo quality:")
            if downloaded:
                print(f"- Downloaded: {_fmt_quality(downloaded)}")
            if best_av:
                print(f"- Best available: {_fmt_quality(best_av)}")
            if best_mp4:
                print(f"- Best MP4: {_fmt_quality(best_mp4)}")

        timings = res.get("timings_seconds") or {}
        if isinstance(timings, dict) and timings:
            def fmt(sec: Optional[float]) -> str:
                if sec is None:
                    return "n/a"
                try:
                    return f"{float(sec):.2f}s"
                except Exception:
                    return "n/a"

            print("\nTimings:")
            if "metadata" in timings:
                print(f"- Metadata/preflight: {fmt(timings.get('metadata'))}")
            if "user_wait" in timings:
                print(f"- User confirmations: {fmt(timings.get('user_wait'))}")
            if "download" in timings:
                print(f"- Download: {fmt(timings.get('download'))}")
            else:
                print("- Download: n/a (local input)")

            tr = timings.get("transcription")
            if tr == 0.0 and args.resume:
                print("- Transcription: 0.00s (cached)")
            else:
                print(f"- Transcription: {fmt(tr)}")

            print(f"- Translation: {fmt(timings.get('translation'))}")
            if "total" in timings:
                print(f"- Total: {fmt(timings.get('total'))}")
    else:
        print(_red("FAILED"))
        print(res.get("error", "Unknown error"))
        sys.exit(1)


if __name__ == "__main__":
    main()
