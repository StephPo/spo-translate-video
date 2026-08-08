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


YOUTUBE_URL_RE = re.compile(r"(youtube\.com/(watch\?|shorts/)|youtu\.be/)", re.IGNORECASE)


def detect_source_type(source: str) -> str:
    """Auto-detect the source type: 'youtube', 'm3u8', or 'local'."""
    if YOUTUBE_URL_RE.search(source):
        return "youtube"
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

    def __init__(self, parent: logging.Logger):
        self._parent = parent
        self.js_runtime_warning = False
        self.js_challenge_warning = False

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
        self._parent.warning(msg)

    def error(self, msg):
        self._parent.error(msg)

    def report_warning(self, msg):
        self.warning(msg)


class VideoDownloader:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        video_cfg = config.get("video", {}) or {}
        output_cfg = config.get("output", {}) or {}

        self.temp_dir = Path(video_cfg.get("temp_directory", "./temp"))
        self.download_dir = Path(output_cfg.get("video_download_directory") or output_cfg.get("output_directory") or "./output")
        self.ffmpeg_path = str(video_cfg.get("ffmpeg_path") or "ffmpeg")

        runtime_cfg = video_cfg.get("youtube_js_runtime") or {}
        self._js_runtime_name = str(runtime_cfg.get("runtime") or "").strip().lower()
        self._js_runtime_path = str(runtime_cfg.get("path") or "").strip()
        self.js_runtimes = self._build_js_runtime(self._js_runtime_name, self._js_runtime_path)

        remote_cfg = video_cfg.get("youtube_remote_components") or {}
        self.remote_components = list(remote_cfg.get("components") or []) if remote_cfg.get("enable") else []

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

    def _base_ydl_opts(self, logger: _YtDlpLogger) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "logger": logger,
        }
        if self.js_runtimes:
            opts["js_runtimes"] = self.js_runtimes
        if self.remote_components:
            opts["remote_components"] = self.remote_components
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

    def preflight_best_height(self, url: str) -> Optional[int]:
        """Return the best available video height for `url` (any container), without downloading."""
        try:
            logger = _YtDlpLogger(self.logger)
            opts = self._base_ydl_opts(logger)
            opts.update({"skip_download": True, "format": "bestvideo+bestaudio/best"})
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if logger.js_runtime_warning:
                self._maybe_warn_missing_js_runtime()
            if logger.js_challenge_warning:
                self._maybe_warn_js_challenge()
            return self._selected_height(info)
        except Exception as e:
            self.logger.warning(f"Could not preflight YouTube quality: {e}")
            return None

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

    def download_from_youtube(self, url: str) -> DownloadResult:
        """Download the best available quality (any container), remuxing to mp4 if needed.

        See SPECIFICATIONS.md section 3.3: the format selector intentionally targets the true
        best quality across all containers (not just mp4), to avoid silently downloading a lower
        resolution than what is actually available (e.g. high-res streams only offered in WebM/VP9).
        """
        try:
            best_height = self.preflight_best_height(url)

            logger = _YtDlpLogger(self.logger)
            opts = self._base_ydl_opts(logger)
            opts.update({
                "format": "bestvideo+bestaudio/best",
                "outtmpl": str(self.temp_dir / "%(id)s.%(ext)s"),
                "restrictfilenames": True,
                "merge_output_format": "mp4",
            })

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_path = Path(ydl.prepare_filename(info))
                # merge_output_format may change the extension after postprocessing.
                if info.get("requested_downloads"):
                    fp = info["requested_downloads"][0].get("filepath")
                    if fp:
                        downloaded_path = Path(fp)

            if logger.js_runtime_warning:
                self._maybe_warn_missing_js_runtime()
            if logger.js_challenge_warning:
                self._maybe_warn_js_challenge()

            final_path = downloaded_path
            if final_path.suffix.lower() != ".mp4":
                final_path = self._remux_to_mp4(downloaded_path)

            sanitized_title = self._sanitize_filename(info.get("title") or "video")
            dest_path = self.download_dir / f"{sanitized_title}{final_path.suffix}"
            if str(final_path) != str(dest_path):
                final_path.replace(dest_path)
                final_path = dest_path

            actual_height = self._selected_height(info)
            quality_warning = None
            if best_height and actual_height and actual_height < best_height:
                quality_warning = (
                    f"Downloaded quality is {actual_height}p, but {best_height}p was detected as available. "
                    f"This can happen if the best stream was temporarily throttled/unavailable. "
                    f"To investigate manually, run: yt-dlp -F \"{url}\" then yt-dlp -f <format_id> \"{url}\""
                )
                self.logger.warning(_yellow(quality_warning))

            return DownloadResult(
                success=True,
                video_path=str(final_path),
                title=info.get("title"),
                quality_warning=quality_warning,
            )
        except Exception as e:
            self.logger.error(f"Error downloading from YouTube: {e}")
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
