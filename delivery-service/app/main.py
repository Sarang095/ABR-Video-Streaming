import os
import json
import asyncio
import boto3
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from botocore.exceptions import ClientError
from loguru import logger
import time
from typing import Dict, Any

# Initialize FastAPI app
app = FastAPI(title="Simple Video Delivery")

# Add CORS middleware with permissive settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize clients
s3_client = boto3.client('s3', region_name=os.getenv("AWS_REGION", "us-east-1"))
sqs_client = boto3.client('sqs', region_name=os.getenv("AWS_REGION", "us-east-1"))

# Get configuration from environment
processed_bucket = os.getenv("S3_PROCESSED_BUCKET", "processed-s3-bucket-49")
delivery_queue_url = os.getenv("DELIVERY_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/014249603349/video-delivery-queue")

# Simple cache for video metadata and content caching
video_cache = {}
content_cache = {}  # Simple memory cache for small files like playlists
cache_ttl = 300  # 5 minutes

def remap_path(video_id: str, file_path: str) -> str:
    """
    Remap frontend path to actual S3 path structure
    
    Args:
        video_id: Video ID
        file_path: Frontend path
        
    Returns:
        The actual S3 key
    """
    # Handle master playlist
    if file_path == "master.m3u8":
        return f"{video_id}/master.m3u8"
    
    # Fix the path structure for quality playlists and segments
    # From: segments/480p/playlist.m3u8 or segments/480p/segment_000.ts 
    # To: 480p/playlist.m3u8 or 480p/segment_000.ts
    if "segments/" in file_path:
        # Remove the 'segments/' prefix
        return f"{video_id}/{file_path.replace('segments/', '')}"
    
    # Handle other paths directly
    return f"{video_id}/{file_path}"

async def stream_from_s3(bucket: str, key: str) -> Response:
    """
    Optimized streaming function that implements different strategies for playlists vs segments
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        
    Returns:
        StreamingResponse or Response depending on content type
    """
    try:
        # Check if we have this content cached (for small files like playlists)
        cache_key = f"{bucket}:{key}"
        if key.endswith(".m3u8") and cache_key in content_cache:
            cache_entry = content_cache[cache_key]
            # Check if cache is still valid
            if time.time() - cache_entry["timestamp"] < 10:  # 10 seconds TTL for playlists
                logger.info(f"Cache hit for {key}")
                return Response(
                    content=cache_entry["content"],
                    headers=cache_entry["headers"]
                )
        
        # Get object from S3
        logger.debug(f"Fetching from S3: {bucket}/{key}")
        response = await asyncio.to_thread(
            s3_client.get_object,
            Bucket=bucket,
            Key=key
        )
        
        # Determine content type
        content_type = "application/octet-stream"
        if key.endswith(".m3u8"):
            content_type = "application/vnd.apple.mpegurl"
        elif key.endswith(".ts"):
            content_type = "video/MP2T"
        
        # For small files like playlists, fetch all at once to reduce latency
        if key.endswith(".m3u8"):
            content = await asyncio.to_thread(response['Body'].read)
            await asyncio.to_thread(response['Body'].close)
            
            headers = {
                "Content-Type": content_type,
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Cache-Control": "max-age=10",  # Short client-side cache for playlists
            }
            
            # Cache the playlist content
            content_cache[cache_key] = {
                "content": content,
                "headers": headers,
                "timestamp": time.time()
            }
            
            return Response(content=content, headers=headers)
        
        # For segment files, optimize for streaming with larger chunks
        else:
            content_length = int(response.get('ContentLength', 0))
            
            async def stream_content():
                body = response['Body']
                # Use larger chunks for video segments (1MB chunks)
                chunk_size = 1024 * 1024
                try:
                    while True:
                        chunk = await asyncio.to_thread(body.read, chunk_size)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    await asyncio.to_thread(body.close)
            
            headers = {
                "Content-Type": content_type,
                "Content-Length": str(content_length),
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Cache-Control": "max-age=604800",  # Cache segments for 7 days on client
                "Accept-Ranges": "bytes"
            }
            
            return StreamingResponse(
                stream_content(),
                headers=headers
            )
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            logger.error(f"Resource not found in S3: {key}")
            raise HTTPException(status_code=404, detail=f"Resource not found: {key}")
        logger.error(f"Error streaming from S3: {str(e)}")
        raise HTTPException(status_code=500, detail="Error accessing content")

async def poll_sqs_queue():
    """Simple SQS polling for video processing notifications."""
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
                        
                        if video_id and status == "ready":
                            # Add to cache
                            video_cache[video_id] = {
                                "id": video_id,
                                "status": "ready",
                                "timestamp": time.time()
                            }
                            logger.info(f"Video {video_id} is ready for playback")
                        
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
    """Start the SQS polling task on app startup."""
    asyncio.create_task(poll_sqs_queue())
    logger.info("Optimized video delivery service started")

@app.get("/videos/{video_id}/play")
async def get_video_info(video_id: str):
    """Simple endpoint to get a video's playback URL."""
    # Check cache or assume it exists
    if video_id in video_cache and video_cache[video_id]["status"] == "ready":
        # Video is known to be ready
        pass
    else:
        # Check if master playlist exists in S3
        try:
            await asyncio.to_thread(
                s3_client.head_object,
                Bucket=processed_bucket,
                Key=f"{video_id}/master.m3u8"
            )
            # If we get here, the file exists
            video_cache[video_id] = {
                "id": video_id,
                "status": "ready",
                "timestamp": time.time()
            }
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                raise HTTPException(status_code=404, detail="Video not found or not processed yet")
            else:
                logger.error(f"Error checking S3: {str(e)}")
                raise HTTPException(status_code=500, detail="Error checking video status")
    
    # Video exists, return playback URL
    return {
        "playback_url": f"/videos/{video_id}/master.m3u8",
        "status": "ready"
    }

@app.get("/videos/{video_id}/{file_path:path}")
async def serve_video_file(video_id: str, file_path: str):
    """
    Serve video files directly from S3 with proper path remapping.
    Maps frontend path requests to actual S3 structure.
    """
    logger.info(f"Requested file: {video_id}/{file_path}")
    
    # Remap the path to match S3 structure
    s3_key = remap_path(video_id, file_path)
    logger.info(f"Remapped to S3 key: {s3_key}")
    
    return await stream_from_s3(processed_bucket, s3_key)

@app.get("/healthcheck")
async def healthcheck():
    """Health check endpoint."""
    return {"status": "ok", "service": "optimized-video-delivery"}

if __name__ == "__main__":
    import uvicorn
    # Increase workers to handle more concurrent connections
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=4)