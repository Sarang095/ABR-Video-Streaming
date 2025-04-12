import os
import json
import asyncio
import signal
import boto3
from botocore.exceptions import ClientError
from loguru import logger
from .services.manifest_service import ManifestService

class SNSWorker:
    """
    Worker for consuming transcoding completion events from SNS.
    
    This worker listens for SNS messages that indicate a video transcoding
    has been completed, then triggers manifest generation.
    """
    
    def __init__(self):
        """Initialize the SNS worker."""
        self.sqs_client = boto3.client('sqs', region_name=os.getenv("AWS_REGION", "us-east-1"))
        self.queue_url = os.getenv("SQS_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/891612545820/ABR-Streaming-Queue")
        
        self.manifest_service = ManifestService()
        
        # Flag for controlling the worker loop
        self.running = False # this will be used to stop the worker loop 
    
    async def start(self):
        """Start the worker to listen for messages."""
        logger.info("Starting SNS worker for manifest generation")
        
        if not self.queue_url:
            logger.error("SQS_QUEUE_URL environment variable is not set")
            return
        
        self.running = True
        
        # This will run the worker in the background its a signal handler for the worker means that when the worker receives a signal it will call the stop method
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_event_loop().add_signal_handler(sig, self.stop)
        
        # Process messages in a loop
        while self.running:
            try:
                # Receive messages from the queue
                messages = await self.receive_messages()
                
                # Process each message
                for message in messages:
                    await self.process_message(message)
                
                # If no messages, wait a bit to avoid hammering the SQS API
                if not messages:
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"Error in worker loop: {str(e)}")
                await asyncio.sleep(5)
    
    def stop(self):
        """Stop the worker."""
        logger.info("Stopping SNS worker")
        self.running = False
    
    async def receive_messages(self):
        """
        Receive messages from the SQS queue.
        
        Returns:
            list: List of SQS message objects
        """
        try:
            # Use asyncio to run SQS operation in a thread
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.sqs_client.receive_message(
                    QueueUrl=self.queue_url,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=20
                )
            )
            
            return response.get('Messages', [])
            
        except ClientError as e:
            logger.error(f"SQS receive error: {str(e)}")
            return []
    
    async def delete_message(self, receipt_handle):
        """
        Delete a message from the SQS queue after processing.
        
        Args:
            receipt_handle: The receipt handle of the message
        """
        try:
            # Use asyncio to run SQS operation in a thread
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.sqs_client.delete_message(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=receipt_handle
                )
            )
            
        except ClientError as e:
            logger.error(f"SQS delete error: {str(e)}")
    
    async def process_message(self, message):
        """
        Process an SNS message from the SQS queue.
        
        Args:
            message: SQS message object
        """
        try:
            # Get message body and receipt handle
            body = message.get('Body', '{}')
            receipt_handle = message.get('ReceiptHandle')
            
            # Parse the message body (SNS message)
            sns_message = json.loads(body)
            
            # Parse the SNS message content
            content = json.loads(sns_message.get('Message', '{}'))
            
            # Check if this is a transcoding complete event
            if content.get('event_type') == 'transcoding_complete':
                video_id = content.get('video_id')
                renditions = content.get('renditions', [])
                
                if video_id and renditions:
                    logger.info(f"Processing transcoding complete event for video {video_id}")
                    
                    # Generate manifests for the video
                    await self.manifest_service.generate_manifest(video_id, renditions)
                else:
                    logger.warning("Invalid transcoding complete event: missing video_id or renditions")
            
            # Delete the message from the queue
            if receipt_handle:
                await self.delete_message(receipt_handle)
                
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {str(e)}")
            
            # Delete malformed messages to avoid processing them repeatedly
            if message.get('ReceiptHandle'):
                await self.delete_message(message['ReceiptHandle'])
                
        except Exception as e:
            logger.error(f"Message processing error: {str(e)}")