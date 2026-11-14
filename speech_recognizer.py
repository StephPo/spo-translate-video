import logging
import os
import subprocess
import sysconfig
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class TranscriptionSegment:
    start_time: float
    end_time: float
    text: str
    confidence: float


@dataclass
class TranscriptionResult:
    success: bool
    segments: Optional[List[TranscriptionSegment]] = None
    full_text: Optional[str] = None
    language: Optional[str] = None
    error: Optional[str] = None


SAMPLE_RATE = 16000

# Handles returned by os.add_dll_directory must stay referenced for the directories to stay active.
_dll_dir_handles: List[Any] = []


def _add_nvidia_dll_dirs() -> None:
    """Make the CUDA DLLs shipped by the nvidia-*-cu12 pip wheels findable by CTranslate2 (Windows).

    Those wheels (requirements-gpu.txt) put cublas64_12.dll / cudnn64_9.dll in
    site-packages/nvidia/<lib>/bin, which is not on the DLL search path by default.
    """
    if os.name != "nt" or _dll_dir_handles:
        return
    roots = {Path(sysconfig.get_paths()["purelib"]), Path(sysconfig.get_paths()["platlib"])}
    for root in roots:
        for bin_dir in sorted((root / "nvidia").glob("*/bin")):
            try:
                _dll_dir_handles.append(os.add_dll_directory(str(bin_dir)))
            except OSError:
                continue
            # CTranslate2 loads cuBLAS/cuDNN lazily with plain LoadLibrary, which also searches PATH.
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


class WhisperRecognizer:
    """Local speech-to-text transcription using faster-whisper (CTranslate2), see SPECIFICATIONS.md 3.6."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        speech_cfg = config.get("speech_recognition", {})

        self.model_name = str(speech_cfg.get("whisper_model", "large-v3"))
        # "auto": float16 on GPU, int8 on CPU.
        self.compute_type = str(speech_cfg.get("compute_type", "auto") or "auto")
        # 0: half the logical CPUs (~ physical cores) instead of CTranslate2's default of 4.
        self.cpu_threads = int(speech_cfg.get("cpu_threads", 0) or 0)
        # Silero VAD: skip silence / music-only stretches before decoding (main pass only). Off by
        # default: it may drop speech over background music.
        self.vad_filter = bool(speech_cfg.get("vad_filter", False))
        # 1 = greedy decoding (what the former openai-whisper call did); 5 = slightly better, slower.
        self.beam_size = max(1, int(speech_cfg.get("beam_size", 1) or 1))
        self.word_timestamps = bool(speech_cfg.get("word_timestamps", True))
        self.confidence_threshold = float(speech_cfg.get("confidence_threshold", 0.2))
        # When True, Whisper feeds the text of the previous 30 s window as a prompt to the next
        # one. On long videos this regularly makes the decoder fall into repetition loops, which
        # degrades a whole window (temperature fallback, very low avg_logprob) and loses ~30 s of
        # speech at once. False by default (SPECIFICATIONS.md section 3.6).
        self.condition_on_previous_text = bool(speech_cfg.get("condition_on_previous_text", False))
        # Second pass re-transcribing in isolation the regions whose segments were dropped.
        self.recover_dropped_regions = bool(speech_cfg.get("recover_dropped_regions", True))

        advanced_cfg = config.get("advanced", {})
        # "auto" (GPU if available and working, else CPU), true (want GPU, CPU fallback), false (CPU).
        gpu = advanced_cfg.get("gpu_acceleration", "auto")
        self.gpu_mode = "auto" if str(gpu).strip().lower() == "auto" else ("on" if bool(gpu) else "off")
        self.gpu_device = int(advanced_cfg.get("gpu_device", 0))
        self.ffmpeg_path = (config.get("video", {}).get("ffmpeg_path") or "").strip() or "ffmpeg"

        self.model = None
        self.device = None

    def _resolve_compute_type(self, device: str) -> str:
        if self.compute_type.lower() != "auto":
            return self.compute_type
        return "float16" if device == "cuda" else "int8"

    def _create_model(self, device: str):
        from faster_whisper import WhisperModel

        compute_type = self._resolve_compute_type(device)
        kwargs: Dict[str, Any] = dict(device=device, compute_type=compute_type)
        if device == "cuda":
            kwargs["device_index"] = self.gpu_device
        else:
            kwargs["cpu_threads"] = self.cpu_threads or max(1, (os.cpu_count() or 2) // 2)
        self.logger.info(
            f"Loading Whisper model '{self.model_name}' on {device} (compute_type={compute_type}"
            + (f", cpu_threads={kwargs['cpu_threads']}" if device == "cpu" else f", device_index={self.gpu_device}")
            + ")"
        )
        return WhisperModel(self.model_name, **kwargs)

    def _load_model(self):
        if self.model is not None:
            return
        # huggingface_hub logs every model-file HTTP request at INFO through httpx: too noisy.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        if self.gpu_mode != "off":
            _add_nvidia_dll_dirs()
            try:
                import ctranslate2

                if ctranslate2.get_cuda_device_count() <= self.gpu_device:
                    raise RuntimeError("no CUDA device found")
                model = self._create_model("cuda")
                # cuBLAS / cuDNN are only loaded on the first encode: validate now on 1 s of silence
                # rather than failing in the middle of a long transcription.
                segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en", beam_size=1)
                list(segments)
                self.model, self.device = model, "cuda"
            except Exception as e:
                log = self.logger.warning if self.gpu_mode == "on" else self.logger.info
                log(f"GPU unavailable or not working ({e}); falling back to CPU")
        if self.model is None:
            self.model, self.device = self._create_model("cpu"), "cpu"
        self.logger.info(f"Whisper model loaded on {self.device}")

    def _load_audio(self, audio_path: Path) -> np.ndarray:
        """Decode any audio/video file to 16 kHz mono float32 with ffmpeg (as openai-whisper did)."""
        cmd = [
            self.ffmpeg_path, "-nostdin", "-threads", "0", "-v", "error",
            "-i", str(audio_path), "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-",
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed to decode audio: {proc.stderr.decode('utf-8', 'replace').strip()}")
        return np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0

    def transcribe(self, audio_path: str, language: str = "ja") -> TranscriptionResult:
        try:
            self._load_model()

            audio_path_p = Path(audio_path)
            if not audio_path_p.exists():
                return TranscriptionResult(success=False, error=f"Audio file not found: {audio_path_p}")

            self.logger.info(f"Transcribing audio: {audio_path_p}")
            audio = self._load_audio(audio_path_p)
            started = time.monotonic()
            result = self._run_whisper(audio, language)
            elapsed = time.monotonic() - started
            duration = len(audio) / SAMPLE_RATE
            self.logger.info(
                f"Transcription pass done in {elapsed:.0f} s for {duration:.0f} s of audio "
                f"({duration / elapsed if elapsed else 0:.1f}x real time, {self.device})"
            )

            segments, dropped = self._filter_segments(result.get("segments", []))
            for seg in dropped:
                self.logger.info(
                    f"Dropped low-confidence segment {self._fmt(seg['start'])}-{self._fmt(seg['end'])} "
                    f"(conf={seg['_conf']:.2f}, avg_logprob={seg.get('avg_logprob', 0):.2f}, "
                    f"no_speech_prob={seg.get('no_speech_prob', 0):.2f}, temperature={seg.get('temperature')}): "
                    f"{(seg.get('text') or '').strip()!r}"
                )

            if self.recover_dropped_regions and dropped:
                segments = self._recover_regions(audio, language, segments, dropped)

            return TranscriptionResult(
                success=True,
                segments=segments,
                full_text=(result.get("text") or "").strip(),
                language=result.get("language", language),
            )
        except Exception as e:
            self.logger.error(f"Error transcribing audio: {e}")
            return TranscriptionResult(success=False, error=str(e))

    def _run_whisper(self, audio, language: str, clip_timestamps=None) -> Dict[str, Any]:
        """Run faster-whisper and return an openai-whisper-shaped result dict (segments as dicts)."""
        options: Dict[str, Any] = dict(
            language=language,
            task="transcribe",
            beam_size=self.beam_size,
            best_of=self.beam_size,
            word_timestamps=self.word_timestamps,
            condition_on_previous_text=self.condition_on_previous_text,
        )
        if clip_timestamps:
            # The recovery pass decodes the region exactly as is (faster-whisper ignores VAD here anyway).
            options["clip_timestamps"] = clip_timestamps
        else:
            options["vad_filter"] = self.vad_filter
            options["log_progress"] = bool(self.config.get("processing", {}).get("show_progress", True))
        segments_iter, info = self.model.transcribe(audio, **options)
        segments = [
            {
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "avg_logprob": seg.avg_logprob,
                "no_speech_prob": seg.no_speech_prob,
                "temperature": seg.temperature,
            }
            for seg in segments_iter  # lazy generator: decoding happens here
        ]
        return {
            "segments": segments,
            "text": "".join(s["text"] for s in segments),
            "language": info.language,
        }

    def _filter_segments(self, raw_segments):
        """Split Whisper segments into (kept TranscriptionSegments, dropped raw segments)."""
        kept: List[TranscriptionSegment] = []
        dropped: List[Dict[str, Any]] = []
        for seg in raw_segments:
            text = (seg.get("text") or "").strip()
            confidence = self._estimate_confidence(text, seg)
            if confidence < self.confidence_threshold:
                # Empty segments carry no speech to recover; don't trigger a recovery pass for them.
                if text:
                    dropped.append({**seg, "_conf": confidence})
                continue
            kept.append(
                TranscriptionSegment(
                    start_time=float(seg.get("start", 0.0)),
                    end_time=float(seg.get("end", 0.0)),
                    text=text,
                    confidence=confidence,
                )
            )
        return kept, dropped

    def _recover_regions(self, audio, language, segments, dropped):
        """Re-transcribe, each in isolation, the regions where segments were dropped.

        Whisper's quality signals (avg_logprob, no_speech_prob, temperature) are computed per
        30 s decoding window, so one degraded window drops all its segments together. The same
        audio decoded on its own (fresh context) is usually fine (SPECIFICATIONS.md section 3.6).
        """
        # Group contiguous dropped segments into regions, bounded by the surrounding kept ones.
        regions: List[List[float]] = []
        for seg in dropped:
            start, end = float(seg["start"]), float(seg["end"])
            if regions and start - regions[-1][1] <= 2.0:
                regions[-1][1] = max(regions[-1][1], end)
            else:
                regions.append([start, end])

        recovered_total = 0
        for start, end in regions:
            prev_end = max((s.end_time for s in segments if s.end_time <= start + 0.01), default=0.0)
            next_start = min((s.start_time for s in segments if s.start_time >= end - 0.01), default=end + 1.0)
            clip_start, clip_end = max(prev_end, start - 1.0), min(next_start, end + 1.0)
            if clip_end - clip_start < 0.5:
                continue
            self.logger.info(f"Re-transcribing dropped region {self._fmt(clip_start)}-{self._fmt(clip_end)}")
            try:
                result = self._run_whisper(audio, language, clip_timestamps=[clip_start, clip_end])
            except Exception as e:
                self.logger.warning(f"Recovery of region {self._fmt(clip_start)}-{self._fmt(clip_end)} failed: {e}")
                continue
            recovered, still_dropped = self._filter_segments(result.get("segments", []))
            recovered = [
                s for s in recovered if s.start_time >= clip_start - 0.5 and s.end_time <= clip_end + 0.5
            ]
            for seg in recovered:
                self.logger.info(f"  recovered {self._fmt(seg.start_time)}-{self._fmt(seg.end_time)} "
                                 f"(conf={seg.confidence:.2f}): {seg.text!r}")
            for seg in still_dropped:
                self.logger.info(f"  still dropped {self._fmt(seg['start'])}-{self._fmt(seg['end'])} "
                                 f"(conf={seg['_conf']:.2f}): {(seg.get('text') or '').strip()!r}")
            segments.extend(recovered)
            recovered_total += len(recovered)

        segments.sort(key=lambda s: s.start_time)
        self.logger.info(
            f"Recovery pass: {len(regions)} dropped region(s) re-transcribed, {recovered_total} segment(s) recovered"
        )
        return segments

    @staticmethod
    def _fmt(seconds: float) -> str:
        seconds = float(seconds)
        return f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}:{seconds % 60:05.2f}"

    @staticmethod
    def _estimate_confidence(text: str, seg: Optional[Dict[str, Any]] = None) -> float:
        # Whisper does not expose a single calibrated 0-1 confidence score, but each
        # segment does carry two real decoding signals we can use: `avg_logprob` (mean
        # log-probability of the decoded tokens; closer to 0 = more confident) and
        # `no_speech_prob` (probability the segment contains no speech at all, useful to
        # catch typical Whisper hallucinations on silence/noise). These are combined with
        # the previous text-shape heuristic as a fallback/complement.
        if not text:
            return 0.0
        confidence = 0.8
        if len(text) < 3:
            confidence -= 0.2
        special_chars = sum(1 for c in text if not c.isalnum() and c not in " .,!?")
        if special_chars > len(text) * 0.3:
            confidence -= 0.1

        if seg:
            avg_logprob = seg.get("avg_logprob")
            if avg_logprob is not None:
                # avg_logprob typically ranges roughly from 0 (confident) to -1 or below
                # (unreliable); clamp and rescale to a 0-1 penalty-free/penalized blend.
                confidence *= max(0.0, min(1.0, 1.0 + float(avg_logprob)))
            no_speech_prob = seg.get("no_speech_prob")
            if no_speech_prob is not None:
                # no_speech_prob is often high (0.7-0.9) on real, correctly decoded speech over
                # background music or soft voice. Like Whisper's own silence rule (no_speech_prob
                # high AND avg_logprob low), only penalize it in proportion to how uncertain the
                # decoding is: fully at avg_logprob <= -0.5, not at all at avg_logprob = 0.
                uncertainty = 1.0
                if avg_logprob is not None:
                    uncertainty = max(0.0, min(1.0, -float(avg_logprob) / 0.5))
                confidence *= max(0.0, 1.0 - float(no_speech_prob) * uncertainty)

        return max(0.0, min(1.0, confidence))


class SpeechRecognizerFactory:
    @staticmethod
    def create_recognizer(config: Dict[str, Any]) -> WhisperRecognizer:
        engine = config.get("speech_recognition", {}).get("engine", "whisper").lower()
        if engine != "whisper":
            raise ValueError(f"Unsupported speech recognition engine: {engine} (only 'whisper' is supported)")
        return WhisperRecognizer(config)
