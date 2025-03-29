import os
import json
import asyncio
import boto3
from loguru import logger
from datetime import datetime
import tempfile
import shutil
from botocore.exceptions import ClientError

from app.services.metadata_service import MetadataService
from app.utils.ffmpeg import FFmpegProcessor
from app.utils.s3 import S3Utilities

class TranscodingService:
    """
    Service for transcoding videos into multiple quality renditions.
    
    This service handles:
    1. Downloading videos from S3
    2. Analyzing video properties
    3. Generating multiple quality renditions
    4. Creating HLS segments
    5. Uploading results back to S3
    6. Notifying downstream services
    """
    
    def __init__(self):
        """Initialize the transcoding service with required connections and utilities."""
        self.s3_client = boto3.client('s3', region_name=os.getenv("AWS_REGION", "us-east-1"))
        
        self.raw_bucket = os.getenv("S3_RAW_BUCKET", "s3-raw-bucket-49")
        self.processed_bucket = os.getenv("S3_PROCESSED_BUCKET", "processed-s3-bucket-49")
        self.sns_client = boto3.client('sns', region_name=os.getenv("AWS_REGION", "us-east-1"))
        
        self.manifest_topic_arn = os.getenv("SNS_MANIFEST_TOPIC", "arn:aws:sns:us-east-1:891612545820:SNS-Manifest-Topic")
        
        # Initialize utility services
        self.metadata_service = MetadataService()
        self.s3_utils = S3Utilities(self.s3_client)
        self.ffmpeg = FFmpegProcessor()
        
        # Define rendition specifications
        # Format: resolution, bitrate (kbps), audio bitrate (kbps)
        self.renditions = [ # this is the list of resolutions that we want to transcode the video into
            ("240p", 350, 64),    # 426x240
            ("360p", 700, 96),    # 640x360
            ("480p", 1200, 128),  # 854x480
            ("720p", 2500, 192),  # 1280x720
            ("1080p", 5000, 192), # 1920x1080
        ]
    
    async def process_video(self, video_id, input_path, filename):
        """
        Process a video by transcoding it into multiple renditions.
        
        Args:
            video_id: The ID of the video
            input_path: The S3 path to the raw video
            filename: The original filename
        """
        # Create a temporary working directory
        temp_dir = tempfile.mkdtemp()
        input_file = os.path.join(temp_dir, filename)
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            logger.info(f"Starting transcoding for video ID: {video_id}")
            
            # Update status to processing
            await self.metadata_service.update_status(video_id, "processing")
            
            # Download file from S3
            logger.info(f"Downloading video from S3: {input_path}")
            await self.s3_utils.download_file(self.raw_bucket, input_path, input_file)
            
            # Analyze video to get properties
            logger.info(f"Analyzing video properties: {input_file}")
            video_info = await self.ffmpeg.analyze_video(input_file)
            
            # Update metadata with video properties
            await self.metadata_service.update_video_info(
                video_id, 
                file_size=os.path.getsize(input_file),
                duration=video_info.get("duration"),
                width=video_info.get("width"),
                height=video_info.get("height")
            )
            
            # Process each rendition
            rendition_data = []
            for resolution, bitrate, audio_bitrate in self.renditions:
                # Skip renditions higher than the source resolution
                if (resolution == "1080p" and video_info.get("height", 0) < 1080 or  #here we are checking if the source video is lower resolution than the resolution we are transcoding to - if it is, we skip it
                    resolution == "720p" and video_info.get("height", 0) < 720): # for example, if the source video is 480p, we skip 720p and 1080p and make the transcode to 480p and 360p i.e hls segments of 480p and 360p
                    logger.info(f"Skipping {resolution} rendition as source is lower resolution")
                    continue
                
                logger.info(f"Processing {resolution} rendition")
                
                # Create directory for this rendition
                rendition_dir = os.path.join(output_dir, resolution)
                os.makedirs(rendition_dir, exist_ok=True)
                
                # Transcode to this rendition and create HLS segments
                await self.ffmpeg.create_hls_rendition(
                    input_file=input_file,
                    output_dir=rendition_dir,
                    resolution=resolution,
                    video_bitrate=bitrate,
                    audio_bitrate=audio_bitrate,
                    segment_time=6  # 6-second segments
                )
                
                # Record the rendition details
                rendition_data.append({
                    "resolution": resolution,
                    "bitrate": bitrate * 1000,  # Convert to bps for consistency
                    "path": f"processed/{video_id}/{resolution}"
                })
            
            # Upload processed files to S3
            logger.info(f"Uploading processed files to S3 for video ID: {video_id}")
            for root, _, files in os.walk(output_dir):
                for file in files:
                    local_path = os.path.join(root, file)
                    
                    # Calculate S3 key (path within bucket)
                    rel_path = os.path.relpath(local_path, output_dir)
                    s3_key = f"processed/{video_id}/{rel_path}"
                    
                    # Determine content type based on file extension
                    content_type = "application/octet-stream"
                    if file.endswith(".ts"):
                        content_type = "video/MP2T"
                    elif file.endswith(".m3u8"):
                        content_type = "application/vnd.apple.mpegurl"
                    
                    # Upload the file
                    await self.s3_utils.upload_file(
                        local_path, 
                        self.processed_bucket, 
                        s3_key,
                        content_type
                    )
            
            # Update metadata with rendition information
            await self.metadata_service.update_renditions(video_id, rendition_data)
            
            # Notify manifest service
            await self._notify_manifest_service(video_id)
            
            # Update status to indicate successful transcoding
            await self.metadata_service.update_status(video_id, "transcoded")
            
            logger.info(f"Transcoding completed successfully for video ID: {video_id}")
            
        except Exception as e:
            logger.error(f"Error transcoding video {video_id}: {str(e)}")
            
            # Update status to failed
            await self.metadata_service.update_status(
                video_id, 
                "failed", 
                error=f"Transcoding error: {str(e)}"
            )
            
            # Re-raise the exception
            raise
            
        finally:
            # Clean up temporary directory
            logger.info(f"Cleaning up temporary files for video ID: {video_id}")
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.error(f"Error cleaning up temporary directory: {str(e)}")
    
    async def _notify_manifest_service(self, video_id):
        """
        Notify the manifest service that transcoding is complete.
        
        Args:
            video_id: The ID of the video
        """
        try:
            # Create notification message
            message = {
                "video_id": video_id,
                "status": "transcoded",
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # Publish to SNS topic
            logger.info(f"Notifying manifest service for video ID: {video_id}")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.sns_client.publish(
                    TopicArn=self.manifest_topic_arn,
                    Message=json.dumps(message),
                    Subject=f"Transcoding Complete: {video_id}"
                )
            )
            
            logger.info(f"Manifest service notified for video ID: {video_id}")
            
        except ClientError as e:
            logger.error(f"Error notifying manifest service: {str(e)}")
            # Don't re-raise the exception here, as transcoding itself was successful