import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


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


class WhisperRecognizer:
    """Local speech-to-text transcription using openai-whisper."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        speech_cfg = config.get("speech_recognition", {})

        self.model_name = speech_cfg.get("whisper_model", "large")
        self.confidence_threshold = float(speech_cfg.get("confidence_threshold", 0.2))

        advanced_cfg = config.get("advanced", {})
        self.gpu_acceleration = bool(advanced_cfg.get("gpu_acceleration", False))
        self.gpu_device = int(advanced_cfg.get("gpu_device", 0))

        self.model = None

    def _load_model(self):
        if self.model is not None:
            return
        import torch
        import whisper

        device = f"cuda:{self.gpu_device}" if self.gpu_acceleration and torch.cuda.is_available() else "cpu"
        self.logger.info(f"Loading Whisper model '{self.model_name}' on device: {device}")
        self.model = whisper.load_model(self.model_name, device=device)
        self.logger.info("Whisper model loaded")

    def transcribe(self, audio_path: str, language: str = "ja") -> TranscriptionResult:
        try:
            self._load_model()

            audio_path_p = Path(audio_path)
            if not audio_path_p.exists():
                return TranscriptionResult(success=False, error=f"Audio file not found: {audio_path_p}")

            self.logger.info(f"Transcribing audio: {audio_path_p}")
            result = self.model.transcribe(
                str(audio_path_p),
                language=language,
                task="transcribe",
                verbose=False,
                fp16=self.gpu_acceleration,
                word_timestamps=True,
            )

            segments: List[TranscriptionSegment] = []
            for seg in result.get("segments", []):
                text = (seg.get("text") or "").strip()
                confidence = self._estimate_confidence(text, seg)
                if confidence < self.confidence_threshold:
                    continue
                segments.append(
                    TranscriptionSegment(
                        start_time=float(seg.get("start", 0.0)),
                        end_time=float(seg.get("end", 0.0)),
                        text=text,
                        confidence=confidence,
                    )
                )

            return TranscriptionResult(
                success=True,
                segments=segments,
                full_text=(result.get("text") or "").strip(),
                language=result.get("language", language),
            )
        except Exception as e:
            self.logger.error(f"Error transcribing audio: {e}")
            return TranscriptionResult(success=False, error=str(e))

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
                confidence *= max(0.0, 1.0 - float(no_speech_prob))

        return max(0.0, min(1.0, confidence))


class SpeechRecognizerFactory:
    @staticmethod
    def create_recognizer(config: Dict[str, Any]) -> WhisperRecognizer:
        engine = config.get("speech_recognition", {}).get("engine", "whisper").lower()
        if engine != "whisper":
            raise ValueError(f"Unsupported speech recognition engine: {engine} (only 'whisper' is supported)")
        return WhisperRecognizer(config)
