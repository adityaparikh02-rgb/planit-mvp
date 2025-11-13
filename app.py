# ─────────────────────────────
# ✅ Fix Render + OpenAI "proxies" crash before anything else
# ─────────────────────────────
import os, sys, logging

# Completely nuke proxy variables before importing OpenAI
for var in [
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy"
]:
    if var in os.environ:
        print(f"Removing {var}")
        os.environ.pop(var)

os.environ["NO_PROXY"] = "*"
os.environ["PYTHONWARNINGS"] = "ignore"

# Prevent Render from re-injecting proxies later
if hasattr(sys, "__interactivehook__"):
    del sys.__interactivehook__

logging.getLogger("moviepy").setLevel(logging.ERROR)
os.environ["YT_DLP_NO_WARNINGS"] = "1"

print("✅ Proxy env cleaned. Ready to import dependencies.")


# ─────────────────────────────
# Now safe to import everything else
# ─────────────────────────────
import tempfile, re, subprocess, json, cv2, numpy as np, requests, sys, shutil, gc
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import sqlite3
from PIL import Image
from moviepy.editor import VideoFileClip
from openai import OpenAI
from httpx import Client as HttpxClient
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# Optional OCR - tesseract may not be available on all systems
OCR_AVAILABLE = False
try:
    from pytesseract import image_to_string
    # Test if tesseract binary is actually available
    import shutil
    tesseract_path = shutil.which("tesseract")
    if tesseract_path:
        OCR_AVAILABLE = True
        print(f"✅ OCR enabled - tesseract found at: {tesseract_path}")
        # Test OCR with a simple import check
        try:
            from PIL import Image
            import numpy as np
            # Quick test to ensure OCR works
            test_img = Image.new('RGB', (100, 100), color='white')
            image_to_string(test_img)  # This should work without error
            print("✅ OCR test successful")
        except Exception as test_e:
            print(f"⚠️ OCR test failed: {test_e}")
            OCR_AVAILABLE = False
    else:
        print("⚠️ pytesseract installed but tesseract binary not found in PATH - OCR will be skipped")
        print("   Try: brew install tesseract (macOS) or apt-get install tesseract-ocr (Linux)")
except ImportError:
    print("⚠️ pytesseract not available - OCR will be skipped")
    print("   Install with: pip install pytesseract")
except Exception as e:
    print(f"⚠️ OCR check failed: {e} - OCR will be skipped")

# ─────────────────────────────
# Setup
# ─────────────────────────────
app = Flask(__name__)
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'your-secret-key-change-in-production')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=30)
jwt = JWTManager(app)

# Allow all origins for CORS - needed for frontend to connect
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# Create a proxy-safe HTTP client
safe_httpx = HttpxClient(trust_env=False, timeout=30.0)

# Initialize OpenAI client lazily to avoid startup issues
_client_instance = None

def get_openai_client():
    global _client_instance
    if _client_instance is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
        _client_instance = OpenAI(api_key=api_key, max_retries=3, http_client=safe_httpx)
    return _client_instance
# Impersonate only works on systems with curl_cffi installed
# On Render (Linux) and localhost, we skip impersonate to avoid dependency issues
# Impersonate is optional - yt-dlp works fine without it
YT_IMPERSONATE = None
# Uncomment below if you have curl_cffi installed and want to use impersonate:
# if not (os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_HOSTNAME")):
#     YT_IMPERSONATE = "chrome-131:macos-14"

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# ─────────────────────────────
# Database Setup
# ─────────────────────────────
DB_PATH = os.path.join(os.getcwd(), "planit.db")

def init_db():
    """Initialize SQLite database with tables for users, places, history, and place cache."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Saved places (user-specific lists)
    c.execute('''
        CREATE TABLE IF NOT EXISTS saved_places (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            list_name TEXT NOT NULL,
            place_name TEXT NOT NULL,
            place_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, list_name, place_name)
        )
    ''')
    
    # History (user-specific extraction history)
    c.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            video_url TEXT NOT NULL,
            summary_title TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Place cache (for merging places across videos)
    c.execute('''
        CREATE TABLE IF NOT EXISTS place_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            place_name TEXT NOT NULL,
            place_address TEXT,
            place_data TEXT NOT NULL,
            video_urls TEXT NOT NULL,
            video_metadata TEXT,
            usernames TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(place_name, place_address)
        )
    ''')
    
    # Add video_metadata column if it doesn't exist (for existing databases)
    try:
        c.execute("ALTER TABLE place_cache ADD COLUMN video_metadata TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    conn.commit()
    conn.close()
    print("✅ Database initialized")

# Initialize database on startup
init_db()

def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ─────────────────────────────
# Cache Setup (for video-level caching)
# ─────────────────────────────
CACHE_PATH = os.path.join(os.getcwd(), "cache.json")
if not os.path.exists(CACHE_PATH):
    with open(CACHE_PATH, "w") as f:
        json.dump({}, f)

# ─────────────────────────────
# Cache Utilities
# ─────────────────────────────
def load_cache():
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except:
        return {}

def save_cache(cache):
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)

def get_tiktok_id(url):
    """Extract TikTok video ID from URL. Returns None for shortened URLs (will extract from metadata later)."""
    # Try standard /video/ format
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    # Shortened URLs (/t/ format) will be handled by extracting ID from metadata
    return None

def fetch_tiktok_photo_post(url):
    """Fetch and parse TikTok photo post HTML to extract caption and photo URLs."""
    try:
        print("🌐 Fetching TikTok photo post HTML...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.tiktok.com/",
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        html = response.text
        
        soup = BeautifulSoup(html, 'html.parser')
        
        meta = {}
        caption = ""
        photo_urls = []
        
        # Method 1: Try window.__UNIVERSAL_DATA__ (most reliable for photo posts)
        match = re.search(r'window\.__UNIVERSAL_DATA__\s*=\s*({.+?});', html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                print("✅ Found window.__UNIVERSAL_DATA__")
                
                # Recursively search for photo URLs and captions
                def find_photos_and_caption(obj, depth=0):
                    if depth > 10:  # Limit recursion depth
                        return None, []
                    
                    if isinstance(obj, dict):
                        # Look for ImageList or similar structures
                        if "ImageList" in obj:
                            urls = []
                            for img in obj["ImageList"]:
                                if isinstance(img, dict) and "UrlList" in img:
                                    if isinstance(img["UrlList"], list) and len(img["UrlList"]) > 0:
                                        urls.append(img["UrlList"][0])
                            if urls:
                                return None, urls
                        
                        # Look for caption/description fields
                        for key in ['desc', 'description', 'text', 'caption', 'content']:
                            if key in obj and obj[key]:
                                caption_text = str(obj[key])
                                if caption_text and len(caption_text) > 5:
                                    return caption_text, []
                        
                        # Recursively search nested objects
                        for value in obj.values():
                            found_caption, found_urls = find_photos_and_caption(value, depth + 1)
                            if found_urls:
                                photo_urls.extend(found_urls)
                            if found_caption and not caption:
                                caption = found_caption
                    
                    elif isinstance(obj, list):
                        for item in obj:
                            found_caption, found_urls = find_photos_and_caption(item, depth + 1)
                            if found_urls:
                                photo_urls.extend(found_urls)
                            if found_caption and not caption:
                                caption = found_caption
                    
                    return None, []
                
                found_caption, found_urls = find_photos_and_caption(data)
                if found_urls:
                    photo_urls = found_urls
                if found_caption:
                    caption = found_caption
                    
            except Exception as e:
                print(f"⚠️ Failed to parse __UNIVERSAL_DATA__: {e}")
        
        # Method 2: Try window.__UNIVERSAL_DATA_FOR_REHYDRATION__ (fallback)
        if not photo_urls:
            match = re.search(r'window\.__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*({.+?});', html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    print("✅ Found window.__UNIVERSAL_DATA_FOR_REHYDRATION__")
                    # Use same recursive search
                    def find_in_data(obj, depth=0):
                        if depth > 10:
                            return None, []
                        if isinstance(obj, dict):
                            if "ImageList" in obj:
                                urls = []
                                for img in obj.get("ImageList", []):
                                    if isinstance(img, dict) and "UrlList" in img:
                                        if isinstance(img["UrlList"], list) and len(img["UrlList"]) > 0:
                                            urls.append(img["UrlList"][0])
                                if urls:
                                    return None, urls
                            for key in ['desc', 'description', 'text', 'caption']:
                                if key in obj and obj[key]:
                                    return str(obj[key]), []
                            for value in obj.values():
                                c, u = find_in_data(value, depth + 1)
                                if u:
                                    photo_urls.extend(u)
                                if c and not caption:
                                    caption = c
                        elif isinstance(obj, list):
                            for item in obj:
                                c, u = find_in_data(item, depth + 1)
                                if u:
                                    photo_urls.extend(u)
                                if c and not caption:
                                    caption = c
                        return None, []
                    find_in_data(data)
                except Exception as e:
                    print(f"⚠️ Failed to parse __UNIVERSAL_DATA_FOR_REHYDRATION__: {e}")
        
        # Method 3: Try to extract from script tags with JSON
        if not photo_urls:
            scripts = soup.find_all('script', type='application/json')
            for script in scripts:
                if script.string:
                    try:
                        data = json.loads(script.string)
                        def find_caption(obj, depth=0):
                            if depth > 5:
                                return None
                            if isinstance(obj, dict):
                                for key in ['desc', 'description', 'text', 'caption', 'content']:
                                    if key in obj and obj[key]:
                                        return str(obj[key])
                                for value in obj.values():
                                    result = find_caption(value, depth + 1)
                                    if result:
                                        return result
                            elif isinstance(obj, list):
                                for item in obj:
                                    result = find_caption(item, depth + 1)
                                    if result:
                                        return result
                            return None
                        found_caption = find_caption(data)
                        if found_caption and not caption:
                            caption = found_caption
                    except:
                        pass
        
        # Method 4: Extract caption from regex in HTML
        if not caption:
            captions = re.findall(r'"desc":"([^"]+)"', html)
            if captions:
                caption = captions[0]
        
        # Method 5: Fallback to meta tags
        if not caption:
            meta_desc = soup.find('meta', property='og:description')
            if meta_desc and meta_desc.get('content'):
                caption = meta_desc['content']
        
        # Extract photo URLs from img tags if not found in JSON
        if not photo_urls:
            images = soup.find_all('img')
            for img in images:
                src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                if src:
                    if src.startswith('//'):
                        src = 'https:' + src
                    elif src.startswith('/'):
                        src = 'https://www.tiktok.com' + src
                    if src.startswith('http') and ('tiktok' in src.lower() or 'cdn' in src.lower() or 'image' in src.lower()):
                        photo_urls.append(src)
        
        # Also try regex for image URLs
        if not photo_urls:
            url_matches = re.findall(r'https?://[^\s"\'<>\)]+\.(?:jpg|jpeg|png|webp)', html, re.I)
            photo_urls.extend(url_matches)
        
        # Remove duplicates
        photo_urls = list(set([url for url in photo_urls if url.startswith('http')]))
        
        meta['description'] = caption
        meta['title'] = caption
        meta['photo_urls'] = photo_urls
        
        print(f"✅ Extracted caption: {caption[:100] if caption else 'None'}...")
        print(f"✅ Found {len(photo_urls)} photo URLs")
        
        return meta, photo_urls
        
    except Exception as e:
        print(f"⚠️ Failed to fetch TikTok photo post HTML: {e}")
        import traceback
        print(traceback.format_exc())
        return {}, []

# ─────────────────────────────
# TikTok Download
# ─────────────────────────────
def download_tiktok(video_url):
    """Download TikTok content (video or photo). Returns file path and metadata."""
    is_photo_url = "/photo/" in video_url.lower()
    
    print(f"🔍 Checking URL type: {'Photo URL' if is_photo_url else 'Video URL'}")
    
    # Strategy: Try HTML parsing FIRST for ALL TikTok URLs (works for photos AND slideshows)
    # Slideshows are videos that show multiple images - HTML parsing can extract them
    # Only fall back to yt-dlp if HTML parsing doesn't find images
    print("🌐 Attempting HTML parsing first (works for photos and slideshows)...")
    try:
        meta, photo_urls = fetch_tiktok_photo_post(video_url)
        
        # If HTML parsing found photo URLs, use them (works for both photo posts and slideshows)
        if photo_urls and len(photo_urls) > 0:
            print(f"✅ HTML parsing found {len(photo_urls)} images - using HTML method (skipping yt-dlp)")
            print(f"   This works for: {'Photo posts' if is_photo_url else 'Slideshow videos'}")
            
            # Download the first photo for OCR/processing
            # IMPORTANT: Always try to download image, even if OCR might not be available
            # We can still use it for processing
            file_path = None
            try:
                print(f"📥 Downloading first image for OCR: {photo_urls[0][:100]}...")
                tmpdir = tempfile.mkdtemp()
                response = requests.get(photo_urls[0], headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }, timeout=30)
                response.raise_for_status()
                
                # Determine file extension
                ext = '.jpg'
                if '.png' in photo_urls[0].lower():
                    ext = '.png'
                elif '.webp' in photo_urls[0].lower():
                    ext = '.webp'
                elif 'image/png' in response.headers.get('content-type', ''):
                    ext = '.png'
                
                file_path = os.path.join(tmpdir, f"image{ext}")
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                print(f"✅ Image downloaded: {file_path}")
            except Exception as e:
                print(f"⚠️ Failed to download image for OCR: {e}")
                file_path = None
            
            # Ensure metadata exists
            if not meta:
                meta = {"description": "", "title": "", "photo_urls": photo_urls}
            
            # Mark as slideshow/photo content
            meta["_is_slideshow"] = len(photo_urls) > 1
            meta["_is_photo"] = is_photo_url
            
            print(f"✅ HTML parsing successful. Found {len(photo_urls)} images. Caption: {meta.get('description', '')[:100] if meta.get('description') else 'None'}...")
            return file_path, meta
        else:
            print("⚠️ HTML parsing didn't find images - will try yt-dlp as fallback")
    except Exception as e:
        print(f"⚠️ HTML parsing failed: {e}")
        import traceback
        print(traceback.format_exc())
        print("🔄 Falling back to yt-dlp...")
    
    # Fallback: Use yt-dlp for videos and photo posts (if HTML parsing didn't work)
    if is_photo_url:
        print("📸 Photo URL - using yt-dlp fallback (HTML parsing didn't find images)")
    else:
            print("🎞 Using yt-dlp for video download (HTML parsing didn't find images)...")
    tmpdir = tempfile.mkdtemp()
    # Use generic filename - will be image or video depending on content
    file_path = os.path.join(tmpdir, "content")

    print("🎞 Downloading TikTok video + metadata with yt-dlp...")
    
    # Always use python -m yt_dlp on Render (safer, works when installed via pip)
    # On Render, the yt-dlp binary path can be broken, so use module import
    is_render = os.getenv("RENDER") is not None or os.getenv("RENDER_EXTERNAL_HOSTNAME") is not None
    
    if is_render:
        # Force python -m yt_dlp on Render
        yt_dlp_cmd = f"{sys.executable} -m yt_dlp"
    else:
        # Local development: try binary first, fallback to module
        yt_dlp_path = shutil.which("yt-dlp")
        if yt_dlp_path and os.path.exists(yt_dlp_path):
            yt_dlp_cmd = yt_dlp_path
        else:
            yt_dlp_cmd = f"{sys.executable} -m yt_dlp"
    
    print(f"Using yt-dlp command: {yt_dlp_cmd}")
    
    # Build yt-dlp command with optional impersonate
    impersonate_flag = f'--impersonate "{YT_IMPERSONATE}"' if YT_IMPERSONATE else ''
    
    # Add extra options to avoid TikTok blocking (403 errors and connection issues)
    # Use better headers, retry logic, and connection handling
    extra_opts = '--user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" --referer "https://www.tiktok.com/" --retries 5 --fragment-retries 5 --socket-timeout 30 --extractor-retries 3'
    
    result1 = subprocess.run(
        f'{yt_dlp_cmd} --skip-download --write-info-json {impersonate_flag} {extra_opts} '
        f'-o "{tmpdir}/content" "{video_url}"', shell=True, check=False, capture_output=True, text=True, timeout=60)
    
    if result1.returncode != 0:
        error1 = (result1.stderr or result1.stdout or "Unknown error")[:1000]
        print(f"⚠️ Metadata download warning: {error1}")
    
    result2 = subprocess.run(
        f'{yt_dlp_cmd} {impersonate_flag} {extra_opts} -o "{file_path}.%(ext)s" "{video_url}"',
        shell=True, check=False, capture_output=True, text=True, timeout=120)
    
    download_failed = result2.returncode != 0
    
    if download_failed:
        error2 = (result2.stderr or result2.stdout or "Unknown error")
        print(f"⚠️ Content download error (full): {error2}")
        
        # CRITICAL: Check if this is actually a photo URL that somehow reached yt-dlp
        if "/photo/" in video_url.lower():
            # Photo URLs can reach yt-dlp if HTML parsing fails - that's okay, yt-dlp can handle them
            print(f"⚠️ Photo URL using yt-dlp fallback (HTML parsing failed)")
        
        # Check if this is a connection error - try HTML parsing as fallback
        is_connection_error = any(keyword in error2.lower() for keyword in [
            "connection aborted", "remote end closed", "transport error",
            "unable to download webpage", "connection reset", "network error"
        ])
        
        if is_connection_error:
            print("🔄 yt-dlp failed with connection error - trying HTML parsing fallback...")
            try:
                # Try HTML parsing as fallback for connection errors
                meta_fallback, photo_urls_fallback = fetch_tiktok_photo_post(video_url)
                if photo_urls_fallback and len(photo_urls_fallback) > 0:
                    print(f"✅ HTML parsing fallback found {len(photo_urls_fallback)} images")
                    # Download first image for OCR
                    file_path_fallback = None
                    try:
                        tmpdir_fallback = tempfile.mkdtemp()
                        response = requests.get(photo_urls_fallback[0], headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                        }, timeout=30)
                        response.raise_for_status()
                        ext = '.jpg'
                        if '.png' in photo_urls_fallback[0].lower():
                            ext = '.png'
                        elif '.webp' in photo_urls_fallback[0].lower():
                            ext = '.webp'
                        file_path_fallback = os.path.join(tmpdir_fallback, f"image{ext}")
                        with open(file_path_fallback, 'wb') as f:
                            f.write(response.content)
                        print(f"✅ Fallback image downloaded: {file_path_fallback}")
                    except Exception as e:
                        print(f"⚠️ Failed to download fallback image: {e}")
                    
                    if not meta_fallback:
                        meta_fallback = {"description": "", "title": "", "photo_urls": photo_urls_fallback}
                    meta_fallback["_is_slideshow"] = len(photo_urls_fallback) > 1
                    return file_path_fallback, meta_fallback
                else:
                    print("⚠️ HTML parsing fallback didn't find images either")
            except Exception as fallback_error:
                print(f"⚠️ HTML parsing fallback also failed: {fallback_error}")
                import traceback
                print(traceback.format_exc())
        
        # For non-photo URLs, check if file was actually downloaded
        downloaded_files = [f for f in os.listdir(tmpdir) if not f.endswith(".info.json")]
        if not downloaded_files:
            # Extract the actual error message from the traceback
            error_lines = error2.split('\n')
            # Find the last meaningful error line
            actual_error = "Unknown yt-dlp error"
            for line in reversed(error_lines):
                if line.strip() and not line.startswith('File "') and not line.startswith('  File '):
                    actual_error = line.strip()
                    break
            
            # Provide helpful error message for connection issues
            if is_connection_error:
                raise Exception(f"TikTok connection error: TikTok closed the connection. This may be due to rate limiting or network issues. Error: {actual_error[:300]}")
            else:
                raise Exception(f"Failed to download content. yt-dlp error: {actual_error[:500]}. Full error: {error2[:1000]}")

    # Find the actual downloaded file (yt-dlp adds extension)
    downloaded_files = [f for f in os.listdir(tmpdir) if not f.endswith(".info.json")]
    if downloaded_files:
        file_path = os.path.join(tmpdir, downloaded_files[0])
    else:
        # If no file downloaded, return None to indicate no file
        file_path = None

    meta = {}
    try:
        info_files = [f for f in os.listdir(tmpdir) if f.endswith(".info.json")]
        if info_files:
            with open(os.path.join(tmpdir, info_files[0]), "r") as f:
                meta = json.load(f)
    except Exception as e:
        print("⚠️ Metadata load fail:", e)
    return file_path, meta
def extract_audio(video_path):
    """Extract audio from video as WAV for Whisper. Uses ffmpeg directly for memory efficiency."""
    try:
        audio_path = video_path.replace(".mp4", ".wav")
        # Use ffmpeg directly instead of MoviePy to save memory
        # MoviePy loads entire video into memory, ffmpeg streams it
        result = subprocess.run(
            ['ffmpeg', '-i', video_path, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', '-y', audio_path],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode != 0:
            print(f"⚠️ ffmpeg failed, trying MoviePy fallback: {result.stderr[:200]}")
            # Fallback to MoviePy if ffmpeg not available
            clip = VideoFileClip(video_path)
            clip.audio.write_audiofile(audio_path, verbose=False, logger=None)
            clip.close()
            del clip
        gc.collect()
        return audio_path
    except FileNotFoundError:
        # ffmpeg not found, use MoviePy
        print("⚠️ ffmpeg not found, using MoviePy (may use more memory)")
        try:
            audio_path = video_path.replace(".mp4", ".wav")
            clip = VideoFileClip(video_path)
            clip.audio.write_audiofile(audio_path, verbose=False, logger=None)
            clip.close()
            del clip
            gc.collect()
            return audio_path
        except Exception as e:
            print(f"⚠️ Audio extraction failed: {e}")
            return video_path  # fallback to mp4
    except Exception as e:
        print(f"⚠️ Audio extraction failed: {e}")
        return video_path  # fallback to mp4

def detect_music_vs_speech(audio_path):
    """Quickly detect if audio is music or speech by transcribing a short sample."""
    try:
        print("🎵 Checking if audio is music or speech...")
        # Extract first 5 seconds for quick detection
        sample_path = audio_path.replace(".wav", "_sample.wav")
        try:
            subprocess.run(
                ['ffmpeg', '-i', audio_path, '-t', '5', '-y', sample_path],
                capture_output=True,
                text=True,
                timeout=10
            )
        except:
            # If ffmpeg fails, just use full audio (will be slower)
            sample_path = audio_path
        
        if not os.path.exists(sample_path):
            sample_path = audio_path  # Fallback to full audio
        
        client = get_openai_client()
        with open(sample_path, "rb") as f:
            text = client.audio.transcriptions.create(
                model="whisper-1",
                file=f
            ).text.strip()
        
        # Clean up sample file
        if sample_path != audio_path and os.path.exists(sample_path):
            try:
                os.remove(sample_path)
            except:
                pass
        
        # Analyze transcript to detect music
        if not text or len(text) < 10:
            print("🎵 Detected: Music (no speech found)")
            return True, ""
        
        # Check for music indicators
        text_lower = text.lower()
        music_indicators = [
            "♪", "♫", "[music]", "[song]", "instrumental",
            "beat", "melody", "rhythm"
        ]
        
        # Count words vs non-words (music often has fewer recognizable words)
        words = text.split()
        if len(words) < 5:  # Very few words = likely music
            print("🎵 Detected: Music (very few words in transcript)")
            return True, ""
        
        # Check if transcript looks like speech (has common speech words)
        speech_indicators = ["the", "and", "is", "are", "this", "that", "you", "i", "we"]
        speech_word_count = sum(1 for word in words if word.lower() in speech_indicators)
        
        if speech_word_count < 2 and len(words) < 15:
            print("🎵 Detected: Music (lacks common speech words)")
            return True, ""
        
        print("🗣️ Detected: Speech (proceeding with full transcription)")
        return False, text  # Return sample transcript as preview
    except Exception as e:
        print(f"⚠️ Music detection failed: {e} - assuming speech")
        return False, ""

def transcribe_audio(media_path):
    print("🎧 Transcribing audio with Whisper…")
    try:
        client = get_openai_client()
        with open(media_path, "rb") as f:
            text = client.audio.transcriptions.create(
                model="whisper-1",
                file=f
            ).text.strip()
        return text
    except Exception as e:
        print("❌ Whisper failed:", e)
        return ""

def is_static_photo(file_path):
    """Check if file is a static image (not a video)."""
    try:
        # Check file extension
        ext = os.path.splitext(file_path)[1].lower()
        image_exts = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        if ext in image_exts:
            return True
        
        # Try to open as image with PIL
        try:
            img = Image.open(file_path)
            img.verify()
            return True
        except:
            pass
        
        # Try to open as video - if it fails or has very few frames, might be an image
        vidcap = cv2.VideoCapture(file_path)
        frame_count = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = vidcap.get(cv2.CAP_PROP_FPS) or 1
        duration = frame_count / fps if fps > 0 else 0
        vidcap.release()
        
        # If it has 1 frame or very short duration, likely a static image
        if frame_count <= 1 or duration < 0.1:
            return True
        
        return False
    except Exception as e:
        print(f"⚠️ Error checking if file is static photo: {e}")
        return False

def run_ocr_on_image(image_path):
    """Run OCR on a single static image. Returns extracted text."""
    if not OCR_AVAILABLE:
        print("⚠️ OCR not available (tesseract not installed) - skipping OCR")
        return ""
    
    try:
        print("🖼️ Running OCR on static image…")
        img = cv2.imread(image_path)
        if img is None:
            # Try with PIL if cv2 fails
            pil_img = Image.open(image_path)
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        
        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Image preprocessing to improve OCR accuracy
        # 1. Increase contrast
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(gray)
        
        # 2. Apply thresholding to make text more distinct
        _, thresh = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        # OCR config for better accuracy on stylized text
        ocr_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,!?;:()[]{}-\'"&@#$% '
        
        # Try OCR on multiple processed versions
        texts = []
        for processed_img in [gray, enhanced, thresh]:
            try:
                txt = image_to_string(Image.fromarray(processed_img), config=ocr_config)
                txt_clean = txt.strip()
                if txt_clean and len(txt_clean) > 2:
                    texts.append(txt_clean)
            except:
                continue
        
        # Return the longest/best result
        if texts:
            result = max(texts, key=len)
            print(f"✅ OCR extracted {len(result)} chars from image")
            print(f"📝 OCR text preview: {result[:200]}...")
            return result
        
        print("⚠️ OCR found no text in image")
        return ""
    except Exception as e:
        print(f"⚠️ OCR extraction from image failed: {e}")
        import traceback
        print(traceback.format_exc())
        return ""

def extract_ocr_text(video_path):
    """Extract on-screen text using OCR from video frames. Returns empty string if OCR unavailable."""
    if not OCR_AVAILABLE:
        print("⚠️ OCR not available (tesseract not installed) - skipping OCR")
        return ""
    
    try:
        print("🧩 Extracting on-screen text with OCR…")
        vidcap = cv2.VideoCapture(video_path)
        total = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = vidcap.get(cv2.CAP_PROP_FPS) or 30
        duration = total / fps if fps > 0 else 0
        
        # Process frames efficiently - balance between accuracy and speed
        # For shorter videos, process more frames. For longer videos, sample strategically
        if duration > 0:
            if duration < 30:
                # Short videos: process every 0.5 seconds (more thorough)
                frames_per_second = 2
                num_frames = min(max(int(duration * frames_per_second), 10), 30)
            else:
                # Longer videos: process every 1 second (faster)
                frames_per_second = 1
                num_frames = min(max(int(duration * frames_per_second), 15), 25)
        else:
            num_frames = 15  # Default to fewer frames for unknown duration
        
        frames = np.linspace(0, total - 1, min(total, num_frames), dtype=int)
        
        print(f"📹 Processing {len(frames)} frames from {total} total frames (duration: {duration:.1f}s)")
        
        texts = []
        seen_texts = set()  # Deduplicate similar text
        
        # OCR config optimized for lists of venue names
        # Try multiple PSM modes to catch different text layouts
        # Reduced to 3 most effective configs for speed
        ocr_configs = [
            r'--oem 3 --psm 11',  # Sparse text - best for lists scattered on screen
            r'--oem 3 --psm 6',   # Uniform block of text
            r'--oem 3 --psm 4',   # Single column of text
        ]
        
        for n in frames:
            vidcap.set(cv2.CAP_PROP_POS_FRAMES, n)
            ok, img = vidcap.read()
            if not ok:
                continue
                
            # Convert to grayscale
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Enhanced image preprocessing for maximum OCR accuracy
            # Reduced to 4 most effective preprocessing methods for speed
            processed_images = []
            
            # 1. Original grayscale (sometimes best as-is)
            processed_images.append(("original", gray))
            
            # 2. Upscale if image is small (critical for small text)
            height, width = gray.shape
            if width < 1000 or height < 800:
                scale_factor = max(1000 / width, 800 / height, 1.5)  # At least 1.5x upscale
                new_width = int(width * scale_factor)
                new_height = int(height * scale_factor)
                upscaled = cv2.resize(gray, (new_width, new_height), interpolation=cv2.INTER_CUBIC)
                processed_images.append(("upscaled", upscaled))
                gray = upscaled  # Use upscaled for further processing
            
            # 3. Increase contrast with CLAHE (stronger)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
            enhanced = clahe.apply(gray)
            processed_images.append(("enhanced", enhanced))
            
            # 4. Adaptive thresholding (better for varying lighting)
            adaptive_thresh = cv2.adaptiveThreshold(
                enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
            )
            processed_images.append(("adaptive", adaptive_thresh))
            
            # Try OCR on all processed versions with different configs
            frame_texts = []
            for img_name, processed_img in processed_images:
                for ocr_config in ocr_configs:
                    try:
                        # Convert to PIL Image for pytesseract
                        pil_img = Image.fromarray(processed_img)
                        txt = image_to_string(pil_img, config=ocr_config)
                        txt_clean = txt.strip()
                        if txt_clean and len(txt_clean) > 2:
                            # Check if this is new text
                            txt_lower = txt_clean.lower()
                            is_duplicate = any(
                                txt_lower in seen or seen in txt_lower 
                                for seen in seen_texts 
                                if len(seen) > 5 and len(txt_lower) > 5
                            )
                            if not is_duplicate:
                                frame_texts.append(txt_clean)
                                seen_texts.add(txt_lower)
                                print(f"   Frame {n} ({img_name}, PSM {ocr_config.split()[-1]}): Found {len(txt_clean)} chars")
                    except Exception as e:
                        # Silently continue - some configs might fail
                        continue
            
            # Add all unique texts from this frame (don't break early - capture all text)
            if frame_texts:
                # For lists, we want to keep ALL text blocks, not just the longest
                # Merge all unique texts from this frame
                for txt in frame_texts:
                    if txt not in texts:  # Simple check to avoid exact duplicates
                        texts.append(txt)
                        print(f"   Frame {n}: Added text block ({len(txt)} chars): {txt[:60]}...")
            
            # Clean up image from memory
            del img
            if 'gray' in locals():
                del gray
            if 'enhanced' in locals():
                del enhanced
            if 'upscaled' in locals():
                del upscaled
            gc.collect()
        
        vidcap.release()
        del vidcap
        gc.collect()  # Force garbage collection
        
        # Merge all texts, preserving line breaks for lists
        merged = "\n".join(texts)
        print(f"✅ OCR extracted {len(merged)} chars from {len(texts)} unique text blocks")
        if merged:
            print(f"📝 OCR text preview (first 500 chars):\n{merged[:500]}...")
            # Count potential venue names (lines with capital letters)
            lines_with_text = [t for t in texts if any(c.isupper() for c in t)]
            print(f"📊 Found {len(lines_with_text)} text blocks that might contain venue names")
            if len(merged) > 100:
                print(f"✅ OCR found substantial text ({len(merged)} chars) - should be extractable!")
        else:
            print("⚠️ OCR found NO text - this might be why venues aren't being extracted")
        return merged
    except Exception as e:
        print(f"⚠️ OCR extraction failed: {e}")
        import traceback
        print(traceback.format_exc())
        return ""  # Return empty string so extraction can continue

# ─────────────────────────────
# Google Places Photo
# ─────────────────────────────
def get_photo_url(name):
    if not GOOGLE_API_KEY:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": f"{name} NYC", "key": GOOGLE_API_KEY}, timeout=6)
        res = r.json().get("results", [])
        if res and "photos" in res[0]:
            ref = res[0]["photos"][0]["photo_reference"]
            return f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=800&photoreference={ref}&key={GOOGLE_API_KEY}"
    except Exception as e:
        print("⚠️ Google photo fail:", e)
    return None

# ─────────────────────────────
# GPT: Extract Venues + Summary
# ─────────────────────────────
def extract_places_and_context(transcript, ocr_text, caption, comments):
    # Log what we have for debugging
    print(f"📋 Content sources:")
    print(f"   - Caption: {len(caption)} chars - {caption[:100] if caption else 'None'}...")
    print(f"   - Transcript: {len(transcript)} chars - {transcript[:100] if transcript else 'None'}...")
    print(f"   - OCR: {len(ocr_text)} chars - {ocr_text[:100] if ocr_text else 'None'}...")
    print(f"   - Comments: {len(comments)} chars - {comments[:100] if comments else 'None'}...")
    
    combined_text = "\n".join(x for x in [ocr_text, transcript, caption, comments] if x)
    
    # Emphasize OCR text if it's available (especially when there's no transcript)
    ocr_emphasis = ""
    if ocr_text and len(ocr_text) > 20:
        if not transcript or len(transcript) < 20:
            # OCR is the PRIMARY source - emphasize heavily (especially for photo posts)
            ocr_emphasis = f"""
   ⚠️⚠️⚠️ CRITICAL: This is a PHOTO POST or video with NO SPEECH. The OCR text below contains ALL venue information.
   ⚠️⚠️⚠️ The OCR text IS THE PRIMARY SOURCE - extract venue names directly from it.
   ⚠️⚠️⚠️ OCR TEXT FROM IMAGES (extract ALL venue names from this):
{ocr_text[:2000]}
   
   • Extract EVERY venue name you see in the OCR text above
   • Photo posts often show venue names in the images themselves - look carefully
   • If OCR shows a numbered list (1. Venue, 2. Venue, etc.), extract ALL of them
   • If OCR shows venue names separated by commas, newlines, bullets, or semicolons, extract ALL of them
   • Venue names might be restaurant names, bar names, café names, or food spot names
   • Don't skip any venue names - extract them all, even if they're partial or misspelled
   • If OCR shows text like "Joe's Pizza", "Lombardi's", "Grimaldi's" - extract ALL of them
   • If the caption mentions "NYC spots" or "favorite places" but doesn't list names, the names are IN THE OCR TEXT
"""
        else:
            ocr_emphasis = f"""
   • IMPORTANT: The OCR text below contains on-screen text from the video/images. This often includes lists of venue names.
   • OCR TEXT: {ocr_text[:1000]}
   • Extract venue names from the OCR text - they're often displayed on screen
"""
    
    # Prioritize caption - if caption exists, emphasize it heavily
    caption_emphasis = ""
    if caption and len(caption) > 10:
        # For photo posts, caption is often the ONLY source
        is_photo_post_context = not transcript or len(transcript) < 20
        if is_photo_post_context:
            caption_emphasis = f"""
   ⚠️⚠️⚠️ CRITICAL: This is a PHOTO POST - the CAPTION below contains ALL the information.
   ⚠️⚠️⚠️ The CAPTION is the PRIMARY and ONLY source for venue names.
   ⚠️⚠️⚠️ CAPTION TEXT (extract ALL venue names from this):
{caption[:1000]}

   • Extract EVERY venue name mentioned in the CAPTION above
   • Look for venue names in lists, hashtags, mentions, or anywhere in the caption
   • If the caption says "Top 5 NYC restaurants: 1. Venue A, 2. Venue B..." extract ALL of them
   • If the caption mentions venues separated by commas, newlines, or bullets, extract ALL
   • Venue names might be in hashtags (#VenueName) - extract those too
   • Don't skip any venue names - be thorough and extract them all
   • If the caption is about NYC venues/restaurants/bars, there ARE venues to extract
"""
        else:
            caption_emphasis = f"""
   ⚠️ CRITICAL PRIORITY: The CAPTION/DESCRIPTION below is the PRIMARY source for venue names.
   ⚠️ If a venue name appears in the CAPTION, it is the MAIN venue being featured.
   ⚠️ Venues mentioned ONLY in speech/comparisons (but NOT in caption) should be EXCLUDED.
   ⚠️ CAPTION TEXT (this is the most important source):
{caption[:500]}

   • Extract venues that appear in the CAPTION above
   • If the video compares venues (e.g., "Gazab vs Dhamaka vs Semma"), ONLY extract the venue(s) mentioned in the CAPTION
   • Do NOT extract venues that are only mentioned in comparisons if they're not in the caption
   • The caption tells you which venue(s) are actually being featured/recommended
"""
    
    prompt = f"""
You are analyzing a TikTok video about NYC venues. Extract venue names from ANY available source.

1️⃣ Extract every **specific** bar, restaurant, café, or food/drink venue mentioned.
{caption_emphasis}
   • CRITICAL PRIORITY: Check the OCR text FIRST - photo posts show venue names IN THE IMAGES
   • IMPORTANT: Check the CAPTION/DESCRIPTION - venue names are often listed there
   • Also check speech (transcript) and comments
   • Look for venue names even if they appear in lists, numbered lists, hashtags, or casual mentions
   • If OCR shows a numbered list (1. Venue Name, 2. Another Venue), extract ALL venue names from that list
   • If OCR shows venue names separated by commas, newlines, bullets, or semicolons, extract ALL of them
   • Venue names might be in hashtags (#VenueName) - extract those too
   • If caption says "My favorite NYC spots" but doesn't list names, the names are IN THE OCR TEXT FROM IMAGES
   • Photo posts: The images contain the venue names - extract them from OCR text
   • Ignore broad neighborhoods like "SoHo" or "Brooklyn" unless they're part of a venue name
   • ONLY list actual venue names that are mentioned. Do NOT use placeholders like "venue 1" or "<venue 1>".
   • Be thorough - if the content is about NYC venues, there ARE venues to extract (likely in OCR text)
   • If no venues are found after careful analysis, return an empty list (no venues, just the Summary line).
{ocr_emphasis}

2️⃣ Write a short, creative title summarizing what this TikTok is about.
   Examples: "Top 10 Pizzerias in NYC", "Hidden Cafes in Manhattan", "NYC Rooftop Bars for Dates".
   Use the ACTUAL content - don't use generic titles like "NYC Venues You Must Visit" unless that's literally what the caption says.

Output ONLY in this format (one venue name per line, no numbers, no placeholders):

VenueName1
VenueName2
Summary: Your actual creative title here

If no venues are found after thorough analysis, output only:
Summary: Your actual creative title here

IMPORTANT: Replace "Your actual creative title here" with a real title based on the content. Do NOT include the placeholder text.
"""
    try:
        if not combined_text or not combined_text.strip():
            print("⚠️ No content to analyze (empty transcript, OCR, caption, comments)")
            return [], "TikTok Venues"
        
        # Increase context window to 8000 chars to capture more OCR content
        content_to_analyze = combined_text[:8000]
        
        # If OCR is the main source (no speech), emphasize it heavily
        if ocr_text and (not transcript or len(transcript) < 50):
            print(f"📝 Analyzing content - OCR PRIMARY SOURCE ({len(ocr_text)} chars OCR, {len(content_to_analyze)} total)")
            print(f"📝 OCR text being analyzed:\n{ocr_text[:500]}...")
        else:
            print(f"📝 Analyzing content ({len(content_to_analyze)} chars): {content_to_analyze[:300]}...")
        
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt + "\n\nContent to analyze:\n" + content_to_analyze}],
            temperature=0.3,  # Lower temperature for more consistent extraction from OCR
        )
        raw = response.choices[0].message.content.strip()
        print(f"🤖 GPT raw response: {raw[:500]}...")

        match = re.search(r"Summary\s*:\s*(.+)", raw, re.I)
        summary = match.group(1).strip() if match else "TikTok Venues"
        summary = re.sub(r"(?i)\bTikTok Text:.*", "", summary).strip()
        summary = re.sub(r"\s+", " ", summary)
        
        # Clean up if GPT output instruction text instead of real title
        instruction_patterns = [
            r"<short creative title.*?>",
            r"<.*?ACTUAL content.*?>",
            r"Your actual creative title here",
            r"short creative title",
        ]
        for pattern in instruction_patterns:
            if re.search(pattern, summary, re.I):
                # Use caption or a default
                if caption and len(caption) > 10:
                    summary = caption[:100] if len(caption) <= 100 else caption[:97] + "..."
                else:
                    summary = "TikTok Photo Post"
                print(f"⚠️ GPT output instruction text, using caption as title: {summary}")
                break

        venues = []
        for l in re.split(r"Summary\s*:", raw)[0].splitlines():
            line = l.strip()
            if not line or re.search(r"names?:", line, re.I):
                continue
            # Remove leading numbers, bullets, dashes
            line = re.sub(r"^[\d\-\•\.\s]+", "", line)
            # Filter out placeholder text like "<venue 1>", "venue 1", etc.
            if re.search(r"<.*venue.*\d+.*>|venue\s*\d+|placeholder", line, re.I):
                print(f"⚠️ Skipping placeholder: {line}")
                continue
            if 2 < len(line) < 60:
                venues.append(line)

        unique, seen = [], set()
        for v in venues:
            v_lower = v.lower().strip()
            # Additional filtering for placeholder-like text
            if v_lower in seen or not v_lower or len(v_lower) < 3:
                continue
            # Skip if it looks like a placeholder
            if re.search(r"^<.*>$|^venue\s*\d+$|^example|^test", v_lower):
                print(f"⚠️ Skipping placeholder-like venue: {v}")
                continue
            seen.add(v_lower)
            unique.append(v)

        print(f"🧠 Parsed {len(unique)} venues: {unique}")
        print(f"🧠 Parsed summary: {summary}")
        return unique, summary
    except Exception as e:
        print("❌ GPT extraction failed:", e)
        return [], "TikTok Venues"

# ─────────────────────────────
# GPT: Enrichment + Vibe Tags
# ─────────────────────────────
def enrich_place_intel(name, transcript, ocr_text, caption, comments):
    context = "\n".join([caption, ocr_text, transcript, comments])
    context_lower = context.lower()
    
    # Determine venue type from context
    is_bar = any(word in context_lower for word in ["bar", "cocktail", "drinks", "happy hour", "bartender", "mixology", "lounge", "pub"])
    is_restaurant = any(word in context_lower for word in ["restaurant", "dining", "food", "menu", "chef", "cuisine", "eatery", "bistro", "cafe", "café"])
    is_club = any(word in context_lower for word in ["club", "nightclub", "dj", "dance", "nightlife", "party", "music venue"])
    
    prompt = f"""
Analyze the TikTok context for "{name}" and return JSON with:

{{
  "summary": "2–3 sentence vivid description (realistic, not fabricated)",
  "when_to_go": "Mention best time/day if clearly stated, else blank",
  "vibe": "Mood or crowd if present",
  "must_try": "Context-aware field: For RESTAURANTS/FOOD places, list specific dishes, drinks, or menu items to try. For BARS/LOUNGES, list signature cocktails, drink specials, or bar features. For CLUBS/MUSIC VENUES, list DJs, events, or music highlights. Only include if mentioned.",
  "specials": "Real deals or special events if mentioned",
  "comments_summary": "Short insight from comments if available"
}}

Context:
{context[:4000]}
"""
    try:
        client = get_openai_client()
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )
        raw = r.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", raw, re.S)
        j = json.loads(match.group(0)) if match else {}
        must_try_value = j.get("must_try", "").strip()
        
        # Determine field name based on venue type (prioritize venue type over content keywords)
        if must_try_value:
            if is_club:
                field_name = "highlights"
            elif is_restaurant:
                field_name = "must_try"  # Restaurants always use "Must Try"
            elif is_bar:
                field_name = "features"  # Bars use "Features"
            else:
                # Fallback to content-based detection if venue type unclear
                must_try_lower = must_try_value.lower()
                if any(word in must_try_lower for word in ["dj", "music", "dance", "club", "nightlife", "party", "event"]):
                    field_name = "highlights"
                elif any(word in must_try_lower for word in ["dish", "drink", "food", "menu", "order", "eat", "cocktail", "beer", "wine"]):
                    field_name = "must_try"
                else:
                    field_name = "features"
        else:
            # Default based on venue type
            if is_restaurant:
                field_name = "must_try"
            elif is_bar:
                field_name = "features"
            else:
                field_name = "must_try"
            must_try_value = ""
        
        data = {
            "summary": j.get("summary", "").strip(),
            "when_to_go": j.get("when_to_go", "").strip(),
            "vibe": j.get("vibe", "").strip(),
            "must_try": must_try_value,
            "must_try_field": field_name,  # Store the field name
            "specials": j.get("specials", "").strip(),
            "comments_summary": j.get("comments_summary", "").strip(),
        }
        vibe_text = " ".join(v for v in data.values())
        data["vibe_tags"] = extract_vibe_tags(vibe_text)
        return data
    except Exception as e:
        print(f"⚠️ Enrichment failed for {name}:", e)
        return {
            "summary": "",
            "when_to_go": "",
            "vibe": "",
            "must_try": "",
            "must_try_field": "must_try",
            "specials": "",
            "comments_summary": "",
            "vibe_tags": [],
        }

def extract_vibe_tags(text):
    if not text.strip():
        return []
    prompt = f"""
Extract up to 6 concise vibe tags from this text for a restaurant, bar, or café.
Use short single words/phrases like:
["Cozy","Date Night","Lively","Romantic","Happy Hour","Brunch","Authentic","Trendy"]

Text: {text}
Return valid JSON list.
"""
    try:
        client = get_openai_client()
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=40,
        )
        raw = r.choices[0].message.content.strip()
        return json.loads(raw) if raw.startswith("[") else []
    except Exception as e:
        print("⚠️ vibe_tags generation failed:", e)
        return []

# ─────────────────────────────
# Helper Functions for Place Merging
# ─────────────────────────────
def get_place_info_from_google(place_name):
    """Get canonical name, address, and other info from Google Maps API."""
    if not GOOGLE_API_KEY:
        return None, None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": f"{place_name} NYC", "key": GOOGLE_API_KEY},
            timeout=6
        )
        res = r.json().get("results", [])
        if res:
            place_info = res[0]
            canonical_name = place_info.get("name", place_name)  # Use Google's canonical name
            address = place_info.get("formatted_address")
            return canonical_name, address
    except Exception as e:
        print(f"⚠️ Failed to get place info from Google for {place_name}: {e}")
    return None, None

def get_place_address(place_name):
    """Get formatted address for a place name using Google Maps API."""
    _, address = get_place_info_from_google(place_name)
    return address

def merge_place_with_cache(place_data, video_url, username=None, video_summary=None):
    """Merge a place with cached places if name+address match. Returns merged place data."""
    place_name = place_data.get("name", "")
    original_name = place_name
    
    # If address not already set, get it (and ensure canonical name)
    if not place_data.get("address"):
        canonical_name, address = get_place_info_from_google(place_name)
        if canonical_name and canonical_name.lower() != place_name.lower():
            place_name = canonical_name  # Update to canonical name
            place_data["name"] = canonical_name
            print(f"✏️  Corrected spelling in cache: '{original_name}' → '{canonical_name}'")
        place_address = address or ""
    else:
        place_address = place_data.get("address")
        # Still check canonical name even if address exists
        canonical_name, _ = get_place_info_from_google(place_name)
        if canonical_name and canonical_name.lower() != place_name.lower():
            place_name = canonical_name
            place_data["name"] = canonical_name
            print(f"✏️  Corrected spelling in cache: '{original_name}' → '{canonical_name}'")
    
    if not place_address:
        place_address = ""  # Use empty string for places without address
    
    conn = get_db()
    c = conn.cursor()
    
    # Check if place exists in cache
    c.execute(
        "SELECT * FROM place_cache WHERE place_name = ? AND place_address = ?",
        (place_name, place_address)
    )
    cached = c.fetchone()
    
    if cached:
        # Merge: update video URLs and usernames
        existing_video_urls = json.loads(cached["video_urls"])
        existing_usernames = json.loads(cached["usernames"]) if cached["usernames"] else []
        existing_metadata = json.loads(cached["video_metadata"]) if cached["video_metadata"] else {}
        
        if video_url not in existing_video_urls:
            existing_video_urls.append(video_url)
            if video_summary:
                existing_metadata[video_url] = {
                    "username": username,
                    "summary": video_summary
                }
        
        if username and username not in existing_usernames:
            existing_usernames.append(username)
        
        # Build other_videos_note - exclude current username
        other_videos = []
        for vid_url in existing_video_urls:
            if vid_url != video_url:  # Exclude current video
                meta = existing_metadata.get(vid_url, {})
                vid_username = meta.get("username", "")
                vid_summary = meta.get("summary", "")
                if vid_username:
                    other_videos.append({
                        "url": vid_url,
                        "username": vid_username,
                        "summary": vid_summary
                    })
        
        # Build formatted note with links - link should be on summary/title, not username
        other_videos_note = ""
        other_videos_data = []
        if other_videos:
            for vid in other_videos[:3]:  # Show up to 3 other videos
                vid_summary = vid.get("summary", "") or "this video"
                vid_username = vid.get("username", "")
                other_videos_data.append({
                    "url": vid["url"],
                    "username": vid_username,
                    "summary": vid_summary
                })
        
        # Merge data (prefer new data but add other_videos_note and address)
        merged_data = {
            **place_data, 
            "other_videos": other_videos_data,
            "address": place_address
        }
        
        # Update cache
        c.execute(
            """UPDATE place_cache 
               SET place_data = ?, video_urls = ?, video_metadata = ?, usernames = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (json.dumps(merged_data), json.dumps(existing_video_urls), json.dumps(existing_metadata), json.dumps(existing_usernames), cached["id"])
        )
        conn.commit()
        conn.close()
        
        return merged_data
    else:
        # Create new cache entry - no other_videos_note for first extraction
        place_data_with_note = {**place_data, "other_videos": [], "address": place_address}
        
        video_metadata = {}
        if video_summary:
            video_metadata[video_url] = {
                "username": username,
                "summary": video_summary
            }
        
        c.execute(
            """INSERT INTO place_cache (place_name, place_address, place_data, video_urls, video_metadata, usernames)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (place_name, place_address, json.dumps(place_data_with_note), json.dumps([video_url]), json.dumps(video_metadata), json.dumps([username] if username else []))
        )
        conn.commit()
        conn.close()
        
        return place_data_with_note

def extract_username_from_url(url):
    """Extract TikTok username from URL."""
    match = re.search(r"@([^/]+)", url)
    return match.group(1) if match else None

def enrich_places_parallel(venues, transcript, ocr_text, caption, comments_text, url, username, context_title):
    """Enrich multiple places in parallel for better performance."""
    places_extracted = []
    
    def enrich_and_fetch_photo(venue_name):
        """Enrich a single venue and fetch its photo - runs in parallel."""
        # Get canonical name from Google Maps (correct spelling)
        canonical_name, address = get_place_info_from_google(venue_name)
        # Use canonical name if available, otherwise use original
        display_name = canonical_name if canonical_name else venue_name
        
        # Log if spelling was corrected
        if canonical_name and canonical_name.lower() != venue_name.lower():
            print(f"✏️  Corrected spelling: '{venue_name}' → '{canonical_name}'")
        
        intel = enrich_place_intel(display_name, transcript, ocr_text, caption, comments_text)
        photo = get_photo_url(display_name)
        place_data = {
            "name": display_name,  # Use canonical name from Google Maps
            "maps_url": f"https://www.google.com/maps/search/{display_name.replace(' ', '+')}",
            "photo_url": photo or "https://via.placeholder.com/600x400?text=No+Photo",
            "description": intel.get("summary", ""),
            "vibe_tags": intel.get("vibe_tags", []),
            "address": address,  # Also get address while we're at it
            **{k: v for k, v in intel.items() if k not in ["summary", "vibe_tags"]}
        }
        return place_data
    
    # Run enrichment and photo fetching in parallel (max 5 concurrent to avoid rate limits)
    if len(venues) > 1:
        print(f"⚡ Enriching {len(venues)} places in parallel...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_venue = {
            executor.submit(enrich_and_fetch_photo, v): v 
            for v in venues
        }
        
        for future in as_completed(future_to_venue):
            venue_name = future_to_venue[future]
            try:
                place_data = future.result()
                # Merge with cached places - pass video summary
                merged_place = merge_place_with_cache(place_data, url, username, context_title)
                places_extracted.append(merged_place)
                if len(venues) > 1:
                    print(f"✅ Enriched: {venue_name}")
            except Exception as e:
                print(f"⚠️ Failed to enrich {venue_name}: {e}")
                # Add basic place data even if enrichment fails
                place_data = {
                    "name": venue_name,
                    "maps_url": f"https://www.google.com/maps/search/{venue_name.replace(' ', '+')}",
                    "photo_url": "https://via.placeholder.com/600x400?text=No+Photo",
                    "summary": "",
                    "description": "",
                    "when_to_go": "",
                    "vibe": "",
                    "must_try": "",
                    "must_try_field": "must_try",
                    "specials": "",
                    "comments_summary": "",
                    "vibe_tags": [],
                }
                merged_place = merge_place_with_cache(place_data, url, username, context_title)
                places_extracted.append(merged_place)
    
    return places_extracted

# ─────────────────────────────
# Authentication Endpoints
# ─────────────────────────────
@app.route("/api/auth/signup", methods=["POST"])
def signup():
    """User signup endpoint."""
    try:
        data = request.json
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        
        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400
        
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400
        
        conn = get_db()
        c = conn.cursor()
        
        # Check if user exists
        c.execute("SELECT id FROM users WHERE email = ?", (email,))
        if c.fetchone():
            conn.close()
            return jsonify({"error": "Email already registered"}), 400
        
        # Create user
        password_hash = generate_password_hash(password)
        c.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, password_hash))
        user_id = c.lastrowid
        conn.commit()
        conn.close()
        
        # Create access token
        access_token = create_access_token(identity=user_id)
        
        return jsonify({
            "access_token": access_token,
            "user_id": user_id,
            "email": email
        }), 201
    except Exception as e:
        print(f"❌ Signup error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/auth/login", methods=["POST"])
def login():
    """User login endpoint."""
    try:
        data = request.json
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        
        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400
        
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        conn.close()
        
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Invalid email or password"}), 401
        
        # Create access token
        access_token = create_access_token(identity=user["id"])
        
        return jsonify({
            "access_token": access_token,
            "user_id": user["id"],
            "email": email
        }), 200
    except Exception as e:
        print(f"❌ Login error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/auth/me", methods=["GET"])
@jwt_required()
def get_current_user():
    """Get current user info."""
    try:
        user_id = get_jwt_identity()
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()
        conn.close()
        
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        return jsonify({
            "user_id": user["id"],
            "email": user["email"],
            "created_at": user["created_at"]
        }), 200
    except Exception as e:
        print(f"❌ Get user error: {e}")
        return jsonify({"error": str(e)}), 500

# ─────────────────────────────
# User-Specific Endpoints
# ─────────────────────────────
@app.route("/api/user/saved-places", methods=["GET"])
@jwt_required()
def get_saved_places():
    """Get all saved places organized by list name."""
    try:
        user_id = get_jwt_identity()
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "SELECT list_name, place_name, place_data FROM saved_places WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )
        rows = c.fetchall()
        conn.close()
        
        # Organize by list name
        saved_places = {}
        for row in rows:
            list_name = row["list_name"]
            if list_name not in saved_places:
                saved_places[list_name] = []
            saved_places[list_name].append(json.loads(row["place_data"]))
        
        return jsonify(saved_places), 200
    except Exception as e:
        print(f"❌ Get saved places error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/user/saved-places", methods=["POST"])
@jwt_required()
def add_saved_place():
    """Add a place to a list."""
    try:
        user_id = get_jwt_identity()
        data = request.json
        list_name = data.get("list_name", "").strip()
        place_data = data.get("place_data", {})
        
        if not list_name or not place_data.get("name"):
            return jsonify({"error": "list_name and place_data.name are required"}), 400
        
        conn = get_db()
        c = conn.cursor()
        
        # Insert or update (upsert)
        c.execute(
            """INSERT OR REPLACE INTO saved_places (user_id, list_name, place_name, place_data)
               VALUES (?, ?, ?, ?)""",
            (user_id, list_name, place_data["name"], json.dumps(place_data))
        )
        conn.commit()
        conn.close()
        
        return jsonify({"success": True}), 200
    except Exception as e:
        print(f"❌ Add saved place error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/user/saved-places", methods=["DELETE"])
@jwt_required()
def remove_saved_place():
    """Remove a place from a list."""
    try:
        user_id = get_jwt_identity()
        data = request.json
        list_name = data.get("list_name", "").strip()
        place_name = data.get("place_name", "").strip()
        
        if not list_name or not place_name:
            return jsonify({"error": "list_name and place_name are required"}), 400
        
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "DELETE FROM saved_places WHERE user_id = ? AND list_name = ? AND place_name = ?",
            (user_id, list_name, place_name)
        )
        conn.commit()
        conn.close()
        
        return jsonify({"success": True}), 200
    except Exception as e:
        print(f"❌ Remove saved place error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/user/history", methods=["GET"])
@jwt_required()
def get_history():
    """Get user's extraction history."""
    try:
        user_id = get_jwt_identity()
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "SELECT video_url, summary_title, timestamp FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 50",
            (user_id,)
        )
        rows = c.fetchall()
        conn.close()
        
        history = []
        for row in rows:
            history.append({
                "video_url": row["video_url"],
                "summary_title": row["summary_title"],
                "timestamp": row["timestamp"]
            })
        
        return jsonify(history), 200
    except Exception as e:
        print(f"❌ Get history error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/user/history", methods=["POST"])
@jwt_required()
def add_history():
    """Add an entry to user's history."""
    try:
        user_id = get_jwt_identity()
        data = request.json
        video_url = data.get("video_url", "").strip()
        summary_title = data.get("summary_title", "").strip()
        
        if not video_url:
            return jsonify({"error": "video_url is required"}), 400
        
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO history (user_id, video_url, summary_title) VALUES (?, ?, ?)",
            (user_id, video_url, summary_title)
        )
        conn.commit()
        conn.close()
        
        return jsonify({"success": True}), 200
    except Exception as e:
        print(f"❌ Add history error: {e}")
        return jsonify({"error": str(e)}), 500

# ─────────────────────────────
# API Endpoint
# ─────────────────────────────
def extract_photo_post(url):
    """Extract photo post data from TikTok URL by scraping HTML."""
    try:
        print(f"🌐 Fetching HTML from: {url}")
        html = None
        
        # Try Playwright FIRST (for dynamic content) - this is critical for photo posts
        playwright_used = False
        try:
            from playwright.sync_api import sync_playwright
            print("🎭 Using Playwright to render dynamic content...")
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                # Set a longer timeout for TikTok to load
                page.set_default_timeout(60000)
                print(f"   Navigating to {url}...")
                # Use networkidle to wait for all resources to load
                page.goto(url, wait_until="networkidle", timeout=60000)
                print("   Waiting for dynamic content to load...")
                # Wait longer for TikTok's JavaScript to load the caption (photo posts need more time)
                page.wait_for_timeout(8000)
                
                # Try multiple selectors that TikTok uses for captions
                caption_selectors = [
                    '[data-e2e="browse-video-desc"]',
                    '[data-e2e="video-desc"]',
                    '.video-meta-caption',
                    '[class*="caption"]',
                    '[class*="description"]',
                ]
                
                caption_found = False
                for selector in caption_selectors:
                    try:
                        page.wait_for_selector(selector, timeout=5000)
                        print(f"   Found content element: {selector}")
                        caption_found = True
                        break
                    except:
                        continue
                
                if not caption_found:
                    print("   No caption selector found, waiting additional time...")
                    page.wait_for_timeout(5000)
                
                html = page.content()
                browser.close()
                playwright_used = True
                print(f"✅ Rendered HTML with Playwright ({len(html)} chars)")
                
                # Check if we got the real caption (not generic TikTok page)
                if "Download TikTok Lite" in html or ("Make Your Day" in html and len(html) < 100000):
                    print("⚠️ Warning: Still seeing generic TikTok page title - TikTok may be blocking automated access")
                    print("   Trying to extract caption from rendered HTML anyway...")
        except ImportError:
            print("⚠️ Playwright not available, falling back to static HTML")
        except Exception as e:
            print(f"⚠️ Playwright failed: {e}")
            import traceback
            print(traceback.format_exc())
            print("   Falling back to static HTML")
        
        # Fallback to requests if Playwright failed or not available
        if not html:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://www.tiktok.com/",
                "Accept-Encoding": "gzip, deflate, br",
            }
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            html = response.text
            print(f"✅ Fetched HTML with requests ({len(html)} chars)")
        
        photos = []
        caption = ""
        
        # Recursive function to search for images and captions in nested JSON
        def find_in_data(obj, depth=0, max_depth=15):
            """Recursively search for ImageList, images, and captions."""
            if depth > max_depth:
                return [], ""
            
            found_photos = []
            found_caption = ""
            
            if isinstance(obj, dict):
                # Check for ImageList (most common pattern)
                if "ImageList" in obj:
                    for img in obj.get("ImageList", []):
                        if isinstance(img, dict):
                            # Try UrlList
                            if "UrlList" in img and isinstance(img["UrlList"], list) and len(img["UrlList"]) > 0:
                                found_photos.append(img["UrlList"][0])
                            # Try direct URL fields
                            for url_key in ["url", "imageURL", "src", "imageUrl"]:
                                if url_key in img and isinstance(img[url_key], str) and img[url_key].startswith("http"):
                                    found_photos.append(img[url_key])
                
                # Check for images array
                if "images" in obj and isinstance(obj["images"], list):
                    for img in obj["images"]:
                        if isinstance(img, str) and img.startswith("http"):
                            found_photos.append(img)
                        elif isinstance(img, dict):
                            for url_key in ["url", "imageURL", "src", "urlList"]:
                                if url_key in img:
                                    if isinstance(img[url_key], str) and img[url_key].startswith("http"):
                                        found_photos.append(img[url_key])
                                    elif isinstance(img[url_key], list) and len(img[url_key]) > 0:
                                        found_photos.append(img[url_key][0])
                
                # Check for photo_urls
                if "photo_urls" in obj and isinstance(obj["photo_urls"], list):
                    found_photos.extend([u for u in obj["photo_urls"] if isinstance(u, str) and u.startswith("http")])
                
                # Check for imagePost structure
                if "imagePost" in obj:
                    image_post = obj["imagePost"]
                    if isinstance(image_post, dict):
                        images = image_post.get("images", [])
                        if isinstance(images, list):
                            for img in images:
                                if isinstance(img, dict):
                                    img_url_obj = img.get("imageURL", {})
                                    if isinstance(img_url_obj, dict):
                                        url_list = img_url_obj.get("urlList", [])
                                        if isinstance(url_list, list) and len(url_list) > 0:
                                            found_photos.append(url_list[0])
                
                # Look for caption fields
                for cap_key in ["desc", "description", "text", "caption", "title"]:
                    if cap_key in obj and obj[cap_key] and not found_caption:
                        found_caption = str(obj[cap_key])
                
                # Recursively search nested objects
                for value in obj.values():
                    nested_photos, nested_caption = find_in_data(value, depth + 1, max_depth)
                    found_photos.extend(nested_photos)
                    if nested_caption and not found_caption:
                        found_caption = nested_caption
                        
            elif isinstance(obj, list):
                for item in obj:
                    nested_photos, nested_caption = find_in_data(item, depth + 1, max_depth)
                    found_photos.extend(nested_photos)
                    if nested_caption and not found_caption:
                        found_caption = nested_caption
            
            return found_photos, found_caption
        
        # Method 1: Try window.__UNIVERSAL_DATA__
        match = re.search(r'window\.__UNIVERSAL_DATA__\s*=\s*({.+?});', html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                print("✅ Found window.__UNIVERSAL_DATA__")
                found_photos, found_caption = find_in_data(data)
                photos.extend(found_photos)
                if found_caption and not caption:
                    caption = found_caption
                print(f"   Found {len(found_photos)} photos, caption: {found_caption[:50] if found_caption else 'None'}...")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"⚠️ Failed to parse __UNIVERSAL_DATA__: {e}")
                import traceback
                print(traceback.format_exc())
        
        # Method 2: Try window.__UNIVERSAL_DATA_FOR_REHYDRATION__
        if not photos:
            match = re.search(r'window\.__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*({.+?});', html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    print("✅ Found window.__UNIVERSAL_DATA_FOR_REHYDRATION__")
                    found_photos, found_caption = find_in_data(data)
                    photos.extend(found_photos)
                    if found_caption and not caption:
                        caption = found_caption
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"⚠️ Failed to parse __UNIVERSAL_DATA_FOR_REHYDRATION__: {e}")
        
        # Method 3: Try __NEXT_DATA__ or similar
        if not photos:
            match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    print("✅ Found __NEXT_DATA__")
                    found_photos, found_caption = find_in_data(data)
                    photos.extend(found_photos)
                    if found_caption and not caption:
                        caption = found_caption
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"⚠️ Failed to parse __NEXT_DATA__: {e}")
        
        # Method 4: Extract from img tags if JSON parsing failed
        if not photos:
            soup = BeautifulSoup(html, 'html.parser')
            images = soup.find_all('img')
            for img in images:
                for attr in ['src', 'data-src', 'data-lazy-src', 'data-original']:
                    src = img.get(attr)
                    if src:
                        if src.startswith('//'):
                            src = 'https:' + src
                        elif src.startswith('/'):
                            src = 'https://www.tiktok.com' + src
                        if src.startswith('http') and ('tiktok' in src.lower() or 'cdn' in src.lower() or 'image' in src.lower() or 'muscdn' in src.lower()):
                            photos.append(src)
        
        # Method 5: Regex fallback for image URLs
        if not photos:
            url_matches = re.findall(r'https?://[^\s"\'<>\)]+\.(?:jpg|jpeg|png|webp)', html, re.I)
            # Filter to likely TikTok CDN URLs
            photos.extend([url for url in url_matches if 'tiktok' in url.lower() or 'cdn' in url.lower() or 'muscdn' in url.lower()])
        
        # Extract caption from HTML if not found in JSON (multiple methods)
        # Filter out obvious metadata/placeholder text
        def is_valid_caption(text):
            if not text or len(text) < 5:
                return False
            # Filter out metadata fields
            invalid_patterns = [
                r'^pc_web_',
                r'^explorePage',
                r'^tiktok\s*-\s*make your day',  # Only reject if it's exactly this
                r'^<script',
                r'^<!DOCTYPE',
                r'^https?://',  # URLs are not captions
            ]
            text_lower = text.lower().strip()
            # Check if it's just metadata (very short or all underscores/numbers)
            if len(text_lower) < 10 or re.match(r'^[_\d\s]+$', text_lower):
                return False
            # Check for invalid patterns
            for pattern in invalid_patterns:
                if re.search(pattern, text_lower):
                    return False
            # Must contain at least some letters (not just numbers/symbols)
            if not re.search(r'[a-z]', text_lower):
                return False
            return True
        
        if not caption or not is_valid_caption(caption):
            # Method 1: Try desc field in JSON (but validate it)
            caption_match = re.search(r'"desc":"([^"]{10,})"', html)
            if caption_match:
                potential = caption_match.group(1)
                if is_valid_caption(potential):
                    caption = potential
        
        if not caption or not is_valid_caption(caption):
            # Method 2: Try description field
            caption_match = re.search(r'"description":"([^"]{10,})"', html)
            if caption_match:
                potential = caption_match.group(1)
                if is_valid_caption(potential):
                    caption = potential
        
        if not caption or not is_valid_caption(caption):
            # Method 3: Try text field
            caption_match = re.search(r'"text":"([^"]{10,})"', html)
            if caption_match:
                potential = caption_match.group(1)
                if is_valid_caption(potential):
                    caption = potential
        
        if not caption or not is_valid_caption(caption):
            # Method 4: Try shareDesc or shareDescText (common TikTok fields)
            for field in ['shareDesc', 'shareDescText', 'shareTitle', 'shareDescription']:
                caption_match = re.search(rf'"{field}":"([^"]{{10,}})"', html)
                if caption_match:
                    potential = caption_match.group(1)
                    if is_valid_caption(potential):
                        caption = potential
                        break
        
        # Method 5: Try meta tags for caption
        if not caption or not is_valid_caption(caption):
            soup = BeautifulSoup(html, 'html.parser')
            # Try og:description
            meta_desc = soup.find('meta', attrs={'property': 'og:description'})
            if meta_desc and meta_desc.get('content'):
                potential = meta_desc['content']
                if is_valid_caption(potential):
                    caption = potential
            
            # Try meta description
            if not caption or not is_valid_caption(caption):
                meta_desc = soup.find('meta', attrs={'name': 'description'})
                if meta_desc and meta_desc.get('content'):
                    potential = meta_desc['content']
                    if is_valid_caption(potential):
                        caption = potential
            
            # Try title tag (but only if it's not generic)
            if not caption or not is_valid_caption(caption):
                title_tag = soup.find('title')
                if title_tag and title_tag.string:
                    potential = title_tag.string
                    if is_valid_caption(potential) and 'tiktok' not in potential.lower():
                        caption = potential
        
        # Method 6: Try to find caption in data attributes or structured data
        if not caption or not is_valid_caption(caption):
            # Look for longer text strings that might be captions
            # Captions are usually 20+ characters and contain actual words
            potential_captions = re.findall(r'"desc":"([^"]{20,200})"', html)
            for potential in potential_captions:
                if is_valid_caption(potential) and len(potential.split()) > 2:
                    caption = potential
                    break
        
        # Method 7: Search entire HTML for caption-like patterns (most aggressive)
        if not caption or not is_valid_caption(caption):
            print("   Trying aggressive caption extraction from entire HTML...")
            # Look for various caption field patterns throughout HTML
            caption_patterns = [
                r'"desc":"([^"]{30,500})"',  # Standard desc field
                r'"description":"([^"]{30,500})"',  # Description field
                r'"text":"([^"]{30,500})"',  # Text field
                r'"shareDesc":"([^"]{30,500})"',  # Share description
                r'"shareDescText":"([^"]{30,500})"',  # Share desc text
                r'"caption":"([^"]{30,500})"',  # Caption field
                r'"title":"([^"]{30,500})"',  # Title field
                r'content="([^"]{30,500})"[^>]*property="og:description"',  # OG description
                r'content="([^"]{30,500})"[^>]*name="description"',  # Meta description
            ]
            
            for pattern in caption_patterns:
                matches = re.findall(pattern, html, re.I)
                for match in matches:
                    # Decode unicode escapes
                    try:
                        decoded = match.encode('latin-1').decode('unicode_escape')
                    except:
                        decoded = match
                    
                    if is_valid_caption(decoded) and len(decoded.split()) > 3:
                        # Prefer longer captions
                        if not caption or len(decoded) > len(caption):
                            caption = decoded
                            print(f"   Found caption via pattern {pattern[:30]}...: {decoded[:80]}...")
                            break
                if caption and is_valid_caption(caption):
                    break
        
        # Clean up caption (decode unicode escapes)
        if caption:
            try:
                caption = caption.encode('latin-1').decode('unicode_escape')
            except:
                try:
                    caption = caption.encode().decode('unicode_escape')
                except:
                    pass  # Keep original if decoding fails
        
        # Remove duplicates and filter invalid URLs
        photos = list(set([p for p in photos if p.startswith('http') and len(p) > 10]))
        
        # Final validation of caption
        if caption and not is_valid_caption(caption):
            print(f"⚠️ Caption '{caption[:50]}...' appears to be metadata, clearing it")
            caption = ""
        
        print(f"📸 Final extraction: {len(photos)} photos, caption: {caption[:100] if caption else 'None'}...")
        if photos:
            print(f"   First photo URL: {photos[0][:100]}...")
        else:
            print("⚠️ No photos found - trying alternative extraction methods...")
            
            # Method: Try SIGI_STATE (newer TikTok format)
            if "SIGI_STATE" in html:
                print("   Found SIGI_STATE in HTML - trying extraction...")
                sigi_match = re.search(r'<script id="SIGI_STATE"[^>]*>(.*?)</script>', html, re.DOTALL)
                if sigi_match:
                    try:
                        sigi_data = json.loads(sigi_match.group(1))
                        found_photos, found_caption = find_in_data(sigi_data)
                        photos.extend(found_photos)
                        if found_caption:
                            if not caption or (is_valid_caption(found_caption) and len(found_caption) > len(caption)):
                                caption = found_caption
                        print(f"   SIGI_STATE extraction: {len(found_photos)} photos, caption: {found_caption[:50] if found_caption else 'None'}...")
                    except Exception as e:
                        print(f"   Failed to parse SIGI_STATE: {e}")
            
            # Method: Try to find image URLs directly in HTML
            if not photos:
                print("   Trying direct image URL extraction from HTML...")
                # Look for TikTok CDN image URLs
                cdn_patterns = [
                    r'https://[^"\s]+\.(?:jpg|jpeg|png|webp)',
                    r'https://[^"\s]*tiktok[^"\s]*\.(?:jpg|jpeg|png|webp)',
                    r'https://[^"\s]*cdn[^"\s]*\.(?:jpg|jpeg|png|webp)',
                ]
                for pattern in cdn_patterns:
                    matches = re.findall(pattern, html, re.I)
                    for match in matches:
                        if 'tiktok' in match.lower() or 'cdn' in match.lower():
                            if match not in photos and len(match) > 20:
                                photos.append(match)
                                print(f"   Found image URL: {match[:80]}...")
                    if photos:
                        break
            
            # Method: Try to find JSON in script tags (newer TikTok format)
            if not photos or not caption:
                print("   Trying to find JSON in script tags...")
                script_tags = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
                for i, script_content in enumerate(script_tags[:15]):  # Check first 15 script tags
                    if len(script_content) > 500:  # Likely contains data
                        try:
                            # Try to find JSON objects - look for larger JSON structures
                            # TikTok often has very large JSON objects
                            json_matches = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*[^{}]{50,}\}', script_content)
                            for json_str in json_matches[:10]:
                                try:
                                    data = json.loads(json_str)
                                    found_photos, found_caption = find_in_data(data)
                                    if found_photos:
                                        photos.extend(found_photos)
                                    if found_caption:
                                        if not caption or (is_valid_caption(found_caption) and len(found_caption) > len(caption or "")):
                                            caption = found_caption
                                            print(f"   Found caption in script tag {i+1}: {found_caption[:50]}...")
                                    if found_photos:
                                        print(f"   Found {len(found_photos)} photos in script tag {i+1}")
                                        break
                                except json.JSONDecodeError:
                                    # Try to extract caption directly from JSON-like strings
                                    # Look for desc/description fields even if JSON is malformed
                                    desc_matches = re.findall(r'"(?:desc|description|text|shareDesc)":"([^"]{20,})"', json_str)
                                    for desc_match in desc_matches:
                                        if is_valid_caption(desc_match) and (not caption or len(desc_match) > len(caption)):
                                            caption = desc_match
                                            print(f"   Found caption in script tag {i+1} (direct): {desc_match[:50]}...")
                                except:
                                    continue
                            if photos and caption:
                                break
                        except:
                            continue
            
            # Method: Look for TikTok image CDN patterns more aggressively
            if not photos:
                print("   Trying aggressive CDN URL extraction...")
                # TikTok uses various CDN patterns
                cdn_domains = ['tiktokcdn', 'tiktok', 'muscdn', 'p16-sign', 'p77-sign', 'p19-sign']
                for domain in cdn_domains:
                    pattern = rf'https://[^"\s<>]*{domain}[^"\s<>]*\.(?:jpg|jpeg|png|webp|gif)'
                    matches = re.findall(pattern, html, re.I)
                    for match in matches:
                        # Clean up URL (remove query params that might break it)
                        clean_url = match.split('?')[0].split('&')[0]
                        if clean_url not in photos and len(clean_url) > 20:
                            photos.append(clean_url)
                            print(f"   Found CDN URL: {clean_url[:80]}...")
                    if photos:
                        break
            
            # Debug: Show what we found
            if not photos:
                print("   ⚠️ Still no photos found. HTML structure might have changed.")
                print(f"   HTML length: {len(html)} chars")
                print(f"   Contains '__UNIVERSAL_DATA__': {'__UNIVERSAL_DATA__' in html}")
                print(f"   Contains 'SIGI_STATE': {'SIGI_STATE' in html}")
                print(f"   Contains 'ImageList': {'ImageList' in html}")
                print(f"   Contains 'imagePost': {'imagePost' in html}")
                print(f"   Contains 'tiktokcdn': {'tiktokcdn' in html.lower()}")
                print(f"   Contains 'p16-sign': {'p16-sign' in html.lower()}")
                # Save a sample of HTML for debugging (first 5000 chars)
                print(f"   HTML sample (first 500 chars): {html[:500]}")
        
        return {"photos": photos, "caption": caption}
    except Exception as e:
        print(f"❌ Error extracting photo post: {e}")
        import traceback
        print(traceback.format_exc())
        return None


@app.route("/api/extract", methods=["POST"])
def extract_api():
    url = request.json.get("video_url")
    
    if not url:
        return jsonify({
            "error": "No video URL provided",
            "message": "Please provide a valid TikTok video URL."
        }), 400
    
    # ===== PHOTO POST HANDLING =====
    # Detect if the TikTok URL contains /photo/ and skip yt-dlp entirely
    if "/photo/" in url.lower():
        print("📸 Detected TikTok photo post - skipping yt-dlp, using HTML extraction")
        
        try:
            # Clean URL - remove query parameters that might interfere
            clean_url = url.split('?')[0] if '?' in url else url
            if clean_url != url:
                print(f"🔗 Cleaned URL: {url} -> {clean_url}")
            
            # Extract photo post data (images + caption) from HTML
            photo_data = extract_photo_post(clean_url)
            
            if not photo_data or not photo_data.get("photos"):
                print("⚠️ HTML extraction failed - trying yt-dlp as fallback...")
                # Fallback: try yt-dlp for photo posts (sometimes it works)
                # Continue to normal video flow below
                pass
            else:
                # Successfully extracted photos via HTML - process them
                photo_urls = photo_data["photos"]
                caption = photo_data.get("caption", "").strip()
                
                print(f"✅ Extracted {len(photo_urls)} photos via HTML, caption: {caption[:100] if caption else 'None'}...")
                
                # Download first few images locally (e.g. temp_photo_1.jpg, temp_photo_2.jpg)
                ocr_text = ""
                tmpdir = tempfile.mkdtemp()
                
                try:
                    # Download first few images (limit to 5 for performance)
                    num_images = min(5, len(photo_urls))
                    print(f"🔍 Downloading {num_images} images for OCR...")
                    
                    for i, img_url in enumerate(photo_urls[:num_images]):
                        try:
                            print(f"📥 Downloading photo {i+1}/{num_images}...")
                            response = requests.get(img_url, headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                            }, timeout=30)
                            response.raise_for_status()
                            
                            # Determine file extension
                            ext = '.jpg'
                            if '.png' in img_url.lower():
                                ext = '.png'
                            elif '.webp' in img_url.lower():
                                ext = '.webp'
                            
                            # Save to temp file (e.g., temp_photo_1.jpg)
                            temp_photo_path = os.path.join(tmpdir, f"temp_photo_{i+1}{ext}")
                            with open(temp_photo_path, "wb") as f:
                                f.write(response.content)
                            
                            # Run OCR on the downloaded image using existing OCR pipeline
                            if OCR_AVAILABLE:
                                print(f"🔍 Running OCR on photo {i+1}...")
                                photo_ocr = run_ocr_on_image(temp_photo_path)
                                if photo_ocr and len(photo_ocr.strip()) > 3:
                                    ocr_text += photo_ocr + " "
                                    print(f"✅ OCR extracted text from photo {i+1} ({len(photo_ocr)} chars): {photo_ocr[:150]}...")
                                else:
                                    print(f"⚠️ OCR found no text in photo {i+1} (OCR returned: {photo_ocr[:50] if photo_ocr else 'None'}...)")
                            else:
                                print(f"⚠️ OCR not available - skipping photo {i+1}")
                                print(f"   OCR_AVAILABLE={OCR_AVAILABLE}")
                        except Exception as e:
                            print(f"⚠️ Failed to process photo {i+1}: {e}")
                            continue
                    
                    ocr_text = ocr_text.strip()
                    print(f"📊 Total OCR text extracted: {len(ocr_text)} chars")
                    if ocr_text:
                        print(f"📝 OCR text preview: {ocr_text[:300]}...")
                    else:
                        print("⚠️ No OCR text extracted from any images")
                    
                finally:
                    # Clean up temp directory
                    try:
                        import shutil
                        shutil.rmtree(tmpdir, ignore_errors=True)
                    except:
                        pass
                
                # Combine OCR + caption text
                combined_text = f"{caption} {ocr_text}".strip()
                
                # Validate we have some text to extract from
                if not combined_text:
                    return jsonify({
                        "error": "No extractable text",
                        "message": "The photo post has no caption and OCR found no text in the images. Unable to extract venue information.",
                        "video_url": url,
                        "places_extracted": []
                    }), 200
            
                print(f"📋 Text sources: Caption={len(caption)} chars, OCR={len(ocr_text)} chars")
                
                # Pass combined OCR + caption through existing GPT-based place extraction function
                print(f"📝 Extracting venues from photo post using GPT...")
                transcript = ""  # No audio for photo posts
                comments_text = ""
                venues, context_title = extract_places_and_context(transcript, ocr_text, caption, comments_text)
                venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
            
                # Build response with same JSON structure as video extraction
                data = {
                    "video_url": url,
                    "summary_title": context_title or caption or "TikTok Photo Post",
                    "context_summary": context_title or caption or "TikTok Photo Post",
                    "places_extracted": [],
                    "photo_urls": photo_urls,  # Include photo URLs in response
                    "caption_extracted": caption  # Include actual caption for debugging
                }
                
                # Enrich places if any were found
                if venues:
                    username = extract_username_from_url(url)
                    places_extracted = enrich_places_parallel(
                        venues, transcript, ocr_text, caption, comments_text,
                        url, username, context_title
                    )
                    data["places_extracted"] = places_extracted
                
                # Cache the result
                vid = get_tiktok_id(url)
                if vid:
                    cache = load_cache()
                    cache[vid] = data
                    save_cache(cache)
                
                return jsonify(data), 200
            
            # If HTML extraction failed, try to use caption if we got one
            if photo_data and photo_data.get("caption"):
                caption = photo_data.get("caption", "").strip()
                print(f"📝 HTML extraction found caption but no photos: {caption[:100]}...")
                print("📝 Attempting venue extraction from caption only...")
                
                # Extract venues from caption only
                transcript = ""
                ocr_text = ""
                comments_text = ""
                venues, context_title = extract_places_and_context(transcript, ocr_text, caption, comments_text)
                venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
                
                # Build response
                data = {
                    "video_url": url,
                    "summary_title": context_title or caption or "TikTok Photo Post",
                    "context_summary": context_title or caption or "TikTok Photo Post",
                    "places_extracted": [],
                    "photo_urls": [],  # No photos found
                    "caption_extracted": caption  # Include actual caption for debugging
                }
                
                if venues:
                    username = extract_username_from_url(url)
                    places_extracted = enrich_places_parallel(
                        venues, transcript, ocr_text, caption, comments_text,
                        url, username, context_title
                    )
                    data["places_extracted"] = places_extracted
                else:
                    data["places_extracted"] = []
            
            # Cache the result
            vid = get_tiktok_id(url)
            if vid:
                cache = load_cache()
                cache[vid] = data
                save_cache(cache)
            
                return jsonify(data), 200
            
            # If no caption either, fall through to yt-dlp (though it likely won't work)
            print("📸 HTML extraction failed - no caption found, trying yt-dlp (may fail)...")
            
        except Exception as e:
            print(f"❌ Error processing photo post: {e}")
            import traceback
            print(traceback.format_exc())
            # Try to use caption if we got one before the error
            if 'photo_data' in locals() and photo_data and photo_data.get("caption"):
                caption = photo_data.get("caption", "").strip()
                print(f"📝 Using caption from before error: {caption[:100]}...")
                # Extract from caption
                transcript = ""
                ocr_text = ""
                comments_text = ""
                venues, context_title = extract_places_and_context(transcript, ocr_text, caption, comments_text)
                venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
                
                data = {
                    "video_url": url,
                    "summary_title": context_title or caption or "TikTok Photo Post",
                    "context_summary": context_title or caption or "TikTok Photo Post",
                    "places_extracted": [],
                    "photo_urls": []
                }
                
                if venues:
                    username = extract_username_from_url(url)
                    places_extracted = enrich_places_parallel(
                        venues, transcript, ocr_text, caption, comments_text,
                        url, username, context_title
                    )
                    data["places_extracted"] = places_extracted
                
                return jsonify(data), 200
            
            # Fall through to yt-dlp as last resort
            print("📸 Falling back to yt-dlp after error...")
    
    # ===== VIDEO POST HANDLING =====
    # Continue with normal video processing (works for both videos and photo posts via yt-dlp fallback)
    
    # Photo URLs are now supported with OCR fallback - no need to block them
    
    # Check if this is a photo post that reached here - yt-dlp doesn't support photo posts
    is_photo_post = "/photo/" in url.lower()
    
    vid = get_tiktok_id(url)
    print(f"\n🟦 Extracting TikTok: {url}")

    cache = load_cache()
    if vid and vid in cache:
        cached_data = cache[vid]
        # Check if cached data has placeholder venues and clear it if so
        places = cached_data.get("places_extracted", [])
        has_placeholders = any(
            re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", p.get("name", ""), re.I)
            for p in places
        )
        if has_placeholders:
            print("⚠️ Cached result contains placeholders, clearing cache and re-extracting")
            del cache[vid]
            save_cache(cache)
        else:
            print("⚡ Using cached result.")
            return jsonify(cached_data)

    # Try HTML extraction first for photo posts (before yt-dlp)
    html_caption = ""
    if is_photo_post:
        print("📸 Photo post detected - trying HTML extraction for caption...")
        try:
            clean_url = url.split('?')[0] if '?' in url else url
            photo_data_html = extract_photo_post(clean_url)
            if photo_data_html:
                html_caption = photo_data_html.get("caption", "").strip()
                if html_caption:
                    print(f"✅ Got caption from HTML ({len(html_caption)} chars): {html_caption[:200]}...")
                else:
                    print(f"⚠️ HTML extraction found no caption")
                if photo_data_html.get("photos"):
                    print(f"✅ Also found {len(photo_data_html.get('photos', []))} photos")
        except Exception as e:
            print(f"⚠️ HTML extraction failed: {e}")
            import traceback
            print(traceback.format_exc())

    try:
        video_path, meta = download_tiktok(url)
        
        # Extract video ID from metadata if not available from URL (for shortened URLs)
        if not vid and meta:
            # Try to get ID from metadata
            vid = meta.get("id") or meta.get("display_id")
            if vid:
                print(f"📹 Extracted video ID from metadata: {vid}")
                # Check cache again with the extracted ID
                cache = load_cache()
                if vid in cache:
                    cached_data = cache[vid]
                    places = cached_data.get("places_extracted", [])
                    has_placeholders = any(
                        re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", p.get("name", ""), re.I)
                        for p in places
                    )
                    if not has_placeholders:
                        print("⚡ Using cached result (from metadata ID).")
                        return jsonify(cached_data)
        
        # Extract caption from multiple possible fields
        caption = (
            meta.get("description", "") or 
            meta.get("fulltitle", "") or 
            meta.get("title", "") or
            meta.get("alt_title", "") or
            ""
        )
        # Also check for additional text fields
        if not caption:
            caption = meta.get("uploader", "") or ""
        
        print(f"📝 Extracted caption: {caption[:200] if caption else 'None'}...")
        
        comments_text = ""
        if "comments" in meta and isinstance(meta["comments"], list):
            comments_text = " | ".join(c.get("text", "") for c in meta["comments"][:10])

        # Handle case where no file was downloaded (e.g., photo URLs that yt-dlp can't download)
        if not video_path or not os.path.exists(video_path):
            # Check if this is a photo/slideshow post (has photo_urls in metadata)
            is_photo_or_slideshow = meta.get("_is_photo") or meta.get("_is_slideshow") or "/photo/" in url.lower()
            
            if is_photo_or_slideshow:
                print("🖼️ Photo/Slideshow post - no file downloaded, trying alternative extraction...")
                transcript = ""
                ocr_text = ""
                
                # Try to download and run OCR on images if available
                photo_urls = meta.get("photo_urls", [])
                
                # If yt-dlp didn't give us photo URLs, try HTML extraction as fallback
                if not photo_urls:
                    print("⚠️ No photo URLs in metadata, trying HTML extraction fallback...")
                    # Clean URL - remove query parameters
                    clean_url = url.split('?')[0] if '?' in url else url
                    photo_data_fallback = extract_photo_post(clean_url)
                    if photo_data_fallback:
                        if photo_data_fallback.get("photos"):
                            photo_urls = photo_data_fallback["photos"]
                            print(f"✅ Fallback extraction found {len(photo_urls)} photos")
                        # Always try to get caption from fallback (even if no photos)
                        if photo_data_fallback.get("caption") and len(photo_data_fallback.get("caption", "")) > len(caption):
                            caption = photo_data_fallback["caption"]
                            print(f"✅ Got better caption from fallback extraction: {caption[:100]}...")
                    else:
                        print("⚠️ HTML fallback also failed - will proceed with caption only if available")
                
                if photo_urls and OCR_AVAILABLE:
                    print(f"🔍 Attempting OCR on {len(photo_urls)} images...")
                    for i, photo_url in enumerate(photo_urls[:5]):  # Try first 5 images
                        try:
                            print(f"📥 Downloading image {i+1} for OCR: {photo_url[:100]}...")
                            tmpdir = tempfile.mkdtemp()
                            response = requests.get(photo_url, headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                            }, timeout=30)
                            response.raise_for_status()
                            
                            ext = '.jpg'
                            if '.png' in photo_url.lower():
                                ext = '.png'
                            elif '.webp' in photo_url.lower():
                                ext = '.webp'
                            
                            img_path = os.path.join(tmpdir, f"img{i}{ext}")
                            with open(img_path, 'wb') as f:
                                f.write(response.content)
                            
                            # Run OCR on the image
                            img_ocr = run_ocr_on_image(img_path)
                            if img_ocr and len(img_ocr.strip()) > 3:
                                ocr_text += " " + img_ocr
                                print(f"✅ OCR extracted {len(img_ocr)} chars from image {i+1}: {img_ocr[:100]}...")
                            else:
                                print(f"⚠️ OCR found no text in image {i+1}")
                            
                            # Clean up
                            try:
                                os.remove(img_path)
                                os.rmdir(tmpdir)
                            except:
                                pass
                        except Exception as e:
                            print(f"⚠️ Failed to process image {i+1} for OCR: {e}")
                            continue
                elif not OCR_AVAILABLE:
                    print("⚠️ OCR not available - will try extraction with caption only")
                
                ocr_text = ocr_text.strip()
                
                # IMPORTANT: Even if OCR fails, try extraction with caption if available
                if not caption and not ocr_text:
                    return jsonify({
                        "error": "Static photo with no extractable text",
                        "message": "The photo post has no caption and OCR found no text in the images. Unable to extract venue information.",
                        "video_url": url,
                        "places_extracted": []
                    }), 200
                
                # Log what we have
                print(f"📋 Text sources: Caption={len(caption)} chars, OCR={len(ocr_text)} chars")
                
                # Extract from caption and/or OCR text
                sources = []
                if caption: sources.append("caption")
                if ocr_text: sources.append("OCR")
                print(f"📝 Extracting venues from: {', '.join(sources) if sources else 'no sources available'}")
                venues, context_title = extract_places_and_context(transcript, ocr_text, caption, comments_text)
                venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
                
                data = {
                    "video_url": url,
                    "context_summary": context_title or "No venues found",
                    "places_extracted": []
                }
                
                if venues:
                    username = extract_username_from_url(url)
                    places_extracted = enrich_places_parallel(
                        venues, transcript, ocr_text, caption, comments_text,
                        url, username, context_title
                    )
                    data["places_extracted"] = places_extracted
                
                if vid:
                    cache = load_cache()
                    cache[vid] = data
                    save_cache(cache)
                
                return jsonify(data)
            else:
                raise Exception("Content file was not downloaded successfully")
        
        file_size = os.path.getsize(video_path)
        print(f"✅ Content downloaded: {video_path} ({file_size} bytes)")
        
        # Check if it's a static photo
        is_photo = is_static_photo(video_path)
        
        if is_photo:
            print("🖼️ Detected static photo - using OCR fallback mode")
            # For static photos, only use OCR + caption
            transcript = ""  # No audio for static photos
            ocr_text = run_ocr_on_image(video_path)
            
            # If OCR failed, try to get caption from HTML extraction as fallback
            if not caption:
                print("⚠️ No caption from metadata, trying HTML extraction fallback...")
                photo_data_fallback = extract_photo_post(url)
                if photo_data_fallback and photo_data_fallback.get("caption"):
                    caption = photo_data_fallback["caption"]
                    print(f"✅ Got caption from fallback extraction: {caption[:100]}...")
            
            # IMPORTANT: Even if OCR fails, try extraction with caption if available
            if not ocr_text and not caption:
                # Clean up file
                if os.path.exists(video_path):
                    try:
                        os.remove(video_path)
                    except:
                        pass
                return jsonify({
                    "error": "Static photo with no extractable text",
                    "message": "The photo post has no text visible in the image and no caption. Unable to extract venue information.",
                    "video_url": url,
                    "places_extracted": []
                }), 200
            
            print(f"📋 Text sources: Caption={len(caption)} chars, OCR={len(ocr_text)} chars")
            
            # Clean up image file
            if os.path.exists(video_path):
                try:
                    os.remove(video_path)
                    print("🗑️ Cleaned up image file")
                    gc.collect()
                except:
                    pass
        else:
            # Regular video processing
            # Check file size - warn if very large
            if file_size > 50 * 1024 * 1024:  # 50MB
                print(f"⚠️ Large video file ({file_size / 1024 / 1024:.1f}MB) - may cause memory issues")

        # OPTIMIZATION: Extract audio first and transcribe immediately for voiceover videos
        # OCR can be deferred if we have a good transcript
        audio_path = extract_audio(video_path)
        print(f"✅ Audio extracted: {audio_path}")
        
        # Quick music detection (saves time if it's just music)
        is_music, sample_transcript = detect_music_vs_speech(audio_path)
        
        transcript = ""
        ocr_text = ""
        
        if is_music:
            print("🎵 Music detected - skipping full transcription, running OCR instead")
            transcript = ""  # No transcript for music
            # For music videos, OCR is critical - run it now
            print("🔍 Running OCR on video frames (music video - OCR is primary source)...")
            ocr_text = extract_ocr_text(video_path)
            # Clean up audio file immediately
            if audio_path != video_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    print("🗑️ Cleaned up audio file")
                except:
                    pass
        else:
            # It's speech - transcribe first (faster than OCR for voiceover)
            print("🗣️ Speech detected - transcribing audio first (OCR will run only if needed)...")
            transcript = transcribe_audio(audio_path)
            print(f"✅ Transcript: {len(transcript)} chars")
            
            # Clean up audio file immediately after transcription
            if audio_path != video_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    print("🗑️ Cleaned up audio file")
                except:
                    pass
            
            # OPTIMIZATION: Only run OCR if transcript is short/insufficient
            # For voiceover videos with good transcripts, OCR is often unnecessary
            transcript_length = len(transcript) if transcript else 0
            if transcript_length < 100:  # Short transcript - might need OCR
                print(f"⚠️ Short transcript ({transcript_length} chars) - running OCR as backup...")
                ocr_text = extract_ocr_text(video_path)
            else:
                print(f"✅ Good transcript length ({transcript_length} chars) - skipping OCR for speed")
                # Still try a quick OCR on just a few frames if video is short
                # But don't do full processing
                ocr_text = ""
        if ocr_text:
            print(f"✅ OCR successfully extracted {len(ocr_text)} chars of on-screen text")
            print(f"📝 OCR text preview: {ocr_text[:200]}...")
        else:
            if OCR_AVAILABLE:
                print("⚠️ OCR ran but found no text in video frames (text may not be visible or clear)")
            else:
                print("⚠️ OCR not available - install tesseract to extract on-screen text")
            
        # Warn if we have no transcript and no OCR (slideshow/image-only videos)
        if not transcript and not ocr_text:
            print("⚠️ No audio transcript and no OCR text - extraction will rely on captions/description only")
            
        # Clean up video file immediately after processing
        if os.path.exists(video_path):
            try:
                os.remove(video_path)
                print("🗑️ Cleaned up video file")
                gc.collect()
            except:
                pass

        venues, context_title = extract_places_and_context(transcript, ocr_text, caption, comments_text)

        # Filter out any remaining placeholder-like venues
        venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
        
        if not venues:
            print("⚠️ No valid venues extracted from video")
            # Check if we had any content to analyze
            has_content = bool(transcript or ocr_text or caption or comments_text)
            warning_msg = ""
            if not transcript and not ocr_text:
                warning_msg = " This appears to be a slideshow/image-only video with no audio. OCR is needed to extract text from images, but tesseract is not available on Render."
            
            # Return empty result with helpful message
            data = {
                "video_url": url,
                "context_summary": context_title or "No venues found",
                "places_extracted": [],
                "warning": warning_msg if warning_msg else None,
            }
            return jsonify(data)

        # OPTIMIZATION: Parallelize enrichment and photo fetching for multiple places
        username = extract_username_from_url(url)
        places_extracted = enrich_places_parallel(
            venues, transcript, ocr_text, caption, comments_text,
            url, username, context_title
        )

        data = {
            "video_url": url,
            "context_summary": context_title,
            "places_extracted": places_extracted,
        }

        if vid:
            cache[vid] = data
            save_cache(cache)
            print(f"💾 Cached result for video {vid}")

        print(f"✅ Extraction complete — {len(places_extracted)} places found")
        return jsonify(data)

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        error_str = str(e)
        
        # Special handling for photo posts when yt-dlp fails
        if is_photo_post and ("Unsupported URL" in error_str or "yt-dlp error" in error_str):
            print(f"⚠️ yt-dlp doesn't support photo posts - using HTML-extracted caption")
            if html_caption:
                print(f"📝 Extracting venues from HTML caption: {html_caption[:100]}...")
                # Extract venues from caption only
                transcript = ""
                ocr_text = ""
                comments_text = ""
                venues, context_title = extract_places_and_context(transcript, ocr_text, html_caption, comments_text)
                venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
                
                data = {
                    "video_url": url,
                    "summary_title": context_title or html_caption or "TikTok Photo Post",
                    "context_summary": context_title or html_caption or "TikTok Photo Post",
                    "places_extracted": [],
                    "photo_urls": [],
                    "caption_extracted": html_caption  # Include actual caption for debugging
                }
                
                if venues:
                    username = extract_username_from_url(url)
                    places_extracted = enrich_places_parallel(
                        venues, transcript, ocr_text, html_caption, comments_text,
                        url, username, context_title
                    )
                    data["places_extracted"] = places_extracted
                
                # Cache the result
                if vid:
                    cache = load_cache()
                    cache[vid] = data
                    save_cache(cache)
                
                return jsonify(data), 200
            else:
                return jsonify({
                    "error": "Photo extraction failed",
                    "message": "Unable to extract photos or caption from the TikTok photo post. yt-dlp doesn't support photo posts and HTML extraction found no caption.",
                    "video_url": url,
                    "places_extracted": []
                }), 200
        
        # Regular error handling for other cases
        print("❌ Fatal error:", e)
        print("Full traceback:")
        print(error_trace)
        return jsonify({
            "error": str(e),
            "message": "Extraction failed. Check logs for details.",
            "traceback": error_trace if os.getenv("DEBUG") else None
        }), 500

@app.route("/healthz", methods=["GET"])
def health_check():
    try:
        # Basic health check - just verify app is running
        return jsonify({"status": "ok", "service": "planit-backend"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ─────────────────────────────
# Run Server
# ─────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    print(f"Running Flask backend on {port}...")
    app.run(host="0.0.0.0", port=port)
