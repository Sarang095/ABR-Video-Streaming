import os
import asyncio
import boto3
from botocore.exceptions import ClientError
from loguru import logger


class S3Helper:
    """Helper class for S3 operations."""
    
    def __init__(self):
        """Initialize the S3 helper with AWS credentials."""
        self.s3_client = boto3.client('s3',region_name=os.getenv("AWS_REGION", "us-east-1"))
        self.cloudfront_domain = os.getenv("CLOUDFRONT_DOMAIN", "")
    
    async def upload_file_content(self, bucket, key, content, content_type="text/plain"):
        """
        Upload content to S3 as a file.
        
        Args:
            bucket: S3 bucket name
            key: S3 object key (path)
            content: File content to upload
            content_type: MIME type of the file
        
        Returns:
            bool: True if upload was successful
        """
        try:
            logger.info(f"Uploading content to s3://{bucket}/{key}")
            
            # Use asyncio to run S3 operation in a thread
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=content,
                    ContentType=content_type
                )
            )
            
            logger.info(f"Upload completed for s3://{bucket}/{key}")
            return True
            
        except ClientError as e:
            logger.error(f"S3 upload error: {str(e)}")
            raise
    
    async def list_objects(self, bucket, prefix):
        """
        List objects in an S3 bucket with a given prefix.
        
        Args:
            bucket: S3 bucket name
            prefix: Object prefix (path)
        
        Returns:
            list: List of object keys
        """
        try:
            logger.info(f"Listing objects in s3://{bucket}/{prefix}")
            
            # Use asyncio to run S3 operation in a thread
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.s3_client.list_objects_v2(
                    Bucket=bucket,
                    Prefix=prefix
                )
            )
            
            # Extract the keys from the response
            objects = []
            if 'Contents' in response:
                objects = [item['Key'] for item in response['Contents']]
            
            logger.info(f"Found {len(objects)} objects in s3://{bucket}/{prefix}")
            return objects
            
        except ClientError as e:
            logger.error(f"S3 list error: {str(e)}")
            raise
    
    def get_object_url(self, bucket, key):
        """
        Get a URL for an S3 object.
        
        If a CloudFront domain is configured, it will use that.
        Otherwise, it will use the S3 URL.
        
        Args:
            bucket: S3 bucket name
            key: S3 object key (path)
        
        Returns:
            str: URL to access the object
        """
        if self.cloudfront_domain:
            # Use CloudFront URL
            return f"https://{self.cloudfront_domain}/{key}"
        else:
            # Use S3 URL
            return f"https://{bucket}.s3.amazonaws.com/{key}"