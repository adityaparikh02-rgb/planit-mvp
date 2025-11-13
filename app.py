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
# On Render (Linux), we skip impersonate to avoid dependency issues
YT_IMPERSONATE = None if (os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_HOSTNAME")) else "chrome-131:macos-14"
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
    
    # Fallback: Use yt-dlp for regular videos (only if HTML parsing didn't work)
    # CRITICAL: Photo URLs should have been handled above, but double-check
    if is_photo_url:
        print("❌ Photo URL reached yt-dlp fallback - this shouldn't happen")
        # Still try to return something useful
        return None, {"description": "", "title": "", "photo_urls": [], "_is_photo": True}
    
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
            raise Exception(f"CRITICAL: Photo URL reached yt-dlp! URL: {video_url}. This should never happen - photo URLs must be handled by HTML parsing.")
        
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
            # OCR is the PRIMARY source - emphasize heavily
            ocr_emphasis = f"""
   ⚠️ CRITICAL: This video has NO SPEECH (only music). The OCR text below contains ALL the information.
   ⚠️ The on-screen text IS THE PRIMARY SOURCE - extract venue names directly from it.
   ⚠️ OCR TEXT (this is what you need to analyze):
{ocr_text[:1000]}
   
   • Extract EVERY venue name you see in the OCR text above
   • If OCR shows a numbered list (1. Venue, 2. Venue, etc.), extract ALL of them
   • If OCR shows venue names separated by commas, newlines, or bullets, extract ALL of them
   • Don't skip any venue names - extract them all
"""
        else:
            ocr_emphasis = f"""
   • IMPORTANT: The OCR text below contains on-screen text from the video. This often includes lists of venue names.
     OCR TEXT: {ocr_text[:500]}
"""
    
    prompt = f"""
You are analyzing a TikTok video about NYC venues. Extract venue names from ANY available source.

1️⃣ Extract every **specific** bar, restaurant, café, or food/drink venue mentioned.
   • IMPORTANT: Check the CAPTION/DESCRIPTION carefully - venue names are often listed there
   • CRITICAL: Check the OCR text (on-screen text) - videos often show lists of venue names on screen
   • Also check speech (transcript) and comments
   • Look for venue names even if they appear in lists, numbered lists, hashtags, or casual mentions
   • If OCR shows a numbered list (1. Venue Name, 2. Another Venue), extract ALL venue names from that list
   • If OCR shows venue names separated by commas, newlines, bullets, or semicolons, extract ALL of them
   • Ignore broad neighborhoods like "SoHo" or "Brooklyn" unless they're part of a venue name
   • ONLY list actual venue names that are mentioned. Do NOT use placeholders like "venue 1" or "<venue 1>".
   • If no venues are found, return an empty list (no venues, just the Summary line).
{ocr_emphasis}
2️⃣ Write a short, creative title summarizing what this TikTok is about.
   Examples: "Top 10 Pizzerias in NYC", "Hidden Cafes in Manhattan", "NYC Rooftop Bars for Dates".

Output ONLY in this format (one venue name per line, no numbers, no placeholders):

<actual venue name>
<another actual venue name>
Summary: <short creative title — DO NOT include transcript>

If no venues are found, output only:
Summary: <short creative title>
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
    prompt = f"""
Analyze the TikTok context for "{name}" and return JSON with:

{{
  "summary": "2–3 sentence vivid description (realistic, not fabricated)",
  "when_to_go": "Mention best time/day if clearly stated, else blank",
  "vibe": "Mood or crowd if present",
  "must_try": "Context-aware field: Use 'Must Try' for restaurants/food (dishes, drinks). Use 'Highlights' for clubs/music venues (DJs, events, music style). Use 'Features' for other venues. Only include if mentioned.",
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
        # Determine field name based on content
        if must_try_value:
            must_try_lower = must_try_value.lower()
            if any(word in must_try_lower for word in ["dj", "music", "dance", "club", "nightlife", "party", "event"]):
                field_name = "highlights"
            elif any(word in must_try_lower for word in ["dish", "drink", "food", "menu", "order", "eat"]):
                field_name = "must_try"
            else:
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
def get_place_address(place_name):
    """Get formatted address for a place name using Google Maps API."""
    if not GOOGLE_API_KEY:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": f"{place_name} NYC", "key": GOOGLE_API_KEY},
            timeout=6
        )
        res = r.json().get("results", [])
        if res:
            return res[0].get("formatted_address")
    except Exception as e:
        print(f"⚠️ Failed to get address for {place_name}: {e}")
    return None

def merge_place_with_cache(place_data, video_url, username=None, video_summary=None):
    """Merge a place with cached places if name+address match. Returns merged place data."""
    place_name = place_data.get("name", "")
    place_address = place_data.get("address") or get_place_address(place_name)
    
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
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.tiktok.com/",
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        html = response.text
        
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
            except (json.JSONDecodeError, KeyError) as e:
                print(f"⚠️ Failed to parse __UNIVERSAL_DATA__: {e}")
        
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
        
        # Extract caption from HTML if not found in JSON
        if not caption:
            caption_match = re.search(r'"desc":"([^"]*)"', html)
            if caption_match:
                caption = caption_match.group(1)
            else:
                caption_match = re.search(r'"description":"([^"]*)"', html)
                if caption_match:
                    caption = caption_match.group(1)
        
        # Try meta tags for caption
        if not caption:
            soup = BeautifulSoup(html, 'html.parser')
            meta_desc = soup.find('meta', attrs={'property': 'og:description'})
            if meta_desc and meta_desc.get('content'):
                caption = meta_desc['content']
        
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
        
        print(f"📸 Extracted {len(photos)} photos, caption: {caption[:100] if caption else 'None'}...")
        if photos:
            print(f"   First photo URL: {photos[0][:100]}...")
        
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
    
    # Check if this is a photo post BEFORE attempting yt-dlp
    if "/photo/" in url.lower():
        print("📸 Detected TikTok photo post - using photo mode")
        photo_data = extract_photo_post(url)
        
        if not photo_data or not photo_data.get("photos"):
            print("⚠️ Photo extraction failed, falling back to yt-dlp...")
            # Fallback: try yt-dlp anyway - sometimes it works for photo posts
            # Continue to normal flow below
        else:
            # Successfully extracted photos - process them
            # Combine caption and OCR text from photos
            text_combined = photo_data.get("caption", "") + " "
            ocr_text = ""
            
            # Run OCR on photos (limit to 5 for performance)
            photo_urls = photo_data["photos"][:5]
            for i, img_url in enumerate(photo_urls):
                try:
                    print(f"📥 Downloading photo {i+1}/{len(photo_urls)} for OCR...")
                    response = requests.get(img_url, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    }, timeout=30)
                    response.raise_for_status()
                    
                    # Save to temp file
                    tmpdir = tempfile.mkdtemp()
                    ext = '.jpg'
                    if '.png' in img_url.lower():
                        ext = '.png'
                    elif '.webp' in img_url.lower():
                        ext = '.webp'
                    
                    temp_photo_path = os.path.join(tmpdir, f"photo_{i}{ext}")
                    with open(temp_photo_path, "wb") as f:
                        f.write(response.content)
                    
                    # Run OCR
                    if OCR_AVAILABLE:
                        photo_ocr = run_ocr_on_image(temp_photo_path)
                        if photo_ocr:
                            ocr_text += photo_ocr + " "
                            print(f"✅ OCR extracted text from photo {i+1}: {photo_ocr[:100]}...")
                    
                    # Clean up
                    try:
                        os.remove(temp_photo_path)
                        os.rmdir(tmpdir)
                    except:
                        pass
                except Exception as e:
                    print(f"⚠️ Failed to process photo {i+1}: {e}")
                    continue
            
            ocr_text = ocr_text.strip()
            text_combined += ocr_text
            
            if not text_combined.strip():
                return jsonify({
                    "error": "No extractable text",
                    "message": "The photo post has no caption and OCR found no text in the images. Unable to extract venue information.",
                    "video_url": url,
                    "places_extracted": []
                }), 200
            
            # Extract places using GPT
            print(f"📝 Extracting venues from photo post (caption + OCR)...")
            transcript = ""  # No audio for photo posts
            comments_text = ""
            venues, context_title = extract_places_and_context(transcript, ocr_text, photo_data.get("caption", ""), comments_text)
            venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
            
            # Build response
            data = {
                "video_url": url,
                "context_summary": context_title or photo_data.get("caption", "TikTok Photo Post"),
                "places_extracted": [],
                "photo_urls": photo_data["photos"]
            }
            
            if venues:
                places_extracted = []
                username = extract_username_from_url(url)
                for v in venues:
                    intel = enrich_place_intel(v, transcript, ocr_text, photo_data.get("caption", ""), comments_text)
                    photo = get_photo_url(v)
                    place_data = {
                        "name": v,
                        "maps_url": f"https://www.google.com/maps/search/{v.replace(' ', '+')}",
                        "photo_url": photo or "https://via.placeholder.com/600x400?text=No+Photo",
                        "description": intel.get("summary", ""),
                        "vibe_tags": intel.get("vibe_tags", []),
                        **{k: v for k, v in intel.items() if k not in ["summary", "vibe_tags"]}
                    }
                    # Merge with cached places
                    merged_place = merge_place_with_cache(place_data, url, username, context_title)
                    places_extracted.append(merged_place)
                data["places_extracted"] = places_extracted
            
            # Cache the result
            vid = get_tiktok_id(url)
            if vid:
                cache = load_cache()
                cache[vid] = data
                save_cache(cache)
            
            return jsonify(data)
    
    # Photo URLs are now supported with OCR fallback - no need to block them
    
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
                print("🖼️ Photo/Slideshow post - no file downloaded, trying OCR on photo URLs...")
                transcript = ""
                ocr_text = ""
                
                # Try to download and run OCR on images if available
                photo_urls = meta.get("photo_urls", [])
                if photo_urls and OCR_AVAILABLE:
                    print(f"🔍 Attempting OCR on {len(photo_urls)} images...")
                    for i, photo_url in enumerate(photo_urls[:3]):  # Try first 3 images
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
                            if img_ocr:
                                ocr_text += " " + img_ocr
                                print(f"✅ OCR extracted {len(img_ocr)} chars from image {i+1}")
                            
                            # Clean up
                            try:
                                os.remove(img_path)
                            except:
                                pass
                        except Exception as e:
                            print(f"⚠️ Failed to process image {i+1} for OCR: {e}")
                            continue
                
                ocr_text = ocr_text.strip()
                
                # If we have neither caption nor OCR text, return error
                if not caption and not ocr_text:
                    return jsonify({
                        "error": "Static photo with no extractable text",
                        "message": "The photo post has no caption and OCR found no text in the images. Unable to extract venue information.",
                        "video_url": url,
                        "places_extracted": []
                    }), 200
                
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
                    places_extracted = []
                    username = extract_username_from_url(url)
                    for v in venues:
                        intel = enrich_place_intel(v, transcript, ocr_text, caption, comments_text)
                        photo = get_photo_url(v)
                        place_data = {
                            "name": v,
                            "maps_url": f"https://www.google.com/maps/search/{v.replace(' ', '+')}",
                            "photo_url": photo or "https://via.placeholder.com/600x400?text=No+Photo",
                            "description": intel.get("summary", ""),
                            "vibe_tags": intel.get("vibe_tags", []),
                            **{k: v for k, v in intel.items() if k not in ["summary", "vibe_tags"]}
                        }
                        # Merge with cached places - pass video summary
                        merged_place = merge_place_with_cache(place_data, url, username, context_title)
                        places_extracted.append(merged_place)
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

        # First, run OCR immediately (don't wait for audio)
        # This is especially important for videos with on-screen text
        print("🔍 Running OCR on video frames to extract on-screen text...")
        ocr_text = extract_ocr_text(video_path)
        
        # Extract audio and detect if it's music or speech
        audio_path = extract_audio(video_path)
        print(f"✅ Audio extracted: {audio_path}")
        
        # Quick music detection (saves time if it's just music)
        is_music, sample_transcript = detect_music_vs_speech(audio_path)
        
        if is_music:
            print("🎵 Music detected - skipping full transcription, prioritizing OCR")
            transcript = ""  # No transcript for music
            # Clean up audio file immediately
            if audio_path != video_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    print("🗑️ Cleaned up audio file")
                except:
                    pass
        else:
            # It's speech - proceed with full transcription
            transcript = transcribe_audio(audio_path)
            print(f"✅ Transcript: {len(transcript)} chars")
            
            # Clean up audio file immediately after transcription
            if audio_path != video_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    print("🗑️ Cleaned up audio file")
                except:
                    pass
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

        places_extracted = []
        username = extract_username_from_url(url)
        for v in venues:
            intel = enrich_place_intel(v, transcript, ocr_text, caption, comments_text)
            photo = get_photo_url(v)
            place_data = {
                "name": v,
                "maps_url": f"https://www.google.com/maps/search/{v.replace(' ', '+')}",
                "photo_url": photo or "https://via.placeholder.com/600x400?text=No+Photo",
                **intel
            }
            # Merge with cached places - pass video summary
            merged_place = merge_place_with_cache(place_data, url, username, context_title)
            places_extracted.append(merged_place)

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
