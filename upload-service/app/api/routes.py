import logging
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional
import uuid

from app.services.upload_service import UploadService
from app.services.validation import validate_video_file
from app.models.video import VideoMetadata

logger = logging.getLogger(__name__)
router = APIRouter()

upload_service = UploadService() # instantiating the UploadService class 

class UploadResponse(BaseModel): # this is the response model for the upload endpoint tells what fields to include in the response and how to format them.
    video_id: str
    status: str
    message: str

@router.post("/videos/upload", response_model=UploadResponse)
async def upload_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(...),
    description: Optional[str] = Form(None),
):
    """
   This endpoint:
    1. Validates the uploaded file
    2. Stores it in the raw video bucket
    3. Creates metadata entry
    4. Queues a transcoding job
    """
    try:
        # Generate a unique video ID
        video_id = str(uuid.uuid4())
        
        logger.info(f"Processing upload for video ID: {video_id}")
        
        # Validate the video file
        await validate_video_file(file)
        
        # Create metadata object
        metadata = VideoMetadata(
            video_id=video_id,
            title=title,
            description=description,
            filename=file.filename,
            content_type=file.content_type,
            status="uploading"
        )
        
        # Process the upload (this could take time, so we do it in the background)
        background_tasks.add_task(
            upload_service.process_upload,
            file,
            metadata
        )
        
        return UploadResponse(
            video_id=video_id,
            status="processing",
            message="Video upload initiated successfully. Transcoding will begin shortly."
        )
        
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/videos/{video_id}/status")
async def get_video_status(video_id: str):
    """
    Get the current status of a video.
    This endpoint retrieves the processing status of a video by its ID.
    """
    try:
        status = await upload_service.get_video_status(video_id)
        return status
    except Exception as e:
        logger.error(f"Failed to get status: {str(e)}")
        raise HTTPException(status_code=404, detail=f"Video with ID {video_id} not found")