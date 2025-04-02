import os
from loguru import logger


class HLSManifestGenerator:
    """Utility class for generating HLS manifests."""
    
    def create_master_playlist(self, renditions):
        """
        Create a master playlist (index.m3u8) for adaptive bitrate streaming.
        
        Args:
            renditions: List of rendition dictionaries containing:
                - resolution: String like "1920x1080"
                - bitrate: Integer bitrate in kbps
                - codec: Video codec (optional, defaults to "avc1.4D401F,mp4a.40.2")
        
        Returns:
            str: HLS master playlist content
        """
        logger.info("Creating master playlist")
        
        # Start with the required header
        playlist = [
            "#EXTM3U",
            "#EXT-X-VERSION:3"
        ]
        
        # Add each rendition
        for rendition in renditions:
            resolution = rendition.get("resolution", "")
            bitrate = rendition.get("bitrate", 0)
            codec = rendition.get("codec", "avc1.4D401F,mp4a.40.2")
            
            # Skip invalid renditions
            if not resolution or not bitrate:
                continue
                
            width, height = resolution.split("x")
            
            # Calculate bandwidth in bits per second (bitrate is in kbps)
            bandwidth = bitrate * 1000
            
            # Add STREAM-INF tag
            playlist.append(
                f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={resolution},CODECS="{codec}"'
            )
            
            # Add the playlist URL (using resolution and bitrate as identifier)
            resolution_id = resolution.replace("x", "_")
            playlist.append(f"{resolution_id}_{bitrate}.m3u8")
        
        # Join all lines with newlines and return
        return "\n".join(playlist)
    
    def create_media_playlist(self, segments, target_duration, segment_base_url=""):
        """
        Create a media playlist (.m3u8) for a specific rendition.
        
        Args:
            segments: List of segment filenames
            target_duration: Target segment duration in seconds
            segment_base_url: Base URL for segments (optional)
        
        Returns:
            str: HLS media playlist content
        """
        logger.info(f"Creating media playlist with {len(segments)} segments")
        
        # Start with the required headers
        playlist = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{target_duration}",
            "#EXT-X-MEDIA-SEQUENCE:0",
            "#EXT-X-PLAYLIST-TYPE:VOD"
        ]
        
        # Add each segment
        for segment in segments:
            # Add EXTINF tag (duration)
            playlist.append(f"#EXTINF:{target_duration},")
            
            # Add the segment URL
            playlist.append(f"{segment_base_url}{segment}")
        
        # Add the end marker
        playlist.append("#EXT-X-ENDLIST")
        
        # Join all lines with newlines and return
        return "\n".join(playlist)