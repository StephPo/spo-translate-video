import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import whisper
import torch
from abc import ABC, abstractmethod

@dataclass
class TranscriptionSegment:
    start_time: float
    end_time: float
    text: str
    confidence: float
    speaker_id: Optional[int] = None

@dataclass
class TranscriptionResult:
    success: bool
    segments: Optional[List[TranscriptionSegment]] = None
    full_text: Optional[str] = None
    language: Optional[str] = None
    error: Optional[str] = None

class SpeechRecognizer(ABC):
    """Abstract base class for speech recognition engines"""
    
    @abstractmethod
    def transcribe(self, audio_path: str, language: str = "ja") -> TranscriptionResult:
        pass

class WhisperRecognizer(SpeechRecognizer):
    """Whisper-based speech recognition"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.speech_config = config.get('speech_recognition', {})
        
        # Whisper settings
        self.model_name = self.speech_config.get('whisper_model', 'large')
        self.language = self.speech_config.get('language', 'ja')
        self.confidence_threshold = self.speech_config.get('confidence_threshold', 0.5)
        self.enable_diarization = self.speech_config.get('enable_diarization', False)
        
        # GPU settings
        self.gpu_acceleration = config.get('advanced', {}).get('gpu_acceleration', False)
        self.gpu_device = config.get('advanced', {}).get('gpu_device', 0)
        
        # Load model
        self.model = None
        self._load_model()
    
    def _load_model(self):
        """Load Whisper model"""
        try:
            device = f"cuda:{self.gpu_device}" if self.gpu_acceleration and torch.cuda.is_available() else "cpu"
            self.logger.info(f"Loading Whisper model '{self.model_name}' on device: {device}")
            
            self.model = whisper.load_model(self.model_name, device=device)
            self.logger.info("Whisper model loaded successfully")
            
        except Exception as e:
            self.logger.error(f"Error loading Whisper model: {str(e)}")
            raise
    
    def transcribe(self, audio_path: str, language: str = "ja") -> TranscriptionResult:
        """Transcribe audio using Whisper"""
        try:
            audio_path = Path(audio_path)
            if not audio_path.exists():
                return TranscriptionResult(success=False, error=f"Audio file not found: {audio_path}")
            
            # Transcribe with Whisper
            self.logger.info(f"Transcribing audio: {audio_path}")
            
            result = self.model.transcribe(
                str(audio_path),
                language=language,
                task="transcribe",
                verbose=False,
                fp16=self.gpu_acceleration,
                word_timestamps=True
            )
            
            # Process segments
            segments = []
            for segment in result.get('segments', []):
                transcription_segment = TranscriptionSegment(
                    start_time=segment.get('start', 0),
                    end_time=segment.get('end', 0),
                    text=segment.get('text', '').strip(),
                    confidence=self._calculate_confidence(segment),
                    speaker_id=None  # Whisper doesn't provide speaker diarization
                )
                
                # Filter by confidence threshold
                if transcription_segment.confidence >= self.confidence_threshold:
                    segments.append(transcription_segment)
            
            full_text = result.get('text', '').strip()
            detected_language = result.get('language', language)
            
            self.logger.info(f"Transcription completed: {len(segments)} segments, language: {detected_language}")
            
            return TranscriptionResult(
                success=True,
                segments=segments,
                full_text=full_text,
                language=detected_language
            )
            
        except Exception as e:
            self.logger.error(f"Error transcribing audio: {str(e)}")
            return TranscriptionResult(success=False, error=str(e))
    
    def _calculate_confidence(self, segment: Dict[str, Any]) -> float:
        """Calculate confidence score for a segment"""
        # Whisper doesn't provide direct confidence scores
        # This is a heuristic based on segment characteristics
        text = segment.get('text', '').strip()
        
        if not text:
            return 0.0
        
        # Base confidence on text length and presence of special characters
        confidence = 0.8  # Base confidence
        
        # Reduce confidence for very short segments
        if len(text) < 3:
            confidence -= 0.2
        
        # Reduce confidence for segments with many special characters
        special_chars = sum(1 for c in text if not c.isalnum() and c not in ' .,!?')
        if special_chars > len(text) * 0.3:
            confidence -= 0.1
        
        return max(0.0, min(1.0, confidence))

class GoogleSpeechRecognizer(SpeechRecognizer):
    """Google Cloud Speech-to-Text recognizer"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.speech_config = config.get('speech_recognition', {})
        
        # Check for Google Cloud credentials
        self.credentials_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        if not self.credentials_path:
            self.logger.warning("Google Cloud credentials not found in environment variables")
    
    def transcribe(self, audio_path: str, language: str = "ja") -> TranscriptionResult:
        """Transcribe audio using Google Cloud Speech-to-Text"""
        try:
            from google.cloud import speech_v1p1beta1 as speech
            from google.cloud.speech_v1p1beta1 import types
            
            if not self.credentials_path:
                return TranscriptionResult(
                    success=False, 
                    error="Google Cloud credentials not configured"
                )
            
            client = speech.SpeechClient()
            
            # Load audio file
            with open(audio_path, 'rb') as audio_file:
                content = audio_file.read()
            
            # Configure recognition
            config = types.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code=f"{language}-JP",
                enable_automatic_punctuation=True,
                enable_word_time_offsets=True,
                enable_speaker_diarization=self.speech_config.get('enable_diarization', False),
                diarization_speaker_count=2 if self.speech_config.get('enable_diarization', False) else None,
                model="latest_long"
            )
            
            audio = types.RecognitionAudio(content=content)
            
            # Perform transcription
            self.logger.info(f"Transcribing audio with Google Speech: {audio_path}")
            response = client.recognize(config=config, audio=audio)
            
            # Process results
            segments = []
            full_text = ""
            
            for result in response.results:
                alternative = result.alternatives[0]
                segment_text = alternative.transcript.strip()
                full_text += segment_text + " "
                
                # Create segment (Google doesn't provide precise timestamps without word-level info)
                if hasattr(alternative, 'words') and alternative.words:
                    start_time = alternative.words[0].start_time.total_seconds()
                    end_time = alternative.words[-1].end_time.total_seconds()
                    confidence = alternative.confidence
                    
                    segments.append(TranscriptionSegment(
                        start_time=start_time,
                        end_time=end_time,
                        text=segment_text,
                        confidence=confidence,
                        speaker_id=getattr(alternative.words[0], 'speaker_tag', None) if self.speech_config.get('enable_diarization', False) else None
                    ))
            
            self.logger.info(f"Google transcription completed: {len(segments)} segments")
            
            return TranscriptionResult(
                success=True,
                segments=segments,
                full_text=full_text.strip(),
                language=language
            )
            
        except ImportError:
            return TranscriptionResult(
                success=False, 
                error="Google Cloud Speech library not installed. Install with: pip install google-cloud-speech"
            )
        except Exception as e:
            self.logger.error(f"Error with Google Speech transcription: {str(e)}")
            return TranscriptionResult(success=False, error=str(e))

class SpeechRecognizerFactory:
    """Factory for creating speech recognizers"""
    
    @staticmethod
    def create_recognizer(config: Dict[str, Any]) -> SpeechRecognizer:
        engine = config.get('speech_recognition', {}).get('engine', 'whisper').lower()
        
        if engine == 'whisper':
            return WhisperRecognizer(config)
        elif engine == 'google':
            return GoogleSpeechRecognizer(config)
        else:
            raise ValueError(f"Unsupported speech recognition engine: {engine}")

def main():
    """Test function for speech recognizer"""
    logging.basicConfig(level=logging.INFO)
    
    # Sample config
    config = {
        'speech_recognition': {
            'engine': 'whisper',
            'whisper_model': 'base',
            'language': 'ja',
            'confidence_threshold': 0.5,
            'enable_diarization': False
        },
        'advanced': {
            'gpu_acceleration': False,
            'gpu_device': 0
        }
    }
    
    # Create recognizer
    recognizer = SpeechRecognizerFactory.create_recognizer(config)
    
    # Test transcription (replace with actual audio path for testing)
    # result = recognizer.transcribe("path/to/audio.wav")
    # if result.success:
    #     print(f"Transcription successful: {len(result.segments)} segments")
    #     print(f"Full text: {result.full_text}")
    # else:
    #     print(f"Error: {result.error}")
    
    print("Speech recognizer ready for use")

if __name__ == "__main__":
    main()
