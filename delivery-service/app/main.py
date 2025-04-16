import os
import json
import asyncio
import boto3
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse
from botocore.exceptions import ClientError
from loguru import logger
import time
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse
from typing import Optional, Dict, Any
import urllib.parse

# Initialize FastAPI app
app = FastAPI(title="Video Delivery Service")

# Add CORS middleware for browser access with more permissive settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges", "ETag"],
    max_age=86400  # Cache preflight requests for 24 hours
)

# Initialize clients
s3_client = boto3.client('s3', region_name=os.getenv("AWS_REGION", "us-east-1"))
sqs_client = boto3.client('sqs', region_name=os.getenv("AWS_REGION", "us-east-1"))

# Get configuration from environment
processed_bucket = os.getenv("S3_PROCESSED_BUCKET", "processed-s3-bucket-49")
cdn_domain = os.getenv("CDN_DOMAIN", None)  # Optional CDN domain
use_presigned_urls = os.getenv("USE_PRESIGNED_URLS", "false").lower() == "true"
presigned_url_expiry = int(os.getenv("PRESIGNED_URL_EXPIRY", "300"))  # 5 minutes default
delivery_queue_url = os.getenv("DELIVERY_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/891612545820/video-delivery-queue")

# Cache for video metadata
video_cache = {}
cache_ttl = 300  # 5 minutes
cache_last_cleanup = time.time()

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
    
    # Add CORS headers for video content for ALL responses
    # This ensures CORS headers are present even for error responses
    if path.endswith((".m3u8", ".ts")) or "videos" in path:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, ETag"
    
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

async def check_s3_object_exists(bucket: str, key: str) -> bool:
    """
    Check if an S3 object exists.
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        
    Returns:
        True if object exists, False otherwise
    """
    try:
        await asyncio.to_thread(
            s3_client.head_object,
            Bucket=bucket,
            Key=key
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        else:
            logger.error(f"Error checking S3 object: {str(e)}")
            raise

async def get_video_metadata(video_id: str) -> Dict[str, Any]:
    """
    Get video metadata from cache or generate it based on video ID.
    
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
    
    # Generate metadata based on video ID
    master_playlist_key = f"{video_id}/master.m3u8"
    
    # Check if video exists in S3
    exists = await check_s3_object_exists(processed_bucket, master_playlist_key)
    if not exists:
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Generate basic metadata
    playback_urls = {
        "hls": f"https://{processed_bucket}.s3.amazonaws.com/{video_id}/master.m3u8",
        "dash": None  # Add DASH support if needed in the future
    }
    
    # Try to get additional metadata if needed
    video_metadata = {
        "_id": video_id,
        "title": f"Video {video_id}",
        "status": "ready",  # If we found it in S3, it's ready
        "playback_urls": playback_urls,
        "created_at": None,  # We don't have this info
        # Add other fields as needed, based on what your frontend expects
    }
    
    # Cache the result
    video_cache[video_id] = {
        "data": video_metadata,
        "timestamp": time.time()
    }
    
    return video_metadata

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
            'Accept-Ranges': 'bytes',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': '*',
            'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, ETag'
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

async def poll_sqs_queue():
    """
    Poll SQS queue for video processing notifications.
    This runs as a background task to receive updates from the transcoding service.
    """
    logger.info("Starting SQS polling task")
    
    while True:
        try:
            # Receive messages from SQS queue
            response = await asyncio.to_thread(
                sqs_client.receive_message,
                QueueUrl=delivery_queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20  # Long polling
            )
            
            if "Messages" in response:
                for message in response["Messages"]:
                    try:
                        # Parse message body
                        body = json.loads(message["Body"])
                        video_id = body.get("video_id")
                        status = body.get("status")
                        
                        if video_id:
                            logger.info(f"Received message for video {video_id} with status {status}")
                            
                            if status == "ready":
                                # Remove from cache if it already exists
                                if video_id in video_cache:
                                    del video_cache[video_id]
                                
                                # Create a placeholder in the cache
                                master_playlist_url = f"https://{processed_bucket}.s3.amazonaws.com/{video_id}/master.m3u8"
                                video_cache[video_id] = {
                                    "data": {
                                        "_id": video_id,
                                        "title": f"Video {video_id}",
                                        "status": "ready",
                                        "playback_urls": {
                                            "hls": master_playlist_url,
                                            "dash": None
                                        },
                                        "created_at": body.get("timestamp")
                                    },
                                    "timestamp": time.time()
                                }
                                logger.info(f"Added video {video_id} to cache with playback URL: {master_playlist_url}")
                            elif status == "failed":
                                # Remove from cache if it exists
                                if video_id in video_cache:
                                    del video_cache[video_id]
                                error = body.get("error", "Unknown error")
                                logger.warning(f"Video {video_id} processing failed: {error}")
                        
                        # Delete the message
                        await asyncio.to_thread(
                            sqs_client.delete_message,
                            QueueUrl=delivery_queue_url,
                            ReceiptHandle=message["ReceiptHandle"]
                        )
                        
                    except Exception as e:
                        logger.error(f"Error processing SQS message: {str(e)}")
            
        except Exception as e:
            logger.error(f"Error polling SQS: {str(e)}")
            await asyncio.sleep(5)  # Backoff before retry

@app.on_event("startup")
async def startup_event():
    """
    Startup event that runs when the FastAPI application starts.
    Launches the SQS polling task in the background.
    """
    # Start the SQS polling task as a background task
    asyncio.create_task(poll_sqs_queue())
    logger.info("Video delivery service started")

# Add OPTIONS route handlers for CORS preflight requests
@app.options("/videos/{video_id}")
@app.options("/videos/{video_id}/play")
@app.options("/videos/{video_id}/manifest/{file_path:path}")
@app.options("/videos/{video_id}/segments/{resolution}/{segment_file:path}")
async def options_route():
    """Handle OPTIONS preflight requests for CORS."""
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "86400",  # Cache preflight for 24 hours
    }
    return Response(status_code=204, headers=headers)

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
    
    # Return safe video info
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
    
    return JSONResponse(
        content=safe_video,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*"
        }
    )

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
    
    if not hls_url:
        # If no HLS URL is stored, generate it
        hls_url = f"https://{processed_bucket}.s3.amazonaws.com/{video_id}/master.m3u8"
    
    # If using a CDN, replace the S3 URL with the CDN URL
    if cdn_domain:
        # Extract the path part from the S3 URL
        parsed_url = urllib.parse.urlparse(hls_url)
        path = parsed_url.path.lstrip('/')
        hls_url = f"https://{cdn_domain}/{path}"
    
    if redirect:
        response = RedirectResponse(url=hls_url)
        # Add CORS headers to redirect
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response
    
    return JSONResponse(
        content={"playback_url": hls_url},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*"
        }
    )

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
    # Check if video exists in cache or S3
    await get_video_metadata(video_id)
    
    s3_key = f"{video_id}/{file_path}"
    
    if use_presigned_urls:
        # Generate a presigned URL and redirect
        url = await get_presigned_url(processed_bucket, s3_key)
        response = RedirectResponse(url=url)
        # Add CORS headers to redirect
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response
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
    # Check if video exists in cache or S3
    await get_video_metadata(video_id)
    
    s3_key = f"{video_id}/segments/{resolution}/{segment_file}"
    
    if use_presigned_urls:
        # Generate a presigned URL and redirect
        url = await get_presigned_url(processed_bucket, s3_key)
        response = RedirectResponse(url=url)
        # Add CORS headers to redirect
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response
    else:
        # Stream the content directly
        return await stream_s3_object(processed_bucket, s3_key, dict(request.headers))

@app.get("/healthcheck")
async def healthcheck():
    """Health check endpoint."""
    return {"status": "ok", "service": "video-delivery"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)