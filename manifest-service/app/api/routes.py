from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from loguru import logger
from ..services.manifest_service import ManifestService

router = APIRouter()
manifest_service = ManifestService()

class RegenerateManifestRequest(BaseModel):
    """Request model for regenerating a manifest."""
    video_id: str


class ManifestInfo(BaseModel):
    """Response model for manifest information."""
    master_playlist: str
    media_playlists: Dict[str, str]
    master_playlist_key: str


@router.post("/manifests/regenerate", response_model=ManifestInfo)
async def regenerate_manifest(request: RegenerateManifestRequest, background_tasks: BackgroundTasks):
    """
    Regenerate manifests for a video.
    
    This endpoint can be used when segments have been updated or added
    and the manifests need to be regenerated.
    """
    try:
        # Regenerate the manifests in the background
        background_tasks.add_task(manifest_service.regenerate_manifest, request.video_id)
        
        # Return a success response
        return {
            "master_playlist": f"Regenerating for {request.video_id}...",
            "media_playlists": {},
            "master_playlist_key": f"{request.video_id}/manifests/index.m3u8"
        }
        
    except Exception as e:
        logger.error(f"Failed to regenerate manifest: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/manifests/{video_id}", response_model=ManifestInfo)
async def get_manifest_info(video_id: str):
    """
    Get manifest information for a video.
    
    This endpoint retrieves the manifest URLs for a specific video.
    """
    try:
        # Get video metadata
        video_metadata = await manifest_service.metadata_service.get_video_metadata(video_id)
        
        if not video_metadata:
            raise HTTPException(status_code=404, detail=f"Video {video_id} not found")
        
        # Get manifest info from metadata
        manifest_info = video_metadata.get("manifest_info")
        
        if not manifest_info:
            raise HTTPException(status_code=404, detail=f"No manifest information found for video {video_id}")
        
        return manifest_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get manifest info: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))