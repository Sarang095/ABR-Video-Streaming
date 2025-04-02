import os
import asyncio
from loguru import logger
from pymongo import MongoClient
from datetime import datetime

class MetadataService:
    """
    Service for interacting with the video metadata in MongoDB.
    
    This service provides methods to read and update metadata about videos
    during the manifest generation process.
    """
    
    def __init__(self):
        """Initialize the metadata service with MongoDB connection."""
        # Initialize MongoDB client
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        self.mongo_client = MongoClient(mongo_uri)
        self.db = self.mongo_client[os.getenv("MONGO_DB", "abr_streaming")]
        self.videos_collection = self.db.videos
    
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
    
    async def update_manifest_info(self, video_id, manifest_info):
        """
        Update the manifest information of a video.
        
        Args:
            video_id: The ID of the video
            manifest_info: Dictionary containing manifest URLs and paths
        """
        logger.info(f"Updating manifest info for video {video_id}")
        
        update_data = {
            "manifest_info": manifest_info,
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
        
        logger.info(f"Manifest info updated for video {video_id}")
    
    async def get_video_metadata(self, video_id):
        """
        Get the complete metadata for a video.
        
        Args:
            video_id: The ID of the video
        
        Returns:
            dict: Video metadata or None if not found
        """
        logger.info(f"Getting metadata for video {video_id}")
        
        # Use asyncio to run MongoDB operation in a thread
        loop = asyncio.get_event_loop()
        video = await loop.run_in_executor(
            None,
            lambda: self.videos_collection.find_one({"video_id": video_id})
        )
        
        if video:
            # Convert ObjectId to string for JSON serialization
            video["_id"] = str(video["_id"])
            logger.info(f"Metadata retrieved for video {video_id}")
        else:
            logger.warning(f"No metadata found for video {video_id}")
        
        return video