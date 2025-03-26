import os
import json
import asyncio
import logging
from typing import List, Dict, Any

import boto3
import pymongo
from fastapi import FastAPI, HTTPException
from loguru import logger
from botocore.exceptions import ClientError
from pymongo import MongoClient
from datetime import datetime

class ManifestService:
    def __init__(self):
        """
        Initialize Manifest Service with AWS S3 and MongoDB connections
        """
        # S3 Client Setup
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY"),
            aws_secret_access_key=os.getenv("AWS_SECRET_KEY"),
            region_name=os.getenv("AWS_REGION", "us-east-1")
        )
        
        # MongoDB Setup
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        self.mongo_client = MongoClient(mongo_uri)
        self.db = self.mongo_client[os.getenv("MONGO_DB", "abr_streaming")]
        self.videos_collection = self.db.videos
        
        # S3 Bucket Configuration
        self.processed_bucket = os.getenv("S3_PROCESSED_BUCKET", "processed-s3-bucket-49")
        
        # SNS Client for notifications
        self.sns_client = boto3.client(
            'sns',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY"),
            aws_secret_access_key=os.getenv("AWS_SECRET_KEY"),
            region_name=os.getenv("AWS_REGION", "us-east-1")
        )
        self.delivery_topic_arn = os.getenv("SNS_DELIVERY_TOPIC", "arn:aws:sns:us-east-1:891612545820:SNS-Delivery-Topic:d602310a-4db3-499b-986e-037e2c6065dd")
        
        # Configure logging
        logger.add("manifest_service.log", rotation="10 MB")
    
    async def _get_video_metadata(self, video_id: str) -> Dict[str, Any]:
        """
        Retrieve video metadata from MongoDB
        
        Args:
            video_id: Unique identifier for the video
        
        Returns:
            Video metadata dictionary
        """
        # Use asyncio to run MongoDB operation in a thread
        loop = asyncio.get_event_loop()
        video_metadata = await loop.run_in_executor(
            None,
            lambda: self.videos_collection.find_one({"video_id": video_id})
        )
        
        if not video_metadata:
            raise HTTPException(status_code=404, detail="Video not found")
        
        return video_metadata
    
    async def generate_master_manifest(self, video_id: str) -> str:
        """
        Generate master HLS manifest for a given video
        
        Args:
            video_id: Unique identifier for the video
        
        Returns:
            Path to generated master manifest
        """
        try:
            # Retrieve video metadata
            video_metadata = await self._get_video_metadata(video_id)
            renditions = video_metadata.get('renditions', [])
            
            # Generate master manifest content
            master_manifest_content = "#EXTM3U\n"
            master_manifest_content += "#EXT-X-VERSION:3\n"
            
            for rendition in renditions:
                resolution = rendition['resolution']
                bandwidth = rendition['bitrate']
                uri = f"processed/{video_id}/{resolution}/playlist.m3u8"
                
                master_manifest_content += (
                    f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},"
                    f"RESOLUTION={resolution}\n"
                    f"{uri}\n"
                )
            
            # Generate file path
            manifest_path = f"processed/{video_id}/master.m3u8"
            
            # Upload to S3
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.put_object(
                    Bucket=self.processed_bucket, 
                    Key=manifest_path, 
                    Body=master_manifest_content.encode('utf-8'),
                    ContentType='application/vnd.apple.mpegurl'
                )
            )
            
            logger.info(f"Master manifest generated for video {video_id}")
            return manifest_path
        
        except Exception as e:
            logger.error(f"Error generating master manifest: {e}")
            raise HTTPException(status_code=500, detail="Manifest generation failed")
    
    async def generate_rendition_manifests(self, video_id: str) -> List[str]:
        """
        Generate individual rendition playlists
        
        Args:
            video_id: Unique identifier for the video
        
        Returns:
            List of generated rendition manifest paths
        """
        try:
            # Retrieve video metadata
            video_metadata = await self._get_video_metadata(video_id)
            renditions = video_metadata.get('renditions', [])
            
            rendition_manifests = []
            loop = asyncio.get_event_loop()
            
            for rendition in renditions:
                resolution = rendition['resolution']
                
                # List segments for this rendition
                list_segments_result = await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.list_objects_v2(
                        Bucket=self.processed_bucket,
                        Prefix=f"processed/{video_id}/{resolution}"
                    )
                )
                
                # Filter .ts segments
                ts_segments = [
                    obj['Key'] for obj in list_segments_result.get('Contents', []) 
                    if obj['Key'].endswith('.ts')
                ]
                
                # Generate rendition playlist
                playlist_content = "#EXTM3U\n"
                playlist_content += "#EXT-X-VERSION:3\n"
                playlist_content += "#EXT-X-TARGETDURATION:10\n"
                playlist_content += "#EXT-X-MEDIA-SEQUENCE:0\n"
                
                for segment in sorted(ts_segments):
                    playlist_content += f"#EXTINF:6.000,\n"  # Assuming 6-second segments from transcoding
                    playlist_content += f"{segment}\n"
                
                playlist_content += "#EXT-X-ENDLIST\n"
                
                # Generate file path
                playlist_path = f"processed/{video_id}/{resolution}/playlist.m3u8"
                
                # Upload to S3
                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.put_object(
                        Bucket=self.processed_bucket, 
                        Key=playlist_path, 
                        Body=playlist_content.encode('utf-8'),
                        ContentType='application/vnd.apple.mpegurl'
                    )
                )
                
                rendition_manifests.append(playlist_path)
            
            logger.info(f"Rendition manifests generated for video {video_id}")
            return rendition_manifests
        
        except Exception as e:
            logger.error(f"Error generating rendition manifests: {e}")
            raise HTTPException(status_code=500, detail="Rendition manifest generation failed")
    
    async def update_video_status(self, video_id: str):
        """
        Update video status after manifest generation
        
        Args:
            video_id: Unique identifier for the video
        """
        try:
            # Update video status in MongoDB
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.videos_collection.update_one(
                    {"video_id": video_id},
                    {"$set": {
                        "status": "MANIFEST_GENERATED",
                        "updated_at": datetime.utcnow()
                    }}
                )
            )
            
            # Notify delivery service via SNS
            await loop.run_in_executor(
                None,
                lambda: self.sns_client.publish(
                    TopicArn=self.delivery_topic_arn,
                    Message=json.dumps({
                        "video_id": video_id,
                        "status": "MANIFEST_GENERATED",
                        "timestamp": datetime.utcnow().isoformat()
                    }),
                    Subject=f"Manifest Generated: {video_id}"
                )
            )
            
            logger.info(f"Video {video_id} status updated and delivery service notified")
        
        except Exception as e:
            logger.error(f"Error updating video status: {e}")
            raise HTTPException(status_code=500, detail="Status update failed")

# SQS Message Consumer for Manifest Generation
async def process_manifest_generation_message(message):
    """
    Process manifest generation messages from SQS
    
    Args:
        message: SQS message containing video processing details
    """
    manifest_service = ManifestService()
    
    try:
        # Parse message body
        body = json.loads(message['Body'])
        video_id = body.get('video_id')
        
        logger.info(f"Processing manifest generation for video ID: {video_id}")
        
        # Generate master and rendition manifests
        await manifest_service.generate_master_manifest(video_id)
        await manifest_service.generate_rendition_manifests(video_id)
        
        # Update video status
        await manifest_service.update_video_status(video_id)
        
        logger.info(f"Manifest generation completed for video ID: {video_id}")
        
    except Exception as e:
        logger.error(f"Error processing manifest generation: {str(e)}")
        raise

# FastAPI Application (optional, for REST API endpoints)
app = FastAPI(title="Manifest Service")

@app.post("/generate-manifests/{video_id}")
async def generate_manifests(video_id: str):
    """
    Manual trigger for manifest generation (for testing/admin purposes)
    """
    manifest_service = ManifestService()
    
    try:
        master_manifest = await manifest_service.generate_master_manifest(video_id)
        rendition_manifests = await manifest_service.generate_rendition_manifests(video_id)
        await manifest_service.update_video_status(video_id)
        
        return {
            "status": "success",
            "master_manifest": master_manifest,
            "rendition_manifests": rendition_manifests
        }
    except HTTPException as e:
        raise e