import logging
import os
from fastapi import UploadFile, HTTPException

logger = logging.getLogger(__name__)

# Define constants
MAX_VIDEO_SIZE = int(os.getenv("MAX_VIDEO_SIZE", 1024 * 1024 * 100))  # 100 MB default
ALLOWED_CONTENT_TYPES = [
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-ms-wmv",
]

async def validate_video_file(file: UploadFile) -> bool:
    """
    Validate that the uploaded file is a valid video file.
    
    This function checks:
    1. File size doesn't exceed the maximum allowed
    2. Content type is in the list of allowed video types
    3. Basic file integrity (if possible)
    
    Args:
        file: The uploaded file to validate
        
    Returns:
        bool: True if the file is valid
        
    Raises:
        HTTPException: If the file is invalid
    """
    # Check if a file was uploaded
    if not file:
        logger.error("No file was uploaded")
        raise HTTPException(status_code=400, detail="No file was uploaded")
    
    # Validate file name
    if not file.filename:
        logger.error("File has no filename")
        raise HTTPException(status_code=400, detail="File has no filename")
    
    # Validate content type
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        logger.error(f"Invalid content type: {file.content_type}")
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid content type. Allowed types: {', '.join(ALLOWED_CONTENT_TYPES)}"
        )
    
    # Check file size
    # FastAPI reads the file in chunks, so we need to read the file to check its size
    contents = await file.read()
    file_size = len(contents)
    
    # Reset file pointer
    await file.seek(0)
    
    if file_size > MAX_VIDEO_SIZE:
        logger.error(f"File size exceeds maximum: {file_size} > {MAX_VIDEO_SIZE}")
        max_size_mb = MAX_VIDEO_SIZE / (1024 * 1024)
        raise HTTPException(
            status_code=400,
            detail=f"File size exceeds maximum allowed ({max_size_mb} MB)"
        )
    
    # For more advanced validation, you could check file headers or use ffprobe
    # to verify that the file is a valid video, but that's beyond the scope
    # of this basic validation
    
    logger.info(f"File validation successful: {file.filename}")
    return True