import os
import json
import asyncio
import subprocess
from loguru import logger

class FFmpegProcessor:
    """
    Utility class for processing videos using FFmpeg.
    
    This class provides methods for:
    1. Analyzing video properties
    2. Transcoding videos to different quality levels
    3. Creating HLS segments and playlists with settings used by major streaming platforms
    """
    
    async def analyze_video(self, input_file):
        """
        Analyze a video file to get its properties.
        
        Args:
            input_file: Path to the input video file
            
        Returns:
            dict: Video properties including duration, dimensions, etc.
        """
        logger.info(f"Analyzing video: {input_file}")
        
        # Construct FFprobe command
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            input_file
        ]
        
        try:
            # Run FFprobe command
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"FFprobe error: {stderr.decode()}")
                raise Exception(f"FFprobe failed with code {process.returncode}")
            
            # Parse JSON output
            probe_data = json.loads(stdout.decode())
            
            # Extract video information
            video_stream = None
            audio_stream = None
            
            for stream in probe_data.get("streams", []):
                if stream.get("codec_type") == "video" and not video_stream:
                    video_stream = stream
                elif stream.get("codec_type") == "audio" and not audio_stream:
                    audio_stream = stream
            
            if not video_stream:
                raise Exception("No video stream found in the file")
            
            # Extract relevant properties
            video_info = {
                "duration": float(probe_data.get("format", {}).get("duration", 0)),
                "width": int(video_stream.get("width", 0)),
                "height": int(video_stream.get("height", 0)),
                "codec": video_stream.get("codec_name"),
                "bitrate": int(probe_data.get("format", {}).get("bit_rate", 0)) // 1000,  # kbps
                "fps": self._parse_frame_rate(video_stream.get("r_frame_rate", "30/1")),
                "has_audio": audio_stream is not None,
                "audio_channels": int(audio_stream.get("channels", 2)) if audio_stream else 0,
                "audio_codec": audio_stream.get("codec_name") if audio_stream else None
            }
            
            logger.info(f"Video analysis complete: {video_info}")
            return video_info
            
        except Exception as e:
            logger.error(f"Error analyzing video: {str(e)}")
            raise
    
    def _parse_frame_rate(self, frame_rate_str):
        """Parse frame rate string (e.g. '30/1') to float."""
        if not frame_rate_str or '/' not in frame_rate_str:
            return 30.0  # Default
            
        num, den = frame_rate_str.split('/')
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return 30.0  # Default
    
    async def create_hls_rendition(self, input_file, output_dir, resolution, video_bitrate, audio_bitrate, segment_time):
        """
        Create an HLS rendition of a video using settings similar to major streaming platforms.
        
        Args:
            input_file: Path to the input video file
            output_dir: Directory to store the output files
            resolution: Resolution label (e.g., "720p")
            video_bitrate: Video bitrate in kbps
            audio_bitrate: Audio bitrate in kbps
            segment_time: Duration of each segment in seconds
        """
        logger.info(f"Creating HLS rendition: {resolution} at {video_bitrate}kbps")
        
        # Map resolution labels to dimensions
        resolution_map = {
            "240p": "426:240",
            "360p": "640:360",
            "480p": "854:480",
            "720p": "1280:720",
            "1080p": "1920:1080"
        }
        
        # Get dimensions from resolution label
        dimensions = resolution_map.get(resolution)
        if not dimensions:
            raise ValueError(f"Invalid resolution: {resolution}")
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Keyframe interval - 2 seconds (common practice for streaming)
        keyint = 2 * 30  # Assuming 30fps, adjust based on input
        
        # Construct FFmpeg command for HLS transcoding with industry-standard settings
        cmd = [
            "ffmpeg",
            "-i", input_file,
            "-c:v", "libx264",                  # H.264 video codec (widely supported)
            "-preset", "slow",                  # Better quality/compression (used by premium services)
            "-profile:v", "main",               # H.264 profile (compatibility)
            "-level:v", "4.0",                  # H.264 level (compatibility)
            "-crf", "23",                       # Quality-based encoding
            "-b:v", f"{video_bitrate}k",        # Target video bitrate
            "-maxrate", f"{int(video_bitrate * 1.2)}k",  # Maximum bitrate (20% higher than target)
            "-bufsize", f"{video_bitrate * 2}k",         # Buffer size (2x bitrate)
            "-keyint_min", str(keyint),         # Minimum GOP size
            "-g", str(keyint),                  # GOP size (keyframe interval)
            "-sc_threshold", "0",               # Disable scene change detection for consistent GOP
            "-vf", f"scale={dimensions}:force_original_aspect_ratio=decrease,pad={dimensions}:(ow-iw)/2:(oh-ih)/2",  # Scale with padding to maintain aspect ratio
            "-c:a", "aac",                      # AAC audio codec (widely supported)
            "-b:a", f"{audio_bitrate}k",        # Audio bitrate
            "-ar", "48000",                     # Audio sample rate
            "-ac", "2",                         # Audio channels (stereo)
            "-f", "hls",                        # HLS format
            "-hls_time", str(segment_time),     # Segment duration
            "-hls_playlist_type", "vod",        # Video on demand playlist
            "-hls_segment_filename", f"{output_dir}/segment_%03d.ts",  # Segment naming
            "-hls_flags", "independent_segments",  # Each segment can be decoded independently
            "-hls_list_size", "0",              # Keep all segments in the playlist
            f"{output_dir}/playlist.m3u8"       # Playlist file
        ]
        
        try:
            # Run FFmpeg command
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"FFmpeg error: {stderr.decode()}")
                raise Exception(f"FFmpeg failed with code {process.returncode}")
            
            # Modify the playlist to include additional HLS directives for better playback
            await self._enhance_playlist(f"{output_dir}/playlist.m3u8", segment_time)
            
            logger.info(f"HLS rendition created successfully: {resolution}")
            
        except Exception as e:
            logger.error(f"Error creating HLS rendition: {str(e)}")
            raise
    
    async def _enhance_playlist(self, playlist_path, segment_duration):
        """
        Enhance the HLS playlist with additional directives used by major streaming platforms.
        
        Args:
            playlist_path: Path to the playlist file
            segment_duration: Duration of each segment in seconds
        """
        try:
            with open(playlist_path, 'r') as file:
                content = file.read()
            
            # Split the content into lines
            lines = content.splitlines()
            
            # Find or add the version line
            version_added = False
            for i, line in enumerate(lines):
                if line.startswith('#EXT-X-VERSION'):
                    lines[i] = '#EXT-X-VERSION:4'
                    version_added = True
                    break
            
            if not version_added:
                lines.insert(1, '#EXT-X-VERSION:4')
            
            # Add additional directives after the version line
            additional_directives = [
                f'#EXT-X-TARGETDURATION:{segment_duration}',
                '#EXT-X-MEDIA-SEQUENCE:0',
                '#EXT-X-PLAYLIST-TYPE:VOD',
                '#EXT-X-INDEPENDENT-SEGMENTS'
            ]
            
            # Find the right position to insert these directives
            insert_position = 1
            for i, line in enumerate(lines):
                if line.startswith('#EXT-X-VERSION'):
                    insert_position = i + 1
                    break
            
            # Insert the additional directives
            for directive in reversed(additional_directives):
                if directive not in lines:
                    lines.insert(insert_position, directive)
            
            # Write the modified content back to the file
            with open(playlist_path, 'w') as file:
                file.write('\n'.join(lines))
            
            logger.info(f"Enhanced HLS playlist: {playlist_path}")
            
        except Exception as e:
            logger.error(f"Error enhancing playlist: {str(e)}")
            # Don't raise the exception as this is an enhancement, not critical