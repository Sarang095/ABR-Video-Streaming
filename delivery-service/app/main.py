import os
import json
import asyncio
import boto3
import pymongo
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse
from botocore.exceptions import ClientError
from loguru import logger
import time
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse
from typing import Optional, Dict, Any, List
import urllib.parse
from datetime import datetime

# Initialize FastAPI app
app = FastAPI(title="Video Delivery Service")

# Add CORS middleware for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"]
)

# Initialize clients
s3_client = boto3.client('s3', region_name=os.getenv("AWS_REGION", "us-east-1"))
mongo_client = pymongo.MongoClient(os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
db = mongo_client[os.getenv("MONGODB_DATABASE", "video_platform")]
videos_collection = db["videos"]

# Get configuration from environment
processed_bucket = os.getenv("S3_PROCESSED_BUCKET", "processed-s3-bucket-49")
cdn_domain = os.getenv("CDN_DOMAIN", None)  # Optional CDN domain
use_presigned_urls = os.getenv("USE_PRESIGNED_URLS", "true").lower() == "true"
presigned_url_expiry = int(os.getenv("PRESIGNED_URL_EXPIRY", "300"))  # 5 minutes default

# Cache for video metadata to reduce MongoDB load
video_cache = {}
cache_ttl = 300  # 5 minutes
cache_last_cleanup = time.time()

# Flag to control MongoDB polling
polling_active = True

@app.middleware("http")
async def add_cache_control_headers(request: Request, call_next):
    """Middleware to add caching headers based on content type."""
    response = await call_next(request)
    
    # For HLS playlists and segments
    path = request.url.path
    if path.endswith(".m3u8"):
        # Master and media playlists - short cache time as they might change
        response.headers["Cache-Control"] = "public, max-age=10"
    elif path.endswith(".ts"):
        # Segments - longer cache time as they don't change
        response.headers["Cache-Control"] = "public, max-age=31536000"  # 1 year
    
    # Add CORS headers for video content
    if path.endswith((".m3u8", ".ts")):
        response.headers["Access-Control-Allow-Origin"] = "*"
    
    return response

async def clean_expired_cache():
    """Clean expired items from the video cache."""
    global cache_last_cleanup
    
    current_time = time.time()
    # Only clean every 5 minutes
    if current_time - cache_last_cleanup < 300:
        return
    
    # Clean expired cache entries
    expired_keys = []
    for video_id, cache_data in video_cache.items():
        if current_time - cache_data["timestamp"] > cache_ttl:
            expired_keys.append(video_id)
    
    for key in expired_keys:
        del video_cache[key]
    
    cache_last_cleanup = current_time

async def get_video_metadata(video_id: str) -> Dict[str, Any]:
    """
    Get video metadata from cache or MongoDB.
    
    Args:
        video_id: The ID of the video
        
    Returns:
        Dict with video metadata
    """
    await clean_expired_cache()
    
    # Check cache first
    if video_id in video_cache:
        cache_data = video_cache[video_id]
        if time.time() - cache_data["timestamp"] < cache_ttl:
            return cache_data["data"]
    
    # Get from MongoDB
    video = await asyncio.to_thread(
        videos_collection.find_one,
        {"_id": video_id}
    )
    
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    if video.get("status") != "ready":
        raise HTTPException(status_code=400, detail=f"Video is not ready for playback. Current status: {video.get('status')}")
    
    # Cache the result
    video_cache[video_id] = {
        "data": video,
        "timestamp": time.time()
    }
    
    return video

async def generate_playback_urls(video_id: str) -> Dict[str, str]:
    """
    Generate playback URLs for a video.
    
    Args:
        video_id: The ID of the video
        
    Returns:
        Dict with playback URLs
    """
    # Check if the master playlist exists in S3
    master_playlist_key = f"{video_id}/master.m3u8"
    
    try:
        # Verify the master playlist exists
        await asyncio.to_thread(
            s3_client.head_object,
            Bucket=processed_bucket,
            Key=master_playlist_key
        )
        
        base_url = f"https://{processed_bucket}.s3.amazonaws.com/{video_id}"
        api_url = f"/api/videos/{video_id}"
        
        if use_presigned_urls:
            # Generate presigned URL for the master playlist
            presigned_url = await get_presigned_url(processed_bucket, master_playlist_key)
            hls_url = presigned_url
        else:
            # Use direct S3 URL or API URL
            if cdn_domain:
                hls_url = f"https://{cdn_domain}/{video_id}/master.m3u8"
            else:
                hls_url = f"{api_url}/manifest/master.m3u8"
        
        return {
            "hls": hls_url,
            "dash": None  # DASH not implemented yet
        }
    
    except ClientError as e:
        logger.error(f"Error checking master playlist for video {video_id}: {str(e)}")
        return {}

async def update_video_playback_urls(video_id: str):
    """
    Update the playback URLs for a video in MongoDB.
    
    Args:
        video_id: The ID of the video
    """
    try:
        # Generate playback URLs
        playback_urls = await generate_playback_urls(video_id)
        
        if not playback_urls:
            logger.warning(f"Could not generate playback URLs for video {video_id}")
            return
        
        # Update MongoDB
        await asyncio.to_thread(
            videos_collection.update_one,
            {"_id": video_id},
            {"$set": {
                "playback_urls": playback_urls,
                "updated_at": datetime.utcnow()
            }}
        )
        
        logger.info(f"Updated playback URLs for video {video_id}")
        
        # Update cache if present
        if video_id in video_cache:
            video_cache[video_id]["data"]["playback_urls"] = playback_urls
            video_cache[video_id]["data"]["updated_at"] = datetime.utcnow()
    
    except Exception as e:
        logger.error(f"Error updating playback URLs for video {video_id}: {str(e)}")

async def poll_ready_videos():
    """
    Continuously poll for videos with 'ready' status but no playback URLs.
    """
    global polling_active
    
    logger.info("Starting MongoDB polling for ready videos")
    
    while polling_active:
        try:
            # Find videos with 'ready' status but no playback_urls
            videos = await asyncio.to_thread(
                lambda: list(videos_collection.find({
                    "status": "ready",
                    "$or": [
                        {"playback_urls": {"$exists": False}},
                        {"playback_urls.hls": {"$exists": False}},
                        {"playback_urls.hls": None}
                    ]
                }))
            )
            
            if videos:
                logger.info(f"Found {len(videos)} videos needing playback URL generation")
                
                for video in videos:
                    video_id = video.get("_id")
                    await update_video_playback_urls(video_id)
            
            # Sleep before next poll
            await asyncio.sleep(5)
            
        except Exception as e:
            logger.error(f"Error in MongoDB polling: {str(e)}")
            await asyncio.sleep(5)  # Sleep on error before retry

async def get_presigned_url(bucket: str, key: str, expires_in: int = presigned_url_expiry) -> str:
    """
    Generate a presigned URL for an S3 object.
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        expires_in: Expiration time in seconds
        
    Returns:
        Presigned URL
    """
    try:
        url = await asyncio.to_thread(
            s3_client.generate_presigned_url,
            'get_object',
            Params={'Bucket': bucket, 'Key': key},
            ExpiresIn=expires_in
        )
        return url
    except ClientError as e:
        logger.error(f"Error generating presigned URL: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to generate URL")

async def stream_s3_object(bucket: str, key: str, request_headers: Dict[str, str]) -> StreamingResponse:
    """
    Stream content directly from S3.
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        request_headers: Headers from the original request
        
    Returns:
        StreamingResponse with the S3 object content
    """
    try:
        # Get object metadata
        head_object = await asyncio.to_thread(
            s3_client.head_object,
            Bucket=bucket,
            Key=key
        )
        
        content_length = head_object.get('ContentLength', 0)
        content_type = head_object.get('ContentType', 'application/octet-stream')
        
        # Get object
        s3_response = await asyncio.to_thread(
            s3_client.get_object,
            Bucket=bucket,
            Key=key
        )
        
        # Create an async generator to stream the content
        async def stream_content():
            body = s3_response['Body']
            while True:
                chunk = await asyncio.to_thread(body.read, 8192)  # 8KB chunks
                if not chunk:
                    break
                yield chunk
            
            # Close the S3 body when done
            await asyncio.to_thread(body.close)
        
        # Prepare response headers
        headers = {
            'Content-Type': content_type,
            'Content-Length': str(content_length),
            'Accept-Ranges': 'bytes'
        }
        
        # Add ETag for caching if available
        if 'ETag' in head_object:
            headers['ETag'] = head_object['ETag']
        
        return StreamingResponse(
            stream_content(),
            headers=headers,
            background=BackgroundTask(lambda: None)  # Dummy task
        )
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            raise HTTPException(status_code=404, detail="Resource not found")
        logger.error(f"Error streaming from S3: {str(e)}")
        raise HTTPException(status_code=500, detail="Error accessing content")

@app.get("/videos/{video_id}")
async def get_video_info(video_id: str):
    """
    Get video information.
    
    Args:
        video_id: The ID of the video
        
    Returns:
        Video metadata
    """
    video = await get_video_metadata(video_id)
    
    # If no playback URLs, try to generate them
    if not video.get("playback_urls") or not video.get("playback_urls", {}).get("hls"):
        await update_video_playback_urls(video_id)
        # Refresh video metadata
        video = await get_video_metadata(video_id)
    
    # Filter sensitive information
    safe_video = {
        "id": video_id,
        "title": video.get("title", "Untitled"),
        "duration": video.get("duration", 0),
        "width": video.get("width", 0),
        "height": video.get("height", 0),
        "thumbnail": video.get("thumbnail"),
        "status": video.get("status"),
        "created_at": video.get("created_at"),
        "playback_urls": video.get("playback_urls", {})
    }
    
    return JSONResponse(safe_video)

@app.get("/videos/{video_id}/play")
async def get_playback_url(video_id: str, redirect: bool = False):
    """
    Get or redirect to the video playback URL.
    
    Args:
        video_id: The ID of the video
        redirect: Whether to redirect to the playback URL
        
    Returns:
        Playback URL or redirect response
    """
    video = await get_video_metadata(video_id)
    
    playback_urls = video.get("playback_urls", {})
    hls_url = playback_urls.get("hls")
    
    # If no HLS URL, try to generate one
    if not hls_url:
        await update_video_playback_urls(video_id)
        video = await get_video_metadata(video_id)
        playback_urls = video.get("playback_urls", {})
        hls_url = playback_urls.get("hls")
    
    if not hls_url:
        raise HTTPException(status_code=400, detail="No HLS playback URL available")
    
    if redirect:
        return RedirectResponse(url=hls_url)
    
    return {"playback_url": hls_url}

@app.get("/videos/{video_id}/manifest/{file_path:path}")
async def serve_manifest(video_id: str, file_path: str, request: Request):
    """
    Serve an HLS manifest file.
    
    Args:
        video_id: The ID of the video
        file_path: Path to the manifest file
        request: FastAPI request object
        
    Returns:
        Manifest content or redirect
    """
    await get_video_metadata(video_id)  # Validate video exists and is ready
    
    s3_key = f"{video_id}/{file_path}"
    
    if use_presigned_urls:
        # Generate a presigned URL and redirect
        url = await get_presigned_url(processed_bucket, s3_key)
        return RedirectResponse(url=url)
    else:
        # Stream the content directly
        return await stream_s3_object(processed_bucket, s3_key, dict(request.headers))

@app.get("/videos/{video_id}/segments/{resolution}/{segment_file:path}")
async def serve_segment(video_id: str, resolution: str, segment_file: str, request: Request):
    """
    Serve an HLS segment file.
    
    Args:
        video_id: The ID of the video
        resolution: Resolution of the segment
        segment_file: Segment filename
        request: FastAPI request object
        
    Returns:
        Segment content or redirect
    """
    await get_video_metadata(video_id)  # Validate video exists and is ready
    
    s3_key = f"{video_id}/segments/{resolution}/{segment_file}"
    
    if use_presigned_urls:
        # Generate a presigned URL and redirect
        url = await get_presigned_url(processed_bucket, s3_key)
        return RedirectResponse(url=url)
    else:
        # Stream the content directly
        return await stream_s3_object(processed_bucket, s3_key, dict(request.headers))

@app.get("/videos/check-ready")
async def check_ready_videos():
    """
    Manually trigger checking for ready videos.
    
    Returns:
        Status of the operation
    """
    try:
        # Find videos with 'ready' status but no playback URLs
        videos = await asyncio.to_thread(
            lambda: list(videos_collection.find({
                "status": "ready",
                "$or": [
                    {"playback_urls": {"$exists": False}},
                    {"playback_urls.hls": {"$exists": False}},
                    {"playback_urls.hls": None}
                ]
            }))
        )
        
        processed = []
        
        for video in videos:
            video_id = video.get("_id")
            await update_video_playback_urls(video_id)
            processed.append(video_id)
        
        return {
            "status": "success",
            "message": f"Processed {len(processed)} videos",
            "processed_videos": processed
        }
    
    except Exception as e:
        logger.error(f"Error in manual check: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing videos: {str(e)}")

@app.get("/healthcheck")
async def healthcheck():
    """Health check endpoint."""
    return {"status": "ok", "service": "video-delivery"}

@app.on_event("startup")
async def startup_event():
    """Start background tasks on application startup."""
    asyncio.create_task(poll_ready_videos())
    logger.info("Video Delivery Service started")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on application shutdown."""
    global polling_active
    polling_active = False
    logger.info("Video Delivery Service shutting down")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)