import logging
import os 
import boto3
from fastapi import UploadFile
from pymongo import MongoClient
from botocore.exceptions import ClientError
import asyncio
from app.models.video import VideoMetadata 
import json

logger = logging.getLogger(__name__)

class UploadService:
    # Correct constructor name: __init__
    def __init__(self):
        mongo_uri = os.getenv("MONGO_URI", "localhost:27017")  # declared the endpoint for the mongo db either from MONGO_URI env variable or a default value
        self.mongo_client = MongoClient(mongo_uri)  # declared the mongo client which handles the communication with the mongo db and we use this client to access the database and collections
        self.db = self.mongo_client[os.getenv("MONGO_DB", "abr_streaming")]  # declared the database to use for the upload service named abr_streaming
        self.videos_collection = self.db.videos  # declared the collection to use for the upload service named videos - collection is like a table in a relational database (here videos_collection is a collection of videos)

        self.s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))  # declared the s3 client which handles the communication with the s3 bucket and we use this client to access the bucket and objects
        self.s3_bucket = os.getenv("S3_RAW_BUCKET", "s3-raw-bucket-49")  # declared the bucket to use for the upload service named abr_streaming
        self.sqs_client = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-1"))  # declared the sqs client which handles the communication with the sqs queue and we use this client to access the queue and messages

        # declared the queue to use for the upload service named abr_streaming
        self.transcoding_queue_url = os.getenv("SQS_TRANSCODING_QUEUE", "https://sqs.us-east-1.amazonaws.com/891612545820/ABR-Streaming-Queue")

    # Helper method to upload file to S3
    async def _upload_to_s3(self, file: UploadFile, s3_key: str):
        logger.info(f"Uploading file to S3: {s3_key}")
        try:
            contents = await file.read()  # Read file contents asynchronously
            self.s3_client.put_object(
                Bucket=self.s3_bucket,  # Use s3_bucket attribute for the bucket name
                Key=s3_key,
                Body=contents,
                ContentType=file.content_type
            )
            await file.seek(0)  # Reset file pointer
            logger.info(f"Uploaded file to S3: {s3_key}")
        except ClientError as e:
            logger.error(f"Error uploading file to S3: {str(e)}")
            raise

    # Helper method to update metadata in MongoDB (corrected method name)
    async def _update_metadata(self, metadata: VideoMetadata):
        logger.info(f"Updating metadata for video: {metadata.video_id}")
        data = metadata.to_dict()
        # here asyncio.get_event_loop() is used to get the event loop and then we use loop.run_in_executor() to run the update_one operation in a separate thread. This is because the update_one operation is blocking and we don't want to block the event loop.
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.videos_collection.update_one(
                {"video_id": metadata.video_id},
                {"$set": data},
                upsert=True
            )
        )
        logger.info(f"Metadata updated for video {metadata.video_id}")

    # Helper method to queue a transcoding job in SQS.
    async def _queue_transcoding_job(self, metadata: VideoMetadata):
        logger.info(f"Queuing transcoding job for video: {metadata.video_id}")
        message = {
            "video_id": metadata.video_id,
            "input_path": metadata.upload_path,
            "filename": metadata.filename,
        }
        # Send message to SQS
        try: #this message is sent to SQS and it is stored in the queue and then the transcoding service will pick it up and process it.
            loop = asyncio.get_event_loop() # this is how the transcioing service will handle it inode snippet form example - 
            await loop.run_in_executor(
                None,
                lambda: self.sqs_client.send_message(
                    QueueUrl=self.transcoding_queue_url,
                    MessageBody=json.dumps(message)
                )
            )
            logger.info(f"Transcoding job queued for video {metadata.video_id}")
        except ClientError as e:
            logger.error(f"Error queueing transcoding job: {str(e)}")
            raise

    # Method to get video status from MongoDB.
    async def get_video_status(self, video_id: str):
        logger.info(f"Getting status for video {video_id}")
        # Query MongoDB for the video
        loop = asyncio.get_event_loop()
        video_doc = await loop.run_in_executor(
            None,
            lambda: self.videos_collection.find_one({"video_id": video_id})
        )
        if not video_doc:
            logger.error(f"Video {video_id} not found")
            raise ValueError(f"Video with ID {video_id} not found")
        # Return status information
        return {
            "video_id": video_id,
            "status": video_doc.get("status", "unknown"),
            "title": video_doc.get("title"),
            "created_at": video_doc.get("created_at"),
            "updated_at": video_doc.get("updated_at"),
            "error": video_doc.get("error")
        }

    # Main method to process the upload
    async def process_upload(self, file: UploadFile, metadata: VideoMetadata):
        """
        Process a video upload by:
        1. Uploading the file to S3
        2. Updating metadata in MongoDB
        3. Queuing a transcoding job
        
        Args:
            file: The uploaded video file
            metadata: The video metadata
        """
        try:
            # Update status to uploading
            metadata.status = "uploading"
            await self._update_metadata(metadata)
            
            # Upload to S3
            s3_key = f"raw/{metadata.video_id}/{metadata.filename}"
            await self._upload_to_s3(file, s3_key)
            
            # Update metadata with upload path
            metadata.upload_path = s3_key
            metadata.status = "uploaded"
            await self._update_metadata(metadata)
            
            # Queue transcoding job
            await self._queue_transcoding_job(metadata)
            
            logger.info(f"Upload processed successfully for video {metadata.video_id}")
        except Exception as e:
            logger.error(f"Error processing upload: {str(e)}")
            # Update metadata with error
            metadata.status = "failed"
            metadata.error = str(e)
            await self._update_metadata(metadata)
            raise
