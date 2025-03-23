from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class VideoMetadata(BaseModel):
    """
    Model for representing video metadata.
    
    This class defines the structure of the metadata that will be stored
    in MongoDB for each uploaded video.
    """
    video_id: str
    title: str
    description: Optional[str] = None
    filename: str
    content_type: str
    upload_path: Optional[str] = None
    status: str = "pending"  # pending, uploading, uploaded, processing, ready, failed
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    file_size: Optional[int] = None
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    renditions: List[Dict[str, Any]] = []
    manifest_path: Optional[str] = None
    error: Optional[str] = None
    
    class Config:
        schema_extra = {
            "example": {
                "video_id": "550e8400-e29b-41d4-a716-446655440000",
                "title": "Sample Video",
                "description": "A test video for the ABR streaming system",
                "filename": "sample.mp4",
                "content_type": "video/mp4",
                "upload_path": "raw/550e8400-e29b-41d4-a716-446655440000/sample.mp4",
                "status": "uploaded",
                "created_at": "2023-06-01T12:00:00",
                "updated_at": "2023-06-01T12:05:00",
                "file_size": 15728640,  # 15 MB
                "duration": 120.5,  # seconds
                "width": 1920,
                "height": 1080,
                "renditions": [
                    {
                        "resolution": "1080p",
                        "bitrate": 5000000,
                        "path": "processed/550e8400-e29b-41d4-a716-446655440000/1080p"
                    }
                ],
                "manifest_path": "processed/550e8400-e29b-41d4-a716-446655440000/master.m3u8"
            }
        }

    def to_dict(self) -> dict:
        """
        Convert the model to a dictionary for storage in MongoDB.
        """
        return self.dict(by_alias=True)

    @classmethod
    def from_dict(cls, data: dict) -> 'VideoMetadata':
        """
        Create a VideoMetadata instance from a dictionary retrieved from MongoDB.
        """
        return cls(**data)