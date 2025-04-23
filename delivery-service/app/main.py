import os
import json
import asyncio
import boto3
from fastapi import FastAPI, HTTPException, Request
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
delivery_queue_url = os.getenv("DELIVERY_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/891612545820/video-delivery-queue")

# Simple cache for video metadata
video_cache = {}
cache_ttl = 300  # 5 minutes

async def stream_from_s3(bucket: str, key: str) -> StreamingResponse:
    """
    Stream content directly from S3 without any complex logic.
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        
    Returns:
        StreamingResponse with the S3 object content
    """
    try:
        # Get object
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
        
        # Create an async generator to stream the content
        async def stream_content():
            body = response['Body']
            while True:
                chunk = await asyncio.to_thread(body.read, 8192)  # 8KB chunks
                if not chunk:
                    break
                yield chunk
            await asyncio.to_thread(body.close)
        
        # Setup headers for HLS content
        headers = {
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Cache-Control": "max-age=31536000" if key.endswith(".ts") else "max-age=10",
        }
        
        return StreamingResponse(
            stream_content(),
            headers=headers
        )
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            raise HTTPException(status_code=404, detail="Resource not found")
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
    logger.info("Simple video delivery service started")

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
    Serve any video file directly from S3.
    This handles both manifest and segment files with the correct structure.
    """
    logger.info(f"Requested file: {video_id}/{file_path}")
    
    # Handle the master playlist
    if file_path == "master.m3u8":
        s3_key = f"{video_id}/master.m3u8"
        return await stream_from_s3(processed_bucket, s3_key)
    
    # Handle the segments paths based on the actual structure
    if file_path.startswith("segments/"):
        # The player UI requests in format: segments/360p/playlist.m3u8 or segments/360p/segment_000.ts
        parts = file_path.split("/")
        if len(parts) >= 2:
            resolution = parts[1]  # This gets the "360p" part
            remaining_path = "/".join(parts[2:])  # This gets "playlist.m3u8" or segment files
            
            # Map to actual structure: video_id/360p/playlist.m3u8 or video_id/360p/segment_000.ts
            s3_key = f"{video_id}/{resolution}/{remaining_path}"
            logger.info(f"Remapped path: {file_path} → {s3_key}")
            return await stream_from_s3(processed_bucket, s3_key)
    
    # For all other files, use the original path structure
    s3_key = f"{video_id}/{file_path}"
    return await stream_from_s3(processed_bucket, s3_key)

@app.get("/healthcheck")
async def healthcheck():
    """Health check endpoint."""
    return {"status": "ok", "service": "simple-video-delivery"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)