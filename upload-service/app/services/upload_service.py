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
    def _init_(self):
        mongo_uri = os.getenv("MONGO_URI") #declared the endpoint for the mongo db either from MONGO_URI env variable or a default value
        self.mongo_client = MongoClient(mongo_uri) #declared the mongo client which handles the communication with the mongo db and we use this client to access the database and collections
        self.db = self.mongo_client[os.getenv("MONGO_DB", "abr_streaming")] #declared the database to use for the upload service named abr_streaming
        self.videos_collection = self.db.videos #declared the collection to use for the upload service named videos - collection is like a table in a relational database (here videos_collection is a collection of videos)

        self.s3_client = boto3.client("s3") #declared the s3 client which handles the communication with the s3 bucket and we use this client to access the bucket and objects
        self.s3_bucket = os.getenv("S3_RAW_BUCKET", "s3-raw-bucket-49") #declared the bucket to use for the upload service named abr_streaming
        self.sqs_client = boto3.client("sqs") #declared the sqs client which handles the communication with the sqs queue and we use this client to access the queue and messages

        self.trascoding_queue_url = os.getenv("SQS_TRANSCODING_QUEUE", "https://sqs.us-east-1.amazonaws.com/891612545820/SQS-ABR-Streaming") #declared the queue to use for the upload service named abr_streaming

    async def _upload_to_s3(self, file: UploadFile, s3_key:str):
        logger.info(f"Uploading file to S3: {s3_key}")

        try:
            contents = await file.read()
            self.s3_client.put_object(
                Bucket=self.raw_bucket,
                Key=s3_key,
                Body=contents,
                ContentType=file.content_type
            )

            await file.seek(0)

            logger.info(f"Uploaded file to S3: {s3_key}")

        except ClientError as e:
            logger.error(f"Error uploading file to S3: {str(e)}")
            raise
    
    async def _update_meatdata(self, metadata: VideoMetadata):
        logger.info(f"Updating metadata for video: {metadata.video_id}")

        data = metadata.to_dict()
        
        loop = asyncio.get_event_loop() #here asyncio.get_event_loop() is used to get the event loop and then we use loop.run_in_executor() to run the update_one operation in a separate thread. This is because the update_one operation is blocking and we don't want to block the event loop.
        await loop.run_in_executor(
            None,
            lambda: self.videos_collection.update_one(
                {"video_id": metadata.video_id},
                {"$set": data},
                upsert=True
            )
        )
        
        logger.info(f"Metadata updated for video {metadata.video_id}")


    async def _queue_transcoding_job(self, metadata: VideoMetadata):
        logger.info(f"Queuing transcoding job for video: {metadata.video_id}")
        message = {
            "video_id": metadata.video_id,
            "input_path": metadata.upload_path,
            "filename": metadata.filename,
        }
        
        # Send message to SQS
        try:
            loop = asyncio.get_event_loop()
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

