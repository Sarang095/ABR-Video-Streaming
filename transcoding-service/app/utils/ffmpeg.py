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
    3. Creating HLS segments and playlists
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
        cmd = [ #here we are creating the ffprobe command to get the video properties 
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            input_file
        ]
        
        try:
            # Run FFprobe command
            process = await asyncio.create_subprocess_exec( #using this to run the ffprobe command
                *cmd,
                stdout=subprocess.PIPE, # piping the output to show the output
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
            for stream in probe_data.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break
            
            if not video_stream:
                raise Exception("No video stream found in the file")
            
            # Extract relevant properties
            video_info = {
                "duration": float(probe_data.get("format", {}).get("duration", 0)),
                "width": int(video_stream.get("width", 0)),
                "height": int(video_stream.get("height", 0)),
                "codec": video_stream.get("codec_name"),
                "bitrate": int(probe_data.get("format", {}).get("bit_rate", 0)) // 1000  # kbps
            }
            
            logger.info(f"Video analysis complete: {video_info}")
            return video_info
            
        except Exception as e:
            logger.error(f"Error analyzing video: {str(e)}")
            raise
    
    async def create_hls_rendition(self, input_file, output_dir, resolution, video_bitrate, audio_bitrate, segment_time):
        """
        Create an HLS rendition of a video.
        
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
        
        # Construct FFmpeg command for HLS transcoding
        cmd = [
            "ffmpeg",
            "-i", input_file,
            "-c:v", "libx264",               # H.264 video codec
            "-preset", "medium",             # Encoding preset (speed/quality tradeoff)
            "-profile:v", "main",            # H.264 profile
            "-crf", "23",                    # Constant Rate Factor (quality)
            "-b:v", f"{video_bitrate}k",     # Video bitrate
            "-maxrate", f"{int(video_bitrate * 1.5)}k",  # Maximum bitrate
            "-bufsize", f"{video_bitrate * 2}k",         # Buffer size
            "-vf", f"scale={dimensions}",    # Scale to target resolution
            "-c:a", "aac",                   # AAC audio codec
            "-b:a", f"{audio_bitrate}k",     # Audio bitrate
            "-ar", "48000",                  # Audio sample rate
            "-ac", "2",                      # Audio channels (stereo)
            "-f", "hls",                     # HLS format
            "-hls_time", str(segment_time),  # Segment duration
            "-hls_list_size", "0",           # Keep all segments in the playlist
            "-hls_segment_filename", f"{output_dir}/segment_%03d.ts",  # Segment naming
            f"{output_dir}/playlist.m3u8"    # Playlist file
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
            
            logger.info(f"HLS rendition created successfully: {resolution}")
            
        except Exception as e:
            logger.error(f"Error creating HLS rendition: {str(e)}")
            raise