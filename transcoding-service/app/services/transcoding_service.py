import os
import json
import asyncio
import boto3
import glob
from loguru import logger
from datetime import datetime
import tempfile
import shutil
import pymongo
from botocore.exceptions import ClientError

from app.utils.ffmpeg import FFmpegProcessor
from app.utils.s3 import S3Utilities

class TranscodingService:
    """
    Service for transcoding videos into multiple quality renditions.
    
    This service now handles:
    1. Downloading videos from S3
    2. Analyzing video properties
    3. Generating multiple quality renditions
    4. Creating HLS segments and master playlist
    5. Uploading results back to S3
    6. Updating metadata in MongoDB directly
    """
    
    def __init__(self):
        """Initialize the transcoding service with required connections and utilities."""
        self.s3_client = boto3.client('s3', region_name=os.getenv("AWS_REGION", "us-east-1"))
            
        self.raw_bucket = os.getenv("S3_RAW_BUCKET", "s3-raw-bucket-49")
        self.processed_bucket = os.getenv("S3_PROCESSED_BUCKET", "processed-s3-bucket-49")
        
        # Connect to MongoDB directly
        mongo_uri = os.getenv("MONGO_URI", "localhost:27017")
        self.mongo_client = pymongo.MongoClient(mongo_uri)
        self.db = self.mongo_client[os.getenv("MONGODB_DATABASE", "video_platform")]
        self.videos_collection = self.db["videos"]
        
        # Initialize utility services
        self.s3_utils = S3Utilities(self.s3_client)
        self.ffmpeg = FFmpegProcessor()
        
        # Define rendition specifications:
        # Format: (resolution, video bitrate in kbps, audio bitrate in kbps)
        self.renditions = [
            ("240p", 350, 64),
            ("360p", 700, 96),
            ("480p", 1200, 128),
            ("720p", 2500, 192),
            ("1080p", 5000, 192),
        ]
    
    async def update_video_status(self, video_id, status, error=None):
        """
        Update the video status in MongoDB.
        
        Args:
            video_id: The ID of the video
            status: The status to set
            error: Optional error message
        """
        update_data = {"status": status, "updated_at": datetime.utcnow()}
        if error:
            update_data["error"] = error
            
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.videos_collection.update_one(
                {"_id": video_id},
                {"$set": update_data}
            )
        )
        logger.info(f"Updated video status to {status} for video ID: {video_id}")
    
    async def update_video_info(self, video_id, **kwargs):
        """
        Update video metadata in MongoDB.
        
        Args:
            video_id: The ID of the video
            **kwargs: Video metadata fields to update
        """
        update_data = {**kwargs, "updated_at": datetime.utcnow()}
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.videos_collection.update_one(
                {"_id": video_id},
                {"$set": update_data}
            )
        )
        logger.info(f"Updated video info for video ID: {video_id}")
    
    async def process_video(self, video_id, input_path, filename):
        """
        Process a video by transcoding it into multiple renditions.
        
        Args:
            video_id: The ID of the video
            input_path: The S3 path to the raw video
            filename: The original filename
        """
        temp_dir = tempfile.mkdtemp()
        input_file = os.path.join(temp_dir, filename)
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            logger.info(f"Starting transcoding for video ID: {video_id}")
            
            await self.update_video_status(video_id, "processing")
            
            logger.info(f"Downloading video from S3: {input_path}")
            await self.s3_utils.download_file(self.raw_bucket, input_path, input_file)
            
            logger.info(f"Analyzing video properties: {input_file}")
            video_info = await self.ffmpeg.analyze_video(input_file)
            
            await self.update_video_info(
                video_id, 
                file_size=os.path.getsize(input_file),
                duration=video_info.get("duration"),
                width=video_info.get("width"),
                height=video_info.get("height")
            )
            
            rendition_data = []
            available_renditions = []
            
            for resolution, bitrate, audio_bitrate in self.renditions:
                if (resolution == "1080p" and video_info.get("height", 0) < 1080 or
                    resolution == "720p" and video_info.get("height", 0) < 720):
                    logger.info(f"Skipping {resolution} rendition as source is lower resolution")
                    continue
                
                logger.info(f"Processing {resolution} rendition")
                rendition_dir = os.path.join(output_dir, resolution)
                os.makedirs(rendition_dir, exist_ok=True)
                
                await self.ffmpeg.create_hls_rendition(
                    input_file=input_file,
                    output_dir=rendition_dir,
                    resolution=resolution,
                    video_bitrate=bitrate,
                    audio_bitrate=audio_bitrate,
                    segment_time=6
                )
                
                # Use glob to count generated segment files
                segment_files = glob.glob(os.path.join(rendition_dir, "segment_*.ts"))
                segment_count = len(segment_files)
                
                s3_path = f"{video_id}/segments/{resolution}"
                
                rendition_data.append({
                    "resolution": resolution,
                    "bitrate": bitrate * 1000,  # in bps
                    "segment_count": segment_count,
                    "path": s3_path,
                    "playlist": f"{s3_path}/playlist.m3u8"
                })
                
                available_renditions.append({
                    "resolution": resolution,
                    "bandwidth": bitrate * 1000,
                    "playlist": f"segments/{resolution}/playlist.m3u8"
                })
            
            # Create master playlist with all renditions
            await self._create_master_playlist(output_dir, available_renditions)
            
            logger.info(f"Uploading processed files to S3 for video ID: {video_id}")
            for root, _, files in os.walk(output_dir):
                for file in files:
                    local_path = os.path.join(root, file)
                    rel_path = os.path.relpath(local_path, output_dir)
                    
                    # Determine content type based on file extension
                    content_type = "application/octet-stream"
                    if file.endswith(".ts"):
                        content_type = "video/MP2T"
                    elif file.endswith(".m3u8"):
                        content_type = "application/vnd.apple.mpegurl"
                    
                    # Upload to S3
                    s3_key = f"{video_id}/{rel_path}"
                    await self.s3_utils.upload_file(
                        local_path, 
                        self.processed_bucket, 
                        s3_key,
                        content_type
                    )
            
            # Update MongoDB with rendition data and playback URLs
            playback_urls = {
                "hls": f"https://{self.processed_bucket}.s3.amazonaws.com/{video_id}/master.m3u8",
                "dash": None  # Add DASH support if needed in the future
            }
            
            await self.update_video_info(
                video_id,
                renditions=rendition_data,
                playback_urls=playback_urls,
                processed=True
            )
            
            await self.update_video_status(video_id, "ready")
            logger.info(f"Transcoding completed successfully for video ID: {video_id}")
            
        except Exception as e:
            logger.error(f"Error transcoding video {video_id}: {str(e)}")
            await self.update_video_status(
                video_id, 
                "failed", 
                error=f"Transcoding error: {str(e)}"
            )
            raise
            
        finally:
            logger.info(f"Cleaning up temporary files for video ID: {video_id}")
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.error(f"Error cleaning up temporary directory: {str(e)}")
    
    async def _create_master_playlist(self, output_dir, renditions):
        """
        Create a master HLS playlist that includes all quality renditions.
        
        Args:
            output_dir: Directory to store the master playlist
            renditions: List of rendition information
        """
        master_playlist_path = os.path.join(output_dir, "master.m3u8")
        
        with open(master_playlist_path, "w") as f:
            f.write("#EXTM3U\n")
            f.write("#EXT-X-VERSION:3\n")
            
            # Add each rendition to the master playlist
            for rendition in renditions:
                f.write(f"#EXT-X-STREAM-INF:BANDWIDTH={rendition['bandwidth']},RESOLUTION={rendition['resolution']}\n")
                f.write(f"{rendition['playlist']}\n")
        
        logger.info(f"Created master playlist at {master_playlist_path}")