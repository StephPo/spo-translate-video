import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import yt_dlp


@dataclass
class DownloadResult:
    success: bool
    video_path: Optional[str] = None
    title: Optional[str] = None
    quality_info: Optional[str] = None
    quality_warning: Optional[str] = None
    error: Optional[str] = None


_VT_ENABLED: Optional[bool] = None


def _supports_ansi() -> bool:
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    return _enable_windows_vt_processing()


def _enable_windows_vt_processing() -> bool:
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
        new_mode = mode.value | 0x0004
        _VT_ENABLED = kernel32.SetConsoleMode(handle, new_mode) != 0
        return _VT_ENABLED
    except Exception:
        _VT_ENABLED = False
        return _VT_ENABLED


def _yellow(text: str) -> str:
    return f"\x1b[93m{text}\x1b[0m" if _supports_ansi() else text


def _framed(lines: list) -> str:
    """Wrap `lines` in an ASCII box so the message is impossible to miss in the terminal."""
    width = max((len(line) for line in lines), default=0) + 4
    border = "!" * max(width, 4)
    body = "\n".join(f"! {line.ljust(width - 4)} !" for line in lines)
    return f"{border}\n{body}\n{border}"


YOUTUBE_URL_RE = re.compile(r"(youtube\.com/(watch\?|shorts/)|youtu\.be/)", re.IGNORECASE)
TWITTER_URL_RE = re.compile(r"(?:twitter\.com|x\.com)/[^/]+/status/\d+", re.IGNORECASE)


def detect_source_type(source: str) -> str:
    """Auto-detect the source type: 'youtube', 'twitter', 'm3u8', or 'local'."""
    if YOUTUBE_URL_RE.search(source):
        return "youtube"
    if TWITTER_URL_RE.search(source):
        return "twitter"
    if source.lower().split("?")[0].endswith(".m3u8"):
        return "m3u8"
    if Path(source).exists():
        return "local"
    # Fall back: any other http(s) URL is treated as m3u8-style direct stream.
    if source.lower().startswith("http"):
        return "m3u8"
    raise ValueError(f"Could not determine source type for: {source} (not an existing local file, YouTube URL, or .m3u8 URL)")


class _YtDlpLogger:
    KEYWORD = "no supported javascript runtime"
    CHALLENGE_KEYWORDS = ("js challenge provider", "signature solving failed", "n challenge solving failed")
    SABR_KEYWORDS = ("sabr-only streaming", "forcing sabr streaming", "missing a url")

    def __init__(self, parent: logging.Logger):
        self._parent = parent
        self.js_runtime_warning = False
        self.js_challenge_warning = False
        self.sabr_warning = False

    def debug(self, msg):
        self._parent.debug(msg)

    def info(self, msg):
        self._parent.info(msg)

    def warning(self, msg):
        if isinstance(msg, str):
            lower = msg.lower()
            if self.KEYWORD in lower:
                self.js_runtime_warning = True
            if any(k in lower for k in self.CHALLENGE_KEYWORDS):
                self.js_challenge_warning = True
            if any(k in lower for k in self.SABR_KEYWORDS):
                self.sabr_warning = True
        self._parent.warning(msg)

    def error(self, msg):
        self._parent.error(msg)

    def report_warning(self, msg):
        self.warning(msg)


class VideoDownloader:
    # Player clients tried automatically as a fallback when YouTube's SABR-only streaming
    # rollout is detected on the configured clients (see SPECIFICATIONS.md section 4.2). The
    # rollout is applied per-session, so the same clients can succeed on one run and fail on the
    # next; retrying once with additional clients within the same run meaningfully improves odds.
    FALLBACK_PLAYER_CLIENTS = ["default", "tv", "ios", "web_safari", "mweb"]

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        video_cfg = config.get("video", {}) or {}
        output_cfg = config.get("output", {}) or {}

        self.temp_dir = Path(video_cfg.get("temp_directory", "./temp"))
        self.download_dir = Path(output_cfg.get("video_download_directory") or output_cfg.get("output_directory") or "./output")
        self.ffmpeg_path = str(video_cfg.get("ffmpeg_path") or "ffmpeg")

        # yt-dlp's cross-run cache (under the user's default cache directory) can end up holding a
        # remote challenge-solver script (`youtube_remote_components`, e.g. `ejs:github`) whose
        # version no longer matches what the installed yt-dlp expects, causing intermittent hard
        # failures ("Challenge solver lib script version X is not supported" -> "ERROR: The
        # downloaded file is empty") that a same-run retry cannot fully recover from (observed in
        # practice). Scoping the cache to this run's temp directory means every run starts from a
        # known-good (empty) cache, eliminating this class of failure, while still avoiding
        # redundant re-fetches across the several yt-dlp calls made within a single run (preflight,
        # download, retry) — see SPECIFICATIONS.md section 4.2.
        self.cache_dir = self.temp_dir / ".yt-dlp-cache"

        runtime_cfg = video_cfg.get("youtube_js_runtime") or {}
        self._js_runtime_name = str(runtime_cfg.get("runtime") or "").strip().lower()
        self._js_runtime_path = str(runtime_cfg.get("path") or "").strip()
        self.js_runtimes = self._build_js_runtime(self._js_runtime_name, self._js_runtime_path)

        remote_cfg = video_cfg.get("youtube_remote_components") or {}
        self.remote_components = list(remote_cfg.get("components") or []) if remote_cfg.get("enable") else []

        player_clients_cfg = video_cfg.get("youtube_player_clients")
        self.player_clients = list(player_clients_cfg) if player_clients_cfg else []

        # YouTube's SABR-only streaming rollout is applied per-request/session (confirmed by
        # repeated live testing: the exact same URL, config, and player clients succeeded on some
        # attempts and got capped at a lower resolution on others, with no discernible pattern) —
        # it is fundamentally probabilistic on YouTube's side, not something client-side
        # configuration can deterministically avoid. Each retry is effectively a new roll of the
        # dice, so allowing more attempts meaningfully raises the odds of getting the true best
        # quality within a single run. See SPECIFICATIONS.md section 4.2.
        self.quality_max_attempts = max(1, int(video_cfg.get("youtube_quality_max_attempts", 3) or 1))

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _build_js_runtime(name: str, path: str):
        if not name:
            return None
        entry: Dict[str, Any] = {}
        if path:
            entry["path"] = path
        return {name: entry}

    def _base_ydl_opts(self, logger: _YtDlpLogger, playlist_index: Optional[int] = None) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "logger": logger,
            "cachedir": str(self.cache_dir),
        }
        if playlist_index is not None:
            # Some sources expose several videos under the exact same webpage_url (e.g. a single
            # tweet with multiple native video attachments, see SPECIFICATIONS.md section 3.1.1):
            # the only way to pick a specific one back up is by 1-based entry position within the
            # pseudo-playlist yt-dlp builds for that URL, not by URL alone.
            opts["noplaylist"] = False
            opts["playlist_items"] = str(playlist_index)
        if self.js_runtimes:
            opts["js_runtimes"] = self.js_runtimes
        if self.remote_components:
            opts["remote_components"] = self.remote_components
        if self.player_clients:
            opts["extractor_args"] = {"youtube": {"player_client": self.player_clients}}
        return opts

    def _maybe_warn_missing_js_runtime(self):
        self.logger.warning(
            "yt-dlp reports no supported JavaScript runtime. Install Node.js LTS and set "
            "video.youtube_js_runtime.runtime: 'node' in config.yaml (see SPECIFICATIONS.md section 4.1)."
        )

    def _maybe_warn_js_challenge(self):
        self.logger.warning(
            "yt-dlp failed to solve a YouTube signature/n-challenge. Ensure "
            "video.youtube_remote_components.enable is true with 'ejs:github', and that Node.js is up to date."
        )

    def _merged_fallback_clients(self) -> list:
        merged = list(self.player_clients)
        for client in self.FALLBACK_PLAYER_CLIENTS:
            if client not in merged:
                merged.append(client)
        return merged

    @staticmethod
    def _describe_cli(url: str, opts: Dict[str, Any], download: bool) -> str:
        """Build a yt-dlp CLI command line equivalent to `opts`, so a run can be reproduced manually."""
        parts = ["yt-dlp"]
        fmt = opts.get("format")
        if fmt:
            parts += ["-f", f'"{fmt}"']
        player_client = ((opts.get("extractor_args") or {}).get("youtube") or {}).get("player_client")
        if player_client:
            parts += ["--extractor-args", f'"youtube:player_client={",".join(player_client)}"']
        js_runtimes = opts.get("js_runtimes")
        if js_runtimes:
            name, cfg = next(iter(js_runtimes.items()))
            path = (cfg or {}).get("path")
            parts += ["--js-runtimes", f'"{name}{":" + path if path else ""}"']
        if opts.get("remote_components"):
            parts += ["--remote-components", ",".join(opts["remote_components"])]
        if opts.get("cachedir"):
            parts += ["--cache-dir", f'"{opts["cachedir"]}"']
        if opts.get("restrictfilenames"):
            parts.append("--restrict-filenames")
        if opts.get("merge_output_format"):
            parts += ["--merge-output-format", opts["merge_output_format"]]
        if opts.get("outtmpl"):
            parts += ["-o", f'"{opts["outtmpl"]}"']
        if not download:
            parts.append("--skip-download")
        parts.append(f'"{url}"')
        return " ".join(parts)

    def _maybe_warn_sabr(self):
        self.logger.warning(
            "YouTube is forcing SABR-only streaming for one or more player clients, causing "
            "higher-resolution direct (https) formats to be skipped (see "
            "https://github.com/yt-dlp/yt-dlp/issues/12482). This is a YouTube-side rollout, not "
            "fixable purely client-side. Try setting video.youtube_player_clients in config.yaml "
            "to a client currently unaffected (e.g. ['default', 'tv']), and/or update yt-dlp "
            "('pip install -U yt-dlp') as fixes for specific clients are shipped frequently."
        )

    def list_twitter_videos(self, url: str) -> list:
        """List the video(s) yt-dlp finds for a tweet URL, without downloading.

        A tweet can expose more than one downloadable video (e.g. multiple native video
        attachments on the same tweet, and/or a quoted/cited tweet's video); in that case yt-dlp
        represents the URL as a playlist with one entry per video. Note that all entries can share
        the exact same `webpage_url` (the tweet's own URL) when they come from multiple native
        attachments on one tweet, so the URL alone cannot be used later to re-select a specific
        entry — each returned dict also carries its 1-based `playlist_index` within that
        pseudo-playlist for that purpose (see `download_from_youtube`'s `playlist_index` param).
        Returns a list of dicts with 'url', 'title', 'uploader', 'playlist_index' (one entry, with
        `playlist_index=None`, if only a single video is found).
        """
        logger = _YtDlpLogger(self.logger)
        opts = self._base_ydl_opts(logger)
        opts.update({"noplaylist": False, "skip_download": True})
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            self.logger.warning(f"Could not list videos for tweet {url}: {e}")
            return [{"url": url, "title": None, "uploader": None, "playlist_index": None}]

        entries = (info or {}).get("entries")
        if entries:
            videos = [
                {
                    "url": e.get("webpage_url") or e.get("url"),
                    "title": e.get("title") or e.get("id"),
                    "uploader": e.get("uploader"),
                    "playlist_index": idx,
                }
                for idx, e in enumerate(entries, 1) if e
            ]
            if videos:
                return videos
        return [{
            "url": (info or {}).get("webpage_url") or url,
            "title": (info or {}).get("title"),
            "uploader": (info or {}).get("uploader"),
            "playlist_index": None,
        }]

    @staticmethod
    def _unwrap_single_entry(info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """If `info` is a (single-entry, thanks to `playlist_items`) playlist wrapper, return that
        entry's own info dict instead — needed to get accurate `formats`/`requested_downloads`/
        `ext` (observed unresolved as "NA" on the wrapper for some extractors, e.g. X/Twitter)."""
        if isinstance(info, dict) and info.get("entries"):
            entries = list(info["entries"])
            if len(entries) == 1 and isinstance(entries[0], dict):
                return entries[0]
        return info

    def preflight_best_height(self, url: str, playlist_index: Optional[int] = None) -> Optional[int]:
        """Return the best available video height for `url` (any container), without downloading.

        Deliberately does NOT pass a `format` selector: format metadata (height, vcodec, ...) is
        available directly from the extracted format list regardless of format selection, and
        computing the "best available" from the full list (rather than from whatever a selector
        like `bestvideo+bestaudio/best` resolves to) avoids under-reporting the true best quality
        when a signature/n-challenge failure has already caused some formats to be excluded from
        the selector's candidates (see SPECIFICATIONS.md section 3.3).
        """
        try:
            self.logger.info(f"Preflight quality check: {url}")
            logger = _YtDlpLogger(self.logger)
            opts = self._base_ydl_opts(logger, playlist_index=playlist_index)
            opts.update({"skip_download": True})
            self.logger.info(f"Equivalent command: {self._describe_cli(url, opts, download=False)}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            info = self._unwrap_single_entry(info)
            if logger.js_runtime_warning:
                self._maybe_warn_missing_js_runtime()
            if logger.js_challenge_warning:
                self._maybe_warn_js_challenge()
            if logger.sabr_warning:
                self._maybe_warn_sabr()
            return self._max_available_height(info)
        except Exception as e:
            self.logger.warning(f"Could not preflight YouTube quality for {url}: {e}")
            return None

    @staticmethod
    def _max_available_height(info: Optional[Dict[str, Any]]) -> Optional[int]:
        """Best height across every listed format, independent of any format selector."""
        if not info:
            return None
        heights = [
            f.get("height")
            for f in (info.get("formats") or [])
            if isinstance(f, dict) and f.get("vcodec") not in (None, "none") and f.get("height")
        ]
        return max(heights) if heights else None

    @staticmethod
    def _selected_height(info: Optional[Dict[str, Any]]) -> Optional[int]:
        if not info:
            return None
        for r in info.get("requested_downloads") or []:
            if isinstance(r, dict) and r.get("vcodec") not in (None, "none"):
                try:
                    return int(r.get("height") or 0) or None
                except Exception:
                    return None
        try:
            return int(info.get("height") or 0) or None
        except Exception:
            return None

    def _attempt_youtube_download(self, url: str, attempt_suffix: str = "", playlist_index: Optional[int] = None):
        """Run one preflight + actual download pass using the currently configured `self.player_clients`.

        `attempt_suffix` is baked into the temp filename so a retry attempt (see
        `download_from_youtube`) never overwrites a still-needed previous attempt on disk.
        `playlist_index` selects a specific entry when `url` exposes several videos under the
        same webpage_url (see `list_twitter_videos`/SPECIFICATIONS.md section 3.1.1).
        """
        best_height = self.preflight_best_height(url, playlist_index=playlist_index)

        logger = _YtDlpLogger(self.logger)
        opts = self._base_ydl_opts(logger, playlist_index=playlist_index)
        opts.update({
            "format": "bestvideo+bestaudio/best",
            "outtmpl": str(self.temp_dir / f"%(id)s{attempt_suffix}.%(ext)s"),
            "restrictfilenames": True,
            "merge_output_format": "mp4",
        })
        self.logger.info(f"Equivalent command: {self._describe_cli(url, opts, download=True)}")

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            info = self._unwrap_single_entry(info)
            downloaded_path = Path(ydl.prepare_filename(info))
            # merge_output_format may change the extension after postprocessing.
            if info.get("requested_downloads"):
                fp = info["requested_downloads"][0].get("filepath")
                if fp:
                    downloaded_path = Path(fp)

        if not downloaded_path.exists():
            # Some extractors (observed with X/Twitter) leave `ext` unresolved ("NA") on the
            # format info, so the path reported/derived above doesn't match the file actually
            # written to disk. Fall back to locating it by id in the temp dir.
            video_id = info.get("id")
            if video_id:
                candidates = [
                    p for p in self.temp_dir.glob(f"{video_id}{attempt_suffix}.*")
                    if p.suffix not in (".part", ".ytdl") and p.is_file()
                ]
                if candidates:
                    downloaded_path = max(candidates, key=lambda p: p.stat().st_mtime)

        return best_height, info, downloaded_path, logger

    def download_from_youtube(self, url: str, playlist_index: Optional[int] = None) -> DownloadResult:
        """Download the best available quality (any container), remuxing to mp4 if needed.

        See SPECIFICATIONS.md section 3.3: the format selector intentionally targets the true
        best quality across all containers (not just mp4), to avoid silently downloading a lower
        resolution than what is actually available (e.g. high-res streams only offered in WebM/VP9).

        Despite the name, this is a generic yt-dlp download and is also reused for the video
        chosen from a tweet URL (see SPECIFICATIONS.md section 3.1.1): none of the logic here is
        YouTube-specific beyond the `youtube_player_clients` extractor arg, which yt-dlp simply
        ignores for other extractors. `playlist_index` (1-based) selects a specific video when
        `url` exposes several under the same webpage_url (e.g. multiple native video attachments
        on one tweet) — see `list_twitter_videos`.
        """
        try:
            self.logger.info(f"Downloading YouTube video: {url}")

            # YouTube's SABR-only streaming rollout (see SPECIFICATIONS.md section 4.2) is applied
            # per-request/session: the exact same player clients (and the exact same config) can
            # succeed on one attempt, degrade the quality on the next, or even hit a hard extraction
            # error (e.g. an incompatible cached challenge-solver script) — confirmed probabilistic
            # by repeated live testing, not deterministically tied to any specific client list. To
            # cope with this, every candidate client list is tried in turn — including on a hard
            # failure, not just a degraded-quality result — up to `youtube_quality_max_attempts`
            # (config.yaml, default 3) total attempts, repeating the fallback clients as needed since
            # each attempt is effectively an independent roll of the dice. The best successful
            # attempt (by resolution obtained) is kept.
            original_clients = list(self.player_clients)
            fallback_clients = self._merged_fallback_clients()
            client_candidates = [original_clients]
            if fallback_clients != original_clients:
                client_candidates.append(fallback_clients)
            while len(client_candidates) < self.quality_max_attempts:
                client_candidates.append(fallback_clients)

            attempts = []
            last_error = None
            try:
                for idx, clients in enumerate(client_candidates):
                    has_more = idx < len(client_candidates) - 1
                    self.player_clients = clients
                    attempt_suffix = "" if idx == 0 else f".attempt{idx}"
                    try:
                        best_height, info, downloaded_path, logger = self._attempt_youtube_download(
                            url, attempt_suffix=attempt_suffix, playlist_index=playlist_index
                        )
                        actual_height = self._selected_height(info)
                        attempts.append({
                            "best_height": best_height, "info": info, "downloaded_path": downloaded_path,
                            "logger": logger, "actual_height": actual_height,
                        })
                        degraded = bool(best_height and actual_height and actual_height < best_height)
                        if not degraded and not logger.sabr_warning:
                            break
                        if has_more:
                            self.logger.info(
                                f"Quality issue detected with player clients {clients or ['<yt-dlp default>']} "
                                f"(downloaded {actual_height or '?'}p); retrying with "
                                f"{client_candidates[idx + 1]}."
                            )
                    except Exception as attempt_err:
                        last_error = attempt_err
                        self.logger.warning(
                            f"Download attempt with player clients {clients or ['<yt-dlp default>']} failed: {attempt_err}"
                        )
                        if has_more:
                            self.logger.info(f"Retrying with fallback player clients {client_candidates[idx + 1]}.")
            finally:
                self.player_clients = original_clients

            if not attempts:
                raise last_error or RuntimeError(f"All download attempts failed (tried player clients: {client_candidates})")

            winner = max(attempts, key=lambda a: (a["actual_height"] or -1))
            for attempt in attempts:
                if attempt is not winner:
                    attempt["downloaded_path"].unlink(missing_ok=True)
            if winner is not attempts[0]:
                self.logger.info(f"Retry with fallback player clients improved quality to {winner['actual_height']}p.")

            best_height = winner["best_height"]
            info = winner["info"]
            downloaded_path = winner["downloaded_path"]
            logger = winner["logger"]
            actual_height = winner["actual_height"]

            if logger.js_runtime_warning:
                self._maybe_warn_missing_js_runtime()
            if logger.js_challenge_warning:
                self._maybe_warn_js_challenge()
            if logger.sabr_warning:
                self._maybe_warn_sabr()

            final_path = downloaded_path
            if final_path.suffix.lower() != ".mp4":
                final_path = self._remux_to_mp4(downloaded_path)

            sanitized_title = self._sanitize_filename(info.get("title") or "video")
            dest_path = self.download_dir / f"{sanitized_title}{final_path.suffix}"
            if str(final_path) != str(dest_path):
                final_path.replace(dest_path)
                final_path = dest_path

            if actual_height and best_height:
                quality_info = f"Video quality: downloaded {actual_height}p (best available: {best_height}p) — {url}"
            elif actual_height:
                quality_info = f"Video quality: downloaded {actual_height}p (best available quality could not be determined) — {url}"
            else:
                quality_info = f"Video quality: could not be determined — {url}"
            self.logger.info(quality_info)

            quality_warning = None
            if best_height and actual_height and actual_height < best_height:
                quality_warning = _framed([
                    "QUALITY WARNING",
                    f"URL: {url}",
                    f"Downloaded quality is {actual_height}p, but {best_height}p was detected as available.",
                    "This can happen if the best stream was temporarily throttled/unavailable, or if a",
                    "signature/n-challenge could not be solved (see video.youtube_js_runtime /",
                    "video.youtube_remote_components in config.yaml, SPECIFICATIONS.md section 4).",
                    "To investigate manually, run:",
                    f'  yt-dlp -F "{url}"',
                    f'  yt-dlp -f <format_id> "{url}"',
                ])
                self.logger.warning(_yellow(quality_warning))
            elif logger.sabr_warning:
                quality_warning = _framed([
                    "QUALITY WARNING",
                    f"URL: {url}",
                    f"Downloaded quality is {actual_height or '?'}p. YouTube's SABR-only streaming",
                    "rollout is preventing yt-dlp from seeing higher-resolution direct (https) formats",
                    "for one or more player clients, so this may be lower than the video's real best",
                    "quality even though it matches what yt-dlp itself reports as 'best available'.",
                    "See https://github.com/yt-dlp/yt-dlp/issues/12482 . Try setting",
                    "video.youtube_player_clients in config.yaml (e.g. ['default', 'tv']) and/or",
                    "updating yt-dlp ('pip install -U yt-dlp').",
                    "To investigate manually, run:",
                    f'  yt-dlp -F "{url}"',
                ])
                self.logger.warning(_yellow(quality_warning))

            return DownloadResult(
                success=True,
                video_path=str(final_path),
                title=info.get("title"),
                quality_info=quality_info,
                quality_warning=quality_warning,
            )
        except Exception as e:
            self.logger.error(f"Error downloading from YouTube ({url}): {e}")
            return DownloadResult(success=False, error=str(e))

    def _remux_to_mp4(self, src_path: Path) -> Path:
        dest_path = src_path.with_suffix(".mp4")
        cmd = [self.ffmpeg_path, "-y", "-i", str(src_path), "-c", "copy", str(dest_path)]
        self.logger.info(f"Remuxing to mp4 (stream copy, no re-encoding): {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg remux to mp4 failed: {proc.stderr[-2000:]}")
        src_path.unlink(missing_ok=True)
        return dest_path

    def download_from_m3u8(self, url: str) -> DownloadResult:
        """Remux an .m3u8 (HLS) URL directly into an .mp4 file via ffmpeg (stream copy, no re-encoding)."""
        try:
            base = self._sanitize_filename(self._name_from_url(url)) or "hls_video"
            dest = self.download_dir / f"{base}.mp4"

            cmd = [
                self.ffmpeg_path, "-y",
                "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
                "-i", url,
                "-c", "copy",
                str(dest),
            ]
            self.logger.info(f"Downloading m3u8 via ffmpeg -> {dest}")
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if proc.returncode != 0:
                return DownloadResult(success=False, error=f"ffmpeg failed: {proc.stderr[-2000:]}")
            return DownloadResult(success=True, video_path=str(dest), title=base)
        except FileNotFoundError:
            return DownloadResult(success=False, error=f"ffmpeg not found. Configure video.ffmpeg_path or add ffmpeg to PATH: {self.ffmpeg_path}")
        except Exception as e:
            self.logger.error(f"Error downloading from m3u8: {e}")
            return DownloadResult(success=False, error=str(e))

    @staticmethod
    def _name_from_url(url: str) -> str:
        try:
            p = urlparse(url)
            name = Path(p.path).name
            if name.lower().endswith(".m3u8"):
                name = name[:-5]
            return name or ""
        except Exception:
            return ""

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "video"
