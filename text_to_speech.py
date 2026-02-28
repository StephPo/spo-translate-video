import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from abc import ABC, abstractmethod
import time
import requests
import subprocess
from pydub import AudioSegment

@dataclass
class TTSSegment:
    text: str
    audio_path: str
    duration: float
    processing_time: float
    voice_name: Optional[str] = None

@dataclass
class TTSResult:
    success: bool
    segments: Optional[List[TTSSegment]] = None
    combined_audio_path: Optional[str] = None
    total_duration: Optional[float] = None
    total_processing_time: Optional[float] = None
    error: Optional[str] = None

class TextToSpeech(ABC):
    """Abstract base class for TTS engines"""
    
    @abstractmethod
    def synthesize(self, text: str, output_path: str) -> TTSSegment:
        pass
    
    @abstractmethod
    def synthesize_segments(self, segments: List[str], output_dir: str) -> TTSResult:
        pass

class GoogleTTS(TextToSpeech):
    """Google Cloud Text-to-Speech"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tts_config = config.get('tts', {})
        self.voice_config = self.tts_config.get('voice', {})
        
        # API settings
        self.api_key = os.environ.get('GOOGLE_TTS_API_KEY')
        self.credentials_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        
        # Voice settings
        self.language = 'fr-FR'  # French
        self.voice_name = self.voice_config.get('name', '')
        self.gender = self.voice_config.get('gender', 'female')
        self.age = self.voice_config.get('age', 'adult')
        self.rate = self.voice_config.get('rate', 1.0)
        self.pitch = self.voice_config.get('pitch', 0)
        self.volume = self.voice_config.get('volume', 1.0)
        
        # Audio settings
        self.output_format = self.tts_config.get('output_format', 'wav')
        self.sample_rate = self.tts_config.get('sample_rate', 22050)
        
        if not self.credentials_path:
            self.logger.warning("Google Cloud credentials not found in environment variables")
    
    def synthesize(self, text: str, output_path: str) -> TTSSegment:
        """Synthesize speech from text"""
        start_time = time.time()
        
        try:
            from google.cloud import texttospeech_v1beta1 as tts
            
            if not self.credentials_path:
                raise Exception("Google Cloud credentials not configured")
            
            client = tts.TextToSpeechClient()
            
            # Build voice selection
            voice = tts.VoiceSelectionParams(
                language_code=self.language,
                name=self.voice_name if self.voice_name else None,
                ssml_gender=tts.SsmlVoiceGender.FEMALE if self.gender == 'female' else tts.SsmlVoiceGender.MALE
            )
            
            # Build audio config
            audio_config = tts.AudioConfig(
                audio_encoding=tts.AudioEncoding.LINEAR16 if self.output_format == 'wav' else tts.AudioEncoding.MP3,
                sample_rate_hertz=self.sample_rate,
                speaking_rate=self.rate,
                pitch=self.pitch,
                volume_gain_db=self.volume * 20 - 20  # Convert to dB
            )
            
            # Synthesize speech
            synthesis_input = tts.SynthesisInput(text=text)
            
            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config
            )
            
            # Save audio file
            with open(output_path, 'wb') as out:
                out.write(response.audio_content)
            
            # Get duration
            audio = AudioSegment.from_file(output_path)
            duration = len(audio) / 1000.0  # Convert to seconds
            
            processing_time = time.time() - start_time
            
            return TTSSegment(
                text=text,
                audio_path=output_path,
                duration=duration,
                processing_time=processing_time,
                voice_name=self.voice_name
            )
            
        except ImportError:
            raise Exception("Google Cloud TTS library not installed. Install with: pip install google-cloud-texttospeech")
        except Exception as e:
            self.logger.error(f"Google TTS error: {str(e)}")
            raise
    
    def synthesize_segments(self, segments: List[str], output_dir: str) -> TTSResult:
        """Synthesize multiple text segments"""
        start_time = time.time()
        tts_segments = []
        total_duration = 0
        
        try:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            for i, text in enumerate(segments):
                if not text.strip():
                    continue
                
                self.logger.info(f"Synthesizing segment {i+1}/{len(segments)}")
                
                output_path = output_dir / f"tts_segment_{i:03d}.{self.output_format}"
                
                try:
                    segment = self.synthesize(text, str(output_path))
                    tts_segments.append(segment)
                    total_duration += segment.duration
                except Exception as e:
                    self.logger.warning(f"Failed to synthesize segment {i+1}: {str(e)}")
                    # Create empty audio segment as fallback
                    empty_path = output_dir / f"tts_segment_{i:03d}_empty.{self.output_format}"
                    silence = AudioSegment.silent(duration=1000)  # 1 second of silence
                    silence.export(empty_path, format=self.output_format)
                    
                    tts_segments.append(TTSSegment(
                        text=text,
                        audio_path=str(empty_path),
                        duration=1.0,
                        processing_time=0.0,
                        voice_name=self.voice_name
                    ))
                    total_duration += 1.0
            
            # Combine all segments
            combined_audio_path = output_dir / f"combined_audio.{self.output_format}"
            self._combine_audio_files(tts_segments, str(combined_audio_path))
            
            total_processing_time = time.time() - start_time
            
            return TTSResult(
                success=True,
                segments=tts_segments,
                combined_audio_path=str(combined_audio_path),
                total_duration=total_duration,
                total_processing_time=total_processing_time
            )
            
        except Exception as e:
            self.logger.error(f"Error synthesizing segments: {str(e)}")
            return TTSResult(success=False, error=str(e))
    
    def _combine_audio_files(self, segments: List[TTSSegment], output_path: str):
        """Combine multiple audio files into one"""
        try:
            combined = AudioSegment.empty()
            
            for segment in segments:
                if Path(segment.audio_path).exists():
                    audio = AudioSegment.from_file(segment.audio_path)
                    combined += audio
            
            combined.export(output_path, format=self.output_format)
            self.logger.info(f"Combined audio saved to: {output_path}")
            
        except Exception as e:
            self.logger.error(f"Error combining audio files: {str(e)}")
            raise

class AzureTTS(TextToSpeech):
    """Azure Cognitive Services Text-to-Speech"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tts_config = config.get('tts', {})
        self.voice_config = self.tts_config.get('voice', {})
        
        # API settings
        self.api_key = self.tts_config.get('api_keys', {}).get('azure') or os.environ.get('AZURE_TTS_API_KEY')
        self.region = os.environ.get('AZURE_TTS_REGION', 'eastus')
        
        # Voice settings
        self.language = 'fr-FR'
        self.voice_name = self.voice_config.get('name', 'fr-FR-DeniseNeural')
        self.rate = self.voice_config.get('rate', 1.0)
        self.pitch = self.voice_config.get('pitch', 0)
        self.volume = self.voice_config.get('volume', 1.0)
        
        # Audio settings
        self.output_format = self.tts_config.get('output_format', 'wav')
        self.sample_rate = self.tts_config.get('sample_rate', 22050)
        
        if not self.api_key:
            self.logger.warning("Azure TTS API key not configured")
    
    def synthesize(self, text: str, output_path: str) -> TTSSegment:
        """Synthesize speech using Azure TTS"""
        start_time = time.time()
        
        try:
            if not self.api_key:
                raise Exception("Azure TTS API key not configured")
            
            # Build SSML
            ssml = f"""
            <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{self.language}">
                <voice name="{self.voice_name}">
                    <prosody rate="{self.rate}" pitch="{self.pitch:+d}Hz" volume="{self.volume}">
                        {text}
                    </prosody>
                </voice>
            </speak>
            """
            
            # API endpoint
            url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
            
            headers = {
                'Ocp-Apim-Subscription-Key': self.api_key,
                'Content-Type': 'application/ssml+xml',
                'X-Microsoft-OutputFormat': f'audio-16khz-{self.sample_rate}bit-mono-{self.output_format}'
            }
            
            # Make request
            response = requests.post(url, headers=headers, data=ssml.encode('utf-8'))
            response.raise_for_status()
            
            # Save audio
            with open(output_path, 'wb') as f:
                f.write(response.content)
            
            # Get duration
            audio = AudioSegment.from_file(output_path)
            duration = len(audio) / 1000.0
            
            processing_time = time.time() - start_time
            
            return TTSSegment(
                text=text,
                audio_path=output_path,
                duration=duration,
                processing_time=processing_time,
                voice_name=self.voice_name
            )
            
        except Exception as e:
            self.logger.error(f"Azure TTS error: {str(e)}")
            raise
    
    def synthesize_segments(self, segments: List[str], output_dir: str) -> TTSResult:
        """Synthesize multiple text segments"""
        start_time = time.time()
        tts_segments = []
        total_duration = 0
        
        try:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            for i, text in enumerate(segments):
                if not text.strip():
                    continue
                
                self.logger.info(f"Synthesizing segment {i+1}/{len(segments)} with Azure TTS")
                
                output_path = output_dir / f"tts_segment_{i:03d}.{self.output_format}"
                
                try:
                    segment = self.synthesize(text, str(output_path))
                    tts_segments.append(segment)
                    total_duration += segment.duration
                except Exception as e:
                    self.logger.warning(f"Failed to synthesize segment {i+1}: {str(e)}")
                    # Create empty audio segment as fallback
                    empty_path = output_dir / f"tts_segment_{i:03d}_empty.{self.output_format}"
                    silence = AudioSegment.silent(duration=1000)
                    silence.export(empty_path, format=self.output_format)
                    
                    tts_segments.append(TTSSegment(
                        text=text,
                        audio_path=str(empty_path),
                        duration=1.0,
                        processing_time=0.0,
                        voice_name=self.voice_name
                    ))
                    total_duration += 1.0
            
            # Combine all segments
            combined_audio_path = output_dir / f"combined_audio.{self.output_format}"
            self._combine_audio_files(tts_segments, str(combined_audio_path))
            
            total_processing_time = time.time() - start_time
            
            return TTSResult(
                success=True,
                segments=tts_segments,
                combined_audio_path=str(combined_audio_path),
                total_duration=total_duration,
                total_processing_time=total_processing_time
            )
            
        except Exception as e:
            self.logger.error(f"Error synthesizing segments with Azure TTS: {str(e)}")
            return TTSResult(success=False, error=str(e))
    
    def _combine_audio_files(self, segments: List[TTSSegment], output_path: str):
        """Combine multiple audio files into one"""
        try:
            combined = AudioSegment.empty()
            
            for segment in segments:
                if Path(segment.audio_path).exists():
                    audio = AudioSegment.from_file(segment.audio_path)
                    combined += audio
            
            combined.export(output_path, format=self.output_format)
            self.logger.info(f"Combined audio saved to: {output_path}")
            
        except Exception as e:
            self.logger.error(f"Error combining audio files: {str(e)}")
            raise

class Pyttsx3TTS(TextToSpeech):
    """Offline TTS using pyttsx3"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tts_config = config.get('tts', {})
        self.voice_config = self.tts_config.get('voice', {})
        
        # Voice settings
        self.rate = self.voice_config.get('rate', 200)  # Words per minute
        self.volume = self.voice_config.get('volume', 1.0)
        
        # Audio settings
        self.output_format = self.tts_config.get('output_format', 'wav')
        
        # Initialize engine
        try:
            import pyttsx3
            self.engine = pyttsx3.init()
            
            # Set voice properties
            voices = self.engine.getProperty('voices')
            french_voice = None
            
            for voice in voices:
                if 'french' in voice.name.lower() or 'fr' in voice.id.lower():
                    french_voice = voice
                    break
            
            if french_voice:
                self.engine.setProperty('voice', french_voice.id)
            
            self.engine.setProperty('rate', self.rate)
            self.engine.setProperty('volume', self.volume)
            
        except ImportError:
            raise Exception("pyttsx3 not installed. Install with: pip install pyttsx3")
    
    def synthesize(self, text: str, output_path: str) -> TTSSegment:
        """Synthesize speech using pyttsx3"""
        start_time = time.time()
        
        try:
            # Save to file
            self.engine.save_to_file(text, output_path)
            self.engine.runAndWait()
            
            # Get duration
            if Path(output_path).exists():
                audio = AudioSegment.from_file(output_path)
                duration = len(audio) / 1000.0
            else:
                duration = 0
            
            processing_time = time.time() - start_time
            
            return TTSSegment(
                text=text,
                audio_path=output_path,
                duration=duration,
                processing_time=processing_time,
                voice_name="pyttsx3_french"
            )
            
        except Exception as e:
            self.logger.error(f"pyttsx3 TTS error: {str(e)}")
            raise
    
    def synthesize_segments(self, segments: List[str], output_dir: str) -> TTSResult:
        """Synthesize multiple text segments"""
        start_time = time.time()
        tts_segments = []
        total_duration = 0
        
        try:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            for i, text in enumerate(segments):
                if not text.strip():
                    continue
                
                self.logger.info(f"Synthesizing segment {i+1}/{len(segments)} with pyttsx3")
                
                output_path = output_dir / f"tts_segment_{i:03d}.{self.output_format}"
                
                try:
                    segment = self.synthesize(text, str(output_path))
                    tts_segments.append(segment)
                    total_duration += segment.duration
                except Exception as e:
                    self.logger.warning(f"Failed to synthesize segment {i+1}: {str(e)}")
                    # Create empty audio segment as fallback
                    empty_path = output_dir / f"tts_segment_{i:03d}_empty.{self.output_format}"
                    silence = AudioSegment.silent(duration=1000)
                    silence.export(empty_path, format=self.output_format)
                    
                    tts_segments.append(TTSSegment(
                        text=text,
                        audio_path=str(empty_path),
                        duration=1.0,
                        processing_time=0.0,
                        voice_name="pyttsx3_french"
                    ))
                    total_duration += 1.0
            
            # Combine all segments
            combined_audio_path = output_dir / f"combined_audio.{self.output_format}"
            self._combine_audio_files(tts_segments, str(combined_audio_path))
            
            total_processing_time = time.time() - start_time
            
            return TTSResult(
                success=True,
                segments=tts_segments,
                combined_audio_path=str(combined_audio_path),
                total_duration=total_duration,
                total_processing_time=total_processing_time
            )
            
        except Exception as e:
            self.logger.error(f"Error synthesizing segments with pyttsx3: {str(e)}")
            return TTSResult(success=False, error=str(e))
    
    def _combine_audio_files(self, segments: List[TTSSegment], output_path: str):
        """Combine multiple audio files into one"""
        try:
            combined = AudioSegment.empty()
            
            for segment in segments:
                if Path(segment.audio_path).exists():
                    audio = AudioSegment.from_file(segment.audio_path)
                    combined += audio
            
            combined.export(output_path, format=self.output_format)
            self.logger.info(f"Combined audio saved to: {output_path}")
            
        except Exception as e:
            self.logger.error(f"Error combining audio files: {str(e)}")
            raise

class TTSFactory:
    """Factory for creating TTS engines"""
    
    @staticmethod
    def create_tts(config: Dict[str, Any]) -> TextToSpeech:
        engine = config.get('tts', {}).get('engine', 'google_tts').lower()
        
        if engine == 'google_tts':
            return GoogleTTS(config)
        elif engine == 'azure_tts':
            return AzureTTS(config)
        elif engine == 'pyttsx3':
            return Pyttsx3TTS(config)
        else:
            raise ValueError(f"Unsupported TTS engine: {engine}")

def main():
    """Test function for TTS"""
    logging.basicConfig(level=logging.INFO)
    
    # Sample config
    config = {
        'tts': {
            'engine': 'pyttsx3',  # Use offline engine for testing
            'output_format': 'wav',
            'sample_rate': 22050,
            'voice': {
                'rate': 1.0,
                'pitch': 0,
                'volume': 1.0
            }
        }
    }
    
    # Create TTS engine
    tts = TTSFactory.create_tts(config)
    
    # Test synthesis
    test_text = "Bonjour, comment allez-vous? Je suis heureux de vous rencontrer."
    
    try:
        segment = tts.synthesize(test_text, "test_output.wav")
        print(f"TTS successful: {segment.audio_path}, Duration: {segment.duration}s")
    except Exception as e:
        print(f"TTS error: {str(e)}")
    
    print("TTS engine ready for use")

if __name__ == "__main__":
    main()
