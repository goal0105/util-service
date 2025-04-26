import os
import logging
from pathlib import Path
import yt_dlp
from yt_dlp.utils import DownloadError
from werkzeug.exceptions import BadRequest

logger = logging.getLogger(__name__)

MAX_DURATION_MINUTES = 30
MAX_FILE_SIZE_MB = 100

class Download:
    def __init__(self, output_dir=os.getcwd(),  debug=False):
        self.output_dir = output_dir
        self.debug_flag = debug
         
        # Ensure 'uploads' directory exists
        self.uploads_dir = os.path.join(self.output_dir, "uploads")
        os.makedirs(self.uploads_dir, exist_ok=True)

    def download_youtube_audio(self, url: str, temp_dir : str) -> str:
        app_dir = Path(__file__).resolve().parent
        cookie_file = os.path.join(app_dir, 'youtube', 'cookies.txt')
        
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'force_generic_extractor': False,
            'cookiefile': cookie_file if os.path.exists(cookie_file) else None,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip,deflate',
                'Referer': 'https://www.youtube.com/'
            }
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # First try to extract info
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    raise BadRequest("Could not fetch video information")
                
                duration = info.get('duration', 0)
                if duration > MAX_DURATION_MINUTES * 60:
                    raise BadRequest(f"Video exceeds maximum duration of {MAX_DURATION_MINUTES} minutes")

                filesize = info.get('filesize', 0)
                if filesize and filesize > MAX_FILE_SIZE_MB * 1024 * 1024:
                    raise BadRequest(f"File size exceeds {MAX_FILE_SIZE_MB}MB limit")
                
                # Now download
                downloaded_info = ydl.extract_info(url, download=True)
                
                return ydl.prepare_filename(downloaded_info)  # download path

        except DownloadError as e:
            error_msg = str(e).lower()
            if "private video" in error_msg:
                raise BadRequest("Private videos are not supported")
            elif "age restriction" in error_msg or "age-restricted" in error_msg:
                raise BadRequest("Age-restricted content is not supported")
            elif "copyright" in error_msg:
                raise BadRequest("Video is not available due to copyright restrictions")
            elif "sign in" in error_msg or "bot" in error_msg:
                if not os.path.exists(cookie_file):
                    raise BadRequest("YouTube authentication required. Server cookies not configured.")
                else:
                    raise BadRequest("Authentication failed. Server cookies may need to be updated.")
            else:
                raise BadRequest(f"Failed to download video: {str(e)}")

