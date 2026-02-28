import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import subprocess
import ffmpeg
from pydub import AudioSegment

@dataclass
class VideoProcessingResult:
    success: bool
    output_path: Optional[str] = None
    duration: Optional[float] = None
    resolution: Optional[tuple] = None
    file_size: Optional[int] = None
    error: Optional[str] = None

class VideoProcessor:
    """Video processing and reconstruction with new audio"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.video_config = config.get('video', {})
        self.output_config = config.get('output', {})
        
        # FFmpeg settings
        self.ffmpeg_path = self.video_config.get('ffmpeg_path', 'ffmpeg')
        self.temp_dir = Path(self.video_config.get('temp_directory', './temp'))
        self.output_dir = Path(self.output_config.get('output_directory', './output'))
        
        # Encoding settings
        self.encoding_config = self.video_config.get('encoding', {})
        self.video_codec = self.encoding_config.get('codec', 'libx264')
        self.preset = self.encoding_config.get('preset', 'medium')
        self.crf = self.encoding_config.get('crf', 23)
        self.audio_codec = self.encoding_config.get('audio_codec', 'aac')
        self.audio_bitrate = self.encoding_config.get('audio_bitrate', 128)
        
        # Output settings
        self.video_format = self.output_config.get('video_format', 'mp4')
        self.video_quality = self.output_config.get('video_quality', 'high')
        self.keep_original_audio = self.output_config.get('keep_original_audio', False)
        self.add_subtitles = self.output_config.get('add_subtitles', True)
        self.subtitle_format = self.output_config.get('subtitle_format', 'srt')
        
        # Processing settings
        self.threads = self.video_config.get('threads', 4)
        
        # Create directories
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def reconstruct_video(self, original_video_path: str, new_audio_path: str, 
                         transcription_segments: Optional[List] = None,
                         translation_segments: Optional[List] = None) -> VideoProcessingResult:
        """Reconstruct video with new French audio track"""
        try:
            original_video_path = Path(original_video_path)
            new_audio_path = Path(new_audio_path)
            
            if not original_video_path.exists():
                return VideoProcessingResult(success=False, error=f"Original video not found: {original_video_path}")
            
            if not new_audio_path.exists():
                return VideoProcessingResult(success=False, error=f"New audio not found: {new_audio_path}")
            
            # Generate output filename
            output_filename = f"{original_video_path.stem}_translated.{self.video_format}"
            output_path = self.output_dir / output_filename
            
            self.logger.info(f"Reconstructing video: {original_video_path} -> {output_path}")
            
            # Get video properties
            video_info = self._get_video_info(str(original_video_path))
            video_duration = video_info.get('duration', 0)
            
            # Adjust audio duration to match video
            adjusted_audio_path = self._adjust_audio_duration(
                str(new_audio_path), video_duration, str(original_video_path)
            )
            
            # Create subtitles if requested and segments are provided
            subtitle_path = None
            if self.add_subtitles and translation_segments:
                subtitle_path = self._create_subtitles(translation_segments, str(original_video_path))
            
            # Reconstruct video using FFmpeg
            self._reconstruct_with_ffmpeg(
                str(original_video_path), 
                adjusted_audio_path, 
                str(output_path),
                subtitle_path
            )
            
            # Get output video properties
            output_info = self._get_video_info(str(output_path))
            
            self.logger.info(f"Video reconstruction completed: {output_path}")
            
            return VideoProcessingResult(
                success=True,
                output_path=str(output_path),
                duration=output_info.get('duration', 0),
                resolution=output_info.get('resolution'),
                file_size=output_info.get('file_size', 0)
            )
            
        except Exception as e:
            self.logger.error(f"Error reconstructing video: {str(e)}")
            return VideoProcessingResult(success=False, error=str(e))
    
    def _get_video_info(self, video_path: str) -> Dict[str, Any]:
        """Get video file information"""
        try:
            probe = ffmpeg.probe(video_path)
            
            # Get video stream info
            video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
            audio_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)
            
            info = {
                'duration': float(probe['format'].get('duration', 0)),
                'file_size': int(probe['format'].get('size', 0)),
                'format_name': probe['format'].get('format_name', ''),
            }
            
            if video_stream:
                width = int(video_stream.get('width', 0))
                height = int(video_stream.get('height', 0))
                info['resolution'] = (width, height)
                info['video_codec'] = video_stream.get('codec_name', '')
                info['frame_rate'] = eval(video_stream.get('r_frame_rate', '0/1'))
            
            if audio_stream:
                info['audio_codec'] = audio_stream.get('codec_name', '')
                info['sample_rate'] = audio_stream.get('sample_rate', '')
                info['audio_channels'] = audio_stream.get('channels', 0)
            
            return info
            
        except Exception as e:
            self.logger.error(f"Error getting video info: {str(e)}")
            return {}
    
    def _adjust_audio_duration(self, audio_path: str, target_duration: float, video_path: str) -> str:
        """Adjust audio duration to match video duration"""
        try:
            # Get current audio duration
            audio = AudioSegment.from_file(audio_path)
            current_duration = len(audio) / 1000.0
            
            output_path = self.temp_dir / f"adjusted_audio_{Path(audio_path).stem}.wav"
            
            if current_duration < target_duration:
                # Audio is shorter than video, extend it
                self.logger.info(f"Extending audio from {current_duration}s to {target_duration}s")
                
                # Calculate how much to extend
                extension_needed = target_duration - current_duration
                
                # Add silence at the end
                silence = AudioSegment.silent(duration=int(extension_needed * 1000))
                extended_audio = audio + silence
                extended_audio.export(str(output_path), format='wav')
                
            elif current_duration > target_duration:
                # Audio is longer than video, trim it
                self.logger.info(f"Trimming audio from {current_duration}s to {target_duration}s")
                
                # Trim to target duration
                trimmed_audio = audio[:int(target_duration * 1000)]
                trimmed_audio.export(str(output_path), format='wav')
                
            else:
                # Durations match, just copy
                import shutil
                shutil.copy2(audio_path, output_path)
            
            return str(output_path)
            
        except Exception as e:
            self.logger.error(f"Error adjusting audio duration: {str(e)}")
            return audio_path  # Return original as fallback
    
    def _create_subtitles(self, translation_segments: List, video_path: str) -> str:
        """Create subtitle file from translation segments"""
        try:
            subtitle_path = self.temp_dir / f"subtitles_{Path(video_path).stem}.{self.subtitle_format}"
            
            with open(subtitle_path, 'w', encoding='utf-8') as f:
                for i, segment in enumerate(translation_segments, 1):
                    start_time = segment.start_time
                    end_time = segment.end_time
                    text = segment.translated_text
                    
                    # Format time for SRT
                    start_srt = self._format_time_srt(start_time)
                    end_srt = self._format_time_srt(end_time)
                    
                    f.write(f"{i}\n")
                    f.write(f"{start_srt} --> {end_srt}\n")
                    f.write(f"{text}\n\n")
            
            self.logger.info(f"Subtitles created: {subtitle_path}")
            return str(subtitle_path)
            
        except Exception as e:
            self.logger.error(f"Error creating subtitles: {str(e)}")
            return None
    
    def _format_time_srt(self, seconds: float) -> str:
        """Format time in seconds to SRT format (HH:MM:SS,mmm)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        milliseconds = int((seconds % 1) * 1000)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"
    
    def _reconstruct_with_ffmpeg(self, video_path: str, audio_path: str, output_path: str, subtitle_path: Optional[str] = None):
        """Reconstruct video using FFmpeg"""
        try:
            # Input streams
            video_input = ffmpeg.input(video_path)
            audio_input = ffmpeg.input(audio_path)
            
            # Video stream - copy original video
            video_stream = video_input.video
            
            # Audio stream - use new audio
            audio_stream = audio_input.audio
            
            # Apply encoding settings
            if self.video_codec == 'copy':
                video_stream = video_stream.output('copy')
            else:
                video_stream = video_stream.output(
                    vcodec=self.video_codec,
                    preset=self.preset,
                    crf=self.crf,
                    threads=self.threads
                )
            
            if self.audio_codec == 'copy':
                audio_stream = audio_stream.output('copy')
            else:
                audio_stream = audio_stream.output(
                    acodec=self.audio_codec,
                    audio_bitrate=f'{self.audio_bitrate}k'
                )
            
            # Combine streams
            output_streams = [video_stream, audio_stream]
            
            # Add subtitles if available
            if subtitle_path and Path(subtitle_path).exists():
                subtitle_input = ffmpeg.input(subtitle_path)
                subtitle_stream = subtitle_input.output('srt')
                output_streams.append(subtitle_stream)
            
            # Create output
            output = ffmpeg.output(*output_streams, output_path, 
                                 vcodec=self.video_codec if self.video_codec != 'copy' else 'copy',
                                 acodec=self.audio_codec if self.audio_codec != 'copy' else 'copy',
                                 preset=self.preset if self.video_codec != 'copy' else None,
                                 crf=self.crf if self.video_codec != 'copy' else None,
                                 audio_bitrate=f'{self.audio_bitrate}k' if self.audio_codec != 'copy' else None,
                                 threads=self.threads)
            
            # Run FFmpeg
            ffmpeg.run(output, overwrite_output=True, quiet=True)
            
            if not Path(output_path).exists():
                raise Exception("FFmpeg failed to create output file")
            
        except Exception as e:
            self.logger.error(f"FFmpeg reconstruction error: {str(e)}")
            raise
    
    def create_dual_audio_video(self, original_video_path: str, french_audio_path: str) -> VideoProcessingResult:
        """Create video with both original and French audio tracks"""
        try:
            original_video_path = Path(original_video_path)
            french_audio_path = Path(french_audio_path)
            
            # Generate output filename
            output_filename = f"{original_video_path.stem}_dual_audio.{self.video_format}"
            output_path = self.output_dir / output_filename
            
            self.logger.info(f"Creating dual audio video: {output_path}")
            
            # Get original audio from video
            original_audio_path = self.temp_dir / f"original_audio_{original_video_path.stem}.wav"
            
            # Extract original audio
            video_input = ffmpeg.input(str(original_video_path))
            original_audio_stream = video_input.audio
            original_audio_stream.output(str(original_audio_path), acodec='pcm_s16le').run(overwrite_output=True, quiet=True)
            
            # Get video info
            video_info = self._get_video_info(str(original_video_path))
            video_duration = video_info.get('duration', 0)
            
            # Adjust French audio duration
            adjusted_french_audio = self._adjust_audio_duration(
                str(french_audio_path), video_duration, str(original_video_path)
            )
            
            # Create video with two audio tracks
            video_stream = video_input.video
            original_audio_stream = ffmpeg.input(str(original_audio_path)).audio
            french_audio_stream = ffmpeg.input(adjusted_french_audio).audio
            
            # Map streams
            output = ffmpeg.output(
                video_stream, original_audio_stream, french_audio_stream,
                str(output_path),
                vcodec=self.video_codec if self.video_codec != 'copy' else 'copy',
                acodec=self.audio_codec if self.audio_codec != 'copy' else 'copy',
                preset=self.preset if self.video_codec != 'copy' else None,
                crf=self.crf if self.video_codec != 'copy' else None,
                audio_bitrate=f'{self.audio_bitrate}k' if self.audio_codec != 'copy' else None,
                map=['0:v:0', '1:a:0', '2:a:0'],  # Map video, original audio, French audio
                metadata=['0:a:0', 'language=jpn'],  # Set original audio language
                metadata=['0:a:1', 'language=fre'],  # Set French audio language
                threads=self.threads
            )
            
            ffmpeg.run(output, overwrite_output=True, quiet=True)
            
            # Get output info
            output_info = self._get_video_info(str(output_path))
            
            self.logger.info(f"Dual audio video created: {output_path}")
            
            return VideoProcessingResult(
                success=True,
                output_path=str(output_path),
                duration=output_info.get('duration', 0),
                resolution=output_info.get('resolution'),
                file_size=output_info.get('file_size', 0)
            )
            
        except Exception as e:
            self.logger.error(f"Error creating dual audio video: {str(e)}")
            return VideoProcessingResult(success=False, error=str(e))
    
    def optimize_video(self, video_path: str) -> VideoProcessingResult:
        """Optimize video for web/streaming"""
        try:
            video_path = Path(video_path)
            
            # Generate output filename
            output_filename = f"{video_path.stem}_optimized.{self.video_format}"
            output_path = self.output_dir / output_filename
            
            self.logger.info(f"Optimizing video: {video_path} -> {output_path}")
            
            # Apply optimization settings based on quality
            if self.video_quality == 'low':
                preset = 'ultrafast'
                crf = 28
                audio_bitrate = 64
            elif self.video_quality == 'medium':
                preset = 'fast'
                crf = 25
                audio_bitrate = 96
            elif self.video_quality == 'high':
                preset = 'medium'
                crf = 23
                audio_bitrate = 128
            else:  # original
                preset = 'slow'
                crf = 18
                audio_bitrate = 192
            
            # Optimize with FFmpeg
            input_video = ffmpeg.input(str(video_path))
            output = ffmpeg.output(
                input_video,
                str(output_path),
                vcodec=self.video_codec,
                acodec=self.audio_codec,
                preset=preset,
                crf=crf,
                audio_bitrate=f'{audio_bitrate}k',
                movflags='+faststart',  # Optimize for web streaming
                threads=self.threads
            )
            
            ffmpeg.run(output, overwrite_output=True, quiet=True)
            
            # Get output info
            output_info = self._get_video_info(str(output_path))
            
            self.logger.info(f"Video optimization completed: {output_path}")
            
            return VideoProcessingResult(
                success=True,
                output_path=str(output_path),
                duration=output_info.get('duration', 0),
                resolution=output_info.get('resolution'),
                file_size=output_info.get('file_size', 0)
            )
            
        except Exception as e:
            self.logger.error(f"Error optimizing video: {str(e)}")
            return VideoProcessingResult(success=False, error=str(e))
    
    def cleanup_temp_files(self):
        """Clean up temporary files"""
        try:
            for file in self.temp_dir.glob('*'):
                if file.is_file():
                    file.unlink()
            self.logger.info("Temporary video files cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up temp files: {str(e)}")

def main():
    """Test function for video processor"""
    logging.basicConfig(level=logging.INFO)
    
    # Sample config
    config = {
        'video': {
            'temp_directory': './temp',
            'ffmpeg_path': 'ffmpeg',
            'threads': 4,
            'encoding': {
                'codec': 'libx264',
                'preset': 'medium',
                'crf': 23,
                'audio_codec': 'aac',
                'audio_bitrate': 128
            }
        },
        'output': {
            'output_directory': './output',
            'video_format': 'mp4',
            'video_quality': 'high',
            'keep_original_audio': False,
            'add_subtitles': True,
            'subtitle_format': 'srt'
        }
    }
    
    processor = VideoProcessor(config)
    
    # Test video reconstruction (replace with actual paths for testing)
    # result = processor.reconstruct_video("original_video.mp4", "french_audio.wav")
    # if result.success:
    #     print(f"Video reconstruction successful: {result.output_path}")
    # else:
    #     print(f"Error: {result.error}")
    
    print("Video processor ready for use")

if __name__ == "__main__":
    main()
