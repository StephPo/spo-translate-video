import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class AudioProcessingResult:
    success: bool
    audio_path: Optional[str] = None
    error: Optional[str] = None


class AudioProcessor:
    """Audio extraction from a video file, using ffmpeg."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.temp_dir = Path(config.get("video", {}).get("temp_directory", "./temp"))
        audio_cfg = config.get("audio", {})
        self.ffmpeg_path = str(config.get("video", {}).get("ffmpeg_path") or "ffmpeg")
        self.extraction_format = audio_cfg.get("extraction_format", "wav")
        self.sample_rate = int(audio_cfg.get("sample_rate", 16000))
        self.channels = int(audio_cfg.get("channels", 1))
        self.normalize_volume = bool(audio_cfg.get("normalize_volume", True))

        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def extract_audio(self, video_path: str, start_time: Optional[float] = None, end_time: Optional[float] = None) -> AudioProcessingResult:
        """Extract audio from `video_path` (optionally a [start_time, end_time] range) to a WAV file."""
        try:
            video_path = Path(video_path)
            if not video_path.exists():
                return AudioProcessingResult(success=False, error=f"Video file not found: {video_path}")

            suffix = f".{self.extraction_format}"
            out_path = self.temp_dir / f"{video_path.stem}_audio{suffix}"

            cmd = [self.ffmpeg_path, "-y"]
            if start_time is not None:
                cmd += ["-ss", str(max(0.0, start_time))]
            cmd += ["-i", str(video_path)]
            if end_time is not None and start_time is not None:
                cmd += ["-t", str(max(0.0, end_time - start_time))]

            filters = []
            if self.normalize_volume:
                filters.append("loudnorm")
            cmd += ["-vn", "-ac", str(self.channels), "-ar", str(self.sample_rate)]
            if filters:
                cmd += ["-af", ",".join(filters)]
            cmd += [str(out_path)]

            self.logger.info(f"Extracting audio: {' '.join(cmd)}")
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if proc.returncode != 0:
                return AudioProcessingResult(success=False, error=f"ffmpeg failed: {proc.stderr[-2000:]}")

            return AudioProcessingResult(success=True, audio_path=str(out_path))
        except FileNotFoundError:
            return AudioProcessingResult(
                success=False,
                error=f"ffmpeg not found. Configure video.ffmpeg_path or add ffmpeg to PATH: {self.ffmpeg_path}",
            )
        except Exception as e:
            self.logger.error(f"Error extracting audio: {e}")
            return AudioProcessingResult(success=False, error=str(e))
