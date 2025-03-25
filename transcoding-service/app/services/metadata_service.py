import os
import asyncio
from loguru import logger
from pymongo import MongoClient
from datetime import datetime

class MetadataService:
    """
    Service for interacting with the video metadata in MongoDB.
    
    This service provides methods to update the metadata of videos
    during the transcoding process.
    """
    
    def __init__(self):
        """Initialize the metadata service with MongoDB connection."""
        # Initialize MongoDB client
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        self.mongo_client = MongoClient(mongo_uri)
        self.db = self.mongo_client[os.getenv("MONGO_DB", "abr_streaming")]
        self.videos_collection = self.db.videos #this is like the table in the database
    
    async def update_status(self, video_id, status, error=None):
        """
        Update the status of a video.
        
        Args:
            video_id: The ID of the video
            status: The new status
            error: Optional error message
        """
        logger.info(f"Updating status for video {video_id} to {status}")
        
        update_data = {
            "status": status,
            "updated_at": datetime.utcnow()
        }
        
        if error:
            update_data["error"] = error
        
        # Use asyncio to run MongoDB operation in a thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.videos_collection.update_one(
                {"video_id": video_id},
                {"$set": update_data}
            )
        )
        
        logger.info(f"Status updated for video {video_id}")
    
    async def update_video_info(self, video_id, file_size=None, duration=None, width=None, height=None):
        """
        Update the technical information of a video.
        
        Args:
            video_id: The ID of the video
            file_size: The size of the file in bytes
            duration: The duration of the video in seconds
            width: The width of the video in pixels
            height: The height of the video in pixels
        """
        logger.info(f"Updating video info for video {video_id}")
        
        update_data = {"updated_at": datetime.utcnow()}
        
        if file_size is not None:
            update_data["file_size"] = file_size
        if duration is not None:
            update_data["duration"] = duration
        if width is not None:
            update_data["width"] = width
        if height is not None:
            update_data["height"] = height
        
        # Use asyncio to run MongoDB operation in a thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.videos_collection.update_one(
                {"video_id": video_id},
                {"$set": update_data}
            )
        )
        
        logger.info(f"Video info updated for video {video_id}")
    
    async def update_renditions(self, video_id, renditions):
        """
        Update the renditions information of a video.
        
        Args:
            video_id: The ID of the video
            renditions: List of rendition dictionaries
        """
        logger.info(f"Updating renditions for video {video_id}")
        
        update_data = {
            "renditions": renditions,
            "updated_at": datetime.utcnow()
        }
        
        # Use asyncio to run MongoDB operation in a thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.videos_collection.update_one(
                {"video_id": video_id},
                {"$set": update_data}
            )
        )
        
        logger.info(f"Renditions updated for video {video_id}")