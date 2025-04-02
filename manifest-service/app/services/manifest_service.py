import os 
import json
from loguru import logger
import boto3
from botocore.exceptions import ClientError
from .metadata_service import MetadataService
from ..utils.s3 import S3Helper
from ..utils.hls import HLSManifestGenerator

class ManifestService:
    """
    Service for generating HLS manifests.

    This service creates the master manifest file which will be used by the client to request the video segments. 
    So when the video is requested by the client the manifest file will be returned and the client will be able to request the video segments from the S3 bucket adaptively.

    """
    def __init_(self):
        self.metadata_service = MetadataService()
        self.s3_helper = S3Helper()
        self.hls_generator = HLSManifestGenerator()

        self.processed_bucket = os.getenv("S3_PROCESSED_BUCKET", "processed-s3-bucket-49")
        self.manifest_folder = os.getenv("MANIFEST_FOLDER", "manifests")
        self.segment_folder = os.getenv("SEGMENT_FOLDER", "segments")
        self.target_duration = int(os.getenv("HLS_TARGET_DURATION", "6"))

    async def generate_manifest(self, video_id, renditions):
        """
        Generate master and media playlists for a video.
        
        Args:
            video_id: The ID of the video
            renditions: List of rendition dictionaries containing:
                - resolution: String like "1920x1080"
                - bitrate: Integer bitrate in kbps
                - segment_count: Number of segments
                - segment_prefix: Prefix for segment files
        
        Returns:
            dict: Manifest URLs and paths
        """

        try:
            logger.info(f"Generating manifests for video {video_id}")

            await self.metadata_service.update_status(video_id, "generating manifests") # update the video metadata in mongodb as "generating"
            master_playlist = self.hls_generator.create_master_playlist(renditions)  # this will call the create_master_playlist method of the HLSManifestGenerator class and get the master playlist
            master_playlist_key = f"{video_id}/{self.manifest_folder}/index.m3u8"

        # Upload the master playlist to S3
            await self.s3_helper.upload_file_content(
                bucket=self.processed_bucket,
                key=master_playlist_key,
                content=master_playlist,
                content_type="application/vnd.apple.mpegurl"
            )

            # Generate media playlists for each reendition
            media_playlist_urls = {}

            for rendition in renditions:
                resolution = rendition.get("resolution", "").replace("x", "_")
                bitrate = rendition.get("bitrate", 0)
                segment_count = rendition.get("segment_count", 0)
                segment_duration = rendition.get("segment_duration", self.target_duration)
                segment_prefix = rendition.get("segment_prefix", f"{video_id}_{resolution}_{bitrate}")

                segments = await self.list_rendition_segments(video_id, segment_prefix) # this method will list the segments for the given rendition and then we will create the media playlist for that rendition

                # now generating the media playlist for the rendition
                media_playlist = self.hls_generator.create_media_playlist( # this will  call the create_media_playlist method of the HLSManifestGenerator class and get the media playlist
                    segments = segments,
                    target_duration = segment_duration,
                    segment_base_url = f"../{self.segment_folder}/"
                )

                media_playlist_key = f"{video_id}/{self.manifest_folder}/{resolution}_{bitrate}.m3u8" # this will be the path of the media playlist in the s3 bucket
                # Upload the media playlist to S3
                await self.s3_helper.upload_file_content(
                    bucket=self.processed_bucket,
                    key=media_playlist_key,
                    content=media_playlist,
                    content_type="application/vnd.apple.mpegurl"
                )
                media_playlist_urls[f"{resolution}_{bitrate}"] = self.s3_helper.get_object_url( #this will add the url of the media playlist to the media_playlist_urls dictionary
                    bucket=self.processed_bucket,
                    key=media_playlist_key
                )
            master_playlist_url = self.s3_helper.get_object_url(
                bucket=self.processed_bucket,
                key=master_playlist_key
            )

            manifest_info = {
                "master_playlist": master_playlist_url,
                "media_playlists": media_playlist_urls,
                "master_playlist_key": master_playlist_key
            }
            
            # Update video status to "ready"
            await self.metadata_service.update_status(video_id, "ready")
            
            # Update manifest URLs in metadata
            await self.metadata_service.update_manifest_info(video_id, manifest_info)
            
            logger.info(f"Manifests generated successfully for video {video_id}")
            return manifest_info
            
        except Exception as e:
            error_message = f"Failed to generate manifests: {str(e)}"
            logger.error(error_message)
            await self.metadata_service.update_status(video_id, "failed", error=error_message)
            raise
    
    async def list_rendition_segments(self, video_id, segment_prefix):
        """
        List all segments for a specific rendition.
        
        Args:
            video_id: The ID of the video
            segment_prefix: Prefix for segment files
        
        Returns:
            list: List of segment filenames sorted by sequence number
        """
        try:
            # Define the segment directory in S3
            segment_dir = f"{video_id}/{self.segment_folder}/"
            
            # List objects in the segment directory with the given prefix
            segment_files = await self.s3_helper.list_objects(
                bucket=self.processed_bucket,
                prefix=f"{segment_dir}{segment_prefix}"
            )
            
            # Extract filenames from the full paths and sort them by sequence number
            segments = [os.path.basename(file) for file in segment_files]
            segments.sort(key=lambda x: int(x.split('_')[-1].split('.')[0]))
            
            return segments
            
        except Exception as e:
            logger.error(f"Failed to list segments: {str(e)}")
            raise
    
    async def regenerate_manifest(self, video_id):
        """
        Regenerate manifests for a video.
        
        This can be used when segments have been updated or added.
        
        Args:
            video_id: The ID of the video
        
        Returns:
            dict: Manifest URLs and paths
        """
        try:
            # Get video metadata including renditions
            video_metadata = await self.metadata_service.get_video_metadata(video_id)
            
            if not video_metadata:
                raise ValueError(f"No metadata found for video {video_id}")
            
            renditions = video_metadata.get("renditions", [])
            
            if not renditions:
                raise ValueError(f"No renditions found for video {video_id}")
            
            # Regenerate manifests
            return await self.generate_manifests(video_id, renditions)
            
        except Exception as e:
            error_message = f"Failed to regenerate manifests: {str(e)}"
            logger.error(error_message)
            await self.metadata_service.update_status(video_id, "failed", error=error_message)
            raise
                
