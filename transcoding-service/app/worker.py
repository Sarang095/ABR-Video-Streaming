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
transcoding_queue_url = os.getenv("SQS_TRANSCODING_QUEUE", "https://sqs.us-east-1.amazonaws.com/891612545820/SQS-ABR-Streaming")
transcoding_service = TranscodingService()

async def poll_queue():
    # this method will continously poll the sqs queue and if for any message it will process it and then delete the message from the queue
    #This is the startup point of this service - Its not like the other HTTP srvices instead it act as the worker in the background which is continously running and polling sqs
    while True:
        try:
            response = sqs_client.receive_message(
                QueueUrl=transcoding_queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                VisibilityTimeout=1800
            )
            if 'Message' in response:
                for message in response['Messagess']:
                    await process_message(message)
            else:
                logger.info("No messages in the queue")
                time.sleep(10)
            await asyncio.sleep(1) #this line is just to make sure that the loop doesn't run too fast because the loop runs too fast and it will consume all the messages in the queue
        except ClientError as e: # the error that might occur while polling the queue
            logger.error(f"Error polling queue: {str(e)}")
            asyncio.sleep(1)
        except Exception as e: # this error is for any other error that might occur while polling the queue
            logger.error(f"Error polling queue: {str(e)}")
            asyncio.sleep(5)

async def process_message(message): # this method will process the message received from the queue
    try:
        body = json.loads(message['Body']) #this is the message that is received from the queue
        logger.info(f"Processing transcoding job for video ID: {body.get('video_id')}")

        await transcoding_service.process_video(
            video_id = body.get('video_id'),
            input_path = body.get('input_path'),
            filename = body.get('filename')
        )

        # Delete the message from the queue after processing successfully
        sqs_client.delete_message(
            QueueUrl=transcoding_queue_url,
            ReceiptHandle=message['ReceiptHandle']  #ReciptHandle is the unique identifier for the message
        )
        logger.info(f"Message processed successfully for video ID: {body.get('video_id')}")

    except Exception as e:
        logger.error(f"Error processing transcoding job: {str(e)}")

if __name__ == "__main__": # this is the entry point of the program means that this is the first thing that will be executed when the program is run and it will start the polling of the queue
    logger.info("Transcoding Service Worker starting")
    asyncio.run(poll_queue())


