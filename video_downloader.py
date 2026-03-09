import os
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional, Dict, Any
import yt_dlp
from dataclasses import dataclass
import re
import subprocess
import time
from urllib.parse import urlparse

@dataclass
class DownloadResult:
    success: bool
    video_path: Optional[str] = None
    audio_path: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
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


def _yellow(text: str) -> str:
    if not _supports_ansi():
        return text
    return f"\x1b[93m{text}\x1b[0m"


class _YtDlpLogger:
    KEYWORD = "no supported javascript runtime"

    def __init__(self, parent: logging.Logger):
        self._parent = parent
        self.js_runtime_warning = False

    def debug(self, msg):
        self._parent.debug(msg)

    def info(self, msg):
        self._parent.info(msg)

    def warning(self, msg):
        if isinstance(msg, str) and self.KEYWORD in msg.lower():
            self.js_runtime_warning = True
        self._parent.warning(msg)

    def error(self, msg):
        self._parent.error(msg)

    def fatal(self, msg):
        self._parent.error(msg)

    def report_warning(self, msg):
        self.warning(msg)

class VideoDownloader:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        video_cfg = config.get('video', {})
        self.output_dir = Path(config.get('output', {}).get('output_directory', './output'))
        self.temp_dir = Path(video_cfg.get('temp_directory', './temp'))
        self.download_dir = Path(config.get('output', {}).get('video_download_directory', str(self.output_dir)))
        self.ffmpeg_path = str(video_cfg.get('ffmpeg_path') or 'ffmpeg')
        self.cookies_path = str(video_cfg.get('youtube_cookies_path') or r"C:\\Temp\\www.youtube.com_cookies.txt")
        runtime_cfg = video_cfg.get('youtube_js_runtime') or {}
        runtime_name = str(runtime_cfg.get('runtime') or '').strip().lower()
        runtime_path = str(runtime_cfg.get('path') or '').strip()
        self._js_runtime_name = runtime_name
        self._js_runtime_requested_path = runtime_path
        self._js_runtime_resolved_path = None
        self.js_runtimes = self._build_js_runtime(runtime_name, runtime_path)
        self._warned_js_runtime = False
        remote_cfg = video_cfg.get('youtube_remote_components') or {}
        self.remote_components = self._build_remote_components(remote_cfg)
        self._progress_last_ts = 0.0
        self._progress_line_len = 0

        # Create directories if they don't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def preflight_youtube_quality(self, url: str) -> Dict[str, Any]:
        """Return which quality we'd get with current MP4-only selector vs best overall.

        Uses yt-dlp's format selection (without downloading) so results match real behavior.
        """
        try:
            base_opts = {
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'skip_download': True,
            }
            if self.js_runtimes:
                base_opts['js_runtimes'] = self.js_runtimes
            if self.remote_components:
                base_opts['remote_components'] = self.remote_components

            # What we would download today (MP4-only selection)
            ydl_opts_mp4 = dict(base_opts)
            ydl_opts_mp4['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'

            # True best available (any container)
            ydl_opts_best = dict(base_opts)
            ydl_opts_best['format'] = 'bestvideo+bestaudio/best'

            ydl_logger = _YtDlpLogger(self.logger)

            with yt_dlp.YoutubeDL({**ydl_opts_mp4, 'logger': ydl_logger}) as ydl:
                info_mp4 = ydl.extract_info(url, download=False)
            with yt_dlp.YoutubeDL({**ydl_opts_best, 'logger': ydl_logger}) as ydl:
                info_best = ydl.extract_info(url, download=False)

            if ydl_logger.js_runtime_warning:
                self._maybe_warn_missing_js_runtime()

            mp4_video = self._extract_selected_video_stream(info_mp4)
            best_video = self._extract_selected_video_stream(info_best)

            best_overall_height = int((best_video or {}).get('height') or 0)
            best_mp4_height = int((mp4_video or {}).get('height') or 0)

            return {
                'best_overall': best_video,
                'best_mp4': mp4_video,
                'best_overall_height': best_overall_height,
                'best_mp4_height': best_mp4_height,
                'is_downgraded': best_overall_height > best_mp4_height and best_mp4_height > 0,
                'title': (info_best or {}).get('title') or (info_mp4 or {}).get('title') or '',
                'id': (info_best or {}).get('id') or (info_mp4 or {}).get('id') or '',
            }
        except Exception as e:
            return {'error': str(e)}

    def _extract_selected_video_stream(self, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            requested_downloads = (info or {}).get('requested_downloads') or []
            for r in requested_downloads:
                if not isinstance(r, dict):
                    continue
                vcodec = r.get('vcodec')
                if vcodec and vcodec != 'none':
                    return r

            # Fallback: if yt-dlp didn't populate requested_downloads, try the top-level dict
            vcodec = (info or {}).get('vcodec')
            if vcodec and vcodec != 'none':
                return info
            return None
        except Exception:
            return None
    
    def download_from_youtube(self, url: str) -> DownloadResult:
        """Download video from YouTube"""
        try:
            ydl_logger = _YtDlpLogger(self.logger)

            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                # Download into temp first so yt-dlp sidecar files (like .info.json) don't pollute the output folder.
                'outtmpl': str(self.temp_dir / '%(id)s.%(ext)s'),
                'restrictfilenames': True,
                'noplaylist': True,
                'writethumbnail': False,
                'writeinfojson': True,
                'quiet': False,
                'no_warnings': False,
                'progress_hooks': [self._progress_hook],
                'postprocessors': [],
                'logger': ydl_logger,
            }
            if self.js_runtimes:
                ydl_opts['js_runtimes'] = self.js_runtimes
            if self.remote_components:
                ydl_opts['remote_components'] = self.remote_components

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            if ydl_logger.js_runtime_warning:
                self._maybe_warn_missing_js_runtime()

            video_path = self._extract_final_video_path(info)
            audio_path = None

            requested_downloads = info.get('requested_downloads') or []
            downloaded_video = None
            for r in requested_downloads:
                if not isinstance(r, dict):
                    continue
                vcodec = r.get('vcodec')
                if vcodec and vcodec != 'none':
                    downloaded_video = r
                    break

            if video_path:
                try:
                    src = Path(video_path)
                    title = info.get('title') or ''
                    vid = info.get('id') or src.stem
                    base_name = self._sanitize_filename(title) or str(vid)
                    dest = self._resolve_existing_destination(self.download_dir / f"{base_name}{src.suffix}")
                    if src.exists() and src.parent.resolve() != self.download_dir.resolve():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if dest.exists():
                            dest.unlink()
                        shutil.move(str(src), str(dest))
                        video_path = str(dest)
                except Exception as e:
                    self.logger.warning(f"Failed to move video to download directory: {e}")

            if not video_path or not Path(video_path).exists():
                return DownloadResult(success=False, error="Failed to locate downloaded video file")
            
            metadata = {
                'title': info.get('title', ''),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', ''),
                'upload_date': info.get('upload_date', ''),
                'description': info.get('description', ''),
                'view_count': info.get('view_count', 0),
                'like_count': info.get('like_count', 0),
                'downloaded_video_height': int((downloaded_video or {}).get('height') or 0),
                'downloaded_video_ext': str((downloaded_video or {}).get('ext') or ''),
                'downloaded_video_vcodec': str((downloaded_video or {}).get('vcodec') or ''),
            }
            
            return DownloadResult(
                success=True,
                video_path=video_path,
                audio_path=audio_path,
                metadata=metadata
            )
                
        except Exception as e:
            self.logger.error(f"Error downloading from YouTube: {str(e)}")
            return DownloadResult(success=False, error=str(e))

    def download_from_m3u8(self, url: str) -> DownloadResult:
        """Download/remux a .m3u8 HLS URL into an .mp4 file using ffmpeg."""
        try:
            base = self._sanitize_filename(self._name_from_url(url)) or "hls_video"
            base_dest = self.download_dir / f"{base}.mp4"
            dest = self._resolve_existing_destination(base_dest)
            overwrite_flag = "-y" if (dest == base_dest and base_dest.exists() and self.config.get('_runtime', {}).get('overwrite_existing') is True) else "-n"

            cmd = [
                self.ffmpeg_path,
                "-hide_banner",
                overwrite_flag,
                "-i",
                url,
                "-c",
                "copy",
                "-bsf:a",
                "aac_adtstoasc",
                str(dest),
            ]

            self.logger.info(f"Downloading m3u8 via ffmpeg -> {dest}")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()
                return DownloadResult(success=False, error=f"ffmpeg failed ({proc.returncode}): {err}")

            if not dest.exists():
                return DownloadResult(success=False, error="ffmpeg reported success but output file was not created")

            metadata = {
                'title': dest.stem,
                'duration': 0,
                'source_url': url,
                'format': 'mp4',
            }

            return DownloadResult(success=True, video_path=str(dest), metadata=metadata)
        except FileNotFoundError:
            return DownloadResult(success=False, error=f"ffmpeg not found. Configure video.ffmpeg_path or add ffmpeg to PATH: {self.ffmpeg_path}")
        except Exception as e:
            self.logger.error(f"Error downloading from m3u8: {str(e)}")
            return DownloadResult(success=False, error=str(e))
    
    def process_local_file(self, file_path: str) -> DownloadResult:
        """Process local video file"""
        try:
            path = Path(file_path)
            
            if not path.exists():
                return DownloadResult(success=False, error=f"File not found: {file_path}")

            suffix = path.suffix.lower().lstrip('.')
            supported = self.config.get('input', {}).get('supported_formats', ['mp4'])
            supported = [str(x).lower().lstrip('.') for x in (supported or [])]
            if suffix not in supported:
                return DownloadResult(success=False, error=f"Unsupported format: {path.suffix}")

            # Basic metadata extraction
            metadata = {
                'title': path.stem,
                'duration': 0,  # Will be filled by audio processing
                'file_size': path.stat().st_size,
                'format': path.suffix.lower(),
            }
            
            return DownloadResult(
                success=True,
                video_path=str(path),
                metadata=metadata
            )
            
        except Exception as e:
            self.logger.error(f"Error processing local file: {str(e)}")
            return DownloadResult(success=False, error=str(e))
    
    def _progress_hook(self, d):
        """Progress hook for yt-dlp"""
        if d['status'] == 'downloading':
            now = time.time()
            if now - self._progress_last_ts < 0.5:
                return
            self._progress_last_ts = now

            downloaded = float(d.get('downloaded_bytes') or 0)
            total = float(d.get('total_bytes') or d.get('total_bytes_estimate') or 0)
            pct = (downloaded / total * 100.0) if total > 0 else 0.0
            downloaded_str = d.get('_downloaded_bytes_str') or f"{downloaded/1_000_000:.2f}MiB"
            total_str = d.get('_total_bytes_str') or (f"{total/1_000_000:.2f}MiB" if total else "?")
            speed = d.get('_speed_str') or "?"
            eta = d.get('_eta_str') or "?"
            line = f"Downloading: {pct:5.1f}% ({downloaded_str} / {total_str}) at {speed} ETA {eta}"
            padding = max(0, self._progress_line_len - len(line))
            sys.stdout.write("\r" + line + (" " * padding))
            sys.stdout.flush()
            self._progress_line_len = len(line)
            return
        elif d['status'] == 'finished':
            if self._progress_line_len:
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._progress_line_len = 0
            filename = d.get('filename') or d.get('_filename') or 'unknown file'
            self.logger.info(f"Download completed: {filename}")
        elif d['status'] == 'error':
            self.logger.error(f"Download error: {d.get('error', 'Unknown error')}")
    
    def cleanup_temp_files(self):
        """Clean up temporary files"""
        try:
            for file in self.temp_dir.glob('*'):
                if file.is_file():
                    file.unlink()
            self.logger.info("Temporary files cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up temp files: {str(e)}")

    def _build_js_runtime(self, runtime_name: str, runtime_path: str) -> Dict[str, Dict[str, Any]]:
        runtime_name = (runtime_name or '').strip().lower()
        runtime_path = (runtime_path or '').strip()
        if not runtime_name:
            return {}

        resolved_path = runtime_path or shutil.which(runtime_name)
        config: Dict[str, Any] = {}
        if resolved_path:
            self._js_runtime_resolved_path = resolved_path
            config['path'] = resolved_path
            self.logger.info(
                f"Enabling JavaScript runtime '{runtime_name}' at {resolved_path}"
            )
        else:
            self.logger.warning(
                f"{_yellow('WARNING')}: JavaScript runtime '{runtime_name}' was not found in PATH. "
                "Install Node.js LTS and/or set video.youtube_js_runtime.path to the full node.exe path."
            )
            return {}

        return {runtime_name: config}

    def _maybe_warn_missing_js_runtime(self):
        if self._warned_js_runtime:
            return
        self._warned_js_runtime = True
        location = self._js_runtime_resolved_path or self._js_runtime_requested_path or "(auto)"
        self.logger.warning(
            f"{_yellow('WARNING')}: YouTube now requires a JavaScript runtime for yt-dlp challenges. "
            "Install Node.js LTS and ensure node.exe is on PATH, or set video.youtube_js_runtime.path explicitly. "
            f"Current setting: {self._js_runtime_name or 'none'} -> {location}"
        )

    def _build_remote_components(self, remote_cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        enabled = remote_cfg.get('enable', True)
        components = remote_cfg.get('components') or ['ejs:github']
        if not enabled:
            return {}

        remotes: Dict[str, Dict[str, Any]] = {}
        for comp in components:
            key = str(comp).strip()
            if not key:
                continue
            remotes[key] = {}
        if remotes:
            joined = ", ".join(remotes.keys())
            self.logger.info(f"Enabling yt-dlp remote components: {joined}")
        return remotes

    def _sanitize_filename(self, name: str) -> str:
        if not name:
            return ""
        name = re.sub(r"[<>:\\/?*\"|]", "", name)
        name = re.sub(r"[\x00-\x1f]", "", name)
        name = re.sub(r"\s+", " ", name).strip()
        name = name.rstrip(". ")
        if not name:
            return ""
        max_len = 140
        if len(name) > max_len:
            name = name[:max_len].rstrip(". ")
        return name

    def _get_overwrite_decision(self) -> bool:
        rt = self.config.setdefault('_runtime', {})
        if 'overwrite_existing' in rt:
            return bool(rt['overwrite_existing'])

        t0 = time.perf_counter()
        ans = input("Output file already exists. Overwrite? (y/n): ").strip().lower()
        rt['user_wait_seconds'] = float(rt.get('user_wait_seconds') or 0.0) + (time.perf_counter() - t0)
        overwrite = ans == 'y'
        rt['overwrite_existing'] = overwrite
        return overwrite

    def _resolve_existing_destination(self, dest: Path) -> Path:
        if not dest.exists():
            return dest

        if self._get_overwrite_decision():
            return dest

        stem = dest.stem
        suffix = dest.suffix
        for i in range(1, 101):
            candidate = dest.with_name(f"{stem}_{i}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Unable to find available filename for {dest} (up to _100)")

    def _name_from_url(self, url: str) -> str:
        try:
            p = urlparse(url)
            name = Path(p.path).name
            if name.lower().endswith('.m3u8'):
                name = name[:-5]
            return name or ""
        except Exception:
            return ""

    def _extract_final_video_path(self, info: Dict[str, Any]) -> Optional[str]:
        """Extract the final merged video filepath from yt-dlp info dict."""
        try:
            fp = info.get('filepath') or info.get('_filename')
            if fp:
                return str(fp)

            requested = info.get('requested_downloads') or []
            for r in requested:
                fp = r.get('filepath') or r.get('_filename')
                if fp:
                    return str(fp)

            vid = info.get('id')
            if vid:
                pattern = str(self.temp_dir / f"{vid}.*")
                matches = sorted(Path(self.temp_dir).glob(f"{vid}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
                if matches:
                    return str(matches[0])

            latest = sorted(self.temp_dir.glob('*.*'), key=lambda p: p.stat().st_mtime, reverse=True)
            if latest:
                return str(latest[0])

            return None
        except Exception:
            return None

def main():
    """Test function for video downloader"""
    logging.basicConfig(level=logging.INFO)
    
    # Sample config
    config = {
        'output': {'output_directory': './output'},
        'video': {'temp_directory': './temp'},
        'audio': {'extraction_format': 'wav'},
        'input': {'supported_formats': ['mp4', 'avi', 'mov', 'mkv', 'webm']}
    }
    
    downloader = VideoDownloader(config)
    
    # Test YouTube download (replace with actual URL for testing)
    # result = downloader.download_from_youtube("https://www.youtube.com/watch?v=example")
    
    # Test local file processing
    # result = downloader.process_local_file("path/to/video.mp4")
    
    print("Video downloader ready for use")

if __name__ == "__main__":
    main()
