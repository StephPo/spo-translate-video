import os
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
import ffmpeg

@dataclass
class AudioSegment:
    start_time: float
    end_time: float
    audio_path: str
    duration: float

@dataclass
class AudioProcessingResult:
    success: bool
    audio_path: Optional[str] = None
    segments: Optional[list] = None
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class AudioProcessor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.temp_dir = Path(config.get('video', {}).get('temp_directory', './temp'))
        self.audio_config = config.get('audio', {})
        
        # FFmpeg settings
        self.ffmpeg_path = config.get('video', {}).get('ffmpeg_path', 'ffmpeg')
        
        # Audio processing parameters
        self.extraction_format = self.audio_config.get('extraction_format', 'wav')
        self.sample_rate = self.audio_config.get('sample_rate', 16000)
        self.channels = self.audio_config.get('channels', 1)
        self.normalize_volume = self.audio_config.get('normalize_volume', True)

    def extract_audio_segment_from_video(self, video_path: str, start_time: float, end_time: float, tag: str) -> AudioProcessingResult:
        """Extract audio from a specific time range of a video file (used for chapter selection)."""
        try:
            video_path = Path(video_path)
            if not video_path.exists():
                return AudioProcessingResult(success=False, error=f"Video file not found: {video_path}")

            if end_time <= start_time:
                return AudioProcessingResult(success=False, error=f"Invalid segment range: {start_time} - {end_time}")

            safe_tag = "".join(c for c in tag if c.isalnum() or c in ("_", "-"))
            safe_tag = safe_tag or "segment"
            audio_filename = f"{video_path.stem}_{safe_tag}_audio.{self.extraction_format}"
            audio_path = self.temp_dir / audio_filename

            input_stream = ffmpeg.input(str(video_path), ss=float(start_time), t=float(end_time - start_time))
            output_stream = input_stream.audio

            if self.channels == 1:
                output_stream = output_stream.filter('aformat', channel_layouts='mono')

            output_stream = output_stream.filter('aresample', self.sample_rate)

            if self.normalize_volume:
                output_stream = output_stream.filter('loudnorm', i=-16.0, tp=-1.5, lra=11.0)

            output_stream = output_stream.output(str(audio_path))
            ffmpeg.run(output_stream, overwrite_output=True, quiet=True)

            if not audio_path.exists():
                return AudioProcessingResult(success=False, error="Failed to extract audio segment")

            metadata = self._get_audio_metadata(str(audio_path))
            self.logger.info(f"Audio segment extracted successfully: {audio_path}")

            return AudioProcessingResult(success=True, audio_path=str(audio_path), metadata=metadata)
        except Exception as e:
            self.logger.error(f"Error extracting audio segment: {str(e)}")
            return AudioProcessingResult(success=False, error=str(e))

    def extract_audio_from_video(self, video_path: str) -> AudioProcessingResult:
        """Extract audio from video file"""
        try:
            video_path = Path(video_path)
            if not video_path.exists():
                return AudioProcessingResult(success=False, error=f"Video file not found: {video_path}")
            
            # Generate output audio filename
            audio_filename = f"{video_path.stem}_audio.{self.extraction_format}"
            audio_path = self.temp_dir / audio_filename
            
            # Build FFmpeg command
            input_stream = ffmpeg.input(str(video_path))
            
            # Apply audio processing
            output_stream = input_stream.audio
            
            # Convert to mono if specified
            if self.channels == 1:
                output_stream = output_stream.filter('aformat', channel_layouts='mono')
            
            # Set sample rate
            output_stream = output_stream.filter('aresample', self.sample_rate)
            
            # Normalize volume if specified
            if self.normalize_volume:
                output_stream = output_stream.filter('loudnorm', i=-16.0, tp=-1.5, lra=11.0)
            
            # Output to file
            output_stream = output_stream.output(str(audio_path))
            
            # Run FFmpeg
            ffmpeg.run(output_stream, overwrite_output=True, quiet=True)
            
            if not audio_path.exists():
                return AudioProcessingResult(success=False, error="Failed to extract audio")
            
            # Get audio metadata
            metadata = self._get_audio_metadata(str(audio_path))
            
            self.logger.info(f"Audio extracted successfully: {audio_path}")
            
            return AudioProcessingResult(
                success=True,
                audio_path=str(audio_path),
                metadata=metadata
            )
            
        except Exception as e:
            self.logger.error(f"Error extracting audio: {str(e)}")
            return AudioProcessingResult(success=False, error=str(e))
    
    def segment_audio(self, audio_path: str, max_segment_length: int = 30) -> AudioProcessingResult:
        """Segment audio into smaller chunks for processing"""
        try:
            audio_path = Path(audio_path)
            if not audio_path.exists():
                return AudioProcessingResult(success=False, error=f"Audio file not found: {audio_path}")
            
            # Get audio duration
            duration = self._get_audio_duration(str(audio_path))
            
            if duration <= max_segment_length:
                # Audio is short enough, return as single segment
                return AudioProcessingResult(
                    success=True,
                    audio_path=str(audio_path),
                    segments=[AudioSegment(0, duration, str(audio_path), duration)]
                )
            
            segments = []
            segment_count = int(duration // max_segment_length) + 1
            
            for i in range(segment_count):
                start_time = i * max_segment_length
                end_time = min((i + 1) * max_segment_length, duration)
                
                segment_filename = f"{audio_path.stem}_segment_{i:03d}.{self.extraction_format}"
                segment_path = self.temp_dir / segment_filename
                
                # Extract segment using FFmpeg
                input_stream = ffmpeg.input(str(audio_path), ss=start_time, t=end_time - start_time)
                output_stream = input_stream.output(str(segment_path))
                ffmpeg.run(output_stream, overwrite_output=True, quiet=True)
                
                if segment_path.exists():
                    segments.append(AudioSegment(
                        start_time=start_time,
                        end_time=end_time,
                        audio_path=str(segment_path),
                        duration=end_time - start_time
                    ))
            
            self.logger.info(f"Audio segmented into {len(segments)} chunks")
            
            return AudioProcessingResult(
                success=True,
                audio_path=str(audio_path),
                segments=segments
            )
            
        except Exception as e:
            self.logger.error(f"Error segmenting audio: {str(e)}")
            return AudioProcessingResult(success=False, error=str(e))
    
    def merge_audio_segments(self, segments: list, output_path: str) -> AudioProcessingResult:
        """Merge audio segments back into a single file"""
        try:
            if not segments:
                return AudioProcessingResult(success=False, error="No segments to merge")
            
            # Create input streams for all segments
            inputs = [ffmpeg.input(segment.audio_path) for segment in segments]
            
            # Concatenate all segments
            joined = ffmpeg.concat(*inputs, v=0, a=1)
            
            # Output to file
            output_stream = joined.output(output_path)
            ffmpeg.run(output_stream, overwrite_output=True, quiet=True)
            
            if not Path(output_path).exists():
                return AudioProcessingResult(success=False, error="Failed to merge audio segments")
            
            self.logger.info(f"Audio segments merged successfully: {output_path}")
            
            return AudioProcessingResult(success=True, audio_path=output_path)
            
        except Exception as e:
            self.logger.error(f"Error merging audio segments: {str(e)}")
            return AudioProcessingResult(success=False, error=str(e))
    
    def _get_audio_metadata(self, audio_path: str) -> Dict[str, Any]:
        """Get audio file metadata"""
        try:
            probe = ffmpeg.probe(audio_path)
            audio_info = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)
            
            if audio_info:
                return {
                    'duration': float(audio_info.get('duration', 0)),
                    'sample_rate': int(audio_info.get('sample_rate', 0)),
                    'channels': int(audio_info.get('channels', 0)),
                    'codec': audio_info.get('codec_name', ''),
                    'bit_rate': audio_info.get('bit_rate', ''),
                }
            else:
                return {'duration': 0}
                
        except Exception as e:
            self.logger.error(f"Error getting audio metadata: {str(e)}")
            return {'duration': 0}
    
    def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds"""
        metadata = self._get_audio_metadata(audio_path)
        return metadata.get('duration', 0)
    
    def detect_silence(self, audio_path: str, silence_threshold: float = -40.0, min_silence_duration: float = 1.0) -> list:
        """Detect silence segments in audio"""
        try:
            # Use FFmpeg silencedetect filter
            cmd = [
                self.ffmpeg_path,
                '-i', audio_path,
                '-af', f'silencedetect=noise={silence_threshold}dB:duration={min_silence_duration}',
                '-f', 'null',
                '-'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            silence_segments = []
            
            # Parse FFmpeg output for silence detection
            lines = result.stderr.split('\n')
            for i, line in enumerate(lines):
                if 'silence_start' in line:
                    start_time = float(line.split('silence_start:')[1].strip())
                    if i + 1 < len(lines) and 'silence_end' in lines[i + 1]:
                        end_time = float(lines[i + 1].split('silence_end:')[1].strip())
                        silence_segments.append((start_time, end_time))
            
            return silence_segments
            
        except Exception as e:
            self.logger.error(f"Error detecting silence: {str(e)}")
            return []
    
    def cleanup_temp_segments(self, segments: list):
        """Clean up temporary segment files"""
        try:
            for segment in segments:
                segment_path = Path(segment.audio_path)
                if segment_path.exists():
                    segment_path.unlink()
            self.logger.info("Temporary audio segments cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up segments: {str(e)}")

def main():
    """Test function for audio processor"""
    logging.basicConfig(level=logging.INFO)
    
    # Sample config
    config = {
        'video': {'temp_directory': './temp', 'ffmpeg_path': 'ffmpeg'},
        'audio': {
            'extraction_format': 'wav',
            'sample_rate': 16000,
            'channels': 1,
            'normalize_volume': True
        }
    }
    
    processor = AudioProcessor(config)
    
    # Test audio extraction (replace with actual video path for testing)
    # result = processor.extract_audio_from_video("path/to/video.mp4")
    # if result.success:
    #     print(f"Audio extracted: {result.audio_path}")
    # else:
    #     print(f"Error: {result.error}")
    
    print("Audio processor ready for use")

if __name__ == "__main__":
    main()
