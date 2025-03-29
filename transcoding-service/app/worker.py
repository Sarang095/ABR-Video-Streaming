import json
import time
import boto3
import os
import asyncio
from loguru import logger
from dotenv import load_dotenv
from botocore.exceptions import ClientError

from app.main import init_app
from app.services.transcoding_service import TranscodingService

load_dotenv()

init_app()

sqs_client = boto3.client('sqs', region_name=os.getenv("AWS_REGION", "us-east-1"))
transcoding_queue_url = os.getenv("SQS_TRANSCODING_QUEUE", "https://sqs.us-east-1.amazonaws.com/891612545820/ABR-Streaming-Queue")
transcoding_service = TranscodingService()

async def poll_queue():
    # This method will continuously poll the SQS queue and process any messages
    while True:
        try:
            response = sqs_client.receive_message(
                QueueUrl=transcoding_queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                VisibilityTimeout=300  # Reduced from 1800 (30 min) to 300 (5 min)
            )
            
            if 'Messages' in response:
                for message in response['Messages']:
                    logger.info(f"Received message: {message['MessageId']}")
                    await process_message(message)
            else:
                logger.info("No messages in the queue")
                # Wait before polling again to avoid excessive polling
                await asyncio.sleep(10)  # Correctly awaited
            
            # Small delay between polls even when messages are found
            await asyncio.sleep(1)  # Added await
        except ClientError as e:
            logger.error(f"Error polling queue: {str(e)}")
            await asyncio.sleep(5)  # Added await
        except Exception as e:
            logger.error(f"Error polling queue: {str(e)}")
            await asyncio.sleep(10)  # Added await

async def process_message(message):
    try:
        body = json.loads(message['Body'])
        logger.info(f"Processing transcoding job for video ID: {body.get('video_id')}")

        await transcoding_service.process_video(
            video_id=body.get('video_id'),
            input_path=body.get('input_path'),
            filename=body.get('filename')
        )

        # Delete the message from the queue after processing successfully
        sqs_client.delete_message(
            QueueUrl=transcoding_queue_url,
            ReceiptHandle=message['ReceiptHandle']
        )
        logger.info(f"Message processed successfully for video ID: {body.get('video_id')}")

    except Exception as e:
        logger.error(f"Error processing transcoding job: {str(e)}")
        # Consider implementing a retry mechanism or dead-letter queue here

if __name__ == "__main__":
    logger.info("Transcoding Service Worker starting")
    asyncio.run(poll_queue())