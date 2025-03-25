import asyncio
from loguru import logger
from botocore.exceptions import ClientError

class S3Utilities:
    """
    Utility class for S3 operations.
    
    This class provides methods for:
    1. Downloading files from S3
    2. Uploading files to S3
    """
    
    def __init__(self, s3_client):
        """
        Initialize the S3 utilities.
        
        Args:
            s3_client: Boto3 S3 client
        """
        self.s3_client = s3_client
    
    async def download_file(self, bucket, key, local_path):
        """
        Download a file from S3.
        
        Args:
            bucket: S3 bucket name
            key: S3 object key
            local_path: Local path to save the file
        """
        logger.info(f"Downloading from S3: {bucket}/{key} to {local_path}")
        try:
            # Use asyncio to run S3 operation in a thread
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.download_file(bucket, key, local_path)
            )
            logger.info(f"Downloaded file from S3: {bucket}/{key}")
        except ClientError as e:
            logger.error(f"Error downloading file from S3: {str(e)}")
            raise

    async def upload_file(self, local_path, bucket, key, content_type):
        """
        Upload a file to S3.
        
        Args:
            local_path: Local file path to upload
            bucket: S3 bucket name
            key: S3 object key (destination path)
            content_type: MIME type of the file
        """
        logger.info(f"Uploading file to S3: {local_path} to {bucket}/{key}")
        try:
            # Use asyncio to run S3 operation in a thread
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.upload_file(
                    local_path,
                    bucket,
                    key,
                    ExtraArgs={"ContentType": content_type}
                )
            )
            logger.info(f"Uploaded file to S3: {bucket}/{key}")
        except ClientError as e:
            logger.error(f"Error uploading file to S3: {str(e)}")
            raise
#this file is used to upload and download files from s3
# our transcoding service will use this file to upload and download files from s3 and then it will use the ffmpeg to transcode the video and then it will upload the transcoded video to s3 using the upload_file method from here