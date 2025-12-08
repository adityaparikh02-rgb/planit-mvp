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

# Track initialization start time (after datetime import)
_app_init_start_time = datetime.now()
print(f"🚀 Starting app initialization at {_app_init_start_time}")
import sqlite3
from PIL import Image
from moviepy.editor import VideoFileClip
from openai import OpenAI
from httpx import Client as HttpxClient
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import new OCR pipeline modules
try:
    from ocr_processor import get_ocr_processor, OCR_AVAILABLE as OCR_PROCESSOR_AVAILABLE
    from slideshow_extractor import extract_text_from_slideshow, extract_text_from_slideshow_weighted
    print("✅ High-quality OCR pipeline modules loaded successfully")
    ADVANCED_OCR_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ Advanced OCR modules not available: {e}")
    ADVANCED_OCR_AVAILABLE = False

# Optional OCR - tesseract may not be available on all systems
OCR_AVAILABLE = False
try:
    import pytesseract
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

# ─────────────────────────────
# App Readiness Tracking
# ─────────────────────────────
# Track app initialization status for health checks
_app_ready = False
_app_init_start_time = None

def mark_app_ready():
    """Mark the app as ready to accept requests."""
    global _app_ready
    _app_ready = True
    init_time = None
    if _app_init_start_time:
        init_time = (datetime.now() - _app_init_start_time).total_seconds()
    print(f"✅ App marked as ready (initialization took {init_time:.2f}s)" if init_time else "✅ App marked as ready")

def is_app_ready():
    """Check if app is ready to accept requests."""
    return _app_ready

# Allow all origins for CORS - needed for frontend to connect
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# ─────────────────────────────
# Status Tracking System
# ─────────────────────────────
from threading import Lock
import uuid

# Mark app as ready after critical imports are complete
print("✅ Critical modules imported successfully")
mark_app_ready()

# In-memory storage for extraction status updates
extraction_status = {}
status_lock = Lock()

def create_extraction_id():
    """Generate unique ID for tracking extraction progress."""
    return str(uuid.uuid4())

def update_status(extraction_id, message):
    """Update status message for an extraction."""
    with status_lock:
        if extraction_id not in extraction_status:
            extraction_status[extraction_id] = []
        extraction_status[extraction_id].append({
            "message": message,
            "timestamp": datetime.now().isoformat()
        })
        print(f"📊 [{extraction_id[:8]}] {message}")

        # Auto-cleanup old status entries (older than 5 minutes)
        current_time = datetime.now()
        for eid in list(extraction_status.keys()):
            if extraction_status[eid]:
                try:
                    last_timestamp_str = extraction_status[eid][-1]["timestamp"]
                    last_timestamp = datetime.fromisoformat(last_timestamp_str)
                    if (current_time - last_timestamp).total_seconds() > 300:  # 5 minutes
                        del extraction_status[eid]
                        print(f"🧹 Cleaned up old status for {eid[:8]}")
                except (KeyError, ValueError, TypeError):
                    # Skip if timestamp format is unexpected
                    pass

def get_status(extraction_id):
    """Get all status messages for an extraction."""
    with status_lock:
        return extraction_status.get(extraction_id, [])

def clear_status(extraction_id):
    """Clear status for an extraction after completion."""
    with status_lock:
        if extraction_id in extraction_status:
            del extraction_status[extraction_id]

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

# Validate Google API Key at startup
if not GOOGLE_API_KEY:
    print("⚠️ WARNING: GOOGLE_API_KEY environment variable is not set")
    print("   Location extraction will rely on title/caption text and address parsing only")
elif len(GOOGLE_API_KEY) < 20:
    print(f"⚠️ WARNING: GOOGLE_API_KEY appears invalid (too short: {len(GOOGLE_API_KEY)} chars)")
    print("   Google Places API calls may fail")
else:
    print(f"✅ GOOGLE_API_KEY is set (length: {len(GOOGLE_API_KEY)} chars)")

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
    # Try /photo/ format
    m = re.search(r"/photo/(\d+)", url)
    if m:
        return m.group(1)
    # Shortened URLs (/t/ format) will be handled by extracting ID from metadata
    return None

def get_tiktok_post_data(url):
    """Fetch TikTok post data using mobile API endpoint. Returns dict with type, caption, photo_urls/video_url."""
    # Clean URL - remove query parameters
    clean_url = url.split('?')[0] if '?' in url else url
    if clean_url != url:
        print(f"🔗 Cleaned URL: {url} -> {clean_url}")
        url = clean_url
    
    try:
        # Extract item ID from URL (handles both /video/ and /photo/ formats)
        item_id_match = re.search(r'/video/(\d+)|/photo/(\d+)', url)
        if not item_id_match:
            raise ValueError("Invalid TikTok URL - no video/photo ID found")
        
        # Get the non-None group
        item_id = next(g for g in item_id_match.groups() if g)
        print(f"📱 Extracted TikTok item ID: {item_id}")
        
        # Call TikTok mobile API
        api_url = f"https://m.tiktok.com/api/item/detail/?itemId={item_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
            "Accept": "application/json",
            "Referer": "https://www.tiktok.com/",
        }
        
        print(f"🌐 Calling TikTok mobile API: {api_url}")
        response = requests.get(api_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        print(f"✅ API response received (status: {response.status_code})")
        
        # Parse response structure
        item_info = data.get("itemInfo", {})
        item_struct = item_info.get("itemStruct", {})
        
        if not item_struct:
            print("⚠️ API response missing itemStruct - TikTok may be blocking access")
            raise ValueError("Invalid API response structure")
        
        # Extract caption
        caption = item_struct.get("desc", "").strip()
        print(f"📝 Caption extracted: {caption[:100] if caption else 'None'}...")
        
        # Check if it's a photo post
        if "imagePost" in item_struct:
            print("📸 Detected photo post via API")
            image_post = item_struct["imagePost"]
            images = image_post.get("images", [])
            
            # CRITICAL: Extract images in order from TikTok API to preserve slide order
            # The order of images in the API response matches the order in the TikTok slideshow
            photo_urls = []
            for idx, img in enumerate(images, 1):
                image_url_obj = img.get("imageURL", {})
                url_list = image_url_obj.get("urlList", [])
                if url_list and len(url_list) > 0:
                    photo_urls.append(url_list[0])
                    print(f"   📸 Image {idx}: {url_list[0][:60]}...")
            
            print(f"✅ Extracted {len(photo_urls)} photo URLs from API in order (image 1 → image {len(photo_urls)})")
            return {
                "type": "photo",
                "caption": caption,
                "photo_urls": photo_urls,
                "author": item_struct.get("author", {}).get("nickname", ""),
            }
        
        # Check if it's a video post
        elif "video" in item_struct:
            print("🎥 Detected video post via API")
            video = item_struct["video"]
            play_addr = video.get("playAddr", "")
            
            if play_addr:
                print(f"✅ Extracted video URL from API")
                return {
                    "type": "video",
                    "caption": caption,
                    "video_url": play_addr,
                    "author": item_struct.get("author", {}).get("nickname", ""),
                }
            else:
                raise ValueError("Video post missing playAddr")
        
        else:
            raise ValueError("Unsupported TikTok post type - neither photo nor video")
            
    except requests.exceptions.RequestException as e:
        print(f"⚠️ TikTok API request failed: {e}")
        raise
    except (KeyError, ValueError) as e:
        print(f"⚠️ TikTok API parsing failed: {e}")
        raise
    except Exception as e:
        print(f"⚠️ Unexpected error calling TikTok API: {e}")
        import traceback
        print(traceback.format_exc())
        raise

def get_tiktok_media(tiktok_url):
    """Fetch TikTok media directly using TikTok's internal API16 endpoint."""
    # Clean URL - remove query parameters
    clean_url = tiktok_url.split('?')[0] if '?' in tiktok_url else tiktok_url
    if clean_url != tiktok_url:
        print(f"🔗 Cleaned URL: {tiktok_url} -> {clean_url}")
        tiktok_url = clean_url
    
    try:
        # Extract item ID from URL (handles both /video/ and /photo/ formats)
        item_id_match = re.search(r'/video/(\d+)|/photo/(\d+)', tiktok_url)
        if not item_id_match:
            raise ValueError("Invalid TikTok URL - no video/photo ID found")
        
        # Get the non-None group
        item_id = next(g for g in item_id_match.groups() if g)
        print(f"📱 Extracted TikTok item ID: {item_id}")
        
        # Call TikTok API16 endpoint (internal mobile API)
        api_url = f"https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/feed/?aweme_id={item_id}"
        headers = {
            "User-Agent": "okhttp/3.14.9 (Linux; Android 10; Pixel 6 Build/QP1A.190711.020; wv)",
            "Referer": "https://www.tiktok.com/",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json",
        }
        
        print(f"🌐 Calling TikTok API16: {api_url}")
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        print(f"✅ API16 response received (status: {response.status_code})")
        
        # Initialize result structure
        media = {
            "source": "tiktok_api16",
            "video_url": None,
            "photo_urls": [],
            "caption": None,
        }
        
        try:
            aweme_list = data.get("aweme_list", [])
            if not aweme_list:
                print("⚠️ API16 response missing aweme_list")
                raise ValueError("Invalid API16 response structure")
            
            aweme = aweme_list[0]
            
            # Extract caption
            caption = aweme.get("desc", "").strip()
            media["caption"] = caption
            if caption:
                print(f"📝 Caption extracted: {caption[:100]}...")
            
            # Check for video post
            if "video" in aweme and "play_addr" in aweme["video"]:
                play_addr = aweme["video"]["play_addr"]
                url_list = play_addr.get("url_list", [])
                if url_list and len(url_list) > 0:
                    media["video_url"] = url_list[0]
                    print(f"✅ Extracted video URL from API16")
            
            # Check for photo post
            if "image_post_info" in aweme:
                image_post_info = aweme["image_post_info"]
                images = image_post_info.get("images", [])
                
                # CRITICAL: Extract images in order from TikTok API to preserve slide order
                # The order of images in the API response matches the order in the TikTok slideshow
                photo_urls = []
                for idx, img in enumerate(images, 1):
                    display_image = img.get("display_image", {})
                    url_list = display_image.get("url_list", [])
                    if url_list and len(url_list) > 0:
                        photo_urls.append(url_list[0])
                        print(f"   📸 Image {idx}: {url_list[0][:60]}...")
                
                media["photo_urls"] = photo_urls
                if photo_urls:
                    print(f"✅ Extracted {len(photo_urls)} photo URLs from API16 in order (image 1 → image {len(photo_urls)})")
            
            return media
            
        except (KeyError, IndexError) as e:
            print(f"⚠️ Could not parse TikTok API16 response: {e}")
            import traceback
            print(traceback.format_exc())
            raise ValueError(f"Failed to parse API16 response: {e}")
            
    except requests.exceptions.RequestException as e:
        print(f"⚠️ TikTok API16 request failed: {e}")
        raise
    except (KeyError, ValueError) as e:
        print(f"⚠️ TikTok API16 parsing failed: {e}")
        raise
    except Exception as e:
        print(f"⚠️ Unexpected error calling TikTok API16: {e}")
        import traceback
        print(traceback.format_exc())
        raise

def robust_tiktok_extractor(url):
    """
    Fool-proof TikTok media extractor.
    Tries TikTok API16 first, then SnapTik fallback, then Playwright.
    Returns dict with {source, caption, photo_urls, video_url}.
    """
    # Clean URL - remove query parameters that might interfere
    clean_url = url.split('?')[0] if '?' in url else url
    if clean_url != url:
        print(f"🔗 Cleaned URL: {url} -> {clean_url}")
        url = clean_url
    
    print(f"🌐 Starting robust extraction for {url}")
    result = {"source": None, "caption": "", "photo_urls": [], "video_url": None}
    
    # --- STEP 1: TikTok Mobile API16 ---
    try:
        match = re.search(r'/video/(\d+)|/photo/(\d+)', url)
        if match:
            # Get the non-None group
            item_id = next(g for g in match.groups() if g)
            api_url = f"https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/feed/?aweme_id={item_id}"
            headers = {
                "User-Agent": "okhttp/3.14.9 (Linux; Android 10; Pixel 6 Build/QP1A.190711.020; wv)",
                "Referer": "https://www.tiktok.com/",
                "Accept-Language": "en-US,en;q=0.9",
            }
            r = requests.get(api_url, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            
            if "aweme_list" in data and len(data["aweme_list"]) > 0:
                aweme = data["aweme_list"][0]
                result["caption"] = aweme.get("desc", "").strip()
                
                # Check for photo post
                # CRITICAL: Extract images in order from TikTok API to preserve slide order
                if "image_post_info" in aweme:
                    images = aweme["image_post_info"].get("images", [])
                    photo_urls_ordered = []
                    for idx, img in enumerate(images, 1):
                        if "display_image" in img and "url_list" in img["display_image"] and len(img["display_image"]["url_list"]) > 0:
                            photo_urls_ordered.append(img["display_image"]["url_list"][0])
                            print(f"   📸 Image {idx}: {img['display_image']['url_list'][0][:60]}...")
                    result["photo_urls"] = photo_urls_ordered
                    if photo_urls_ordered:
                        print(f"✅ Extracted {len(photo_urls_ordered)} photo URLs in order (image 1 → image {len(photo_urls_ordered)})")
                
                # Check for video post
                if "video" in aweme and "play_addr" in aweme["video"]:
                    play_addr = aweme["video"]["play_addr"]
                    if "url_list" in play_addr and len(play_addr["url_list"]) > 0:
                        result["video_url"] = play_addr["url_list"][0]
                
                if result["photo_urls"] or result["video_url"]:
                    result["source"] = "tiktok_api16"
                    print(f"✅ TikTok API16 success: {len(result['photo_urls'])} photos, video: {bool(result['video_url'])}")
                    return result
    except Exception as e:
        print(f"⚠️ TikTok API16 failed: {e}")
    
    # --- STEP 2: SnapTik fallback ---
    try:
        print("🔄 Trying SnapTik fallback…")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Referer": "https://snaptik.kim/",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        r = requests.post(
            "https://snaptik.kim/?sd=1",
            headers=headers,
            data={"url": url},
            timeout=25,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Extract all tiktokcdn links
        links = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "tiktokcdn" in href:
                # Make sure it's a full URL
                if href.startswith("http"):
                    links.append(href)
                elif href.startswith("//"):
                    links.append("https:" + href)
        
        # Remove duplicates
        seen = set()
        unique_links = []
        for link in links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)
        
        if unique_links:
            # Separate photos and videos
            photo_urls = []
            video_url = None
            for link in unique_links:
                if ".mp4" in link.lower() or "/video/" in link.lower():
                    if not video_url:
                        video_url = link
                else:
                    photo_urls.append(link)
            
            result["photo_urls"] = photo_urls
            result["video_url"] = video_url
            
            # Try to extract caption from SnapTik page
            caption_elem = soup.find("div", class_=re.compile("desc|description|caption", re.I))
            if caption_elem:
                result["caption"] = caption_elem.get_text(strip=True)
            else:
                # Try meta tags
                meta_desc = soup.find("meta", attrs={"property": "og:description"})
                if meta_desc:
                    result["caption"] = meta_desc.get("content", "").strip()
            
            if result["photo_urls"] or result["video_url"]:
                result["source"] = "snaptik"
                print(f"✅ SnapTik fallback success: {len(result['photo_urls'])} photos, video: {bool(result['video_url'])}")
                return result
        else:
            print("⚠️ SnapTik returned no links.")
    except Exception as e:
        print(f"❌ SnapTik fallback error: {e}")
        import traceback
        print(traceback.format_exc())
    
    # --- STEP 3: Playwright fallback (delegated to existing extract_photo_post) ---
    print("🎭 Falling back to Playwright dynamic scraping…")
    try:
        # Delegate to extract_photo_post which has full Playwright implementation
        photo_data = extract_photo_post(url)
        if photo_data and photo_data.get("photos"):
            result["photo_urls"] = photo_data["photos"]
            result["caption"] = photo_data.get("caption", "").strip()
            result["source"] = "playwright"
            print(f"✅ Playwright fallback succeeded: {len(result['photo_urls'])} photos")
            return result
        else:
            print("⚠️ Playwright fallback returned no photos")
    except Exception as e:
        print(f"⚠️ Playwright fallback error: {e}")
        import traceback
        print(traceback.format_exc())
    
    result["source"] = "playwright_failed"
    return result

# SnapTik fallback (kept as backup but not used by default)
def snaptik_fallback(tiktok_url):
    """Fallback function to extract TikTok media using SnapTik service."""
    try:
        print(f"🔄 Trying SnapTik fallback for: {tiktok_url}")
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Referer": "https://snaptik.kim/",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        
        data = {"url": tiktok_url}
        api_url = "https://snaptik.kim/?sd=1"
        
        print(f"🌐 POSTing to SnapTik: {api_url}")
        response = requests.post(api_url, headers=headers, data=data, timeout=15)
        response.raise_for_status()
        
        print(f"✅ SnapTik response received (status: {response.status_code})")
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Extract all links containing tiktokcdn
        links = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "tiktokcdn" in href:
                # Make sure it's a full URL
                if href.startswith("http"):
                    links.append(href)
                elif href.startswith("//"):
                    links.append("https:" + href)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_links = []
        for link in links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)
        
        if not unique_links:
            print("⚠️ SnapTik returned no tiktokcdn links")
            # Try to extract caption from SnapTik page
            caption = ""
            # Look for caption in various places
            desc_elem = soup.find("div", class_=re.compile("desc|description|caption", re.I))
            if desc_elem:
                caption = desc_elem.get_text(strip=True)
            
            if not caption:
                # Try meta tags
                meta_desc = soup.find("meta", attrs={"property": "og:description"})
                if meta_desc:
                    caption = meta_desc.get("content", "").strip()
            
            if caption:
                print(f"📝 SnapTik extracted caption: {caption[:100]}...")
                return {
                    "source": "snaptik",
                    "photo_urls": [],
                    "video_url": "",
                    "caption": caption,
                }
            return None
        
        print(f"✅ SnapTik extracted {len(unique_links)} media files")
        
        # Determine if these are photos or videos based on URL patterns
        photo_urls = []
        video_url = ""
        
        for link in unique_links:
            # Check if it's a video (usually contains 'video' or ends with .mp4)
            if ".mp4" in link.lower() or "/video/" in link.lower():
                if not video_url:  # Use first video URL found
                    video_url = link
            else:
                # Assume it's a photo
                photo_urls.append(link)
        
        # Try to extract caption
        caption = ""
        desc_elem = soup.find("div", class_=re.compile("desc|description|caption", re.I))
        if desc_elem:
            caption = desc_elem.get_text(strip=True)
        
        if not caption:
            meta_desc = soup.find("meta", attrs={"property": "og:description"})
            if meta_desc:
                caption = meta_desc.get("content", "").strip()
        
        result = {
            "source": "snaptik",
            "photo_urls": photo_urls,
            "video_url": video_url,
        }
        
        if caption:
            result["caption"] = caption
            print(f"📝 SnapTik extracted caption: {caption[:100]}...")
        
        return result
        
    except requests.exceptions.RequestException as e:
        print(f"❌ SnapTik request failed: {e}")
        return None
    except Exception as e:
        print(f"❌ SnapTik fallback failed: {e}")
        import traceback
        print(traceback.format_exc())
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
                        # CRITICAL: Extract images in order to preserve slide order
                        if "ImageList" in obj:
                            urls = []
                            for idx, img in enumerate(obj["ImageList"], 1):
                                if isinstance(img, dict) and "UrlList" in img:
                                    if isinstance(img["UrlList"], list) and len(img["UrlList"]) > 0:
                                        urls.append(img["UrlList"][0])
                                        print(f"   📸 Image {idx} from ImageList: {img['UrlList'][0][:60]}...")
                            if urls:
                                print(f"✅ Extracted {len(urls)} photo URLs from ImageList in order (image 1 → image {len(urls)})")
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
    # Clean URL - remove query parameters that might interfere with yt-dlp
    clean_url = video_url.split('?')[0] if '?' in video_url else video_url
    if clean_url != video_url:
        print(f"🔗 Cleaned URL: {video_url} -> {clean_url}")
        video_url = clean_url
    
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

def clean_ocr_text(text):
    """
    Clean OCR text by removing garbled characters, excessive punctuation, and noise.
    """
    if not text:
        return ""
    
    # Remove excessive special characters (keep only common punctuation)
    import string
    # Keep letters, numbers, spaces, and common punctuation
    allowed_chars = string.ascii_letters + string.digits + string.whitespace + ".,!?;:'\"-()[]"
    cleaned = ''.join(c if c in allowed_chars else ' ' for c in text)
    
    # Remove excessive whitespace
    import re
    cleaned = re.sub(r'\s+', ' ', cleaned)
    
    # Remove lines that are mostly special characters or very short (< 2 chars)
    lines = cleaned.split('\n')
    good_lines = []
    for line in lines:
        line = line.strip()
        if len(line) < 2:
            continue
        # Skip lines that are mostly punctuation or special chars
        alpha_count = sum(1 for c in line if c.isalnum())
        if alpha_count < len(line) * 0.3:  # At least 30% should be alphanumeric
            continue
        good_lines.append(line)
    
    cleaned = '\n'.join(good_lines)
    
    # Remove standalone single characters or very short words that are likely OCR errors
    words = cleaned.split()
    good_words = []
    for word in words:
        # Keep words that are at least 2 chars OR are common short words
        if len(word) >= 2 or word.lower() in ['a', 'i', 'at', 'in', 'on', 'to', 'of', 'is', 'it']:
            # Remove words that are mostly punctuation
            if sum(1 for c in word if c.isalnum()) >= len(word) * 0.5:
                good_words.append(word)
    
    cleaned = ' '.join(good_words)
    
    return cleaned.strip()

def run_ocr_on_image(image_path):
    """
    AGGRESSIVE OCR for TikTok photos with multiple preprocessing strategies.
    Optimized specifically for white text on dark backgrounds (TikTok caption style).
    
    Returns cleaned extracted text or empty string if all methods fail quality checks.
    """
    if not OCR_AVAILABLE:
        print("⚠️ OCR not available (tesseract not installed) - skipping OCR")
        return ""
    
    try:
        print(f"🖼️ Running AGGRESSIVE OCR on image: {image_path[:60]}...")
        
        # Download if URL
        if image_path.startswith("http://") or image_path.startswith("https://"):
            print(f"📥 Downloading image for OCR: {image_path[:60]}...")
            r = requests.get(image_path, timeout=10)
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp.write(r.content)
                image_path = tmp.name
        
        # Read image
        img = cv2.imread(image_path)
        if img is None:
            print("⚠️ OpenCV failed, trying PIL...")
            pil_img = Image.open(image_path)
            img = np.array(pil_img.convert("RGB"))
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        
        # Downsize if too large
        height, width = img.shape[:2]
        if width > 2500 or height > 2500:
            scale = min(2500 / width, 2500 / height)
            new_width = int(width * scale)
            new_height = int(height * scale)
            img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_AREA)
            print(f"📏 Resized {width}x{height} → {new_width}x{new_height}")
        
        # ========== PRE-PROCESSING: Improve image quality FIRST ==========
        # This runs BEFORE all the other methods - improves base input
        print(f"  ▶️ Pre-processing: Denoise + Sharpen + Enhance...")
        
        # Aggressive denoising (removes JPEG compression artifacts)
        denoised = cv2.fastNlMeansDenoisingColored(img, None, 15, 15, 7, 21)
        
        # Sharpen (enhances text edges)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        sharpened = cv2.morphologyEx(denoised, cv2.MORPH_GRADIENT, kernel)
        img = cv2.addWeighted(denoised, 1.5, sharpened, -0.5, 0)
        
        # Unsharp mask for extra clarity
        blurred = cv2.GaussianBlur(img, (0, 0), 2)
        img = cv2.addWeighted(img, 1.8, blurred, -0.8, 0)
        
        print(f"  ✅ Pre-processed: Denoised + Sharpened + Unsharp mask applied")
        
        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Upscale small images (critical for OCR)
        height, width = gray.shape
        if width < 1500:  # Increased from 1200 to 1500
            scale = 1500 / width
            new_size = (int(width * scale), int(height * scale))
            gray = cv2.resize(gray, new_size, interpolation=cv2.INTER_CUBIC)
            print(f"📏 Upscaled {scale:.1f}x to {new_size[0]}x{new_size[1]} for OCR accuracy")
        
        # ===== AGGRESSIVE MULTI-METHOD PREPROCESSING =====
        texts_with_methods = []
        
        # METHOD 1: INVERT + DENOISE + ENHANCE (BEST for white TikTok text on dark)
        print("  ▶️ Method 1: Inverted + Denoised + Enhanced...")
        inverted = cv2.bitwise_not(gray)
        denoised_inv = cv2.fastNlMeansDenoising(inverted, None, 12, 9, 25)
        clahe_inv = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
        enhanced_inv = clahe_inv.apply(denoised_inv)
        text1 = pytesseract.image_to_string(enhanced_inv, config="--oem 3 --psm 6")
        if text1.strip():
            texts_with_methods.append(("inv_denoise_enhance", text1))
        
        # METHOD 2: INVERT + OTSU (high contrast)
        print("  ▶️ Method 2: Inverted + Otsu...")
        _, otsu_inv = cv2.threshold(enhanced_inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        text2 = pytesseract.image_to_string(otsu_inv, config="--oem 3 --psm 6")
        if text2.strip():
            texts_with_methods.append(("inv_otsu", text2))
        
        # METHOD 3: INVERT + ADAPTIVE (smooth varying backgrounds)
        print("  ▶️ Method 3: Inverted + Adaptive...")
        adaptive_inv = cv2.adaptiveThreshold(enhanced_inv, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 13, 3)
        text3 = pytesseract.image_to_string(adaptive_inv, config="--oem 3 --psm 6")
        if text3.strip():
            texts_with_methods.append(("inv_adaptive", text3))
        
        # METHOD 4: REGULAR grayscale + DENOISE + ENHANCE (for black text on light)
        print("  ▶️ Method 4: Regular + Denoised + Enhanced...")
        denoised = cv2.fastNlMeansDenoising(gray, None, 12, 9, 25)
        clahe = cv2.createCLAHE(clipLimit=3.5, tileGridSize=(8, 8))
        enhanced = clahe.apply(denoised)
        text4 = pytesseract.image_to_string(enhanced, config="--oem 3 --psm 6")
        if text4.strip():
            texts_with_methods.append(("gray_denoise_enhance", text4))
        
        # METHOD 5: REGULAR + OTSU
        print("  ▶️ Method 5: Regular + Otsu...")
        _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        text5 = pytesseract.image_to_string(otsu, config="--oem 3 --psm 6")
        if text5.strip():
            texts_with_methods.append(("gray_otsu", text5))
        
        # METHOD 6: REGULAR + ADAPTIVE
        print("  ▶️ Method 6: Regular + Adaptive...")
        adaptive = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 13, 3)
        text6 = pytesseract.image_to_string(adaptive, config="--oem 3 --psm 6")
        if text6.strip():
            texts_with_methods.append(("gray_adaptive", text6))
        
        # METHOD 7: BILATERAL + CLAHE (smooth + contrast)
        print("  ▶️ Method 7: Bilateral + CLAHE...")
        bilateral = cv2.bilateralFilter(gray, 11, 85, 85)
        clahe_bi = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced_bi = clahe_bi.apply(bilateral)
        text7 = pytesseract.image_to_string(enhanced_bi, config="--oem 3 --psm 6")
        if text7.strip():
            texts_with_methods.append(("bilateral_clahe", text7))
        
        # ===== SMART TEXT SELECTION =====
        best_text = ""
        best_method = ""
        best_score = 0
        
        print(f"\n📊 Evaluating {len(texts_with_methods)} extraction methods...")
        
        for method, text in texts_with_methods:
            cleaned = clean_ocr_text(text)
            
            if len(cleaned) < 8:
                continue
            
            words = [w for w in cleaned.split() if w]
            if not words or len(words) < 2:
                continue
            
            # QUALITY CHECKS
            word_lengths = [len(w) for w in words]
            avg_word_len = sum(word_lengths) / len(words)
            
            # Check 1: Word lengths reasonable (3-13 chars average for real text)
            if avg_word_len < 2.5 or avg_word_len > 16:
                print(f"   ❌ {method}: word_len={avg_word_len:.1f} (garbled)")
                continue
            
            # Check 2: Consonant clustering (max 5 in a row for real text)
            max_cons = 0
            curr_cons = 0
            for c in cleaned.lower():
                if c.isalpha() and c not in 'aeiou':
                    curr_cons += 1
                    max_cons = max(max_cons, curr_cons)
                else:
                    curr_cons = 0
            
            if max_cons > 6:
                print(f"   ❌ {method}: consonant_run={max_cons} (garbled)")
                continue
            
            # Check 3: Alphanumeric ratio
            alpha_count = sum(1 for c in cleaned if c.isalnum() or c in ' ,.-')
            alpha_ratio = alpha_count / len(cleaned) if cleaned else 0
            
            if alpha_ratio < 0.60:
                print(f"   ❌ {method}: alpha_ratio={alpha_ratio:.1%} (too many symbols)")
                continue
            
            # Check 4: Spacing
            space_ratio = cleaned.count(' ') / len(cleaned) if cleaned else 0
            if space_ratio < 0.07 or space_ratio > 0.50:
                print(f"   ❌ {method}: space_ratio={space_ratio:.1%} (weird spacing)")
                continue
            
            # Check 5: Case balance
            upper = sum(1 for c in cleaned if c.isupper())
            lower = sum(1 for c in cleaned if c.islower())
            if upper > 0 and lower > 0:
                upper_ratio = upper / (upper + lower)
                if upper_ratio > 0.55:
                    print(f"   ❌ {method}: uppercase={upper_ratio:.1%} (too many caps)")
                    continue
            
            # PASSED ALL CHECKS - Calculate score
            quality = (
                (alpha_ratio * 0.35) +  # Alphanumeric
                (min(len(cleaned) / 250, 1.0) * 0.35) +  # Length
                ((1.0 - max_cons / 12) * 0.30)  # Consonant factor
            )
            
            print(f"   ✅ {method}: score={quality:.2f}, words={len(words)}, len={len(cleaned)}")
            
            if quality > best_score:
                best_score = quality
                best_text = cleaned
                best_method = method
        
        if best_text and best_score > 0.45:
            print(f"\n✅ BEST: {best_method} (score={best_score:.2f}, {len(best_text)} chars)")
            print(f"   Preview: {best_text[:120]}...\n")
            return best_text
        else:
            print(f"\n⚠️ All methods failed quality check (best_score={best_score:.2f})")
            print(f"   This image likely has no readable text or only garbled OCR.\n")
            return ""
            
    except Exception as e:
        print(f"❌ OCR exception: {e}")
        import traceback
        print(traceback.format_exc())
        return ""

def detect_list_format(text):
    """
    Detect if text contains list patterns (numbered lists, bullet points, etc.)
    Returns True if list patterns are detected.
    """
    if not text or len(text.strip()) < 10:
        return False
    
    text_lower = text.lower()
    
    # Check for numbered list patterns (1., 2., 3., etc.)
    numbered_patterns = [
        r'\b\d+[\.\)]\s+[A-Z]',  # "1. Place" or "1) Place"
        r'\b\d+/\d+',  # "1/5", "2/5" (common in TikTok lists)
        r'#\d+',  # "#1", "#2"
    ]
    
    # Check for bullet point patterns
    bullet_patterns = [
        r'[•·▪▫◦]\s+[A-Z]',  # Bullet points
        r'[-*]\s+[A-Z]',  # Dashes or asterisks
    ]
    
    # Check for vertical list structure (multiple lines starting with capital letters)
    lines = text.split('\n')
    capital_start_lines = [l.strip() for l in lines if l.strip() and l.strip()[0].isupper()]
    
    # Count matches
    numbered_matches = sum(1 for pattern in numbered_patterns if re.search(pattern, text))
    bullet_matches = sum(1 for pattern in bullet_patterns if re.search(pattern, text))
    
    # If we have numbered patterns or multiple capital-start lines, likely a list
    has_numbered_list = numbered_matches > 0
    has_bullet_list = bullet_matches > 0
    has_vertical_list = len(capital_start_lines) >= 3  # At least 3 items
    
    # Also check for common list keywords
    list_keywords = ['top', 'best', 'favorite', 'must try', 'places', 'spots', 'restaurants', 'bars']
    has_list_keywords = any(keyword in text_lower for keyword in list_keywords)
    
    is_list = has_numbered_list or has_bullet_list or (has_vertical_list and has_list_keywords)
    
    if is_list:
        print(f"📋 List format detected: numbered={has_numbered_list}, bullet={has_bullet_list}, vertical={has_vertical_list}, keywords={has_list_keywords}")
    
    return is_list

def extract_ocr_text(video_path, sample_rate=1.0):
    """
    Extract on-screen text using OCR from video frames.
    
    Args:
        video_path: Path to video file
        sample_rate: Fraction of frames to process (1.0 = all frames, 0.5 = 50%, etc.)
    
    Returns:
        Extracted text or empty string if OCR unavailable
    """
    if not OCR_AVAILABLE:
        print("⚠️ OCR not available (tesseract not installed) - skipping OCR")
        return ""
    
    try:
        print(f"🧩 Extracting on-screen text with OCR (sample_rate={sample_rate})…")
        vidcap = cv2.VideoCapture(video_path)
        total = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = vidcap.get(cv2.CAP_PROP_FPS) or 30
        duration = total / fps if fps > 0 else 0
        
        print(f"📹 Video: {total} frames, {fps:.1f} fps, {duration:.1f}s duration")
        
        # DETECT SLIDESHOW: If frames are very similar, it's likely a slideshow
        # Slideshows have static slides with transitions
        is_slideshow = _detect_slideshow(vidcap, total)
        
        if is_slideshow:
            print("📸 SLIDESHOW DETECTED - Extracting text per slide")
            return _extract_ocr_per_slide(vidcap, total, fps, duration, sample_rate)
        else:
            print("🎥 REGULAR VIDEO - Extracting text from sampled frames")
            return _extract_ocr_all_frames(vidcap, total, fps, duration, sample_rate)

    except Exception as e:
        print(f"❌ OCR error: {e}")
        import traceback
        print(traceback.format_exc())
        return ""


def _detect_slideshow(vidcap, total):
    """
    Detect if video is a slideshow by checking if frames are similar.
    Slideshows have static slides that repeat.
    """
    try:
        if total < 10:
            return False
        
        # Sample frames throughout video
        sample_indices = [int(total * i / 5) for i in range(5)]
        
        frames_data = []
        for idx in sample_indices:
            vidcap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, img = vidcap.read()
            if ok:
                # Resize for comparison
                small = cv2.resize(img, (64, 64))
                frames_data.append(small)
        
        if len(frames_data) < 2:
            return False
        
        # Compare frames - if they're very different, it's a regular video
        # If they're similar, it might be a slideshow
        diffs = []
        for i in range(len(frames_data) - 1):
            diff = cv2.absdiff(frames_data[i], frames_data[i+1])
            mean_diff = diff.mean()
            diffs.append(mean_diff)
        
        avg_diff = sum(diffs) / len(diffs) if diffs else 0
        print(f"   Frame similarity: avg_diff={avg_diff:.1f}")
        
        # If average difference is low, frames are similar = slideshow
        is_slideshow = avg_diff < 5.0  # Threshold for "similar" frames
        
        return is_slideshow
    except Exception as e:
        print(f"   Slideshow detection failed: {e}")
        return False


def _extract_ocr_per_slide(vidcap, total, fps, duration, sample_rate):
    """
    Extract OCR text for each slide separately.
    Detects slide changes and extracts text per slide to avoid mixing context.
    """
    print("🔍 Detecting slide boundaries...")
    
    slide_boundaries = _detect_slide_boundaries(vidcap, total)
    print(f"   Found {len(slide_boundaries)} slides")
    
    all_slides_text = []
    
    for slide_num, (start, end) in enumerate(slide_boundaries, 1):
        print(f"\n📄 Slide {slide_num}: frames {start}-{end}")
        
        # Sample frames from this slide
        num_frames = max(1, int((end - start) / fps * 2))  # 2 frames per second
        slide_frames = np.linspace(start, end, min(num_frames, 5), dtype=int)
        
        slide_text_parts = []
        
        for frame_idx in slide_frames:
            vidcap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, img = vidcap.read()
            if not ok:
                continue
                
            # Run OCR on this frame
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Preprocess
            height, width = gray.shape
            if width < 1000:
                scale = 1000 / width
                new_size = (int(width * scale), int(height * scale))
                gray = cv2.resize(gray, new_size, interpolation=cv2.INTER_CUBIC)
            
            # OCR
            pil_img = Image.fromarray(gray)
            try:
                configs = [
                    r'--oem 3 --psm 11',  # Sparse text
                    r'--oem 3 --psm 6',   # Uniform block
                ]
                frame_text = ""
                for config in configs:
                    text = image_to_string(pil_img, config=config)
                    if len(text) > len(frame_text):
                        frame_text = text
                
                if frame_text.strip():
                    slide_text_parts.append(frame_text.strip())
            except Exception as e:
                print(f"   ⚠️ OCR failed on frame {frame_idx}: {e}")
                continue
        
        # Deduplicate and combine slide text
        slide_text = " ".join(dict.fromkeys(slide_text_parts))
        
        if slide_text.strip():
            print(f"   ✅ Extracted {len(slide_text)} chars: {slide_text[:100]}...")
            all_slides_text.append(f"SLIDE {slide_num}:\n{slide_text}")
        else:
            print(f"   ⚠️ No text found on slide {slide_num}")
    
    # Return all slides with clear separation
    combined = "\n\n".join(all_slides_text)
    print(f"\n✅ Total OCR text: {len(combined)} chars from {len(all_slides_text)} slides")
    
    return combined


def _detect_slide_boundaries(vidcap, total):
    """
    Detect where slides change in a slideshow.
    Returns list of (start_frame, end_frame) tuples for each slide.
    """
    try:
        # Sample every 10 frames to detect changes
        sample_interval = max(1, total // 30)  # Sample ~30 points
        
        prev_gray = None
        boundaries = [0]  # Start with frame 0
        
        for idx in range(0, total, sample_interval):
            vidcap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, img = vidcap.read()
            if not ok:
                continue
            
            # Downscale for faster comparison
            gray = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (160, 120))
            
            if prev_gray is not None:
                # Calculate difference
                diff = cv2.absdiff(gray, prev_gray)
                mean_diff = diff.mean()
                
                # Significant change = slide transition
                if mean_diff > 10.0:  # Threshold for "different" slide
                    boundaries.append(idx)
            
            prev_gray = gray
        
        boundaries.append(total - 1)  # End with last frame

        # Convert boundaries to (start, end) tuples
        slide_ranges = []
        for i in range(len(boundaries) - 1):
            slide_ranges.append((boundaries[i], boundaries[i + 1]))

        return slide_ranges
    except Exception as e:
        print(f"⚠️ Slide detection failed: {e}, using fallback")
        # Fallback: assume 3-4 slides
        slide_count = max(3, min(10, total // 100))
        frames_per_slide = total // slide_count
        return [(i * frames_per_slide, (i + 1) * frames_per_slide) for i in range(slide_count)]


def _extract_ocr_all_frames(vidcap, total, fps, duration, sample_rate):
    """
    Extract OCR text from sampled frames (for regular videos, not slideshows).
    """
    print(f"📹 Extracting OCR from {total} frames...")
    
    # Determine sampling
    if duration > 0:
        frames_per_second = 2 if duration < 30 else 1
        num_frames = int(duration * frames_per_second)
    else:
        num_frames = 20
    
    # Apply sample_rate
    num_frames = max(1, int(num_frames * sample_rate))
    
    frames = np.linspace(0, total - 1, min(total, num_frames), dtype=int)
    print(f"   Sampling {len(frames)} frames")
    
    all_texts = []
    
    for frame_idx in frames:
        vidcap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, img = vidcap.read()
        if not ok:
                        continue
            
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Upscale if needed
        height, width = gray.shape
        if width < 1000:
            scale = 1000 / width
            new_size = (int(width * scale), int(height * scale))
            gray = cv2.resize(gray, new_size, interpolation=cv2.INTER_CUBIC)
        
        # Try multiple configs
        best_text = ""
        for config in [r'--oem 3 --psm 11', r'--oem 3 --psm 6']:
            try:
                pil_img = Image.fromarray(gray)
                text = image_to_string(pil_img, config=config)
                if len(text) > len(best_text):
                    best_text = text
            except:
                pass
        
        if best_text.strip():
            all_texts.append(best_text.strip())
    
    combined = " ".join(all_texts)
    print(f"✅ Extracted {len(combined)} chars from {len(frames)} frames")
    return combined


# ─────────────────────────────
# Google Places API - Optimized Geocoding Service
# ─────────────────────────────
# Initialize fallback cache (always available)
_places_cache = {}
_MAX_CACHE_SIZE = 1000

# Import the optimized geocoding service (reduces costs by 80-95%)
OPTIMIZED_GEOCODING_AVAILABLE = False
try:
    from geocoding_service import get_geocoding_service
    # Test if we can actually create the service (googlemaps might not be installed)
    try:
        _test_service = get_geocoding_service()
        OPTIMIZED_GEOCODING_AVAILABLE = True
        print("✅ Optimized geocoding service loaded - cost reduction enabled")
    except (ImportError, ValueError) as e:
        print(f"⚠️ Optimized geocoding service not available: {e}")
        print("   Falling back to basic caching. Install googlemaps and rapidfuzz for full optimization.")
        OPTIMIZED_GEOCODING_AVAILABLE = False
except ImportError as e:
    print(f"⚠️ Optimized geocoding service not available: {e}")
    print("   Falling back to basic caching. Install googlemaps and rapidfuzz for full optimization.")
    OPTIMIZED_GEOCODING_AVAILABLE = False

def _clear_places_cache_if_needed():
    """Clear cache if it gets too large (keep most recent 500 entries)."""
    global _places_cache
    try:
        # Ensure _places_cache exists
        if _places_cache is None:
            _places_cache = {}
    except NameError:
        _places_cache = {}
    
    if len(_places_cache) > _MAX_CACHE_SIZE:
        keys_to_remove = list(_places_cache.keys())[:len(_places_cache) // 2]
        for key in keys_to_remove:
            del _places_cache[key]
        print(f"🧹 Cleared {len(keys_to_remove)} old cache entries (cache size: {len(_places_cache)})")

def _extract_neighborhood_from_text(text):
    """Extract neighborhood/area from context text (OCR, caption, etc).

    Looks for neighborhood mentions in the text like "hottest new Soho bar".

    Args:
        text: Context text from OCR/caption/transcript

    Returns:
        Neighborhood name (e.g., "SoHo", "East Village") or None
    """
    if not text:
        return None

    # Comprehensive NYC neighborhoods list
    neighborhoods = [
        # Downtown / Below 14th
        "Downtown", "Lower Manhattan",
        "Lower East Side", "LES",
        "East Village", "EV",
        "Alphabet City",
        "NoHo", "Noho",
        "Nolita", "NoLita",
        "SoHo", "Soho",
        "Chinatown",
        "Little Italy",
        "Two Bridges",
        "Tribeca", "TriBeCa",
        "West Village",
        "Greenwich Village",
        "Hudson Square",
        "Battery Park City",
        "Financial District", "FiDi", "FIDI",
        # Midtown-ish
        "Koreatown", "K-Town", "KTown",
        "Hell's Kitchen", "Hells Kitchen",
        "Midtown West", "Theater District",
        "Midtown East",
        "Murray Hill",
        "Gramercy",
        "Flatiron",
        "Kips Bay",
        "Chelsea",
        "Hudson Yards",
        "NoMad", "Nomad", "NOMAD",
        # Islands / Special Areas
        "Roosevelt Island",
        # Uptown
        "Upper West Side", "UWS",
        "Upper East Side", "UES",
        "Harlem",
        "East Harlem",
        "Morningside Heights",
        "Washington Heights",
        "Inwood",
        # Brooklyn - Waterfront / North Brooklyn
        "Williamsburg",
        "East Williamsburg",
        "Greenpoint",
        "Bushwick",
        # Brooklyn - Brownstone Brooklyn
        "Brooklyn Heights",
        "DUMBO",
        "Cobble Hill",
        "Carroll Gardens",
        "Boerum Hill",
        "Gowanus",
        "Park Slope",
        "Prospect Heights",
        "Fort Greene",
        "Clinton Hill",
        # Brooklyn - Further Out
        "Bedford-Stuyvesant", "Bed-Stuy", "BedStuy",
        "Crown Heights",
        "Red Hook",
        "Sunset Park",
        "Bay Ridge",
        # Queens
        "Astoria",
        "Long Island City", "LIC",
        "Sunnyside",
        "Jackson Heights",
        "Elmhurst",
        "Flushing",
        "Forest Hills",
        # Bronx
        "Belmont", "Arthur Avenue",
        "Mott Haven",
        # Staten Island
        "St. George", "St George",
    ]

    text_lower = text.lower()

    # Sort neighborhoods by length (longest first) to prioritize more specific matches
    # This ensures "Greenwich Village" matches before "East Village" when both could match
    sorted_neighborhoods = sorted(neighborhoods, key=len, reverse=True)

    # Look for neighborhood names in the text
    for neighborhood in sorted_neighborhoods:
        if neighborhood.lower() in text_lower:
            return neighborhood

    # If no specific neighborhood found, try to extract borough
    boroughs = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]
    for borough in boroughs:
        if borough.lower() in text_lower:
            return borough

    return None


def _extract_neighborhood_from_address(address):
    """Extract neighborhood/area from Google Maps formatted address.

    Args:
        address: Formatted address from Google Maps

    Returns:
        Neighborhood name (e.g., "SoHo", "East Village") or None
    """
    if not address:
        return None

    # Comprehensive NYC neighborhoods list
    neighborhoods = [
        # Downtown / Below 14th
        "Downtown", "Lower Manhattan",
        "Lower East Side", "LES",
        "East Village", "EV",
        "Alphabet City",
        "NoHo", "Noho",
        "Nolita", "NoLita",
        "SoHo", "Soho",
        "Chinatown",
        "Little Italy",
        "Two Bridges",
        "Tribeca", "TriBeCa",
        "West Village",
        "Greenwich Village",
        "Hudson Square",
        "Battery Park City",
        "Financial District", "FiDi", "FIDI",
        # Midtown-ish
        "Koreatown", "K-Town", "KTown",
        "Hell's Kitchen", "Hells Kitchen",
        "Midtown West", "Theater District",
        "Midtown East",
        "Murray Hill",
        "Gramercy",
        "Flatiron",
        "Kips Bay",
        "Chelsea",
        "Hudson Yards",
        "NoMad", "Nomad", "NOMAD",
        # Islands / Special Areas
        "Roosevelt Island",
        # Uptown
        "Upper West Side", "UWS",
        "Upper East Side", "UES",
        "Harlem",
        "East Harlem",
        "Morningside Heights",
        "Washington Heights",
        "Inwood",
        # Brooklyn - Waterfront / North Brooklyn
        "Williamsburg",
        "East Williamsburg",
        "Greenpoint",
        "Bushwick",
        # Brooklyn - Brownstone Brooklyn
        "Brooklyn Heights",
        "DUMBO",
        "Cobble Hill",
        "Carroll Gardens",
        "Boerum Hill",
        "Gowanus",
        "Park Slope",
        "Prospect Heights",
        "Fort Greene",
        "Clinton Hill",
        # Brooklyn - Further Out
        "Bedford-Stuyvesant", "Bed-Stuy", "BedStuy",
        "Crown Heights",
        "Red Hook",
        "Sunset Park",
        "Bay Ridge",
        # Queens
        "Astoria",
        "Long Island City", "LIC",
        "Sunnyside",
        "Jackson Heights",
        "Elmhurst",
        "Flushing",
        "Forest Hills",
        # Bronx
        "Belmont", "Arthur Avenue",
        "Mott Haven",
        # Staten Island
        "St. George", "St George",
    ]

    address_lower = address.lower()

    # Sort neighborhoods by length (longest first) to prioritize more specific matches
    sorted_neighborhoods = sorted(neighborhoods, key=len, reverse=True)

    # Check neighborhoods FIRST (prioritize specific neighborhoods over boroughs)
    for neighborhood in sorted_neighborhoods:
        if neighborhood.lower() in address_lower:
            return neighborhood  # Return immediately when found

    # Only check boroughs if no specific neighborhood found
    boroughs = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]
    for borough in boroughs:
        if borough.lower() in address_lower:
            return borough

    return None


def get_nyc_neighborhood_strict(venue_name="", address="", latitude=None, longitude=None):
    """
    Extract NYC neighborhood following strict priority rules.
    
    RULE 1: Static overrides (venue name exact matches) - ALWAYS RETURN THESE
    RULE 2: If lat/lon provided, use neighborhood boundary grid
    RULE 3: If only address provided, parse and infer from grid
    RULE 4: Return ONLY the neighborhood name (no commentary, no JSON)
    
    Args:
        venue_name: Venue name (for static overrides)
        address: Address string (for parsing)
        latitude: Latitude coordinate (optional)
        longitude: Longitude coordinate (optional)
    
    Returns:
        Neighborhood name string or "Unknown"
    """
    import re
    
    # RULE 1: STATIC OVERRIDES (ALWAYS RETURN THESE)
    static_overrides = {
        "Soogil": "East Village",
        "Ulysses'": "Financial District",
        "Old Mates Pub": "Financial District",
        "Ten Degrees": "East Village",
        "Drift In": "West Village"
    }
    
    if venue_name and venue_name in static_overrides:
        return static_overrides[venue_name]
    
    # RULE 2: IF LAT/LON IS PROVIDED - Use neighborhood boundary grid
    if latitude is not None and longitude is not None:
        lat = float(latitude)
        lon = float(longitude)
        
        # MANHATTAN NEIGHBORHOOD GRID
        # Lower Manhattan
        if 40.6990 <= lat <= 40.7089 and -74.0180 <= lon <= -74.0030:
            return "Financial District"
        if 40.7050 <= lat <= 40.7175 and -74.0195 <= lon <= -74.0120:
            return "Battery Park City"
        if 40.7130 <= lat <= 40.7250 and -74.0135 <= lon <= -74.0060:
            return "Tribeca"
        if 40.7210 <= lat <= 40.7290 and -74.0060 <= lon <= -73.9980:
            return "SoHo"
        if 40.7200 <= lat <= 40.7265 and -73.9980 <= lon <= -73.9920:
            return "Nolita"
        if 40.7150 <= lat <= 40.7250 and -73.9990 <= lon <= -73.9910:
            return "Little Italy"
        if 40.7105 <= lat <= 40.7195 and -74.0035 <= lon <= -73.9930:
            return "Chinatown"
        if 40.7090 <= lat <= 40.7245 and -73.9940 <= lon <= -73.9770:
            return "Lower East Side"
        if 40.7230 <= lat <= 40.7335 and -73.9925 <= lon <= -73.9730:
            return "East Village"
        if 40.7280 <= lat <= 40.7410 and -74.0100 <= lon <= -74.0000:
            return "West Village"
        if 40.7280 <= lat <= 40.7370 and -74.0000 <= lon <= -73.9940:
            return "Greenwich Village"
        
        # Mid-Manhattan
        if 40.7370 <= lat <= 40.7540 and -74.0055 <= lon <= -73.9920:
            return "Chelsea"
        if 40.7360 <= lat <= 40.7475 and -73.9920 <= lon <= -73.9840:
            return "Flatiron"
        if 40.7310 <= lat <= 40.7395 and -73.9890 <= lon <= -73.9780:
            return "Gramercy"
        if 40.7345 <= lat <= 40.7445 and -73.9780 <= lon <= -73.9710:
            return "Kips Bay"
        if 40.7430 <= lat <= 40.7520 and -73.9840 <= lon <= -73.9715:
            return "Murray Hill"
        if 40.7480 <= lat <= 40.7640 and -73.9715 <= lon <= -73.9550:
            return "Midtown East"
        if 40.7475 <= lat <= 40.7710 and -74.0050 <= lon <= -73.9850:
            return "Midtown West"
        if 40.7510 <= lat <= 40.7630 and -74.0070 <= lon <= -73.9950:
            return "Hudson Yards"
        if 40.7570 <= lat <= 40.7680 and -73.9880 <= lon <= -73.9800:
            return "Theater District"
        
        # Upper Manhattan
        if 40.7680 <= lat <= 40.8060 and -73.9950 <= lon <= -73.9580:
            return "Upper West Side"
        if 40.7620 <= lat <= 40.7850 and -73.9670 <= lon <= -73.9440:
            return "Upper East Side"
        if 40.8000 <= lat <= 40.8340 and -73.9700 <= lon <= -73.9300:
            return "Harlem"
        if 40.8400 <= lat <= 40.8700 and -73.9450 <= lon <= -73.9150:
            return "Washington Heights"
        
        # BROOKLYN NEIGHBORHOOD GRID
        # Williamsburg
        if 40.7210 <= lat <= 40.7265 and -73.9635 <= lon <= -73.9440:
            return "Williamsburg"
        if 40.7070 <= lat <= 40.7210 and -73.9660 <= lon <= -73.9420:
            return "Williamsburg"
        if 40.7110 <= lat <= 40.7250 and -73.9420 <= lon <= -73.9180:
            return "East Williamsburg"
        
        # Bushwick
        if 40.6890 <= lat <= 40.7110 and -73.9390 <= lon <= -73.9000:
            return "Bushwick"
        
        # Greenpoint
        if 40.7230 <= lat <= 40.7400 and -73.9630 <= lon <= -73.9330:
            return "Greenpoint"
        
        # Dumbo
        if 40.6990 <= lat <= 40.7070 and -73.9935 <= lon <= -73.9810:
            return "DUMBO"
        
        # Brooklyn Heights
        if 40.6920 <= lat <= 40.7015 and -73.9990 <= lon <= -73.9880:
            return "Brooklyn Heights"
        
        # Cobble Hill
        if 40.6840 <= lat <= 40.6920 and -73.9990 <= lon <= -73.9840:
            return "Cobble Hill"
        
        # Carroll Gardens
        if 40.6750 <= lat <= 40.6890 and -74.0060 <= lon <= -73.9880:
            return "Carroll Gardens"
        
        # Park Slope
        if 40.6745 <= lat <= 40.6815 and -73.9830 <= lon <= -73.9700:
            return "Park Slope"
        if 40.6560 <= lat <= 40.6745 and -74.0000 <= lon <= -73.9730:
            return "Park Slope"
        
        # Gowanus
        if 40.6675 <= lat <= 40.6825 and -73.9990 <= lon <= -73.9800:
            return "Gowanus"
        
        # Fort Greene / Clinton Hill
        if 40.6830 <= lat <= 40.6960 and -73.9890 <= lon <= -73.9685:
            return "Fort Greene"
        if 40.6840 <= lat <= 40.7000 and -73.9685 <= lon <= -73.9535:
            return "Clinton Hill"
        
        # Bed-Stuy
        if 40.6790 <= lat <= 40.7000 and -73.9535 <= lon <= -73.9100:
            return "Bedford-Stuyvesant"
        
        # Prospect Heights
        if 40.6730 <= lat <= 40.6830 and -73.9760 <= lon <= -73.9620:
            return "Prospect Heights"
        
        # Crown Heights
        if 40.6640 <= lat <= 40.6810 and -73.9600 <= lon <= -73.9300:
            return "Crown Heights"
        
        # QUEENS
        # Astoria
        if 40.7550 <= lat <= 40.7890 and -73.9440 <= lon <= -73.8950:
            return "Astoria"
        
        # Long Island City
        if 40.7350 <= lat <= 40.7525 and -73.9620 <= lon <= -73.9340:
            return "Long Island City"
        
        # Sunnyside
        if 40.7390 <= lat <= 40.7500 and -73.9330 <= lon <= -73.9150:
            return "Sunnyside"
        
        # Jackson Heights
        if 40.7460 <= lat <= 40.7620 and -73.9030 <= lon <= -73.8700:
            return "Jackson Heights"
        
        # Flushing
        if 40.7445 <= lat <= 40.7780 and -73.8400 <= lon <= -73.8150:
            return "Flushing"
    
    # RULE 3: IF ONLY ADDRESS IS PROVIDED - Parse street and infer from grid
    if address:
        # Extract just the street address part (before first comma) for better parsing
        # Google Maps addresses are often "35 W 19th St, New York, NY 10011"
        street_address = address.split(',')[0].strip()
        address_lower = address.lower()
        street_address_lower = street_address.lower()
        
        # Check for Roosevelt Island (N Loop Rd is unique to Roosevelt Island)
        if "n loop rd" in address_lower or "north loop road" in address_lower or "roosevelt island" in address_lower:
            return "Roosevelt Island"
        
        # Check for Financial District indicators (including Nassau St)
        if any(street in address_lower for street in ["stone st", "wall st", "broad st", "water st", "john st", "william st", "pearl st", "nassau st", "nassau street"]):
            return "Financial District"
        
        # Check for Park Avenue South (Flatiron/Gramercy area)
        if "park ave s" in address_lower or "park avenue south" in address_lower:
            # Park Ave S runs from ~14th to ~34th, generally Flatiron/Gramercy area
            return "Flatiron"
        
        # Check for Flatiron indicators
        if any(street in address_lower for street in ["broadway", "5th ave", "5th avenue", "madison square"]):
            # Check if it's in the Flatiron range (roughly 14th-34th)
            street_match = re.search(r'(\d+)(?:st|nd|rd|th|street)', address_lower)
            if street_match:
                street_num = int(street_match.group(1))
                if 14 <= street_num <= 34:
                    return "Flatiron"
        
        # Check for East Village indicators
        if any(street in address_lower for street in ["st marks", "st mark's", "avenue a", "avenue b", "avenue c", "e 4th", "e 5th", "e 6th", "e 7th", "e 8th", "e 9th", "e 10th", "e 11th", "e 12th", "e 13th"]):
            if "e " in address_lower or "east" in address_lower:
                return "East Village"
        
        # Check for West Village indicators (including W 12th St)
        if any(street in address_lower for street in ["west st", "hudson st", "greenwich ave", "christopher st", "bleecker st", "w 4th", "w 10th", "w 11th", "w 12th", "w 13th"]):
            if "w " in address_lower or "west" in address_lower:
                return "West Village"
        
        # Try to extract street number and direction - improved regex to handle "35 W 19th St" pattern
        # Pattern 1: "35 W 19th St" or "35 West 19th Street" - use street_address for better matching
        street_match = re.search(r'\b(\d+)\s+(W|E|West|East)\s+(\d+)(?:st|nd|rd|th|street)', street_address, re.I)
        if street_match:
            street_num = int(street_match.group(3))
            street_dir = street_match.group(2)
            street_dir_lower = street_dir.lower() if street_dir else ""
            
            # Upper East Side: 60th-96th St, east side
            if 60 <= street_num <= 96 and ('e' in street_dir_lower or 'east' in street_dir_lower):
                return "Upper East Side"
            # Upper West Side: 60th-110th St, west side
            elif 60 <= street_num <= 110 and ('w' in street_dir_lower or 'west' in street_dir_lower):
                return "Upper West Side"
            # Midtown East: 34th-59th St, east side
            elif 34 <= street_num <= 59 and ('e' in street_dir_lower or 'east' in street_dir_lower):
                return "Midtown East"
            # Midtown West: 34th-59th St, west side
            elif 34 <= street_num <= 59 and ('w' in street_dir_lower or 'west' in street_dir_lower):
                return "Midtown West"
            # Chelsea: 14th-34th St, west side
            elif 14 <= street_num <= 34 and ('w' in street_dir_lower or 'west' in street_dir_lower):
                return "Chelsea"
            # Gramercy/Murray Hill: 14th-34th St, east side
            elif 14 <= street_num <= 34 and ('e' in street_dir_lower or 'east' in street_dir_lower):
                if street_num <= 23:
                    return "Gramercy"
                else:
                    return "Murray Hill"
            # Below 14th St
            elif street_num < 14:
                if 'e' in street_dir_lower or 'east' in street_dir_lower:
                    return "East Village"
                elif 'w' in street_dir_lower or 'west' in street_dir_lower:
                    return "West Village"
        else:
            # Pattern 2: Try simpler pattern like "19th St" or "W 19th St" - use street_address
            street_match2 = re.search(r'\b(W|E|West|East)?\s*(\d+)(?:st|nd|rd|th|street)', street_address, re.I)
            if street_match2:
                street_num = int(street_match2.group(2))
                street_dir = street_match2.group(1) if street_match2.group(1) else ""
                street_dir_lower = street_dir.lower() if street_dir else ""
                
                # Check address for direction if not in street_dir
                if not street_dir_lower:
                    if "w " in address_lower or "west" in address_lower:
                        street_dir_lower = "w"
                    elif "e " in address_lower or "east" in address_lower:
                        street_dir_lower = "e"
                
                # Upper East Side: 60th-96th St, east side
                if 60 <= street_num <= 96 and ('e' in street_dir_lower or 'east' in street_dir_lower):
                    return "Upper East Side"
                # Upper West Side: 60th-110th St, west side
                elif 60 <= street_num <= 110 and ('w' in street_dir_lower or 'west' in street_dir_lower):
                    return "Upper West Side"
                # Midtown East: 34th-59th St, east side
                elif 34 <= street_num <= 59 and ('e' in street_dir_lower or 'east' in street_dir_lower):
                    return "Midtown East"
                # Midtown West: 34th-59th St, west side
                elif 34 <= street_num <= 59 and ('w' in street_dir_lower or 'west' in street_dir_lower):
                    return "Midtown West"
                # Chelsea: 14th-34th St, west side
                elif 14 <= street_num <= 34 and ('w' in street_dir_lower or 'west' in street_dir_lower):
                    return "Chelsea"
                # Gramercy/Murray Hill: 14th-34th St, east side
                elif 14 <= street_num <= 34 and ('e' in street_dir_lower or 'east' in street_dir_lower):
                    if street_num <= 23:
                        return "Gramercy"
                    else:
                        return "Murray Hill"
                # Below 14th St
                elif street_num < 14:
                    if 'e' in street_dir_lower or 'east' in street_dir_lower:
                        return "East Village"
                    elif 'w' in street_dir_lower or 'west' in street_dir_lower:
                        return "West Village"
    
    return "Unknown"


def infer_nyc_neighborhood_from_address(address, venue_name=""):
    """
    Infer NYC neighborhood from address and venue name using geographic knowledge.

    STRICT RULES:
    - Always return one of the allowed neighborhoods
    - Do NOT invent new neighborhoods
    - If unsure, pick the closest correct neighborhood by NYC geography
    - Never return boroughs (like "Manhattan"), only neighborhoods

    Args:
        address: Google Maps address string
        venue_name: Venue name (may contain neighborhood hints)

    Returns:
        Neighborhood string from allowed list, or None if can't determine
    """
    import re  # Import re at function level to avoid scope issues
    
    if not address:
        return None

    # First, try to extract neighborhood from street address using street numbers
    # This handles addresses like "35 E 76th St" -> Upper East Side
    street_match = re.search(r'(\d+)\s*(?:E|W|East|West)?\s*(\d+)(?:st|nd|rd|th)', address, re.I)
    if street_match:
        street_num = int(street_match.group(2))
        street_dir = street_match.group(1) if street_match.group(1) else ""
        address_lower = address.lower()
        
        # Upper East Side: 60th-96th St, east of Central Park
        if 60 <= street_num <= 96 and ('east' in address_lower or 'e ' in address_lower or 'east ' in address_lower):
            return "Upper East Side"
        # Upper West Side: 60th-110th St, west of Central Park
        elif 60 <= street_num <= 110 and ('west' in address_lower or 'w ' in address_lower or 'west ' in address_lower):
            return "Upper West Side"
        # Midtown East: 34th-59th St, east side
        elif 34 <= street_num <= 59 and ('east' in address_lower or 'e ' in address_lower or 'east ' in address_lower):
            return "Midtown East"
        # Midtown West: 34th-59th St, west side
        elif 34 <= street_num <= 59 and ('west' in address_lower or 'w ' in address_lower or 'west ' in address_lower):
            return "Midtown West"
        # Chelsea: 14th-34th St, west side
        elif 14 <= street_num <= 34 and ('west' in address_lower or 'w ' in address_lower or 'west ' in address_lower or 'chelsea' in address_lower):
            return "Chelsea"
        # Gramercy/Murray Hill: 14th-34th St, east side
        elif 14 <= street_num <= 34 and ('east' in address_lower or 'e ' in address_lower or 'east ' in address_lower):
            if street_num <= 23:
                return "Gramercy"
            else:
                return "Murray Hill"
        # Below 14th St - various neighborhoods
        elif street_num < 14:
            if 'east' in address_lower or 'e ' in address_lower:
                return "East Village"
            elif 'west' in address_lower or 'w ' in address_lower:
                return "West Village"
    
    # Check for specific streets/avenues that indicate neighborhoods
    address_lower = address.lower()
    if 'vanderbilt' in address_lower or 'grand central' in address_lower:
        return "Midtown East"
    if 'madison' in address_lower and ('70' in address or '77' in address or '76' in address):
        return "Upper East Side"
    if 'park avenue' in address_lower or 'park ave' in address_lower:
        if any(str(n) in address for n in range(50, 100)):
            return "Upper East Side"
        elif any(str(n) in address for n in range(34, 50)):
            return "Midtown East"
    
    # Combine address and venue name for matching
    combined_text = f"{address} {venue_name}".lower()

    # Define allowed neighborhoods with geographic aliases and street boundaries
    nyc_neighborhoods = {
        # Lower Manhattan
        "Lower East Side": ["lower east side", "les", "ludlow", "orchard street", "essex", "delancey"],
        "East Village": ["east village", "ev", "st marks", "avenue a", "avenue b", "tompkins square"],
        "West Village": ["west village", "christopher street", "bleecker street", "hudson street", "jane street", "charles street"],
        "Greenwich Village": ["greenwich village", "washington square", "macdougal", "minetta"],
        "SoHo": ["soho", "spring street soho", "broome street soho", "prince street soho", "wooster", "mercer street"],
        "Nolita": ["nolita", "elizabeth street", "mott street nolita", "kenmare"],
        "NoHo": ["noho", "bond street", "great jones"],
        "Little Italy": ["little italy", "mulberry street", "mott street little italy"],
        "Chinatown": ["chinatown", "canal street", "bayard", "pell street", "doyers"],
        "Tribeca": ["tribeca", "franklin street", "harrison street", "chambers street tribeca"],
        "FiDi": ["financial district", "fidi", "wall street", "broad street", "stone street", "water street fidi"],
        "Lower Manhattan": ["battery park", "bowling green"],

        # Midtown
        "Chelsea": ["chelsea", "8th avenue chelsea", "9th avenue chelsea", "10th avenue chelsea", "w 23rd", "w 22nd", "w 21st", "w 20th", "w 19th"],
        "Flatiron": ["flatiron", "broadway flatiron", "5th avenue flatiron", "madison square"],
        "Gramercy": ["gramercy", "park avenue south", "irving place", "e 23rd", "e 22nd", "e 21st", "e 20th", "e 19th"],
        "Midtown East": ["midtown east", "lexington avenue", "3rd avenue midtown", "e 50th", "e 49th", "e 48th", "e 47th", "e 46th", "e 45th", "e 44th", "e 43rd", "e 42nd midtown"],
        "Midtown West": ["midtown west", "w 50th", "w 49th", "w 48th", "w 47th", "w 46th", "w 45th", "w 44th", "w 43rd", "w 42nd midtown", "8th avenue midtown", "9th avenue midtown"],
        "Hell's Kitchen": ["hell's kitchen", "hells kitchen", "9th avenue hell's", "10th avenue hell's", "w 57th", "w 56th", "w 55th", "w 54th", "w 53rd", "w 52nd", "w 51st"],

        # Uptown
        "Upper East Side": ["upper east side", "ues", "park avenue upper", "madison avenue upper", "lexington upper", "e 86th", "e 85th", "e 84th", "e 83rd", "e 82nd", "e 81st", "e 80th", "e 79th", "e 78th", "e 77th", "e 76th", "e 75th", "e 74th", "e 73rd", "e 72nd", "e 71st", "e 70th", "e 69th", "e 68th", "e 67th", "e 66th", "e 65th"],
        "Upper West Side": ["upper west side", "uws", "amsterdam avenue", "columbus avenue", "w 86th", "w 85th", "w 84th", "w 83rd", "w 82nd", "w 81st", "w 80th", "w 79th", "w 78th", "w 77th", "w 76th", "w 75th", "w 74th", "w 73rd", "w 72nd", "w 71st", "w 70th", "w 69th", "w 68th", "w 67th", "w 66th", "w 65th"],
        "Harlem": ["harlem", "125th street", "lenox avenue", "malcolm x boulevard", "frederick douglass"],

        # Brooklyn
        "Bushwick": ["bushwick", "knickerbocker", "myrtle avenue bushwick", "flushing avenue bushwick"],
        "Williamsburg": ["williamsburg", "bedford avenue", "n 6th", "n 7th", "n 8th", "berry street"],
        "Greenpoint": ["greenpoint", "manhattan avenue", "franklin street greenpoint"],

        # Queens
        "Long Island City": ["long island city", "lic", "jackson avenue", "court square", "hunters point"],
        "Astoria": ["astoria", "steinway", "31st avenue", "30th avenue", "broadway astoria"],
    }

    # Try exact neighborhood matches first
    for neighborhood, patterns in nyc_neighborhoods.items():
        for pattern in patterns:
            if pattern in combined_text:
                return neighborhood

    # If no match, use zip code as fallback
    zip_to_neighborhood = {
        # Manhattan
        "10002": "Lower East Side", "10003": "East Village", "10009": "East Village",
        "10012": "SoHo", "10013": "Tribeca", "10014": "West Village",
        "10004": "FiDi", "10005": "FiDi", "10006": "FiDi", "10007": "Tribeca",
        "10010": "Gramercy", "10011": "Chelsea", "10016": "Gramercy",
        "10017": "Midtown East", "10018": "Midtown West", "10019": "Hell's Kitchen",
        "10021": "Upper East Side", "10022": "Midtown East", "10023": "Upper West Side",
        "10024": "Upper West Side", "10025": "Upper West Side",
        "10028": "Upper East Side", "10029": "Harlem",
        "10128": "Upper East Side", "10075": "Upper East Side",

        # Brooklyn
        "11206": "Bushwick", "11211": "Williamsburg", "11222": "Greenpoint",

        # Queens
        "11101": "Long Island City", "11102": "Astoria", "11103": "Astoria",
    }

    # Extract zip code from address
    zip_match = re.search(r'\b(\d{5})\b', address)
    if zip_match:
        zip_code = zip_match.group(1)
        if zip_code in zip_to_neighborhood:
            return zip_to_neighborhood[zip_code]

    return None


def get_place_info_from_google(place_name, use_cache=True, location_hint=""):
    """Get canonical name, address, place_id, photos, neighborhood, and price_level from Google Maps API.

    Uses optimized geocoding service if available (80-95% cost reduction).
    Falls back to basic caching if service not available.

    Args:
        place_name: Name of the place to search for
        use_cache: If True, use cached results to avoid redundant API calls
        location_hint: Optional location hint (e.g., "NYC", "Brooklyn")

    Returns:
        Tuple of (canonical_name, address, place_id, photos, neighborhood, price_level)
        price_level: 0-4 (0=Free, 1=Inexpensive, 2=Moderate, 3=Expensive, 4=Very Expensive) or None
    """
    if not GOOGLE_API_KEY:
        print(f"⚠️ GOOGLE_API_KEY not set - cannot get place info for {place_name}")
        return None, None, None, None, None, None

    # Use optimized geocoding service if available
    if OPTIMIZED_GEOCODING_AVAILABLE and use_cache:
        try:
            service = get_geocoding_service()
            result = service.resolve_single_place(place_name, location_hint)

            if result:
                canonical_name = result.get('canonical_name') or result.get('name', place_name)
                address = result.get('address') or result.get('formatted_address', '')
                place_id = result.get('place_id')
                photos = result.get('photos', [])
                # Don't extract neighborhood here - let Place Details API handle it (more accurate)
                neighborhood = None
                price_level = result.get('price_level')  # May not be in optimized service

                print(f"✅ Found place (optimized): {canonical_name} (place_id: {place_id[:20] if place_id else 'None'}..., photos: {len(photos) if photos else 0}, price_level: {price_level})")
                return canonical_name, address, place_id, photos, neighborhood, price_level
            else:
                print(f"⚠️ No results found for {place_name} (optimized service) - falling back to basic method")
                # Don't return early - fall through to basic method below
        except (ImportError, ValueError, Exception) as e:
            # If optimized service fails, disable it and fall back to basic method
            print(f"⚠️ Optimized geocoding service error: {e} - falling back to basic method")
            # Don't print full traceback for expected ImportError
            if not isinstance(e, ImportError):
                import traceback
                print(traceback.format_exc())
            # Continue to fallback below
    
    # Fallback to basic caching method
    # Ensure _places_cache is available (should always be, but check for safety)
    global _places_cache
    try:
        # Try to access _places_cache to ensure it exists
        if _places_cache is None:
            _places_cache = {}
    except NameError:
        # If _places_cache doesn't exist, create it
        _places_cache = {}
    
    cache_key = place_name.lower().strip()
    if use_cache and cache_key in _places_cache:
        cached_result = _places_cache[cache_key]
        print(f"💾 Using cached result for: {place_name}")
        return cached_result
    
    try:
        # Add "NYC" to search query for better matching (e.g., "Vee Ray's NYC")
        search_query = f"{place_name} NYC" if "NYC" not in place_name.upper() and "New York" not in place_name else place_name
        # Add location hint to prioritize NYC results
        location_hint_param = "New York, NY" if location_hint != "NYC" else "New York, NY"
        print(f"🔍 Searching Google Places for: {search_query} (location hint: {location_hint_param})")
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": search_query, "location": "40.7128,-74.0060", "radius": "50000", "key": GOOGLE_API_KEY},  # NYC coordinates, 50km radius
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        
        api_status = data.get("status")
        if api_status != "OK":
            error_message = data.get('error_message', 'Unknown error')
            print(f"⚠️ Google Places search error for {place_name}: {api_status} - {error_message}")
            
            # Provide specific guidance for common errors
            if api_status == "REQUEST_DENIED":
                print(f"   ❌ REQUEST_DENIED: Check that GOOGLE_API_KEY is valid and Places API is enabled")
            elif api_status == "OVER_QUERY_LIMIT":
                print(f"   ❌ OVER_QUERY_LIMIT: Google Places API quota exceeded")
            elif api_status == "INVALID_REQUEST":
                print(f"   ❌ INVALID_REQUEST: Invalid search query or parameters")
            elif api_status == "ZERO_RESULTS":
                print(f"   ⚠️ ZERO_RESULTS: No places found matching '{place_name}'")
            
            return None, None, None, None, None, None

        res = data.get("results", [])
        if res and len(res) > 0:
            # Filter results to prioritize NYC venues
            nyc_results = []
            for place_info in res:
                address = place_info.get("formatted_address", "").lower()
                # Check if address indicates NYC
                nyc_indicators = ["new york", "ny", "manhattan", "brooklyn", "queens", "bronx", "staten island"]
                non_nyc_indicators = ["denver", "co", "colorado", "california", "ca", "los angeles", "la", "chicago", "il", "illinois", "miami", "fl", "florida", "boston", "ma", "massachusetts", "seattle", "wa", "washington", "portland", "or", "oregon", "philadelphia", "pa", "pennsylvania", "atlanta", "ga", "georgia", "dallas", "tx", "texas", "houston", "austin", "san francisco", "sf", "san diego", "phoenix", "az", "arizona", "las vegas", "nv", "nevada"]
                is_nyc = any(indicator in address for indicator in nyc_indicators)
                is_non_nyc = any(indicator in address for indicator in non_nyc_indicators)
                if is_nyc and not is_non_nyc:
                    nyc_results.append(place_info)

            # Use first NYC result if available, otherwise validate with is_nyc_venue
            if nyc_results:
                place_info = nyc_results[0]
                print(f"   ✅ Found NYC venue: {place_info.get('name')} ({place_info.get('formatted_address', '')[:50]}...)")
            else:
                # No NYC results found in filtering - use first result and validate
                from location_filters import is_nyc_venue
                place_info = res[0]
                is_nyc, reason = is_nyc_venue(place_info.get("formatted_address", ""))

                if not is_nyc:
                    print(f"   ⚠️ Warning: Non-NYC venue found: {place_info.get('name')} - {reason}")
                    # Try to find a better match by searching with more specific NYC terms
                    return None, None, None, None, None, None

                print(f"   ✅ Found NYC venue: {place_info.get('name')} ({place_info.get('formatted_address', '')[:50]}...)")
            
            canonical_name = place_info.get("name", place_name)
            address = place_info.get("formatted_address")
            place_id = place_info.get("place_id")
            photos = place_info.get("photos", [])
            # Don't extract neighborhood here - let Place Details API handle it (more accurate)
            neighborhood = None
            price_level = place_info.get("price_level")  # 0-4 or None

            result = (canonical_name, address, place_id, photos, neighborhood, price_level)

            # Cache the result
            if use_cache:
                # Ensure _places_cache exists before accessing
                try:
                    if _places_cache is None:
                        _places_cache = {}
                except NameError:
                    _places_cache = {}
                _places_cache[cache_key] = result
                if len(_places_cache) > _MAX_CACHE_SIZE:
                    _clear_places_cache_if_needed()
                print(f"💾 Cached result for: {place_name}")

            print(f"✅ Found place: {canonical_name} (place_id: {place_id[:20] if place_id else 'None'}..., photos: {len(photos) if photos else 0}, price_level: {price_level})")
            return result
        else:
            print(f"⚠️ No results found for {place_name}")
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Failed to get place info from Google for {place_name} - Request error: {e}")
    except Exception as e:
        print(f"⚠️ Failed to get place info from Google for {place_name} - Unexpected error: {e}")
        import traceback
        print(traceback.format_exc())
    return None, None, None, None, None, None

# ─────────────────────────────
# Price Level Helper
# ─────────────────────────────
def price_level_to_dollars(price_level):
    """
    Convert Google Maps price_level (0-4) to dollar signs.

    Args:
        price_level: Integer 0-4 or None
            0 = Free
            1 = Inexpensive ($)
            2 = Moderate ($$)
            3 = Expensive ($$$)
            4 = Very Expensive ($$$$)

    Returns:
        String like "$", "$$", "$$$", "$$$$" or None
    """
    if price_level is None:
        return None

    price_map = {
        0: None,  # Free - don't show anything
        1: "$",
        2: "$$",
        3: "$$$",
        4: "$$$$"
    }

    return price_map.get(price_level)

# ─────────────────────────────
# Google Places Photo
# ─────────────────────────────
def get_photo_url(name, place_id=None, photos=None):
    """Get photo URL from Google Places API. Can use place_id/photos if already fetched."""
    if not GOOGLE_API_KEY:
        print(f"⚠️ GOOGLE_API_KEY not set - cannot fetch photo for {name}")
        return None
    
    # If photos already provided, use them
    if photos and len(photos) > 0:
        try:
            # Handle both dict and list formats
            photo_data = photos[0] if isinstance(photos[0], dict) else photos[0]
            ref = photo_data.get("photo_reference") if isinstance(photo_data, dict) else None
            if ref:
                photo_url = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=800&photoreference={ref}&key={GOOGLE_API_KEY}"
                print(f"✅ Using photo from search results for {name}")
                return photo_url
        except Exception as e:
            print(f"⚠️ Error extracting photo from provided photos: {e}")
    
    # If place_id provided, use Place Details API (more reliable)
    if place_id:
        try:
            print(f"🔍 Fetching photo via Place Details API for place_id: {place_id[:20]}...")
            r = requests.get(
                "https://maps.googleapis.com/maps/api/place/details/json",
                params={"place_id": place_id, "fields": "photo,name", "key": GOOGLE_API_KEY},
                timeout=10
            )
            r.raise_for_status()
            data = r.json()
            
            if data.get("status") != "OK":
                print(f"⚠️ Place Details API error: {data.get('status')} - {data.get('error_message', 'Unknown error')}")
            else:
                result = data.get("result", {})
                photos = result.get("photos", [])
                if photos and len(photos) > 0:
                    ref = photos[0].get("photo_reference")
                    if ref:
                        photo_url = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=800&photoreference={ref}&key={GOOGLE_API_KEY}"
                        print(f"✅ Got photo via Place Details API for {result.get('name', name)}")
                        return photo_url
                else:
                    print(f"⚠️ No photos found in Place Details for {place_id[:20]}...")
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Google photo fail (place_id) - Request error: {e}")
        except Exception as e:
            print(f"⚠️ Google photo fail (place_id) - Unexpected error: {e}")
            import traceback
            print(traceback.format_exc())
    
    # Fallback: search by name with NYC
    try:
        search_query = f"{name} NYC" if "NYC" not in name.upper() and "New York" not in name else name
        print(f"🔍 Fallback: Searching for photo by name: {search_query}")
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": search_query, "key": GOOGLE_API_KEY}, 
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        
        if data.get("status") != "OK":
            print(f"⚠️ Text Search API error: {data.get('status')} - {data.get('error_message', 'Unknown error')}")
        else:
            res = data.get("results", [])
            if res and len(res) > 0:
                place_info = res[0]
                photos = place_info.get("photos", [])
                if photos and len(photos) > 0:
                    ref = photos[0].get("photo_reference")
                    if ref:
                        photo_url = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=800&photoreference={ref}&key={GOOGLE_API_KEY}"
                        print(f"✅ Got photo via Text Search for {place_info.get('name', name)}")
                        return photo_url
                else:
                    print(f"⚠️ No photos found in search results for {name}")
            else:
                print(f"⚠️ No search results found for {name}")
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Google photo fail (search) - Request error: {e}")
    except Exception as e:
        print(f"⚠️ Google photo fail (search) - Unexpected error: {e}")
        import traceback
        print(traceback.format_exc())
    
    print(f"❌ Failed to get photo for {name} after all attempts")
    return None

# ─────────────────────────────
# Organize Slides by Venue
# ─────────────────────────────
def _is_slide_unrelated_content(slide_text):
    """
    Detect if slide contains unrelated TikTok content (hashtags, CTAs, etc.)
    that should not be attributed to any venue.

    Returns True if slide appears to be unrelated/promotional content.
    """
    if not slide_text or len(slide_text.strip()) < 10:
        return True

    # Count hashtags
    hashtag_count = slide_text.count('#')
    if hashtag_count > 3:
        print(f"      🚫 Detected unrelated content: excessive hashtags ({hashtag_count})")
        return True

    # Check for common TikTok CTAs (like, follow, share, subscribe)
    cta_patterns = [
        r'\bfollow\s+for\s+more\b',
        r'\blike\s+and\s+subscribe\b',
        r'\bshare\s+this\s+video\b',
        r'\bturn\s+on\s+notifications\b',
        r'\blink\s+in\s+bio\b',
        r'\bfollow\s+me\s+on\b',
        r'\bcheck\s+out\s+my\b',
    ]
    for pattern in cta_patterns:
        if re.search(pattern, slide_text.lower()):
            print(f"      🚫 Detected unrelated content: TikTok CTA pattern")
            return True

    # If slide is mostly hashtags or symbols, it's unrelated
    alphanumeric = sum(1 for c in slide_text if c.isalnum() or c.isspace())
    if alphanumeric / len(slide_text) < 0.5:
        print(f"      🚫 Detected unrelated content: mostly non-alphanumeric")
        return True

    return False


def organize_slides_by_venue(ocr_text):
    """
    Organize OCR text from slides by venue attribution.

    Rules:
    1. If a slide explicitly mentions a place name, create new Place section
    2. If next slide has NO place but adds context -> attach to most recent place
    3. If context slides appear BEFORE place name -> attach to place that appears at end
    4. Preserve chronological order within each place group

    Args:
        ocr_text: OCR text with "SLIDE N:" markers

    Returns:
        dict: {place_name: ["Page X: text", "Page Y: text", ...]}
    """
    if not ocr_text or 'SLIDE' not in ocr_text.upper():
        return {}

    # Parse slides
    slide_dict = _parse_slide_text(ocr_text)
    if len(slide_dict) < 2:
        return {}  # Not a slideshow

    print(f"\n📚 Organizing {len(slide_dict)} slides by venue attribution...")

    # Step 1: Detect place names in each slide using GPT
    # CRITICAL: Sort slides by numeric slide number (not lexicographically)
    slide_to_places = {}
    slides_sorted = _sort_slides_by_number(slide_dict)
    for slide_key, _ in slides_sorted:
        slide_text = slide_dict[slide_key]

        if not slide_text or len(slide_text.strip()) < 5:
            continue

        # Use GPT to extract place names from this slide
        try:
            client = get_openai_client()
            prompt = f"""Extract ONLY the place/venue name from this slide text.
If multiple places mentioned, list them (one per line).
If NO places mentioned, output: (none)

IMPORTANT: OCR text may be garbled. If you see text that could be a garbled venue name (e.g., "CTU REN" might be "CACTUS WREN") 
AND there's context suggesting a venue (e.g., "cactus shaped corn bread" suggests "Cactus Wren"), extract the most likely venue name.
However, ONLY extract if there's clear context - don't extract random garbled text.

Slide text:
{slide_text[:500]}

Place names only:"""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                timeout=10
            )
            result = response.choices[0].message.content.strip()

            places = []
            for line in result.split('\n'):
                line = line.strip()
                if not line or line.lower() == '(none)':
                    continue
                # Remove bullets/numbers
                line = re.sub(r"^[\d\-\•\.\s]+", "", line).strip()
                if 2 < len(line) < 60:
                    places.append(line)

            if places:
                slide_to_places[slide_key] = places
                print(f"   {slide_key}: {places}")
            else:
                slide_to_places[slide_key] = []
                print(f"   {slide_key}: (context only - no place name)")

        except Exception as e:
            print(f"   ⚠️ Failed to analyze {slide_key}: {e}")
            slide_to_places[slide_key] = []

    # Step 2: Apply attribution rules
    place_to_slides = {}
    current_place = None
    context_buffer = []  # Slides without place names

    # CRITICAL: Sort slides by numeric slide number (not lexicographically)
    slides_sorted = _sort_slides_by_number(slide_dict)

    for slide_key, slide_text in slides_sorted:
        places_in_slide = slide_to_places.get(slide_key, [])

        if places_in_slide:
            # Rule 1: Explicit place mention -> new Place section
            for place in places_in_slide:
                if place not in place_to_slides:
                    place_to_slides[place] = []

                # If we have buffered context slides, attach them here
                # (Rule 3: context slides before place name)
                if context_buffer and not current_place:
                    for ctx_key, ctx_text in context_buffer:
                        place_to_slides[place].append(f"{ctx_key}: {ctx_text}")
                    context_buffer = []

                # Add current slide
                place_to_slides[place].append(f"{slide_key}: {slide_text}")
                current_place = place
        else:
            # Rule 2: No place name -> context slide
            # CRITICAL: Check if slide is unrelated content (TikTok CTAs, hashtags, etc.)
            if _is_slide_unrelated_content(slide_text):
                print(f"   ⚠️ {slide_key}: Skipping unrelated content")
                # Don't attach to any venue or buffer it
            elif current_place:
                # Attach to most recent place
                place_to_slides[current_place].append(f"{slide_key}: {slide_text}")
            else:
                # Buffer for next place (Rule 3)
                context_buffer.append((slide_key, slide_text))

    # Handle any remaining context buffer (no place found after them)
    if context_buffer:
        place_to_slides["Unknown / Context"] = [f"{key}: {text}" for key, text in context_buffer]

    # Log results
    print(f"\n📖 Attribution complete:")
    for place, slides in place_to_slides.items():
        print(f"   {place}: {len(slides)} slide(s)")

    return place_to_slides

# ─────────────────────────────
# GPT: Extract Venues + Summary
# ─────────────────────────────
def _is_ocr_garbled(text):
    """
    Detect if OCR text is too garbled/corrupted to be useful.
    Garbled text has too many special characters, random letters, and few real words.
    """
    if not text or len(text) < 30:
        return False
    
    # Count different character types
    total_chars = len(text)
    alphanumeric = sum(1 for c in text if c.isalnum() or c.isspace())
    special_chars = total_chars - alphanumeric
    
    # Calculate garbling ratio
    garble_ratio = special_chars / total_chars if total_chars > 0 else 0
    
    # If more than 35% special characters, it's probably garbled (lowered threshold)
    if garble_ratio > 0.35:
        print(f"   🚫 Garble detection: {garble_ratio:.1%} special chars (threshold: 35%)")
        return True
    
    # Check for excessive consonant clusters (like "ERR oe on vee BA RES")
    words = text.split()
    if len(words) > 10:
        # Count words with excessive consonants (3+ consonants in a row)
        consonant_cluster_words = 0
        for word in words[:20]:  # Check first 20 words
            max_consonants = 0
            current_run = 0
            for char in word.lower():
                if char.isalpha() and char not in 'aeiou':
                    current_run += 1
                    max_consonants = max(max_consonants, current_run)
                else:
                    current_run = 0
            if max_consonants >= 4:  # 4+ consonants in a row is suspicious
                consonant_cluster_words += 1
        
        if consonant_cluster_words > len(words[:20]) * 0.3:  # More than 30% have excessive clusters
            print(f"   🚫 Garble detection: {consonant_cluster_words}/{len(words[:20])} words have excessive consonant clusters")
            return True
    
    # Check for structured OCR (SLIDE markers indicate legitimate OCR)
    has_slide_markers = 'SLIDE' in text.upper() or re.search(r'SLIDE\s+\d+', text, re.I)
    
    # Check for proper nouns (capitalized words) - common in venue names
    # FIXED: Allow all capitalized words (not just Title Case) to count as proper nouns
    # This includes "SLIDE", "LEMON", acronyms, and regular Title Case words
    capitalized_words = sum(1 for w in words if len(w) > 1 and w[0].isupper())
    proper_noun_ratio = capitalized_words / len(words) if words else 0
    
    # CRITICAL: Check if text is mostly random 1-3 character "words" (garbled OCR)
    # Example: "vee ae ra Me we a a ee ee cf a. ay USSG nn. ib. it ray af fh i SY"
    short_words = sum(1 for w in words if len(w.strip('.,!?;:')) <= 3)
    short_word_ratio = short_words / len(words) if words else 0

    # If text has SLIDE markers, it's structured OCR from slideshow - be more lenient
    # (Slideshow OCR often has short words but is still valuable)
    threshold = 0.90 if has_slide_markers else 0.75

    # If more than threshold% of words are 1-3 characters, it's likely garbled
    # (real text has longer words mixed in)
    if short_word_ratio > threshold and len(words) > 20:
        print(f"   🚫 Garble detection: {short_word_ratio:.1%} of words are 1-3 chars ({short_words}/{len(words)}) - likely garbled OCR")
        return True
    
    # Check for patterns of random letters (like "vee ae ra Me we a a ee ee cf")
    # Count sequences of 1-2 char words followed by another 1-2 char word
    random_letter_sequences = 0
    for i in range(len(words) - 2):
        w1 = words[i].strip('.,!?;:')
        w2 = words[i+1].strip('.,!?;:')
        w3 = words[i+2].strip('.,!?;:')
        # If we have 3+ consecutive very short words, it's suspicious
        if len(w1) <= 2 and len(w2) <= 2 and len(w3) <= 2:
            random_letter_sequences += 1
    
    if random_letter_sequences > len(words) * 0.15:  # More than 15% of word positions are in random sequences
        print(f"   🚫 Garble detection: Found {random_letter_sequences} random letter sequences (pattern: 'vee ae ra Me we a a ee ee')")
        return True
    
    # If text has slide markers AND high ratio of proper nouns AND not mostly short words, it's likely legitimate OCR
    # (venue names, menu items are proper nouns)
    if has_slide_markers and proper_noun_ratio > 0.15 and short_word_ratio < 0.5:
        print(f"   ✅ OCR appears legitimate: has slide markers, {proper_noun_ratio:.1%} proper nouns, {short_word_ratio:.1%} short words")
        return False  # Not garbled - has structure and proper nouns
    elif proper_noun_ratio > 0.25 and short_word_ratio < 0.4:  # High proper nouns, low short words
        print(f"   ✅ OCR appears legitimate: {proper_noun_ratio:.1%} proper nouns, {short_word_ratio:.1%} short words")
        return False
    
    # Check for common words - if barely any AND no proper nouns, it's garbled
    # FIXED: Expanded to include food/menu/atmosphere vocabulary
    common_words = [
        'the', 'and', 'a', 'to', 'of', 'in', 'is', 'at', 'for', 'nyc', 'restaurant', 'food', 'pizza', 'bar', 'cafe', 'coffee', 'ny', 'new', 'york',
        # Food and menu vocabulary
        'lemon', 'chicken', 'pasta', 'salad', 'with', 'or', 'but', 'all', 'very', 'so',
        # Atmosphere vocabulary
        'vibes', 'romantic', 'exclusive', 'were', 'yet', 'menu', 'order', 'get'
    ]
    word_matches = sum(1 for w in words if w.lower().strip('.,!?') in common_words)

    # Only flag as garbled if no common words AND no proper nouns
    # FIXED: Lowered threshold from 0.1 (10%) to 0.05 (5%) to allow 7.4% proper nouns to pass
    if len(words) > 15 and word_matches < 2 and proper_noun_ratio < 0.05:
        print(f"   🚫 Garble detection: Found only {word_matches} common words and {proper_noun_ratio:.1%} proper nouns in {len(words)} words")
        return True
    
    # Check for patterns that indicate garbled text (like "wee ce ERR oe")
    suspicious_patterns = [
        r'\b\w{1,2}\s+\w{1,2}\s+\w{1,2}\b',  # Many 1-2 char words in a row
        r'\b[A-Z]{3,}\s+[a-z]{1,2}\s+[A-Z]{3,}\b',  # Mixed case patterns like "ERR oe BA"
    ]
    for pattern in suspicious_patterns:
        matches = len(re.findall(pattern, text[:500]))  # Check first 500 chars
        if matches > 5:
            print(f"   🚫 Garble detection: Found {matches} suspicious patterns")
            return True
    
    return False


def _parse_slide_text(ocr_text):
    """
    Parse OCR text that contains SLIDE markers and return a dict of slides.
    Returns: {"slide_1": "text from slide 1", "slide_2": "text from slide 2", ...}
    Slides are stored in order of appearance in the OCR text.
    """
    if not ocr_text:
        return {}
    
    slides = {}
    current_slide = None
    current_text = []
    
    for line in ocr_text.split('\n'):
        # Check if line starts with "SLIDE N:"
        match = re.match(r"^SLIDE\s+(\d+)\s*:\s*(.*)$", line.strip(), re.I)
        if match:
            # Save previous slide if exists
            if current_slide is not None:
                slides[f"slide_{current_slide}"] = "\n".join(current_text).strip()
            
            # Start new slide
            current_slide = int(match.group(1))
            current_text = [match.group(2)] if match.group(2) else []
        elif current_slide is not None:
            # Add to current slide
            if line.strip():
                current_text.append(line.strip())
    
    # Don't forget the last slide
    if current_slide is not None:
        slides[f"slide_{current_slide}"] = "\n".join(current_text).strip()
    
    return slides

def _sort_slides_by_number(slide_dict):
    """
    Sort slides by their numeric slide number (not lexicographically).
    This ensures slide_2 comes before slide_10, slide_14, etc.
    
    Args:
        slide_dict: Dictionary with keys like "slide_1", "slide_2", etc.
    
    Returns:
        List of (slide_key, slide_text) tuples sorted by slide number
    """
    def get_slide_number(slide_key):
        """Extract numeric slide number from key like 'slide_2' -> 2"""
        try:
            return int(slide_key.split('_')[1])
        except (IndexError, ValueError):
            return 999  # Put invalid slides at the end
    
    # Sort by numeric slide number
    return sorted(slide_dict.items(), key=lambda x: get_slide_number(x[0]))


def _validate_venue_context_isolation(venue_to_context, all_venue_names):
    """
    Validate that each venue's context doesn't mention other venues.
    Returns cleaned venue_to_context dict with warnings for any bleeding detected.

    This is a final safety check to catch any context bleeding that slipped through
    earlier filters during context building.
    """
    validated_context = {}

    for venue, context in venue_to_context.items():
        if not context:
            validated_context[venue] = context
            continue

        context_lower = context.lower()
        venue_lower = venue.lower()

        # Check if context mentions other venues
        other_venues = [v for v in all_venue_names if v.lower() != venue_lower]
        mentions_others = []

        for other_venue in other_venues:
            other_lower = other_venue.lower()
            # Use word boundary to avoid false positives (e.g., "bar" matching "Monkey Bar")
            if len(other_lower) > 3 and re.search(r'\b' + re.escape(other_lower) + r'\b', context_lower):
                mentions_others.append(other_venue)

        if mentions_others:
            print(f"   ⚠️ CONTEXT BLEEDING DETECTED: {venue} context mentions {mentions_others}")
            print(f"   🔧 Re-filtering to remove sentences mentioning other venues...")

            # Re-filter: split into sentences, keep only those mentioning THIS venue
            sentences = re.split(r'[.!?]\s+', context)
            clean_sentences = []

            for sentence in sentences:
                sentence_lower = sentence.lower()
                # Check if sentence mentions this venue (word boundary matching)
                mentions_this = re.search(r'\b' + re.escape(venue_lower) + r'\b', sentence_lower)
                # Check if mentions other venues (word boundary matching)
                mentions_other = any(
                    len(v.lower()) > 3 and re.search(r'\b' + re.escape(v.lower()) + r'\b', sentence_lower)
                    for v in other_venues
                )

                # Only keep sentences that mention THIS venue and NOT other venues
                if mentions_this and not mentions_other:
                    clean_sentences.append(sentence)

            cleaned_context = ". ".join(clean_sentences)
            validated_context[venue] = cleaned_context
            print(f"   ✅ Cleaned context: {len(cleaned_context)} chars (was {len(context)})")
        else:
            validated_context[venue] = context

    return validated_context


def _build_venue_attribution(unique_venues, venue_to_slide, slides_with_attribution, all_venues_per_slide):
    """
    Build explicit venue attribution mapping with slide ownership.

    Args:
        unique_venues: List of venue names
        venue_to_slide: Dict mapping venue -> slide_key (e.g., {"Venue A": "slide_1"})
        slides_with_attribution: List of slide attribution dicts from OCR
        all_venues_per_slide: Dict mapping slide_key -> [venues] showing which venues are on each slide

    Returns:
        Dict mapping venue_name -> attribution data:
        {
            "VENUE_NAME": {
                "primary_slide": int,  # Slide number where venue first appears
                "contextual_slides": [int],  # Following slides that belong to this venue
                "all_slides": [int],  # All slides (primary + contextual)
                "ocr_content": {"slide_N": "text"},  # OCR text for each slide
                "full_context": str  # Combined text from all slides
            }
        }
    """
    venue_attribution = {}

    # Create slide_number -> slide_data mapping for quick lookup
    slide_lookup = {s["slide_number"]: s for s in slides_with_attribution}

    # Extract slide number from slide_key (e.g., "slide_3" -> 3)
    def get_slide_number(slide_key):
        try:
            return int(slide_key.split('_')[1])
        except (IndexError, ValueError):
            return None

    # Build attribution for each venue
    for venue in unique_venues:
        primary_slide_key = venue_to_slide.get(venue)
        if not primary_slide_key:
            continue

        primary_slide_num = get_slide_number(primary_slide_key)
        if primary_slide_num is None:
            continue

        # Find which slides this venue appears on (primary)
        # and which following slides belong to it (contextual)
        contextual_slides = []
        all_slides = [primary_slide_num]
        ocr_content = {}

        # Add primary slide content
        if primary_slide_num in slide_lookup:
            ocr_content[f"slide_{primary_slide_num}"] = slide_lookup[primary_slide_num]["full_text"]

        # Find contextual slides: slides after primary until next venue
        # Iterate through all slides after the primary
        for slide_num in sorted(slide_lookup.keys()):
            if slide_num <= primary_slide_num:
                continue  # Skip slides before or at primary

            # Check if this slide has a new venue
            slide_key = f"slide_{slide_num}"
            has_new_venue = any(
                slide_key == venue_to_slide.get(v)
                for v in unique_venues if v != venue
            )

            if has_new_venue:
                # New venue starts here, stop collecting contextual slides
                break

            # This slide belongs to the current venue as contextual
            contextual_slides.append(slide_num)
            all_slides.append(slide_num)
            if slide_num in slide_lookup:
                ocr_content[f"slide_{slide_num}"] = slide_lookup[slide_num]["full_text"]

        # Build full context by combining all slide texts
        context_parts = []
        for slide_num in sorted(all_slides):
            slide_key = f"slide_{slide_num}"
            if slide_key in ocr_content:
                context_parts.append(ocr_content[slide_key])

        full_context = "\n".join(context_parts)

        # Store attribution
        venue_attribution[venue] = {
            "primary_slide": primary_slide_num,
            "contextual_slides": contextual_slides,
            "all_slides": all_slides,
            "ocr_content": ocr_content,
            "full_context": full_context
        }

        print(f"   📍 {venue}: slide {primary_slide_num} (primary) + {len(contextual_slides)} contextual slides")
        print(f"      All slides: {all_slides}")
        print(f"      Context: {len(full_context)} chars")

    return venue_attribution


def extract_places_and_context(transcript, ocr_text, caption, comments, slides_with_attribution=None):
    """
    Extract venues and context from TikTok content.

    Args:
        transcript: Audio transcript text
        ocr_text: OCR text (formatted with SLIDE markers)
        caption: TikTok caption
        comments: Comments text
        slides_with_attribution: Optional list of dicts with slide attribution data from OCR
            Format: [{"slide_number": int, "tiktok_photo_index": int, "ocr_lines": [str], "full_text": str}, ...]

    Returns:
        4-tuple: (venues, summary, venue_to_slide, venue_to_context)
            OR 5-tuple: (venues, summary, venue_to_slide, venue_to_context, venue_attribution) when slides_with_attribution provided
    """
    # Clean OCR text before processing
    original_ocr_len = len(ocr_text) if ocr_text else 0
    if ocr_text:
        ocr_text = clean_ocr_text(ocr_text)
        if original_ocr_len != len(ocr_text):
            print(f"🧹 Cleaned OCR text: {len(ocr_text)} chars (was {original_ocr_len} chars before cleaning)")
    
    # Parse OCR into slides for slide-aware extraction
    slide_dict = _parse_slide_text(ocr_text)
    is_slideshow = len(slide_dict) > 1

    # NEW: Track if we have attribution data
    print(f"🔍 DEBUG: extract_places_and_context called with slides_with_attribution={type(slides_with_attribution)}")
    if slides_with_attribution is not None:
        print(f"🔍 DEBUG: slides_with_attribution is not None, length={len(slides_with_attribution)}")
    else:
        print(f"🔍 DEBUG: slides_with_attribution is None")

    has_attribution = slides_with_attribution is not None and len(slides_with_attribution) > 0
    print(f"🔍 DEBUG: has_attribution={has_attribution}")

    if has_attribution:
        print(f"✅ Using explicit slide attribution data ({len(slides_with_attribution)} slides)")
        print(f"🔍 DEBUG: First slide attribution sample: {slides_with_attribution[0] if slides_with_attribution else 'N/A'}")
    else:
        print(f"⚠️ DEBUG: NOT using slide attribution (will use old sentence-level filtering)")

    if is_slideshow:
        print(f"📖 SLIDE-AWARE EXTRACTION: Detected {len(slide_dict)} slides")
        print(f"   Extracting places per-slide to maintain context (like reading a book)")

    # CRITICAL: Detect if OCR is too garbled to be useful
    # SKIP garble check if we detected valid SLIDE markers (slideshow is structured content)
    # IMPORTANT: If OCR is the ONLY content source (no transcript/caption), be more lenient
    # Even garbled OCR is better than nothing when it's the only source
    has_other_content = bool(transcript and len(transcript.strip()) > 50) or bool(caption and len(caption.strip()) > 20)

    # FIXED: Skip garble check for any text with SLIDE markers (not just multi-slide)
    has_slide_markers = bool(ocr_text and 'SLIDE' in ocr_text.upper())

    if ocr_text and not (is_slideshow or has_slide_markers) and _is_ocr_garbled(ocr_text):
        if has_other_content:
            print("⚠️ OCR text appears to be heavily garbled/corrupted - IGNORING IT")
            print(f"   Reason: Too many non-alphanumeric characters or random text")
            print(f"   Garbled OCR preview: {ocr_text[:200]}...")
            print(f"   Will use caption/transcript only instead")
            # If OCR is garbled AND we have other content, ignore it - it will confuse GPT
            ocr_text = ""  # Ignore garbled OCR completely
            slide_dict = {}  # Clear slide dict
        else:
            print("⚠️ OCR text appears garbled BUT it's the only content source - KEEPING IT")
            print(f"   Reason: No transcript or caption available, so we'll try to extract from OCR anyway")
            print(f"   Garbled OCR preview: {ocr_text[:200]}...")
            # Keep OCR even if garbled - better than nothing when it's the only source
            # GPT might still be able to extract some venue names from garbled OCR
    
    # Log what we have for debugging
    print(f"📋 Content sources:")
    print(f"   - Caption: {len(caption)} chars - {caption[:100] if caption else 'None'}...")
    print(f"   - Transcript: {len(transcript)} chars - {transcript[:100] if transcript else 'None'}...")
    print(f"   - OCR: {len(ocr_text)} chars - {ocr_text[:100] if ocr_text else 'None'}...")
    print(f"   - Comments: {len(comments)} chars - {comments[:100] if comments else 'None'}...")
    
    # NEW: Organize slides by venue BEFORE extraction
    organized_context = ""
    if is_slideshow:
        place_to_slides_map = organize_slides_by_venue(ocr_text)
        if place_to_slides_map:
            # Format as book-style organized text
            organized_parts = []
            for place_name, slide_texts in place_to_slides_map.items():
                organized_parts.append(f"\n{place_name}:")
                for slide_text in slide_texts:
                    organized_parts.append(f"  - {slide_text}")
            organized_context = "\n".join(organized_parts)
            print(f"\n📖 Organized context ready ({len(organized_context)} chars)")
            print(f"   Using organized format for GPT extraction")

    # If we have slides, extract per-slide to maintain context
    if is_slideshow and not organized_context:
        print(f"\n🔄 Extracting places per-slide (slide-aware mode)...")
        all_venues_per_slide = {}
        overall_summary = ""
        
        # Analyze each slide independently
        # CRITICAL: Sort slides by numeric slide number (not lexicographically)
        slides_sorted = _sort_slides_by_number(slide_dict)
        for slide_key, slide_text in slides_sorted:
            print(f"\n  📄 Analyzing {slide_key}...")
            
            # For each slide, create a targeted extraction prompt
            slide_content = f"{slide_text}\nCaption: {caption if caption else '(no caption)'}"
            
            if not slide_text or len(slide_text.strip()) < 5:
                print(f"     ⚠️ Slide has no text content")
                continue
            
            try:
                slide_prompt = f"""
You are analyzing SLIDE from a TikTok photo slideshow about NYC venues.
Extract venue names ONLY from THIS SPECIFIC SLIDE's content.
Do NOT use context from other slides - only what you see here.

IMPORTANT: Extract venue names that appear in THIS slide's text ONLY.
CRITICAL: Do NOT extract venues mentioned as "team behind", "created by", "made by", or "founded by".
Only extract venues that are actually being featured/reviewed/visited in this slide.

HANDLING GARBLED OCR: The slide text may be garbled due to OCR errors. Try your best to extract venue names even from partially garbled text. Look for:
- Partial venue names that might be readable (e.g., "Katana" even if followed by garbled text)
- Common NYC venue patterns (e.g., "Bar", "Restaurant", "Lounge" followed by a name)
- Text that looks like a venue name even if some characters are wrong
- Use the caption context to help identify venue names if the OCR is unclear

Slide content (may be garbled):
{slide_text}

Caption context: {caption if caption else '(none)'}

CRITICAL: Extract ALL venue names and details from this slide. Do NOT stop after finding 1-2 items. 
Read EVERY WORD in the OCR text, including smaller font text, fine print, and all details.

Output format: One venue name per line, or empty if none found.
VenueName1
VenueName2
...

If no venues found, output: (none)
"""
                
                # Check if OpenAI API key is set before attempting extraction
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    raise ValueError("OPENAI_API_KEY environment variable is not set. Cannot extract venues without OpenAI API access.")
                
                client = get_openai_client()
                
                try:
                    response = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": slide_prompt}],
                        temperature=0.2,  # Very low temperature for consistent extraction
                        timeout=30  # Add timeout to prevent hanging
                    )
                    slide_response = response.choices[0].message.content.strip()
                except Exception as api_error:
                    print(f"     ❌ OpenAI API call failed for slide: {api_error}")
                    print(f"     Error type: {type(api_error).__name__}")
                    raise  # Re-raise to be caught by outer exception handler
                
                # Parse venues from this slide
                slide_venues = []
                for line in slide_response.split('\n'):
                    line = line.strip()
                    if not line or line.lower() == '(none)':
                        continue
                    # Remove bullets/numbers
                    line = re.sub(r"^[\d\-\•\.\s]+", "", line).strip()
                    if 2 < len(line) < 60 and not re.search(r"^<.*>$", line):
                        slide_venues.append(line)
                
                if slide_venues:
                    print(f"     ✅ Found {len(slide_venues)} venue(s): {slide_venues}")
                    all_venues_per_slide[slide_key] = slide_venues
                else:
                    print(f"     ⚠️ No venues found in this slide")
            
            except Exception as e:
                print(f"     ❌ Slide extraction failed: {e}")
                continue
        
        # Build context for each venue (slide content + following contextual slides)
        # CRITICAL: Make context venue-specific to prevent bleeding between venues
        # If a venue is on slide 2, and slide 3 has no venue, slide 3's content belongs to slide 2's venue
        # BUT: Only include slides that actually mention this specific venue
        # CRITICAL: Sort slides by numeric slide number (not lexicographically) to preserve order
        print(f"\n📖 Building context for each venue (reading like a book, venue-specific)...")
        venue_to_context = {}
        slides_sorted = _sort_slides_by_number(slide_dict)
        
        # First, collect all venue names for reference (before building context)
        all_venue_names = []
        for venues in all_venues_per_slide.values():
            all_venue_names.extend(venues)
        all_venue_names_lower = [v.lower() for v in all_venue_names]

        # Sort venues by their slide number (numeric, not lexicographic)
        def get_slide_number_from_key(slide_key):
            """Extract numeric slide number from key like 'slide_2' -> 2"""
            try:
                return int(slide_key.split('_')[1])
            except (IndexError, ValueError):
                return 999  # Put invalid slides at the end
        
        # Sort venues_per_slide by slide number to process in correct order
        venues_per_slide_sorted = sorted(all_venues_per_slide.items(), key=lambda x: get_slide_number_from_key(x[0]))
        
        for slide_key, venues in venues_per_slide_sorted:
            # Get index of this slide in the sorted slides list
            slide_idx = next(i for i, (k, _) in enumerate(slides_sorted) if k == slide_key)

            for venue in venues:
                venue_lower = venue.lower()
                venue_words = set(venue_lower.split())
                
                # Start with current slide content, but filter to venue-specific parts
                current_slide_text = slide_dict[slide_key]
                
                # If multiple venues on same slide, extract only sentences mentioning THIS venue
                if len(venues) > 1:
                    sentences = re.split(r'[.!?]\s+', current_slide_text)
                    venue_specific_sentences = []
                    for sentence in sentences:
                        sentence_lower = sentence.lower()
                        # Check if sentence mentions this venue (use word boundaries to avoid substring matches)
                        if re.search(r'\b' + re.escape(venue_lower) + r'\b', sentence_lower):
                            venue_specific_sentences.append(sentence)
                        elif len(venue_words) > 1:
                            # For multi-word names, require ALL words to be present (strict matching)
                            words_found = sum(1 for word in venue_words if re.search(r'\b' + re.escape(word) + r'\b', sentence_lower))
                            if words_found >= len(venue_words):  # All words must be present
                                venue_specific_sentences.append(sentence)
                    
                    if venue_specific_sentences:
                        context_parts = [". ".join(venue_specific_sentences)]
                    else:
                        # FIXED: Don't use full slide when multiple venues - too risky for bleeding
                        # Instead, use empty context and rely on venue name only
                        print(f"   ⚠️ {venue}: No specific sentences found on shared slide, using venue name only")
                        context_parts = []
                else:
                    # Only one venue on this slide - use full slide content
                    context_parts = [current_slide_text]

                # Collect following slides until next venue appears
                # BUT: Only include slides that mention this venue and don't mention other venues
                for j in range(slide_idx + 1, len(slides_sorted)):
                    next_key, next_text = slides_sorted[j]
                    
                    # If next slide has venues, stop here
                    if next_key in all_venues_per_slide:
                        break
                    
                    # Check if this contextual slide mentions the current venue (word boundary matching)
                    next_text_lower = next_text.lower()
                    mentions_venue = (
                        re.search(r'\b' + re.escape(venue_lower) + r'\b', next_text_lower) or
                        (len(venue_words) > 1 and sum(1 for word in venue_words if re.search(r'\b' + re.escape(word) + r'\b', next_text_lower)) >= len(venue_words))
                    )

                    # Check if it mentions other venues (word boundary matching to avoid false positives)
                    mentions_other_venue = any(
                        len(v_lower) > 3 and re.search(r'\b' + re.escape(v_lower) + r'\b', next_text_lower)
                        for v_lower in all_venue_names_lower if v_lower != venue_lower
                    )
                    
                    # Only include if it mentions the venue and doesn't mention other venues
                    # FIXED: Removed permissive "short slide fallback" that caused context bleeding
                    # Now we ONLY include slides that explicitly mention this venue
                    if mentions_venue and not mentions_other_venue:
                        context_parts.append(next_text)
                    else:
                        # Stop if slide doesn't mention this venue or mentions other venues
                        break

                # Combine all context for this venue
                full_context = "\n".join(context_parts)
                venue_to_context[venue] = full_context

                # DEBUG: Show which slides were included for this venue
                slides_included = [slide_key] + [slides_sorted[slide_idx + i + 1][0] for i in range(len(context_parts) - 1) if slide_idx + i + 1 < len(slides_sorted)]
                print(f"   📝 {venue}: {len(full_context)} chars of context from {len(context_parts)} slide(s)")
                print(f"      Slides: {', '.join(slides_included)}")

        # Combine venues from all slides (preserving slide info for enrichment)
        all_venues_with_slides = []
        for slide_key, venues in all_venues_per_slide.items():
            for venue in venues:
                all_venues_with_slides.append({
                    "name": venue,
                    "source_slide": slide_key
                })

        # Create overall summary from caption
        if caption and len(caption) > 10:
            overall_summary = caption[:100] if len(caption) <= 100 else caption[:97] + "..."
        else:
            overall_summary = f"Photo Slideshow ({len(all_venues_per_slide)} slides)"

        # Deduplicate venues by name (keeping first occurrence and slide info)
        # IMPORTANT: Preserve slide order - venues should appear in order of slides
        unique_venues = []
        seen = set()
        venue_to_slide = {}
        venue_to_slide_order = {}  # Track slide order for sorting
        
        # Extract slide number from slide_key (e.g., "slide_3" -> 3)
        def get_slide_number(slide_key):
            try:
                return int(slide_key.split('_')[1])
            except (IndexError, ValueError):
                return 999  # Put unknown slides at the end
        
        for v_dict in all_venues_with_slides:
            v_lower = v_dict["name"].lower().strip()
            if v_lower not in seen and len(v_lower) >= 3:
                seen.add(v_lower)
                unique_venues.append(v_dict["name"])  # Return just names for compatibility
                venue_to_slide[v_dict["name"]] = v_dict["source_slide"]
                # Store slide order for sorting
                venue_to_slide_order[v_dict["name"]] = get_slide_number(v_dict["source_slide"])

        # Sort venues by slide order (earlier slides first)
        unique_venues.sort(key=lambda v: venue_to_slide_order.get(v, 999))

        print(f"\n📖 Slide-aware extraction complete:")
        print(f"   Total unique venues: {len(unique_venues)} (ordered by slide appearance)")
        print(f"   Summary: {overall_summary}")

        # CRITICAL: Validate venue context isolation (final safety check for bleeding)
        print(f"\n🔍 Validating venue context isolation...")
        venue_to_context = _validate_venue_context_isolation(venue_to_context, all_venue_names)

        # NEW: Build venue attribution if we have explicit slide attribution data
        venue_attribution = None
        if has_attribution:
            print(f"\n🔗 Building explicit venue attribution...")
            print(f"🔍 DEBUG: unique_venues count: {len(unique_venues)}")
            print(f"🔍 DEBUG: venue_to_slide: {venue_to_slide}")
            print(f"🔍 DEBUG: slides_with_attribution count: {len(slides_with_attribution)}")
            print(f"🔍 DEBUG: all_venues_per_slide: {all_venues_per_slide}")
            venue_attribution = _build_venue_attribution(
                unique_venues,
                venue_to_slide,
                slides_with_attribution,
                all_venues_per_slide
            )
            print(f"🔍 DEBUG: _build_venue_attribution returned: {type(venue_attribution)}, len={len(venue_attribution) if venue_attribution else 0}")
            print(f"🔍 DEBUG: venue_attribution is None: {venue_attribution is None}")
            print(f"🔍 DEBUG: venue_attribution is truthy: {bool(venue_attribution)}")
            if venue_attribution:
                print(f"🔍 DEBUG: First venue in attribution: {list(venue_attribution.keys())[:1] if venue_attribution else []}")

        # Return tuple: (venue_names, summary, venue_to_slide_mapping, venue_to_context_mapping)
        # CRITICAL FIX: Always return 5-tuple if attribution was requested, even if empty
        if has_attribution:
            if not venue_attribution:
                print(f"⚠️ WARNING: venue_attribution is empty/None despite has_attribution=True!")
            return unique_venues, overall_summary, venue_to_slide, venue_to_context, venue_attribution
        else:
            return unique_venues, overall_summary, venue_to_slide, venue_to_context
    
    # NEW: Organized slideshow extraction using book-style format
    elif organized_context:
        print(f"\n📚 Using organized slideshow extraction...")

        # Send organized context to GPT for extraction
        organized_prompt = f"""You are analyzing a TikTok photo slideshow where slides have been organized by venue.

Each venue section shows:
- The venue name
- All slides mentioning that venue (with page numbers)
- Context like descriptions, menu items, recommendations

Extract:
1. All venue names
2. For each venue, the "what to get" items mentioned in its slides

Organized slideshow content:
{organized_context}

CRITICAL: Extract ALL venue names and details. Read EVERY WORD in the OCR text, including smaller font text and fine print.
Do NOT stop after finding 1-2 venues - extract EVERYTHING mentioned.

Caption: {caption if caption else '(none)'}

Output format (one venue per line with optional dish):
VenueName1|dish item
VenueName2|another dish
VenueName3|
...

If no dish mentioned for a venue, leave it blank after the |."""

        try:
            client = get_openai_client()
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": organized_prompt}],
                temperature=0.2,
                timeout=30
            )
            result = response.choices[0].message.content.strip()

            # Parse venues and dishes
            venues = []
            venue_to_context = {}
            for line in result.split('\n'):
                line = line.strip()
                if not line or '|' not in line:
                    continue
                parts = line.split('|', 1)
                venue_name = parts[0].strip()
                dish = parts[1].strip() if len(parts) > 1 else ""

                if 2 < len(venue_name) < 60:
                    venues.append(venue_name)
                    # Store dish info in context
                    if dish:
                        venue_to_context[venue_name] = f"What to get: {dish}"

            summary = caption[:100] if caption else "Organized Photo Slideshow"
            print(f"\n📚 Organized extraction complete: {len(venues)} venues")

            return venues, summary, {}, venue_to_context

        except Exception as e:
            print(f"❌ Organized extraction failed: {e}")
            # Fall through to non-slideshow extraction
    
    # Non-slideshow extraction (fallback to combined text)
    combined_text = "\n".join(x for x in [ocr_text, transcript, caption, comments] if x)
    
    # Emphasize OCR text if it's available (especially when there's no transcript)
    ocr_emphasis = ""
    if ocr_text and len(ocr_text) > 20:
        if not transcript or len(transcript) < 20:
            # OCR is the PRIMARY source - emphasize heavily (especially for photo posts)
            ocr_emphasis = f"""
   ⚠️⚠️⚠️ CRITICAL: This is a PHOTO POST or video with NO SPEECH. The OCR text below contains ALL venue information.
   ⚠️⚠️⚠️ The OCR text IS THE PRIMARY SOURCE - extract venue names directly from it.
   ⚠️⚠️⚠️ OCR TEXT FROM IMAGES (extract ALL venue names from this - NO TRUNCATION):
{ocr_text}
   
   • Extract EVERY venue name you see in the OCR text above - read EVERY WORD
   • Photo posts often show venue names in the images themselves - look carefully
   • Do NOT stop after finding 1-2 venues - extract ALL venues mentioned
   • Pay attention to smaller font text, fine print, and all details - extract EVERYTHING
   • CRITICAL: Venue names often appear at the START of slides in these formats:
     Format A: "VenueName\nNeighborhood\nRating\nDescription" → Extract "VenueName"
       Example: "↑ Supper\nEast Village\n9/10\nYou get this room..." → Extract "Supper" (ignore ↑)
       Example: "9 Bar Veloce\nNolita\n10/10\nIt's free..." → Extract "9 Bar Veloce"
       Example: "12 Chairs\nSoho\n9/10\nMore of a lowkey..." → Extract "12 Chairs"
       Example: "Solas\nEast Village\n5/10\nThis is a rite..." → Extract "Solas"
     Format B: "VenueName (description)" → Extract "VenueName"
       Example: "Club Room (It's a classy spot...)" → Extract "Club Room"
   • CRITICAL: If a slide starts with text followed by neighborhood/rating, that FIRST LINE is the venue name
   • CRITICAL: Ignore symbols (↑, *, etc.), ratings (9/10, 10/10), and neighborhood names - extract the ACTUAL VENUE NAME
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
   • IMPORTANT: If caption only has generic hashtags (#nycfood, #nyceats) but no specific venue names,
     AND OCR was garbled/ignored, then the venue names are in the images but OCR failed.
     In this case, return empty venues list with a summary like "NYC Food Recommendations" or similar.
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
   • IMPORTANT: If OCR text is garbled (random characters like "wee ce ERR oe"), IGNORE IT and focus ONLY on the caption
   • Look for venue names in hashtags (#VenueName) - extract those too
"""
    
    prompt = f"""
You are analyzing a TikTok video about NYC venues. Extract venue names from ANY available source.

1️⃣ Extract every **specific** bar, restaurant, café, or food/drink venue mentioned.
{caption_emphasis}
   • CRITICAL PRIORITY: Check the OCR text FIRST - photo posts show venue names IN THE IMAGES
   • CRITICAL: Venue names appear in MULTIPLE formats - extract them ALL:
     Format 1: "VenueName (description)" → Extract "VenueName" (name is BEFORE parenthesis)
       Example: "Club Room (It's a classy spot...)" → Extract "Club Room"
       Example: "Paul's Baby Grand (This is one of...)" → Extract "Paul's Baby Grand"
     Format 2: "VenueName\nNeighborhood\nRating\nDescription" → Extract "VenueName" (name is FIRST LINE)
       Example: "↑ Supper\nEast Village\n9/10\nYou get this room..." → Extract "Supper" (ignore arrow symbols)
       Example: "9 Bar Veloce\nNolita\n10/10\nIt's free to rent..." → Extract "9 Bar Veloce"
       Example: "12 Chairs\nSoho\n9/10\nMore of a lowkey vibe..." → Extract "12 Chairs"
       Example: "Solas\nEast Village\n5/10\nThis is a rite..." → Extract "Solas"
     CRITICAL: If a slide starts with a venue name followed by neighborhood/rating/description, extract the FIRST LINE as the venue name
     CRITICAL: Ignore symbols like arrows (↑), numbers (9/10, 10/10), and neighborhood names - extract the ACTUAL VENUE NAME
     CRITICAL: Check EVERY slide - each slide may have a venue name at the start
   • CRITICAL: Check ALL slides including the LAST slide - venue names are often on the final slide
     Example: "Elvis Noho" might only appear on the last slide - make sure to check SLIDE N (where N is the last slide number)
     If you see "SLIDE 1:", "SLIDE 2:", "SLIDE 3:", make sure to check "SLIDE 3:" for venue names
   • IMPORTANT: Check the CAPTION/DESCRIPTION - venue names are often listed there
   • Also check speech (transcript) and comments - LISTEN CAREFULLY to what the creator actually says
   • IMPORTANT: If transcript says "high life in east village", extract "High Life" as the venue name, NOT "high life dispo"
   • IMPORTANT: If transcript says "go to employees only", extract "Employees Only" as the venue name
   • Pay attention to phrases like "go to X", "check out X", "visit X", "at X", "try X", "hit up X" - X is the venue name
   • Pay attention to context - if creator says "X in Y neighborhood", X is the venue name, Y is the location
   • Venue names can be phrases like "Employees Only", "Death & Co", "Katana Kitten", "Wealth Over Now" - extract them exactly as spoken
   • IMPORTANT: Listen to what the creator ACTUALLY says. If transcript has errors (e.g., "wealth over now" when creator said "employees only"), 
     prioritize the actual spoken words over transcription errors. Cross-reference with caption and OCR if available.
   • If multiple venues are mentioned, extract ALL of them separately (e.g., "Employees Only" and "Katana Kitten" are two different venues)
   • Do NOT extract random phrases that don't match venue names - verify venue names make sense as actual restaurant/bar names
   • Look for venue names even if they appear in lists, numbered lists, hashtags, or casual mentions
   • If OCR shows a numbered list (1. Venue Name, 2. Another Venue), extract ALL venue names from that list
   • If OCR shows venue names separated by commas, newlines, bullets, or semicolons, extract ALL of them
   • Venue names might be in hashtags (#VenueName) - extract those too
   • If caption says "My favorite NYC spots" but doesn't list names, the names are IN THE OCR TEXT FROM IMAGES
   • Photo posts: The images contain the venue names - extract them from OCR text
   • CRITICAL: Read EVERY WORD in the OCR text - do NOT stop after finding 1-2 venues
   • Extract ALL venue names, including those in smaller font, fine print, or less prominent positions
   • Be thorough - if there are multiple venues mentioned, extract ALL of them, not just the first few
   • Ignore broad neighborhoods like "SoHo" or "Brooklyn" unless they're part of a venue name
   • CRITICAL: Do NOT invent or infer venue names. Only extract venues that are EXPLICITLY mentioned by name.
     For example, if text says "wine bar in Soho" but doesn't name the wine bar, do NOT extract "Soho Wine Bar".
     Only extract venues that are actually named (e.g., "Marjories", "Employees Only", "Katana Kitten", "Blank Street").
   • IMPORTANT: OCR text may contain garbled venue names. If you see text that looks like it could be a garbled venue name (e.g., "CTU REN" might be "CACTUS WREN", "GLASVIN" might be garbled), AND there's strong context suggesting a venue name (like "cactus shaped corn bread" suggesting "Cactus Wren"), try to extract the most likely venue name. However, ONLY do this if there's clear context - don't extract random garbled text as venues.
   • IMPORTANT: Venue names can be single words or multiple words. Examples: "Blank Street" (two words), "The Elk" (two words), "caffe paradiso" (two words). Extract them exactly as written, preserving capitalization and spacing.
   • If a venue name appears on a slide with its menu items, extract the venue name even if it's brief. Example: If slide says "Blank Street" followed by drink items, extract "Blank Street" as a venue.
   • ONLY list actual venue names that are mentioned. Do NOT use placeholders like "venue 1" or "<venue 1>".
   • IMPORTANT: OCR text may contain garbled characters or errors. Look for REAL venue names, not random words.
   • IMPORTANT: OCR may garble venue names (e.g., "CTU REN" might be "CACTUS WREN", "GLASVIN" might be garbled). 
     If you see garbled text that COULD be a venue name AND there's strong context (e.g., "cactus shaped corn bread" suggests "Cactus Wren"), 
     try to extract the most likely venue name. However, ONLY do this if there's clear context - don't extract random garbled text.
   • IMPORTANT: Transcript may have transcription errors. If transcript says "X dispo" but context suggests "X in Y", trust the context and extract "X"
   • If OCR text is mostly garbled (lots of special characters, random letters), rely MORE on the caption and transcript.
   • Only extract venue names that look like REAL restaurant/bar/café names (e.g., "Joe's Pizza", "Lombardi's").
   • Do NOT extract random words from garbled OCR text (e.g., "Danny's" or "Ballerina" if they don't appear in context).
   • CRITICAL: Do NOT extract single words or short phrases that appear randomly in slides unless they're clearly venue names.
     Examples of things to EXCLUDE: "KWORK", "Fidelity", "DIPWAY ARCH" (unless they're clearly mentioned as venues with context like "restaurant" or "bar").
     Only extract names that appear with clear context indicating they're restaurants/bars/cafes (e.g., "la tete d'or by Daniel").
   • CRITICAL: If you see garbled text like "CTU REN" near context about "cactus" items, it might be "CACTUS WREN" - extract it.
   • IMPORTANT: If a venue name appears multiple times in garbled form (e.g., "LA TETE DOR" appears 3 times), extract it ONCE as the most likely correct version (e.g., "la tete d'or").
   • If OCR text is too garbled or unclear, prioritize the caption and transcript for venue names.
   • Do NOT combine neighborhood names with generic terms to create venue names (e.g., don't extract "Soho Wine Bar" from "wine bar in Soho").
   • CRITICAL: Do NOT extract venues that are mentioned as "team behind", "created by", "made by", "founded by", or similar contexts. 
     For example, if text says "the team behind Sami & Susu made Shifka", extract ONLY "Shifka", NOT "Sami & Susu".
     Only extract venues that are actually being featured/reviewed/visited, not venues mentioned as creators or previous projects.
   • CRITICAL: Do NOT extract chain locations with addresses/neighborhoods in parentheses or as suffixes.
     Examples to EXCLUDE: "WatchHouse 5th Ave", "HEYTEA (Times Square)", "Starbucks Times Square", "Chipotle Broadway"
     These are chain locations, not specific venues. Only extract the base venue name if it's mentioned WITHOUT a location suffix.
     If you see "WatchHouse" mentioned alone, extract it. But if you see "WatchHouse 5th Ave" or "WatchHouse (5th Ave)", do NOT extract it.
     Same for "HEYTEA" alone vs "HEYTEA (Times Square)" - only extract if mentioned without the location.
   • CRITICAL: Extract venues with special characters and accents (e.g., "TÁN", "Café", "José"). 
     Do NOT skip venues just because they have accents or special characters. Extract them exactly as written.
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
            return [], "TikTok Venues", {}
        
        # Use ALL OCR content - no truncation to capture every detail including smaller font text
        # GPT-4o-mini supports up to 128k tokens, so we can send much more content
        content_to_analyze = combined_text  # No truncation - extract ALL text
        
        # If OCR is the main source (no speech), emphasize it heavily
        if ocr_text and (not transcript or len(transcript) < 50):
            print(f"📝 Analyzing content - OCR PRIMARY SOURCE ({len(ocr_text)} chars OCR, {len(content_to_analyze)} total)")
            print(f"📝 OCR text being analyzed:\n{ocr_text[:500]}...")
        else:
            print(f"📝 Analyzing content ({len(content_to_analyze)} chars): {content_to_analyze[:300]}...")
        
        # Check if OpenAI API key is set before attempting extraction
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set. Cannot extract venues without OpenAI API access.")
        
        client = get_openai_client()
        
        # Log the content length being sent to GPT
        content_length = len(content_to_analyze)
        print(f"📤 Sending {content_length} chars to GPT for venue extraction...")
        
        try:
            response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt + "\n\nContent to analyze:\n" + content_to_analyze}],
            temperature=0.3,  # Lower temperature for more consistent extraction from OCR
                timeout=30  # Add timeout to prevent hanging
            )
            raw = response.choices[0].message.content.strip()
            print(f"🤖 GPT raw response: {raw[:500]}...")
        except Exception as api_error:
            print(f"❌ OpenAI API call failed: {api_error}")
            print(f"   Error type: {type(api_error).__name__}")
            import traceback
            print(f"   Full traceback:")
            print(traceback.format_exc())
            raise  # Re-raise to be caught by outer exception handler

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
            # Filter out venues that don't look like real venue names
            # Only filter out very obvious OCR garbage - be lenient with short names
            # Many legitimate venues have short names (e.g., "Rao's", "Joe's", "Luke's", "Pat's", "L'Artusi", etc.)
            
            # Only filter out single words that are VERY short (<= 3 chars) AND look like OCR garbage
            # AND don't have common venue patterns (apostrophes, known venues, etc.)
            if len(v.split()) == 1 and len(v) <= 3:
                # Very short single words like "KW", "RA", "AM" are likely OCR garbage
                # But allow if they have apostrophes or are known venues
                known_famous_venues = ["rao's", "raos", "joes", "joe's", "lukes", "luke's", "pats", "pat's", "l'artusi", "lartusi"]
                has_apostrophe = "'" in v or "'" in v  # Allow names with apostrophes
                is_all_caps_garbage = v.isupper() and len(v) <= 3  # Very short all-caps like "KW", "RA"
                
                # Only filter if it's very short, all caps, AND doesn't have venue patterns
                if is_all_caps_garbage and not has_apostrophe and v_lower not in known_famous_venues:
                    print(f"⚠️ Skipping very short all-caps word (likely OCR garbage): {v}")
                    continue
            
            # Exclude all-caps single words that are likely OCR errors (unless they're acronyms or have context)
            # Only filter if VERY short (<= 4 chars) and all caps
            if v.isupper() and len(v.split()) == 1 and len(v) <= 4:
                # Check if it's a known acronym or if it appears with context
                known_acronyms = ['NYC', 'LES', 'UWS', 'UES', 'LIC', 'DUMBO', 'NOLITA', 'NOHO']
                if v not in known_acronyms:
                    print(f"⚠️ Skipping very short all-caps word (likely OCR error): {v}")
                continue
            seen.add(v_lower)
            unique.append(v)

        # CRITICAL: Filter out chain locations with addresses/neighborhoods
        # Pattern: "VenueName (Location)" or "VenueName Location" where Location is a neighborhood/address
        nyc_neighborhoods = [
            "Times Square", "5th Ave", "5th Avenue", "Broadway", "Madison Ave", "Park Ave",
            "SoHo", "Soho", "Nolita", "NoHo", "TriBeCa", "Tribeca", "West Village", "East Village",
            "Upper East Side", "UES", "Upper West Side", "UWS", "Lower East Side", "LES",
            "Chinatown", "Little Italy", "Greenwich Village", "Chelsea", "Flatiron", "Gramercy",
            "Midtown", "Midtown West", "Midtown East", "Hell's Kitchen", "Koreatown", "KTown",
            "Brooklyn", "Queens", "Manhattan", "Bronx", "Staten Island"
        ]
        filtered_unique = []
        ocr_text_lower = ocr_text.lower() if ocr_text else ""
        caption_lower = caption.lower() if caption else ""
        combined_text_lower = f"{ocr_text_lower} {caption_lower}".lower()
        
        for v in unique:
            v_lower = v.lower()
            # Check if venue name ends with a known neighborhood/address
            is_chain_location = False
            for neighborhood in nyc_neighborhoods:
                neighborhood_lower = neighborhood.lower()
                # Check patterns: "VenueName (Location)" or "VenueName Location"
                if f"({neighborhood_lower})" in v_lower or v_lower.endswith(f" {neighborhood_lower}"):
                    is_chain_location = True
                    print(f"⚠️ Filtering out chain location: '{v}' (contains location '{neighborhood}')")
                    break
            
            # CRITICAL: Verify venue is actually mentioned in OCR/caption (filter false positives)
            if not is_chain_location:
                # Check if venue name (or key parts) appears in OCR/caption
                venue_words = v_lower.split()
                # For multi-word venues, check if at least 2 words appear, or if single word appears
                if len(venue_words) > 1:
                    # Multi-word: check if at least 2 words appear together or separately
                    words_found = sum(1 for word in venue_words if len(word) > 2 and word in combined_text_lower)
                    if words_found < min(2, len(venue_words) - 1):
                        print(f"⚠️ Filtering out venue not mentioned in OCR/caption: '{v}' (only {words_found}/{len(venue_words)} words found)")
                        continue
                else:
                    # Single word: must appear in text (but allow for OCR variations)
                    if len(v_lower) > 3 and v_lower not in combined_text_lower:
                        # Try normalized version (remove accents, special chars)
                        import unicodedata
                        normalized_v = unicodedata.normalize('NFD', v_lower).encode('ascii', 'ignore').decode('ascii')
                        normalized_text = unicodedata.normalize('NFD', combined_text_lower).encode('ascii', 'ignore').decode('ascii')
                        if normalized_v not in normalized_text:
                            print(f"⚠️ Filtering out venue not mentioned in OCR/caption: '{v}'")
                            continue
                
                filtered_unique.append(v)
        
        unique = filtered_unique
        print(f"🧠 Parsed {len(unique)} venues: {unique}")
        print(f"🧠 Parsed summary: {summary}")
        return unique, summary, {}, {}  # Empty venue_to_slide and venue_to_context for non-slideshow videos
    except ValueError as e:
        # OpenAI API key missing or client initialization failed
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ GPT extraction failed - API key issue: {e}")
        print(f"📋 Full traceback:\n{error_trace}")
        print(f"⚠️ OPENAI_API_KEY check: {os.getenv('OPENAI_API_KEY')[:10] if os.getenv('OPENAI_API_KEY') else 'NOT SET'}...")
        return [], "TikTok Venues", {}, {}
    except Exception as e:
        # Any other error (network, API error, timeout, etc.)
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ GPT extraction failed: {e}")
        print(f"📋 Full traceback:\n{error_trace}")
        print(f"⚠️ Error type: {type(e).__name__}")
        # Check if OpenAI API key is set
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print(f"⚠️ CRITICAL: OPENAI_API_KEY environment variable is NOT SET!")
        else:
            print(f"✅ OPENAI_API_KEY is set (first 10 chars: {api_key[:10]}...)")
        return [], "TikTok Venues", {}, {}

# ─────────────────────────────
# GPT: Enrichment + Vibe Tags
# ─────────────────────────────
def enrich_place_intel(name, transcript, ocr_text, caption, comments, source_slide=None, slide_context=None, all_venues=None, venue_attribution=None):
    """
    Enrich place information with slide-aware context.
    If slide_context is provided, use that pre-built context (reads slides sequentially "like a book").
    Otherwise, if source_slide is provided (e.g., "slide_1"), only use context from that slide.
    If all_venues is provided, filter context to only include mentions of THIS venue, not others.
    """
    import re  # Import re at function level to avoid scope issues
    
    # Helper function to clean slide markers from text
    def clean_slide_markers(text):
        """Remove 'SLIDE X:' markers from text."""
        if not text:
            return text
        # Remove "SLIDE X:" or "SLIDEX:" patterns (case-insensitive)
        # (re is imported at parent function level)
        cleaned = re.sub(r'SLIDE\s*\d+\s*:\s*', '', text, flags=re.IGNORECASE)
        return cleaned.strip()
    
    # Helper function to filter garbled sentences from text
    def filter_garbled_sentences(text):
        """Remove garbled sentences, hashtags, and OCR noise from text before sending to GPT."""
        if not text:
            return text
        # (re is imported at parent function level)
        
        # Remove hashtags
        text = re.sub(r'#\w+', '', text)
        
        # Remove Unicode garbage
        text = re.sub(r'â\x80\x99', "'", text)
        text = re.sub(r'â\x80\x9c', '"', text)
        text = re.sub(r'â\x80\x9d', '"', text)
        text = re.sub(r'â\x80\x94', '—', text)
        
        # Remove all-caps OCR garbage fragments (3+ consecutive uppercase words)
        text = re.sub(r'\b[A-Z]{3,}(?:\s+[A-Z]{3,}){2,}\b', '', text)
        
        # Remove known OCR garbage patterns
        garbage_patterns = [
            r'\bREX\s+TAREX\s+SAN\s+MAL\s+PAR\s+BIE\s+WALL\b',
            r'\bVALLAGASH\s+PETER\s+REST\s+PHO\b',
            r'\bMUURE\s+DAME\b',
            r'\bBASIL\s+PAYDER\s+AVE\s+BRAU\s+COUL\s+CENTRAL\s+UNPONT\s+ONTEN\s+IDE\s+APE\s+AVARON\b',
            r'\bQ\s+BASIL\s+PAYDER\s+AVE\s+BRAU\b',
        ]
        for pattern in garbage_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        
        sentences = re.split(r'[.!?]\s+', text)
        good_sentences = []
        for sentence in sentences:
            sentence = sentence.strip()
            # Skip very short sentences (< 10 chars) that are likely OCR errors
            if len(sentence) < 10:
                continue
            # Skip if sentence is mostly garbled
            if _is_ocr_garbled(sentence):
                continue
            # Skip sentences that are mostly hashtags or random characters
            if re.match(r'^[#\s]+$', sentence):
                continue
            good_sentences.append(sentence)
        return '. '.join(good_sentences)
    
    # Helper function to clean vibe text
    def normalize_brand_names(text):
        """Normalize brand name mentions in extracted text.

        Replaces variations of 'belly' (the dining app) with 'Beli'.
        """
        if not text:
            return text
        # (re is imported at parent function level)

        # Replace "belly list" with "Beli" (case-insensitive)
        text = re.sub(r'\bbelly\s+list\b', 'Beli', text, flags=re.IGNORECASE)

        # Replace standalone "belly" when used in app/rating context
        # Common patterns: "on belly", "belly rating", "via belly", "using belly", etc.
        text = re.sub(r'\bon\s+belly\b', 'on Beli', text, flags=re.IGNORECASE)
        text = re.sub(r'\bbelly\s+rating\b', 'Beli rating', text, flags=re.IGNORECASE)
        text = re.sub(r'\bvia\s+belly\b', 'via Beli', text, flags=re.IGNORECASE)
        text = re.sub(r'\busing\s+belly\b', 'using Beli', text, flags=re.IGNORECASE)
        text = re.sub(r'\bcheck\s+out\s+belly\b', 'check out Beli', text, flags=re.IGNORECASE)
        text = re.sub(r'\brank.*\s+on\s+belly\b', lambda m: m.group(0).replace('belly', 'Beli').replace('Belly', 'Beli'), text, flags=re.IGNORECASE)

        return text

    def extract_cuisine_from_google_types(place_types):
        """
        Extract cuisine type from Google Maps place types.
        Returns a single cuisine category based on Google Maps types field.

        Args:
            place_types: List of Google Maps types (e.g., ['italian_restaurant', 'restaurant', 'food'])

        Returns:
            String cuisine category (e.g., 'Italian', 'Japanese') or None
        """
        if not place_types or not isinstance(place_types, list):
            return None

        # Google Maps type to cuisine category mapping
        # Based on official Google Places API types
        cuisine_mapping = {
            # Asian cuisines
            'chinese_restaurant': 'Chinese',
            'japanese_restaurant': 'Japanese',
            'korean_restaurant': 'Korean',
            'thai_restaurant': 'Thai',
            'vietnamese_restaurant': 'Vietnamese',
            'indian_restaurant': 'Indian',
            'asian_restaurant': 'Asian',

            # European cuisines
            'italian_restaurant': 'Italian',
            'french_restaurant': 'French',
            'greek_restaurant': 'Greek',
            'spanish_restaurant': 'Spanish',
            'german_restaurant': 'German',

            # Mediterranean & Middle Eastern
            'mediterranean_restaurant': 'Mediterranean',
            'middle_eastern_restaurant': 'Middle Eastern',
            'turkish_restaurant': 'Turkish',
            'lebanese_restaurant': 'Lebanese',

            # Americas
            'mexican_restaurant': 'Mexican',
            'american_restaurant': 'American',
            'brazilian_restaurant': 'Brazilian',
            'latin_american_restaurant': 'Latin American',

            # Specific food types
            'pizza_restaurant': 'Pizza',
            'sushi_restaurant': 'Sushi',
            'ramen_restaurant': 'Ramen',
            'steak_house': 'Steakhouse',
            'seafood_restaurant': 'Seafood',
            'hamburger_restaurant': 'Burger',
            'sandwich_shop': 'Sandwiches',
        }

        # Check each type in order of priority
        for place_type in place_types:
            if place_type in cuisine_mapping:
                cuisine = cuisine_mapping[place_type]
                print(f"   🍽️ Found cuisine from Google Maps: {cuisine} (type: {place_type})")
                return cuisine

        return None

    def clean_vibe_text(vibe_text, venue_name):
        """Remove venue name, hashtags, garbled text, and 'the vibes:' prefixes from vibe text."""
        if not vibe_text:
            return vibe_text
        # (re is imported at parent function level)
        
        # Remove hashtags
        cleaned = re.sub(r'#\w+', '', vibe_text)
        
        # Remove venue name (case-insensitive)
        cleaned = re.sub(re.escape(venue_name), '', cleaned, flags=re.IGNORECASE)
        
        # Remove "the vibes:" prefix
        cleaned = re.sub(r'the\s+vibes?\s*:\s*', '', cleaned, flags=re.IGNORECASE)
        
        # Remove garbled OCR patterns (all caps fragments, random letters)
        # Pattern: 3+ consecutive uppercase letters/words that look like OCR garbage
        cleaned = re.sub(r'\b[A-Z]{3,}(?:\s+[A-Z]{3,}){2,}\b', '', cleaned)
        
        # Remove Unicode garbage like "NYCâ's" -> "NYC's"
        cleaned = re.sub(r'â\x80\x99', "'", cleaned)  # Fix curly apostrophe
        cleaned = re.sub(r'â\x80\x9c', '"', cleaned)  # Fix curly quotes
        cleaned = re.sub(r'â\x80\x9d', '"', cleaned)
        cleaned = re.sub(r'â\x80\x94', '—', cleaned)  # Fix em dash
        
        # Remove random OCR fragments (words that are all caps and don't make sense)
        words = cleaned.split()
        good_words = []
        garbage_words = {'REX', 'TAREX', 'SAN', 'MAL', 'PAR', 'BIE', 'WALL', 'H', 'F', 'Q', 'BASIL', 'PAYDER', 'AVE', 'BRAU', 'COUL', 'CENTRAL', 'UNPONT', 'ONTEN', 'IDE', 'APE', 'AVARON', 'NO', 'VALLAGASH', 'PETER', 'REST', 'PHO', 'MUURE', 'DAME'}
        for word in words:
            word_clean = re.sub(r'[^\w\s]', '', word)  # Remove punctuation for check
            # Skip if it's known OCR garbage
            if word_clean.upper() in garbage_words:
                continue
            # Skip if it's all caps and longer than 3 chars but doesn't look like a real word
            if word_clean.isupper() and len(word_clean) > 3:
                # Check if it looks like a real acronym or proper noun
                if word_clean not in ['NYC', 'NY', 'USA']:
                    # Skip if it's not a common acronym
                    continue
            good_words.append(word)
        cleaned = ' '.join(good_words)
        
        # Remove extra whitespace
        cleaned = ' '.join(cleaned.split())
        
        # Remove leading/trailing punctuation
        cleaned = cleaned.strip('.,;:!?')
        
        return cleaned.strip()
    
    # Use pre-built slide context if available (already includes sequential reading)
    # NOTE: We still need to filter slide_context to prevent bleeding between venues on the same slide
    needs_strict_filtering = False  # Track if we need strict filtering (no lenient mode) due to context bleeding
    if slide_context:
        print(f"   📖 Enriching {name} using pre-built slide context (already filtered, {len(slide_context)} chars)")
        # Clean slide markers from context
        cleaned_slide_context = clean_slide_markers(slide_context)
        raw_context = "\n".join(x for x in [cleaned_slide_context, caption] if x)
        # FIXED: venue_to_context already contains venue-specific context built during slideshow extraction
        # CRITICAL: Ensure this context is ONLY from this venue's slide(s), not other slides
        # Additional safety check: verify context doesn't mention other venues
        context_is_already_filtered = True
        if all_venues and len(all_venues) > 1:
            other_venues_lower = [v.lower() for v in all_venues if v.lower() != name.lower()]
            slide_context_lower = slide_context.lower()
            mentions_other_venue = False
            detected_other_venue = None
            for other_venue in other_venues_lower:
                if len(other_venue) > 3:
                    # Check if other venue name appears in this venue's context (shouldn't happen)
                    if re.search(r'\b' + re.escape(other_venue) + r'\b', slide_context_lower):
                        mentions_other_venue = True
                        detected_other_venue = other_venue
                        break
            
            if mentions_other_venue:
                print(f"   ⚠️ WARNING: slide_context for {name} mentions other venue '{detected_other_venue}' - extracting ONLY from venue's specific slide(s)")
                # CRITICAL FIX: If we have venue_attribution, extract context ONLY from this venue's slides
                # Don't use slide_context which may contain other venues - use ocr_content from attribution
                if venue_attribution and name in venue_attribution:
                    attr = venue_attribution[name]
                    ocr_content = attr.get("ocr_content", {})
                    all_slides = attr.get("all_slides", [])
                    
                    # Extract context ONLY from this venue's slides
                    venue_specific_parts = []
                    for slide_num in sorted(all_slides):
                        slide_key = f"slide_{slide_num}"
                        if slide_key in ocr_content:
                            slide_text = ocr_content[slide_key]
                            # Clean slide markers
                            cleaned_slide_text = clean_slide_markers(slide_text)
                            venue_specific_parts.append(cleaned_slide_text)
                    
                    # Build context ONLY from this venue's slides
                    isolated_context = "\n".join(venue_specific_parts)
                    raw_context = "\n".join(x for x in [isolated_context, caption] if x)
                    print(f"   ✅ Extracted isolated context from {len(all_slides)} slide(s) for {name}: {len(isolated_context)} chars")
                    print(f"      Slides used: {all_slides}")
                    # Still need to filter to remove any sentences mentioning other venues
                    context_is_already_filtered = False
                    needs_strict_filtering = True
                else:
                    # No attribution data - re-filter slide_context strictly
                    print(f"   ⚠️ No venue_attribution available - re-filtering slide_context STRICTLY to prevent bleeding")
                    context_is_already_filtered = False
                    raw_context = slide_context  # Use slide_context as raw_context for filtering
                    # Mark that we need strict filtering (no lenient mode)
                    needs_strict_filtering = True
    elif source_slide:
        # Fallback: Parse slides if OCR has them
        slide_dict = _parse_slide_text(ocr_text) if ocr_text else {}
        # If we have slide info and this place came from a specific slide, use only that slide's context
        if source_slide in slide_dict:
            print(f"   🔍 Enriching {name} using context from {source_slide} only (slide-aware)")
            slide_specific_text = slide_dict[source_slide]
            # Clean slide markers
            cleaned_slide_text = clean_slide_markers(slide_specific_text)
            raw_context = "\n".join(x for x in [cleaned_slide_text, caption] if x)
            context_is_already_filtered = False
        else:
            # FIXED: Fallback to caption only (safer than full OCR with all slides)
            print(f"   ⚠️ {source_slide} not found in slide_dict, using caption only to avoid context bleeding")
            raw_context = caption if caption else ""
            context_is_already_filtered = False
    else:
        # Fallback: use full context (for non-slideshow videos or if source_slide not found)
        cleaned_ocr = clean_slide_markers(ocr_text) if ocr_text else ""
        raw_context = "\n".join(x for x in [caption, cleaned_ocr, transcript, comments] if x)
        context_is_already_filtered = False

    # CRITICAL: Filter context to only include parts relevant to THIS venue
    # This prevents mixing details from other venues mentioned in the same video
    # Skip if context is already venue-specific (from slide_context)
    if not context_is_already_filtered and all_venues and len(all_venues) > 1:
        print(f"   🎯 Filtering context for {name} (excluding {len(all_venues)-1} other venues)")
        # Split context into sentences/segments
        sentences = re.split(r'[.!?]\s+', raw_context)
        
        # Keep sentences that mention THIS venue name OR are general tips/advice
        name_lower = name.lower()
        name_words = set(name_lower.split())
        # Also check for partial matches (e.g., "employees only" matches "Employees Only")
        relevant_sentences = []
        
        # Patterns for general tips/advice that should be included even without venue name
        general_tip_patterns = [
            r'save.*\$\$', r'save.*money', r'cash.*only', r'reserve.*ahead', r'worth.*it',
            r'bring.*cash', r'no.*reservation', r'walk.*in', r'call.*ahead', r'book.*ahead',
            r'worth.*visit', r'must.*try', r'don\'t.*miss', r'highly.*recommend', r'best.*time',
            r'go.*early', r'go.*late', r'avoid.*crowd', r'busy.*time', r'quiet.*time',
            r'price', r'cost', r'affordable', r'expensive', r'cheap', r'budget', r'\$\$',
            r'tip', r'tips', r'note', r'important', r'remember', r'know'
        ]

        # Helper function for fuzzy venue name matching
        def venue_name_matches(text_lower, venue_name_lower):
            """Check if venue name appears in text, handling partial matches."""
            # Exact match
            if venue_name_lower in text_lower:
                return True

            # Split canonical name and check if first significant part matches
            # E.g., "Shy Shy Rooftop" → check for "shy shy"
            name_parts = venue_name_lower.split()
            if len(name_parts) >= 2:
                first_part = ' '.join(name_parts[:2])
                if first_part in text_lower and len(first_part) > 4:
                    return True

            # Check individual words for unique venue names
            # E.g., "Dante" is unique enough
            if len(name_parts) == 1 and len(venue_name_lower) > 5:
                return venue_name_lower in text_lower

            return False

        for sentence in sentences:
            sentence_lower = sentence.lower()
            sentence_stripped = sentence.strip()
            
            # Skip very short sentences (likely OCR fragments)
            if len(sentence_stripped) < 10:
                continue
            
            # Check if sentence mentions this venue using fuzzy matching
            mentions_venue = venue_name_matches(sentence_lower, name_lower)

            # Fallback to old multi-word logic if fuzzy match didn't work
            if not mentions_venue and len(name_words) > 1:
                # For multi-word names, check if most words appear together
                words_found = sum(1 for word in name_words if word in sentence_lower)
                if words_found >= min(2, len(name_words) - 1):  # At least 2 words or all but 1
                    mentions_venue = True
            elif not mentions_venue and len(name_words) == 1:
                # Single word - be more careful, check it's not part of another word
                if re.search(r'\b' + re.escape(name_lower) + r'\b', sentence_lower):
                    mentions_venue = True
            
            # Check if sentence is a general tip/advice (even if it doesn't mention venue name)
            is_general_tip = any(re.search(pattern, sentence_lower) for pattern in general_tip_patterns)
            
            # Include if it mentions venue OR is a general tip
            if mentions_venue or is_general_tip:
                    relevant_sentences.append(sentence)
        
        # Remove sentences that mention OTHER venues
        # Filter AGGRESSIVELY to prevent context bleeding between venues
        other_venues = [v.lower() for v in all_venues if v.lower() != name_lower]
        filtered_sentences = []

        # CRITICAL: Filter to prevent bleeding but keep useful general tips
        # Keep sentences that:
        # 1. Mention THIS venue (and don't mention other venues)
        # 2. Are general tips/advice (like "save your $$", "cash only") even if they don't mention venue name
        # 3. Exclude sentences that mention OTHER venues
        for sentence in relevant_sentences:
            sentence_lower = sentence.lower()
            # Skip if sentence mentions another venue (be strict - use word boundaries)
            mentions_other = False
            for v in other_venues:
                if len(v) > 2:
                    # Use word boundary regex to avoid substring matches
                    if re.search(r'\b' + re.escape(v) + r'\b', sentence_lower):
                        mentions_other = True
                        break
            # Check if sentence mentions this venue (use word boundaries for single words)
            mentions_this = False
            if len(name_words) == 1:
                # Single word - use word boundary
                mentions_this = bool(re.search(r'\b' + re.escape(name_lower) + r'\b', sentence_lower))
            else:
                # Multi-word - check if name appears or if key words appear together
                mentions_this = name_lower in sentence_lower or any(word in sentence_lower for word in name_words)
            
            # Check if sentence is a general tip/advice (even if it doesn't mention venue name)
            # Common tip patterns: "save your $$", "cash only", "reserve ahead", "worth it", etc.
            general_tip_patterns = [
                r'save.*\$\$', r'save.*money', r'cash.*only', r'reserve.*ahead', r'worth.*it',
                r'bring.*cash', r'no.*reservation', r'walk.*in', r'call.*ahead', r'book.*ahead',
                r'worth.*visit', r'must.*try', r'don\'t.*miss', r'highly.*recommend', r'best.*time',
                r'go.*early', r'go.*late', r'avoid.*crowd', r'busy.*time', r'quiet.*time'
            ]
            is_general_tip = any(re.search(pattern, sentence_lower) for pattern in general_tip_patterns)
            
            # CRITICAL: For "what to get" items, ONLY include sentences that explicitly mention the venue
            # Don't include general tips that mention food items - those can bleed between venues
            # Only include general tips that are truly general (pricing, reservations, etc.) not food items
            
            # Check if sentence mentions food items (likely to bleed)
            food_item_patterns = [
                r'\b(fried chicken|pizza|pasta|burger|sandwich|sushi|taco|burrito|wings|fries|salad|soup|steak|fish|chicken|beef|pork|lamb|shrimp|crab|lobster|oyster|mussel|clam|scallop|salmon|tuna|rice|noodle|dumpling|roll|bowl|wrap|tart|cake|pie|ice cream|dessert)\b',
                r'\b(cheese|gruyere|fritter|oyster|kombucha|carpaccio|wagyu|uni|mussel|vada pav|dosa)\b'
            ]
            mentions_food_items = any(re.search(pattern, sentence_lower) for pattern in food_item_patterns)
            
            # CRITICAL: Be EXTREMELY strict to prevent bleeding
            # ONLY include sentences that:
            # 1. Mention THIS venue AND don't mention other venues (always include)
            # 2. Are general tips (pricing/reservations) AND don't mention other venues AND don't mention food items
            # 3. If both venues mentioned, exclude UNLESS this venue is mentioned 3x more and appears first
            
            if mentions_this and not mentions_other:
                # Safe: mentions this venue, no other venues
                filtered_sentences.append(sentence)
                print(f"      ✓ Kept (mentions venue only): '{sentence[:80]}...'")
            elif is_general_tip and not mentions_other and not mentions_food_items and not mentions_this:
                # Safe: general tip (pricing/reservations) with no venue mentions and no food items
                # Only include if it's truly general (like "save your $$", "cash only")
                filtered_sentences.append(sentence)
                print(f"      ✓ Kept (general tip, no venues/food): '{sentence[:80]}...'")
            elif is_general_tip and mentions_food_items:
                # Exclude: general tips with food items can bleed between venues
                print(f"      ✗ Dropped (general tip with food items - prevents bleeding): '{sentence[:80]}...'")
            elif mentions_this and mentions_other:
                # CRITICAL: Both venues mentioned - be EXTREMELY strict
                this_pos = sentence_lower.find(name_lower)
                # Use word boundary regex for finding other venue positions
                other_positions = []
                for v in other_venues:
                    if len(v) > 2:
                        match = re.search(r'\b' + re.escape(v) + r'\b', sentence_lower)
                        if match:
                            other_positions.append(match.start())
                this_count = len(re.findall(r'\b' + re.escape(name_lower) + r'\b', sentence_lower))
                other_counts = []
                for v in other_venues:
                    if len(v) > 2:
                        count = len(re.findall(r'\b' + re.escape(v) + r'\b', sentence_lower))
                        other_counts.append(count)
                max_other_count = max(other_counts, default=0)
                # EXTREMELY strict: this venue must appear first AND be mentioned at least 3x more than others
                # Also require that this venue appears at least 3 times if others are mentioned
                # This prevents sentences like "Don Angie is great, Sartiano's is also good" from bleeding
                if this_pos != -1 and (not other_positions or (this_pos < min(other_positions) and this_count >= 3 * max_other_count and this_count >= 3)):
                    filtered_sentences.append(sentence)
                    print(f"      ✓ Kept (primary, very strict): '{sentence[:80]}...'")
                else:
                    print(f"      ✗ Dropped (mentions other venues - very strict filtering): '{sentence[:80]}...'")
            else:
                print(f"      ✗ Dropped (doesn't mention venue): '{sentence[:80]}...'")

        # Use filtered sentences or fall back to raw context if filtering removed everything
        if filtered_sentences:
            context = ". ".join(filtered_sentences)
            print(f"   ✂️ Filtered context: {len(filtered_sentences)}/{len(sentences)} sentences kept for {name}")
            
            # CRITICAL FIX: If filtered context is short (< 400 chars), be more lenient
            # BUT: Do NOT use lenient mode if context bleeding was detected (needs_strict_filtering)
            # This prevents venues like "The Clocktower" from having no context, but prevents bleeding
            # Increased from 200 to 400 to allow more valid contexts to benefit from lenient matching
            if len(context) < 400 and not needs_strict_filtering:
                print(f"   ⚠️ Filtered context is short ({len(context)} chars) for {name} - being more lenient")
                # Re-filter with relaxed rules: keep sentences that mention venue even if they mention other venues
                # (if venue appears first or multiple times)
                relaxed_sentences = []
                for sentence in sentences:
                    sentence_lower = sentence.lower()
                    sentence_stripped = sentence.strip()
                    
                    # Skip very short sentences
                    if len(sentence_stripped) < 10:
                        continue
                    
                    # Check if sentence mentions this venue
                    mentions_this = False
                    if name_lower in sentence_lower:
                        mentions_this = True
                    elif len(name_words) > 1:
                        words_found = sum(1 for word in name_words if word in sentence_lower)
                        if words_found >= min(2, len(name_words) - 1):
                            mentions_this = True
                    elif len(name_words) == 1:
                        if re.search(r'\b' + re.escape(name_lower) + r'\b', sentence_lower):
                            mentions_this = True
                    
                    # If it mentions this venue, keep it even if it mentions other venues
                    # (as long as this venue appears first or multiple times)
                    if mentions_this:
                        mentions_other = False
                        for v in other_venues:
                            if len(v) > 2:
                                if re.search(r'\b' + re.escape(v) + r'\b', sentence_lower):
                                    mentions_other = True
                                    break
                        
                        if not mentions_other:
                            # Safe: mentions this venue, no other venues
                            if sentence not in filtered_sentences:
                                relaxed_sentences.append(sentence)
                        else:
                            # Mentions both - check if this venue appears first or multiple times
                            this_pos = sentence_lower.find(name_lower)
                            other_positions = []
                            for v in other_venues:
                                if len(v) > 2:
                                    match = re.search(r'\b' + re.escape(v) + r'\b', sentence_lower)
                                    if match:
                                        other_positions.append(match.start())
                            this_count = len(re.findall(r'\b' + re.escape(name_lower) + r'\b', sentence_lower))
                            # More lenient: keep if venue appears first OR appears 2+ times
                            if this_pos != -1 and (not other_positions or this_pos < min(other_positions) or this_count >= 2):
                                if sentence not in filtered_sentences:
                                    relaxed_sentences.append(sentence)
                                    print(f"      ✓ Kept (lenient mode): '{sentence[:80]}...'")
                
                # Add relaxed sentences to context
                if relaxed_sentences:
                    context = ". ".join(filtered_sentences + relaxed_sentences)
                    print(f"   ✂️ Added {len(relaxed_sentences)} more sentences in lenient mode (total: {len(filtered_sentences) + len(relaxed_sentences)} sentences, {len(context)} chars)")
        else:
            # No venue-specific sentences found - DO NOT use full raw context (causes bleeding)
            # This can happen with uncommon venue names or abbreviated names in OCR
            # CRITICAL: Using full context causes menu items/details to bleed between venues
            # Instead, use only caption (safer) or empty string to prevent cross-venue contamination
            context = caption if caption else ""
            print(f"   ⚠️ No venue-specific sentences found for {name} - using caption only to prevent bleeding (was {len(raw_context)} chars, now {len(context)} chars)")
    else:
        context = raw_context
    
    # Filter out garbled sentences before sending to GPT
    # But preserve original context as fallback if filtering removes too much
    filtered_context = filter_garbled_sentences(context)

    # Safety check: if filtering removed too much content, use original context
    if len(filtered_context.strip()) < 20:
        print(f"   ⚠️ Garbled filtering removed too much content for {name} - using unfiltered context")
        context = context  # Keep original
    else:
        context = filtered_context

    # Final safety check: ensure we have meaningful context
    if len(context.strip()) < 10:
        print(f"   ❌ WARNING: Very little context available for {name} ({len(context)} chars) - GPT extraction may be incomplete")
    
    context_lower = context.lower()
    
    # Determine venue type from context
    is_bar = any(word in context_lower for word in ["bar", "cocktail", "drinks", "happy hour", "bartender", "mixology", "lounge", "pub"])
    is_restaurant = any(word in context_lower for word in ["restaurant", "dining", "food", "menu", "chef", "cuisine", "eatery", "bistro", "cafe", "café"])
    is_club = any(word in context_lower for word in ["club", "nightclub", "dj", "dance", "nightlife", "party", "music venue"])
    
    prompt = f"""
Analyze the TikTok context for "{name}" ONLY. Return JSON with details SPECIFICALLY about "{name}".

CRITICAL: Only extract information that is clearly about "{name}". 
- If context mentions "{name}" AND another venue, only extract details that are explicitly about "{name}"
- Do NOT include details about other venues (even if they're mentioned in the same context)
- If context says "at Katana Kitten, try XYZ" but you're analyzing a different venue, do NOT include "at Katana Kitten" in the response
- Only use information that directly relates to "{name}"
- IMPORTANT: If this is from a slideshow, ONLY use information from the slide(s) that mention "{name}". Do NOT aggregate information from other slides about different venues.
- CRITICAL FOR "must_try": Only include menu items/dishes that are EXPLICITLY mentioned for "{name}". If an item is mentioned for another venue (even in the same sentence), do NOT include it. Example: If context says "PB&J matcha latte at caffe paradiso" and you're analyzing "The Elk", do NOT include "PB&J matcha latte" even if it appears in the context.
- CRITICAL FOR "must_try": If an item appears in the context but is NOT explicitly associated with "{name}" (e.g., it appears in a list but the venue name isn't mentioned nearby), do NOT include it. Only extract items that are clearly linked to "{name}" in the context.
- CRITICAL FOR COMPLETENESS: Extract ALL dishes/items mentioned for "{name}" - do not skip any. Read EVERY WORD in the context, including smaller font text and fine print. Do NOT stop after extracting 1-2 items - extract ALL items mentioned. If you see a list like "tostada de jaiba, tostada de pulpo, razor clams, seared scallop", extract ALL items, not just some of them. Be thorough and complete - capture every dish/item the creator mentioned FOR THIS SPECIFIC VENUE. Pay attention to all details, even if they're in smaller font or less prominent positions.

{{
  "summary": "2–3 sentence vivid description about {name} specifically, using ONLY information from this venue's slide/page. Be concise and focus on key details. Do NOT include information from other venues or slides. CRITICAL: Write in THIRD PERSON only - NEVER use first-person pronouns like 'I', 'we', 'our', 'my', 'us'. Rephrase any personal opinions from the creator into objective descriptions (e.g., instead of 'our favorite wine bar' say 'a popular wine bar' or 'highly rated wine bar'). CRITICAL: Include ALL details from the OCR text - do NOT skip any information. Read EVERY WORD and extract ALL descriptive details, features, and context mentioned about {name}. Present everything neatly in sentences, but include ALL information.",
  "when_to_go": "Mention best time/day for {name} if clearly stated, else blank",
  "vibe": "Extract the EXACT vibe/atmosphere description from the slide text for {name}. Use the creator's actual words and phrases - do NOT make up generic descriptions like 'lively and energetic'. Quote or paraphrase what's explicitly written on the slide. If the slide says 'rooftop bar with city views', include that. If it says 'sexy cocktail bar', include that. If it mentions 'good views' or special features, include those too. Include ALL descriptive details mentioned (e.g., 'very light and fluffy', 'super light and fluffy', 'very generous on the hot honey', 'perfect level of crisp', 'unique and flavorful', 'sourdough crust was bomb'). CRITICAL: Read EVERY WORD in the OCR text - do NOT truncate or cut off descriptions. If the OCR says 'A FUN, CREATIVE COCKTAIL SPOT KNOWN FOR FOOD-INSPIRED DRINKS AND GREAT CHICKEN SANDWICHES IN A COOL, LIVELY SPACE', extract ALL of it, not just 'A FUN, - IN A'. Remove only: hashtags, OCR garbage, random fragments, venue names, and 'the vibes:' prefix. Keep the creator's authentic voice and ALL specific details about the atmosphere, setting, food quality, texture, and notable features. Do NOT stop after extracting 1-2 words - extract the COMPLETE description. CRITICAL: Extract ALL food quality descriptors (e.g., 'perfect level of crisp', 'unique and flavorful', 'bomb', 'so flimsy') - these are part of the vibe/experience.",
  "must_try": "What to get/order at {name}. Format as a natural, grammatically correct sentence listing ONLY the SPECIFIC dishes, drinks, or menu items mentioned by the creator FOR THIS SPECIFIC VENUE. Start with 'Try the' followed by the items. Use proper grammar: no unnecessary commas between adjectives and nouns, proper use of 'and', and natural phrasing (e.g., 'Try the original acai bowl, spicy salmon wrap, and iced latte' or 'Try the Miami mocha and perfect egg sandwich'). CRITICAL GRAMMAR RULES: (1) No commas between adjectives unless they're coordinate adjectives (e.g., 'very tasty slice' NOT 'very, tasty slice'), (2) Use 'and' only to connect the last two items in a list, (3) Ensure proper articles (a/an/the) are used correctly, (4) Never write incomplete phrases like 'and of' or 'the and'. CRITICAL: Extract EVERY dish/item mentioned FOR {name} - do not skip any. Read EVERY WORD in the context, including smaller font text, fine print, and all details. Do NOT stop after extracting 1-2 items - extract ALL items mentioned. Include ALL modifiers and details (e.g., 'very generous hot honey', 'very light and fluffy pancakes', 'deep fried short rib ragu pizza'). If the creator mentions multiple dishes FOR {name} (e.g., 'tostada de jaiba, tostada de pulpo, razor clams, seared scallop'), include ALL of them. CRITICAL PATTERN RECOGNITION: If you see patterns like 'X at {name}' or '{name}'s X' or 'the X from {name}', these are EXPLICIT associations - extract ALL of them. Examples: 'Caesar Salad Martini at Shy Shy', 'Dante Martini at Dante', 'Popcorn at Bar Belly' - extract EVERY item that has 'at {name}' in the text. These explicit 'at {name}' patterns are the STRONGEST signal of what to order - never skip them. CRITICAL: Only extract items that are EXPLICITLY associated with {name} in the context. If an item appears in the context but is NOT clearly linked to {name} (e.g., it appears in a list but {name} isn't mentioned nearby), do NOT include it. If an item is mentioned for another venue (even in the same sentence or in a different slide), do NOT include it. For RESTAURANTS/FOOD places, extract ALL SPECIFIC dishes they actually tried and mentioned liking AT {name}. For BARS/LOUNGES, list signature cocktails, drink specials, or bar features AT {name}. For CLUBS/MUSIC VENUES, list DJs, events, or music highlights AT {name}. Always prioritize SPECIFIC items the creator tried and mentioned AT {name} over generic recommendations. IMPORTANT: Be thorough - if you see a list of dishes FOR {name}, extract ALL of them, not just a subset. Read the ENTIRE context carefully - do NOT miss items in smaller font or less prominent positions. But ONLY extract items that are clearly FOR {name}. DO NOT include generic category names without specific items (e.g., if the context only says 'dinner' or 'American food' without mentioning specific dishes, leave this field blank).",
  "good_to_know": "Important tips or things to know about {name} (e.g., 'Reserve ahead of time', 'Cash only', 'Dress code required', 'Save your $$', 'Worth the price', 'Affordable', 'Budget-friendly', 'Quality isn't always consistent', 'Long lines', 'Sometimes inconsistent', 'a little on the sweeter side', 'pizza is flimsy', 'new special pizza every week'). Capture ALL practical tips, pricing notes, reservation requirements, payment methods, helpful advice, AND any warnings or negative feedback mentioned in the context (e.g., 'quality isn't always consistent', 'long lines', 'sometimes inconsistent', 'a little on the sweeter side', 'pizza is flimsy'). Include both positive tips AND realistic warnings/limitations if mentioned. CRITICAL: Extract ALL descriptive details about food quality, texture, taste characteristics, and special features (e.g., 'perfect level of crisp', 'unique and flavorful', 'sourdough crust was bomb', 'new special pizza every week'). Read EVERY WORD in the context - do NOT skip descriptive details. Only include if clearly mentioned in the context.",
  "features": "Specific physical features, amenities, or notable elements mentioned about {name}. Examples: 'DJ booth at night', 'seating around the bar', 'outdoor patio', 'rooftop views', 'photo-op spots', 'dance floor', 'private booths'. Capture ALL specific details mentioned in the context. If multiple features are mentioned, list them all.",
  "team_behind": "If context mentions '{name}' is 'from the team behind X' or 'from the chefs behind X', extract that information here. Examples: 'From the team behind Employees Only', 'From the chefs behind Le Bernardin', 'From the creators of Death & Co'. This adds context/color about the venue's background. ONLY include if explicitly mentioned - do NOT infer or make up this information.",
  "specials": "Real deals, special events, pricing tips, or money-saving information at {name} if mentioned (e.g., 'Save your $$', 'Happy hour deals', 'Weekend specials', 'Affordable prices', 'new special pizza every week'). Capture any cost-related tips or special offers mentioned. CRITICAL: Extract ALL recurring specials or rotating menu items (e.g., 'new special pizza every week', 'rotating menu', 'weekly specials'). Read EVERY WORD in the context - do NOT skip special features or recurring events.",
  "comments_summary": "Short insight from comments about {name} if available",
  "creator_insights": "Capture personal recommendations, comparisons, or unique context from the creator SPECIFICALLY about {name}. This is where first-person opinions should go (e.g., 'quickly become our favorite', 'we rank every meal'). Examples: 'I'm from California and this is the only burger comparable to In-N-Out', 'This place reminds me of my favorite spot back home', 'Only place in NYC that does X like this', 'Has quickly become our favorite wine bar', 'We rank them an 8.7'. Include personal anecdotes, ratings, comparisons to other places, or unique selling points the creator emphasizes ABOUT {name}. Keep the creator's authentic voice and first-person language here - this will be shown in a 'Show More' section."
}}

Context (filtered to only include mentions of "{name}"):
{context}

CRITICAL: Extract ALL details from this context. Read EVERY WORD including smaller font text and fine print.
Do NOT stop after extracting 1-2 items - extract ALL dishes, features, tips, and details mentioned.
"""
    try:
        client = get_openai_client()
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )
        raw = completion.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", raw, re.S)
        j = json.loads(match.group(0)) if match else {}
        # Handle case where GPT returns a list instead of string
        must_try_raw = j.get("must_try", "")
        if isinstance(must_try_raw, list):
            # Format list items as a natural sentence
            items = [str(x).strip() for x in must_try_raw if str(x).strip()]
            if len(items) == 1:
                must_try_value = items[0]
            elif len(items) == 2:
                must_try_value = f"{items[0]} and {items[1]}"
            elif len(items) > 2:
                # Join with commas and 'and' for the last item
                must_try_value = ", ".join(items[:-1]) + f", and {items[-1]}"
            else:
                must_try_value = ""
        else:
            must_try_value = str(must_try_raw).strip() if must_try_raw else ""
        
        # Post-processing: Fix grammar issues and validate output
        if must_try_value:
            # CRITICAL: Fix grammar issues - remove incorrect commas and fix malformed phrases

            # 1. Fix incorrect commas between adjectives/modifiers and nouns
            # "very, tasty" -> "very tasty"
            must_try_value = re.sub(r'\b(very|super|really|quite|pretty|so|too|extremely|incredibly|absolutely)\s*,\s*', r'\1 ', must_try_value, flags=re.IGNORECASE)

            # 2. Fix malformed phrases like "and of pizza" -> just remove them
            must_try_value = re.sub(r'\band\s+of\b', '', must_try_value, flags=re.IGNORECASE)

            # 3. Fix patterns like "tasty good slice, and of pizza" -> "tasty good slice of pizza"
            must_try_value = re.sub(r',\s+and\s+of\b', ' of', must_try_value, flags=re.IGNORECASE)

            # 2.5. Fix "with and" -> "with" (e.g., "with and hot honey" -> "with hot honey")
            must_try_value = re.sub(r'\bwith\s+and\s+', 'with ', must_try_value, flags=re.IGNORECASE)

            # 2.6. Fix "slice with a," -> "slice with" (e.g., "mushroom, slice with a," -> "mushroom slice with")
            must_try_value = re.sub(r'\bslice\s+with\s+a\s*,?\s*', 'slice with ', must_try_value, flags=re.IGNORECASE)

            # 4. Fix incorrect commas in compound adjectives: "tasty, good slice" -> "tasty good slice"
            # Only remove comma if both words are short (likely adjectives)
            must_try_value = re.sub(r'\b([a-z]{3,8})\s*,\s*([a-z]{3,8})\s+', lambda m: f"{m.group(1)} {m.group(2)} " if m.group(2).lower() in ['good', 'tasty', 'fresh', 'hot', 'cold', 'spicy', 'sweet', 'yummy', 'crispy', 'fluffy', 'light', 'heavy'] else m.group(0), must_try_value, flags=re.IGNORECASE)

            # 5. Remove generic category names without specifics
            # If it only says "Try the American and dinner" or "Try the dinner", clear it
            generic_only_patterns = [
                r'^try\s+the\s+(american|italian|french|chinese|japanese|mexican|thai|indian|greek)\s+and\s+(dinner|lunch|breakfast|brunch)\s*\.?$',
                r'^try\s+the\s+(dinner|lunch|breakfast|brunch)\s*\.?$',
                r'^try\s+the\s+(food|cuisine|meal)\s*\.?$',
                r'^try\s+the\s+(american|italian|french|chinese|japanese|mexican|thai|indian|greek)\s*\.?$'
            ]
            for pattern in generic_only_patterns:
                if re.match(pattern, must_try_value.lower()):
                    print(f"   ⚠️ Detected generic-only 'must_try' (no specific dishes): '{must_try_value}' - clearing it")
                    must_try_value = ""
                    break

            # 6. Fix "Try the and X" patterns (incomplete)
            if must_try_value and re.search(r'^(try|get|order)\s+(the|a|an)\s+and\s+', must_try_value.lower()):
                print(f"   ⚠️ Detected incomplete 'must_try' (starts with 'Try the and'): '{must_try_value}' - clearing it")
                must_try_value = ""

            # 7. Fix redundant category names: "cheesecake and dessert" -> "cheesecake"
            # Remove generic category if it's redundant with a specific item
            redundant_patterns = [
                (r'\bcheesecake\s+and\s+dessert\b', 'cheesecake'),
                (r'\bpizza\s+and\s+(slice|pie)\b', 'pizza'),
                (r'\b(burger|hamburger)\s+and\s+sandwich\b', r'\1'),
                (r'\b(latte|cappuccino|espresso|mocha)\s+and\s+coffee\b', r'\1'),
                (r'\bpasta\s+and\s+(italian|dinner)\b', 'pasta'),
            ]
            for pattern, replacement in redundant_patterns:
                if re.search(pattern, must_try_value, flags=re.IGNORECASE):
                    must_try_value = re.sub(pattern, replacement, must_try_value, flags=re.IGNORECASE)
                    print(f"   ℹ️ Fixed redundant category in 'must_try': {pattern} -> {replacement}")

            # 8. Clean up multiple spaces and trim
            if must_try_value:
                must_try_value = re.sub(r'\s+', ' ', must_try_value).strip()
                # Remove trailing periods if present
                must_try_value = must_try_value.rstrip('.')

        # CRITICAL: Preserve "item at venue" patterns - these are explicit associations
        # Examples: "Caesar Salad Martini at Shy Shy", "Popcorn at Bar Belly", "Dante Martini at Dante"
        if must_try_value and name:
            # Detect and preserve "item at venue" patterns before other processing
            at_venue_pattern = rf'\b(.+?)\s+at\s+' + re.escape(name.lower()) + r'\b'
            at_venue_matches = re.findall(at_venue_pattern, must_try_value.lower())

            if at_venue_matches:
                # These are explicitly tagged items - extract and format them
                preserved_items = []
                for match in at_venue_matches:
                    # Clean and properly capitalize
                    item = match.strip().title()
                    # Remove "try the" prefix if present
                    item = re.sub(r'^try\s+the\s+', '', item, flags=re.IGNORECASE).strip()
                    if item and len(item) > 3:
                        preserved_items.append(item)

                if preserved_items:
                    # Format as proper sentence
                    if len(preserved_items) == 1:
                        must_try_value = f"Try the {preserved_items[0]}"
                    else:
                        must_try_value = "Try the " + ", ".join(preserved_items[:-1]) + " and " + preserved_items[-1]

                    # Set the value - this will be assigned to place_data later
                    print(f"   ✅ Preserved explicit 'at {name}' pattern: {must_try_value}")
                    # Don't do further splitting on this value since it's already properly formatted

        # Clean up formatting: if it's still space-separated (old format), convert to sentence
        # Detect space-separated items and intelligently split them into proper items
        if must_try_value and " " in must_try_value and "," not in must_try_value and "and" not in must_try_value.lower():
            words = must_try_value.split()
            items = []
            current_item = []
            
            # Smart splitting: look for words that commonly START new menu items (not end them)
            # These are usually adjectives or modifiers that appear at the beginning
            item_starters = ['iced', 'hot', 'spicy', 'sweet', 'perfect', 'original', 'miami', 'crispy', 
                           'grilled', 'fried', 'fresh', 'cold', 'warm', 'frozen', 'salted', 'maple', 
                           'whipped', 'seasonal', 'blue', 'coconut', 'brown', 'butter', 'oat', 'matcha']
            
            # Words that typically END items (not start new ones)
            item_enders = ['bowl', 'wrap', 'sandwich', 'burger', 'pizza', 'pasta', 'sushi', 'taco', 
                          'burrito', 'latte', 'espresso', 'cappuccino', 'mocha', 'coffee', 'tea', 
                          'soup', 'salad', 'smoothie', 'shake', 'juice']
            
            for i, word in enumerate(words):
                word_lower = word.lower().strip('.,!?')
                current_item.append(word)
                
                # Check if we should finish current item
                if i + 1 < len(words):
                    next_word_lower = words[i + 1].lower().strip('.,!?')
                    # Finish item if:
                    # 1. Current word is an item ender (bowl, wrap, etc.) AND we have at least 2 words
                    # 2. Next word is an item starter AND we have at least 2 words
                    if (word_lower in item_enders and len(current_item) >= 2) or \
                       (next_word_lower in item_starters and len(current_item) >= 2):
                        items.append(" ".join(current_item))
                        current_item = []
                elif i == len(words) - 1:
                    # Last word, finish current item
                    items.append(" ".join(current_item))
            
            # If we didn't find good splits, try splitting every 2-3 words
            if len(items) <= 1 and len(words) > 3:
                # Fallback: split into groups of 2-3 words
                items = []
                i = 0
                while i < len(words):
                    # Take 2-3 words as an item
                    chunk_size = 3 if i + 3 < len(words) else min(2, len(words) - i)
                    items.append(" ".join(words[i:i+chunk_size]))
                    i += chunk_size
            
            # Format as sentence
            if len(items) > 1:
                if len(items) == 2:
                    must_try_value = f"{items[0]} and {items[1]}"
                else:
                    must_try_value = ", ".join(items[:-1]) + f", and {items[-1]}"
            elif len(items) == 1:
                must_try_value = items[0]
        
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
        
        # Extract short vibe keywords from the full context for bubble tags
        vibe_keywords = extract_vibe_keywords(context)

        # Get vibe from GPT response and clean it
        # Handle case where GPT returns a list instead of string for all fields
        def safe_get_str(field_name, default=""):
            value = j.get(field_name, default)
            if isinstance(value, list):
                text = " ".join(str(x) for x in value).strip()
            else:
                text = str(value).strip() if value else default
            # Normalize brand names (belly → Beli)
            return normalize_brand_names(text)
        
        vibe_raw = safe_get_str("vibe", "")
        vibe_cleaned = clean_vibe_text(vibe_raw, name)
        
        # Get features field (new field for specific amenities/features)
        features_value = safe_get_str("features", "")
        
        # Get team_behind field (for "from the team behind X" information)
        team_behind_value = safe_get_str("team_behind", "")

        data = {
            "summary": safe_get_str("summary", ""),
            "when_to_go": safe_get_str("when_to_go", ""),
            "vibe": vibe_cleaned,  # Use cleaned GPT-extracted vibe, not raw context
            "vibe_keywords": vibe_keywords,  # Short keywords for bubble tags
            "must_try": must_try_value,
            "must_try_field": field_name,  # Store the field name
            "good_to_know": safe_get_str("good_to_know", ""),  # Add good_to_know field
            "features": features_value,  # Add features field for specific amenities
            "team_behind": team_behind_value,  # Add team_behind field for "from the team behind X" context
            "specials": safe_get_str("specials", ""),
            "comments_summary": safe_get_str("comments_summary", ""),
            "creator_insights": safe_get_str("creator_insights", ""),  # Personal recommendations and comparisons
        }
        # Extract vibe_tags from the FILTERED venue-specific context
        # CRITICAL: The context has already been filtered to only include sentences about THIS venue
        # This ensures tags are specific to each venue, not generic or bleeding from other venues
        # Pass venue name to ensure unique, context-specific tags
        # The context variable here is venue-specific (filtered in lines 4020-4171)
        print(f"   🏷️ Extracting vibe tags for {name} from {len(context)} chars of venue-specific context")
        data["vibe_tags"] = extract_vibe_tags(context, venue_name=name)
        
        # CRITICAL: Verify tags are venue-specific - log for debugging
        if data["vibe_tags"]:
            print(f"   ✅ Extracted {len(data['vibe_tags'])} vibe tags for {name}: {data['vibe_tags']}")
        else:
            print(f"   ⚠️ No vibe tags extracted for {name} (context may not contain specific vibe info)")
        
        # Fallback: Add important vibe keywords that GPT might have missed
        # Check if context mentions romantic/date night but GPT didn't extract it
        context_lower_for_tags = context.lower()
        important_tags_to_check = {
            "romantic": ["romantic", "date night", "romantic spot", "romantic dinner", "romantic italian"],
            "intimate": ["intimate", "intimate setting", "intimate spot"],
            "cozy": ["cozy", "cozy spot", "cozy atmosphere"],
        }
        for tag, keywords in important_tags_to_check.items():
            if tag.capitalize() not in data["vibe_tags"]:
                if any(kw in context_lower_for_tags for kw in keywords):
                    data["vibe_tags"].append(tag.capitalize())
                    print(f"   ✅ Added '{tag.capitalize()}' tag from context keywords")
        
        # CRITICAL: ALWAYS filter out cuisine tags for non-restaurants (cafes/bars shouldn't have cuisine tags)
        # This must run REGARDLESS of whether GPT extraction succeeded or not
        context_lower = context.lower()
        is_restaurant_context = any(word in context_lower for word in ["restaurant", "dining", "chef", "cuisine", "eatery", "bistro"])
        is_bar_context = any(word in context_lower for word in ["bar", "cocktail", "drinks", "happy hour", "bartender", "lounge", "pub"])
        is_cafe_context = any(word in context_lower for word in ["cafe", "coffee", "latte", "espresso", "cappuccino"])

        # Remove cuisine tags if this is a cafe/bar (not a restaurant)
        # CRITICAL: Also filter out cuisine tags that are actually drink names (e.g., "Spanish" from "Spanish latte")
        # CRITICAL: This filtering MUST happen even if vibe_tags is not empty
        original_tag_count = len(data["vibe_tags"])
        if data["vibe_tags"] and (is_cafe_context or is_bar_context) and not is_restaurant_context:
            cuisine_tags = {"Thai", "Italian", "French", "Spanish", "Japanese", "Mexican", "Indian", "Greek", "Mediterranean", "Seafood", "Steakhouse", "Sushi", "Pasta", "Tapas", "Ramen", "Chinese", "Korean"}
            # Check if cuisine tag appears in context as a drink name (e.g., "Spanish latte", "Italian espresso")
            context_lower_for_drink_check = context.lower()
            drink_patterns = ["latte", "espresso", "coffee", "drink", "cocktail", "mocha", "cappuccino", "americano", "macchiato"]
            filtered_tags = []
            for tag in data["vibe_tags"]:
                if tag in cuisine_tags:
                    # Check if this cuisine tag appears next to a drink name (e.g., "Spanish latte")
                    tag_lower = tag.lower()
                    # Look for patterns like "spanish latte", "italian espresso", etc.
                    is_drink_name = any(f"{tag_lower} {drink}" in context_lower_for_drink_check or f"{drink} {tag_lower}" in context_lower_for_drink_check 
                                       for drink in drink_patterns)
                    if is_drink_name:
                        print(f"   🏷️ Filtered out '{tag}' tag - appears to be a drink name (e.g., '{tag} latte'), not a cuisine tag")
                        continue
                filtered_tags.append(tag)
            data["vibe_tags"] = filtered_tags
            if len(data["vibe_tags"]) < original_tag_count:
                print(f"   🏷️ Filtered out cuisine tags for {name} (cafe/bar, not restaurant)")

        # CRITICAL FIX: Fallback to vibe_keywords if GPT extraction failed or returned empty list
        # BUT: Filter out cuisine keywords (Thai, Italian, etc.) for non-restaurants
        if not data["vibe_tags"] and vibe_keywords:
            # Determine if this is a restaurant from context
            context_lower = context.lower()
            is_restaurant_context = any(word in context_lower for word in ["restaurant", "dining", "chef", "cuisine", "eatery", "bistro"])
            is_bar_context = any(word in context_lower for word in ["bar", "cocktail", "drinks", "happy hour", "bartender"])
            is_cafe_context = any(word in context_lower for word in ["cafe", "coffee", "latte", "espresso", "cappuccino"])
            
            # Filter out cuisine keywords for cafes/bars (unless it's clearly a restaurant)
            cuisine_keywords = {"Thai", "Italian", "French", "Spanish", "Japanese", "Mexican", "Indian", "Greek", "Mediterranean", "Seafood", "Steakhouse", "Sushi", "Pasta", "Tapas", "Ramen"}
            filtered_keywords = []
            for kw in vibe_keywords:
                # Keep cuisine keywords only if it's a restaurant context (not cafe/bar)
                if kw in cuisine_keywords:
                    if is_restaurant_context and not is_cafe_context and not is_bar_context:
                        filtered_keywords.append(kw)
                else:
                    # Keep non-cuisine keywords
                    filtered_keywords.append(kw)
            
            data["vibe_tags"] = filtered_keywords[:6]  # Limit to 6 tags max
            print(f"   ⚠️ GPT vibe_tags extraction failed for {name}, using filtered vibe_keywords fallback: {data['vibe_tags']}")

        # Add "Vegan" tag if explicitly mentioned in the context
        context_lower = context.lower()
        vegan_indicators = ["vegan", "plant-based", "plant based"]
        if any(indicator in context_lower for indicator in vegan_indicators):
            if "Vegan" not in data["vibe_tags"]:
                data["vibe_tags"].append("Vegan")
                print(f"   ✅ Added 'Vegan' tag for {name}")

        # Add "Rooftop" tag if explicitly mentioned in the context or features
        # Check both context and features field for rooftop indicators
        rooftop_indicators = ["rooftop", "roof top", "rooftop bar", "rooftop views", "on the roof"]
        context_and_features = context_lower + " " + data.get("features", "").lower()
        if any(indicator in context_and_features for indicator in rooftop_indicators):
            if "Rooftop" not in data["vibe_tags"]:
                data["vibe_tags"].append("Rooftop")
                print(f"   ✅ Added 'Rooftop' tag for {name}")

        # Note: Cuisine types are now extracted from Google Maps Place Details API
        # (see extract_cuisine_from_google_types function and place_types_from_google)

        return data
    except Exception as e:
        print(f"⚠️ Enrichment failed for {name}:", e)
        return {
            "summary": "",
            "when_to_go": "",
            "vibe": "",
            "vibe_keywords": [],
            "must_try": "",
            "must_try_field": "must_try",
            "good_to_know": "",
            "features": "",
            "team_behind": "",
            "specials": "",
            "comments_summary": "",
            "creator_insights": "",
            "vibe_tags": [],
        }

def extract_vibe_keywords(text):
    """Extract short descriptive keywords from context text for bubble tags.

    Examples: "Lively", "Energetic", "Social", "Cozy", "Romantic"
    """
    if not text or len(text.strip()) < 10:
        return []

    # Common vibe/atmosphere keywords to look for
    # ONLY POSITIVE, APPEALING descriptors - removed negative words like "Serious", "Crowded", "Packed", "Gritty", "Raw"
    vibe_keywords = [
        # Energy level
        "Lively", "Energetic", "Vibrant", "Dynamic", "Buzzing", "Electric",
        "Chill", "Relaxed", "Calm", "Peaceful", "Laid-back", "Casual",

        # Atmosphere
        "Cozy", "Intimate", "Romantic", "Charming", "Elegant", "Sophisticated",
        "Trendy", "Hip", "Modern", "Contemporary", "Stylish", "Chic", "Sexy",
        "Rustic", "Vintage", "Classic", "Traditional", "Old-school",

        # Social
        "Social", "Friendly", "Welcoming", "Inviting", "Warm",
        "Upscale", "Classy", "Fancy", "Luxurious", "Premium",
        "Casual", "Unpretentious", "Down-to-earth", "Authentic",

        # Activity - removed "Crowded", "Packed", "Bustling" (negative connotation)
        "Popular", "Lively",
        "Quiet", "Intimate", "Hidden", "Secret", "Local",

        # Mood - removed "Serious" and other negative words
        "Fun", "Playful", "Quirky", "Eclectic", "Unique",
        "Polished", "Refined",
        "Artsy", "Creative", "Bohemian", "Alternative",
        "Underground", "Dive", "Edgy",

        # Special features
        "Rooftop", "Views", "Scenic", "Waterfront", "Outdoor",

        # Cuisine types (for venue categorization)
        # Excluded: Chinese, Korean, American, Pizza, Burger (too generic)
        "Wine Bar", "Cocktail Bar", "Greek", "Italian", "French", "Spanish",
        "Japanese", "Mexican", "Thai", "Indian",
        "Mediterranean", "Seafood", "Steakhouse", "Sushi",
        "Pasta", "Tapas", "Ramen",
    ]

    text_lower = text.lower()
    found_keywords = []

    # Look for keywords in the text using word boundary matching to avoid false positives
    # E.g., "casual" won't match "occasionally" or "casual drink" in a sentence about something else
    import re
    for keyword in vibe_keywords:
        keyword_lower = keyword.lower()
        # Use word boundary matching for better precision
        # Match whole words only, case-insensitive
        if re.search(r'\b' + re.escape(keyword_lower) + r'\b', text_lower) and keyword not in found_keywords:
            found_keywords.append(keyword)
            if len(found_keywords) >= 5:  # Limit to 5 keywords
                break

    return found_keywords


def extract_adjectives_from_text(text, max_words=10, venue_name=None):
    """
    Extract 1-word adjectives from text for vibe tags.
    Returns list of capitalized adjectives.

    This function looks for common single-word adjectives that describe
    venue vibes, atmosphere, and style. Only returns adjectives explicitly
    found in the text.

    Args:
        text: Text to extract adjectives from
        max_words: Maximum number of adjectives to return
        venue_name: If provided, only extract adjectives near this venue name (within 150 chars)
    """
    if not text or len(text.strip()) < 5:
        return []

    # Common vibe adjectives to look for (1 word only)
    # These should be positive, appealing descriptors
    # EXCLUDE cuisine types - those come from Google Places API
    vibe_adjectives = {
        'casual', 'cozy', 'romantic', 'lively', 'upscale', 'trendy',
        'authentic', 'modern', 'classic', 'intimate', 'energetic',
        'chill', 'vibrant', 'relaxed', 'stylish', 'chic', 'elegant',
        'rustic', 'minimalist', 'eclectic', 'bohemian', 'industrial',
        'warm', 'inviting', 'sophisticated', 'playful', 'quirky',
        'sleek', 'contemporary', 'traditional', 'hip', 'cool', 'fancy',
        'charming', 'classy', 'luxurious', 'polished', 'artsy',
        'creative', 'unique', 'refined', 'welcoming', 'friendly',
        'popular', 'hidden', 'local', 'fun', 'scenic', 'outdoor'
    }

    # Simple extraction: find adjectives in text using word boundaries
    text_lower = text.lower()
    found = []
    import re

    # If venue_name provided and text is long (likely multi-venue), only look near venue name
    if venue_name and len(text) > 500:
        # Find all occurrences of venue name (case-insensitive)
        venue_lower = venue_name.lower()
        # Look for venue name mentions
        venue_pattern = r'\b' + re.escape(venue_lower) + r'\b'
        matches = list(re.finditer(venue_pattern, text_lower, re.IGNORECASE))

        if matches:
            # Extract text chunks around venue name mentions (150 chars before/after)
            relevant_chunks = []
            for match in matches:
                start = max(0, match.start() - 150)
                end = min(len(text), match.end() + 150)
                relevant_chunks.append(text_lower[start:end])

            # Search only in relevant chunks
            search_text = " ".join(relevant_chunks)
        else:
            # Venue name not found - don't extract adjectives (avoid bleeding)
            return []
    else:
        # Short text or no venue name - search entire text
        search_text = text_lower

    for adj in vibe_adjectives:
        # Use word boundary matching to avoid partial matches
        if re.search(r'\b' + re.escape(adj) + r'\b', search_text):
            # Capitalize for display consistency
            found.append(adj.capitalize())
            if len(found) >= max_words:
                break

    return found


def extract_vibe_tags(text, venue_name=None):
    if not text.strip():
        return []

    venue_context = f" for {venue_name}" if venue_name else ""
    prompt = f"""
Extract up to 6 unique, POSITIVE vibe tags from this text{venue_context}.
Tags should be SHORT (1-2 words) and capture the SPECIFIC atmosphere, style, or features mentioned in the text.

CRITICAL: This text is SPECIFICALLY about "{venue_name}" only. Extract tags that are SPECIFIC to THIS venue, not generic tags that could apply to any venue.
CRITICAL: Only extract tags that are EXPLICITLY mentioned or clearly implied about THIS specific venue in the text.
CRITICAL: If the text mentions multiple venues, ONLY extract tags that are clearly about "{venue_name}" - do NOT extract tags from descriptions of other venues.
CRITICAL: Do NOT extract generic tags like 'Fun', 'Good', 'Nice' unless specifically emphasized for "{venue_name}".
CRITICAL: If the text doesn't contain specific information about "{venue_name}", return fewer tags or an empty list rather than generic tags.
CRITICAL: Do NOT extract generic tags like "Fun", "Good", "Nice", "Views", "Rooftop" unless they're specifically emphasized for this venue.
CRITICAL: If the text mentions multiple venues, ONLY extract tags that are clearly about "{venue_name}".
CRITICAL: Do NOT extract cuisine tags (like "Mexican", "Italian", "French", "Thai", "Chinese", "Japanese", "Indian", "Greek", "Spanish", "Korean", "Seafood") unless the text explicitly states "{venue_name}" serves that cuisine. If "{venue_name}" is not mentioned with a cuisine, do NOT extract cuisine tags.
CRITICAL: If you see "rooftop" or "views" mentioned for OTHER venues but not for "{venue_name}", do NOT include "Rooftop" or "Views" tags for "{venue_name}".
CRITICAL: NEVER extract cuisine-related tags - cuisine types come from a different source. Focus ONLY on atmosphere, style, and features.

Only use POSITIVE, APPEALING descriptors. NEVER use negative words like "Crowded", "Loud", "Busy", "Packed", "Expensive", etc.

Focus on what makes THIS place unique:
- Atmosphere: Cozy, Lively, Intimate, Energetic, Chill, Upscale, Vibrant, Relaxed, Romantic
- Best for: Date Night, Groups, Solo, Brunch, Late Night, Happy Hour, Romantic Dinner
- Style: Trendy, Classic, Authentic, Modern, Casual, Fancy, Hip, Stylish, Romantic
- Features: Outdoor, Rooftop, Live Music, DJ, Games, Dancing, Cocktails

DO NOT USE: French, Italian, Thai, Chinese, Japanese, Mexican, Indian, Korean, Spanish, Greek, Seafood, or any other cuisine/food types

Extract tags that are SPECIFIC to what's written about THIS venue in the text. Each venue should have different tags based on its unique characteristics.
If the text doesn't contain specific information about "{venue_name}", return fewer tags or an empty list rather than generic tags.

Text about "{venue_name}":
{text}

Return ONLY a valid JSON list of 3-6 unique POSITIVE tags SPECIFIC to "{venue_name}".
"""
    # Try GPT extraction first
    gpt_tags = []
    try:
        client = get_openai_client()
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,  # Increased from 0.3 for more variety
            max_tokens=50,  # Increased from 40 to allow for longer tag lists
        )
        raw = r.choices[0].message.content.strip()
        gpt_tags = json.loads(raw) if raw.startswith("[") else []
    except Exception as e:
        print("⚠️ vibe_tags generation failed:", e)
        gpt_tags = []

    # NEW: Extract adjectives as supplementary tags
    # This catches simple descriptive words that GPT might miss
    # Pass venue_name to avoid extracting adjectives from other venues in multi-venue text
    adjective_tags = extract_adjectives_from_text(text, max_words=3, venue_name=venue_name)

    # Merge GPT tags + adjective tags (deduplicate, case-insensitive)
    all_tags = []
    seen = set()

    for tag in gpt_tags + adjective_tags:
        tag_lower = tag.lower()
        if tag_lower not in seen:
            seen.add(tag_lower)
            all_tags.append(tag)

    # Limit to 6 tags max
    return all_tags[:6]

# ─────────────────────────────
# Helper Functions for Place Merging
# ─────────────────────────────

def get_place_address(place_name):
    """Get formatted address for a place name using Google Maps API."""
    _, address, _, _, _, _ = get_place_info_from_google(place_name, use_cache=True)
    return address

def merge_place_with_cache(place_data, video_url, username=None, video_summary=None):
    """Merge a place with cached places if name+address match. Returns merged place data."""
    place_name = place_data.get("name", "")
    original_name = place_name
    
    # If address not already set, get it (and ensure canonical name)
    # Use cache to avoid redundant API calls
    if not place_data.get("address"):
        canonical_name, address, _, _, _, _ = get_place_info_from_google(place_name, use_cache=True)
        if canonical_name and canonical_name.lower() != place_name.lower():
            place_name = canonical_name  # Update to canonical name
            place_data["name"] = canonical_name
            print(f"✏️  Corrected spelling in cache: '{original_name}' → '{canonical_name}'")
        place_address = address or ""
    else:
        place_address = place_data.get("address")
        # Only check canonical name if we don't have it cached - avoid redundant API call
        # Check cache first
        # Ensure _places_cache is available
        global _places_cache
        try:
            if _places_cache is None:
                _places_cache = {}
        except NameError:
            _places_cache = {}
        cache_key = place_name.lower().strip()
        if cache_key in _places_cache:
            canonical_name, _, _, _, _, _ = _places_cache[cache_key]
        else:
            # Only make API call if not in cache
            canonical_name, _, _, _, _, _ = get_place_info_from_google(place_name, use_cache=True)
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
        # CRITICAL: Load cached place_data to merge intelligently (prefer new but keep old fields if new is missing)
        cached_place_data = {}
        if cached.get("place_data"):
            try:
                cached_place_data = json.loads(cached["place_data"])
                print(f"   🔄 Found cached place_data for {place_name}, merging with new data")
            except Exception as e:
                print(f"   ⚠️ Failed to parse cached place_data: {e}")
                cached_place_data = {}
        
        # Merge: prefer new place_data fields, but fall back to cached if new is empty/missing
        merged_data = {
            **cached_place_data,  # Start with cached data (may have old fields)
            **place_data,  # Overwrite with new data (prefer new fields)
            "other_videos": other_videos_data,
            "address": place_address  # Always use current address
        }
        
        # CRITICAL: Ensure new fields are preserved even if cached data is old
        # If new place_data has these fields, use them (even if empty)
        for field in ["neighborhood", "vibe_tags", "description", "photo_url", "must_try", "good_to_know", "features"]:
            if field in place_data:
                merged_data[field] = place_data[field]
        
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
        # CRITICAL: Ensure all required fields are present
        place_data_with_note = {
            **place_data, 
            "other_videos": [], 
            "address": place_address,
            # Ensure these fields exist even if empty
            "neighborhood": place_data.get("neighborhood", "NYC"),
            "vibe_tags": place_data.get("vibe_tags", []),
            "description": place_data.get("description", ""),
            "photo_url": place_data.get("photo_url", "https://via.placeholder.com/600x400?text=No+Photo")
        }
        
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

def enrich_places_parallel(venues, transcript, ocr_text, caption, comments_text, url, username, context_title, venue_to_slide=None, venue_to_context=None, photo_urls=None, venue_attribution=None):
    """Enrich multiple places in parallel for better performance.

    Args:
        venues: List of venue names to enrich
        venue_to_slide: Optional dict mapping venue names to their source slides (DEPRECATED: use venue_attribution)
        venue_to_context: Optional dict mapping venue names to their slide-specific context (DEPRECATED: use venue_attribution)
        photo_urls: Optional list of photo URLs from TikTok photo post (first image used as thumbnail)
        venue_attribution: Optional dict with full attribution data per venue
            Format: {
                "VENUE_NAME": {
                    "primary_slide": int,
                    "contextual_slides": [int],
                    "all_slides": [int],
                    "ocr_content": {"slide_N": "text"},
                    "full_context": str
                }
            }
    """
    if venue_to_slide is None:
        venue_to_slide = {}
    if venue_to_context is None:
        venue_to_context = {}

    # NEW: Track if we have attribution data
    has_attribution = venue_attribution is not None and len(venue_attribution) > 0
    if has_attribution:
        print(f"✅ Using venue attribution data for enrichment ({len(venue_attribution)} venues)")
    
    # Combine caption and context_title for neighborhood extraction (title often contains location info)
    # Example: "The Cutest New Spot in Soho" -> extract "Soho" as neighborhood
    combined_text_for_neighborhood = f"{context_title} {caption}".strip() if context_title else caption
    
    places_extracted = []
    
    def enrich_and_fetch_photo(venue_name):
        """Enrich a single venue and fetch its photo - runs in parallel."""
        # Get canonical name, address, place_id, photos, neighborhood, and price_level from Google Maps (correct spelling)
        # Use cache to avoid redundant API calls
        # For MVP, bias results towards NYC to avoid confusion with non-NYC venues
        canonical_name, address, place_id, photos, neighborhood, price_level = get_place_info_from_google(venue_name, use_cache=True, location_hint="NYC")
        
        # Track business_status (will be set from Place Details API)
        business_status = None
        
        # Initialize place_types_from_google early (used for hotel detection and cuisine extraction)
        place_types_from_google = []
        
        # Use canonical name, but be careful about major changes
        # If canonical name is too different from original (e.g., "LEI" → "LES"), prefer original
        # Only use canonical name if it's a minor spelling correction or capitalization fix
        if canonical_name:
            original_lower = venue_name.lower().strip()
            canonical_lower = canonical_name.lower().strip()
            
            # Check if canonical name is substantially different (not just spelling/capitalization)
            # Examples of bad matches: "LEI wine bar" → "LES Wine Bar" (different venue)
            # Examples of good matches: "employees only" → "Employees Only" (same venue, capitalization)
            is_substantial_change = False
            
            # If names are completely different (no significant overlap), it's likely a wrong match
            if original_lower != canonical_lower:
                # Check if canonical name contains the original name (or vice versa)
                # If not, it might be a different venue
                if original_lower not in canonical_lower and canonical_lower not in original_lower:
                    # For very short names (3-4 chars), be very strict - they must be nearly identical
                    # Examples: "LEI" vs "LES" are different venues, not a spelling correction
                    if len(original_lower) <= 4 and len(canonical_lower) <= 4:
                        # For 3-4 char names, require at least 2/3 characters to match
                        matching_chars = sum(1 for a, b in zip(original_lower, canonical_lower) if a == b)
                        if matching_chars < max(len(original_lower), len(canonical_lower)) * 0.67:
                            is_substantial_change = True
                    elif len(original_lower) <= 5 and len(canonical_lower) <= 5:
                        # Short names (5 chars) that are different are likely wrong matches
                        if original_lower != canonical_lower:
                            is_substantial_change = True
                    # For longer names, check if they share at least 70% of characters
                    elif len(set(original_lower) & set(canonical_lower)) / max(len(set(original_lower)), len(set(canonical_lower))) < 0.5:
                        is_substantial_change = True
            
            if is_substantial_change:
                print(f"⚠️  Google Maps returned different venue '{canonical_name}' for '{venue_name}' - using original name")
                display_name = venue_name
            else:
                display_name = canonical_name
                if canonical_lower != original_lower:
                    print(f"✏️  Corrected spelling: '{venue_name}' → '{canonical_name}'")
        else:
            display_name = venue_name
        
        # NEW: Get attribution data if available
        source_slide = None
        slide_context = None
        venue_name_lower = venue_name.lower()

        if has_attribution:
            # Use venue_attribution for slide context and source
            attribution_found = False
            for key, attr in venue_attribution.items():
                if key.lower() == venue_name_lower:
                    slide_context = attr.get("full_context", "")
                    source_slide = f"slide_{attr.get('primary_slide', 1)}"

                    print(f"   📖 Enriching {venue_name} using attributed context:")
                    print(f"      Primary slide: {attr.get('primary_slide', 'N/A')}")
                    print(f"      Contextual slides: {attr.get('contextual_slides', [])}")
                    print(f"      Total slides: {attr.get('all_slides', [])}")
                    print(f"      Context length: {len(slide_context)} chars")
                    attribution_found = True
                    break
            
            if not attribution_found:
                print(f"   ⚠️ Attribution data exists but venue '{venue_name}' not found in attribution dict")
                print(f"      Available venues in attribution: {list(venue_attribution.keys())}")
        else:
            # DEPRECATED: Fallback to old separate dicts
            for key, value in venue_to_slide.items():
                if key.lower() == venue_name_lower:
                    source_slide = value
                    break

            for key, value in venue_to_context.items():
                if key.lower() == venue_name_lower:
                    slide_context = value
                    break
        if venue_attribution and venue_name.lower() in venue_attribution:
            attr = venue_attribution[venue_name.lower()]
            # Use full_context from attribution (already filtered to this venue's slides)
            venue_specific_context = attr.get("full_context", "")
            venue_specific_slides = attr.get("all_slides", [])
            print(f"   📖 Using venue attribution context for {display_name} ({len(venue_specific_context)} chars from slides {venue_specific_slides})")

        # CRITICAL: For hotels, ensure we have context even if filtered context is empty
        # Hotels might not have much OCR context (they're not restaurants), so use caption/transcript
        is_hotel = place_types_from_google and ("lodging" in place_types_from_google or any("hotel" in pt.lower() for pt in place_types_from_google))
        if is_hotel and (not slide_context or len(slide_context.strip()) < 50):
            # Use caption or transcript for hotels if slide_context is minimal
            hotel_context = "\n".join(x for x in [caption, transcript] if x and len(x.strip()) > 10)
            if hotel_context:
                print(f"   🏨 Hotel detected with minimal context - using caption/transcript ({len(hotel_context)} chars)")
                slide_context = hotel_context

        # Pass source_slide, slide_context, and all_venues to enrichment for slide-aware and venue-specific context
        intel = enrich_place_intel(display_name, transcript, ocr_text, caption, comments_text, source_slide=source_slide, slide_context=slide_context, all_venues=venues, venue_attribution=venue_attribution)

        # PRIORITY ORDER FOR NEIGHBORHOOD EXTRACTION:
        # 1. Google Maps Place Details API (most reliable - factual data with specific neighborhoods)
        # 2. Title/Caption extraction (but defer to Google if Google is more specific)
        # 3. Place name extraction (from parentheses like "(NOMAD)")
        # 4. Google Maps address parsing (rarely works but worth trying)
        # 5. NYC geography inference

        final_neighborhood = None
        text_extracted_neighborhood = None  # Store text-extracted neighborhood separately
        google_maps_neighborhood = None  # Store Google Maps neighborhood separately
        # place_types_from_google already initialized above
        latitude = None  # Store latitude for strict neighborhood extraction
        longitude = None  # Store longitude for strict neighborhood extraction
        country_code = None  # Store country code for NYC filtering
        country_name = None  # Store country name for NYC filtering

        # STEP 1: Extract neighborhood from title/caption (but don't finalize yet)
        # Use combined text (context_title + caption) for neighborhood extraction
        # Example: "The Cutest New Spot in Soho" or "Discovering the Newest Wine Bar in the Lower East Side"
        neighborhood_source_text = combined_text_for_neighborhood

        if neighborhood_source_text:
            print(f"   🔍 Extracting neighborhood from caption/title...")
            text_neighborhood = _extract_neighborhood_from_text(neighborhood_source_text)
            # Only store if it's a specific neighborhood (not a borough)
            if text_neighborhood and text_neighborhood not in ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]:
                text_extracted_neighborhood = text_neighborhood
                print(f"   📍 Found neighborhood from title/caption: {text_extracted_neighborhood}")
            elif text_neighborhood:
                print(f"   ⚠️ Skipping borough-level neighborhood '{text_neighborhood}' from title")
        
        # STEP 2: Get neighborhood from Place Details API (Google Maps - most reliable)
        if place_id:
            if not GOOGLE_API_KEY:
                print(f"   ⚠️ Skipping Place Details API - GOOGLE_API_KEY not set")
            else:
                try:
                    print(f"   🔍 Trying Place Details API for neighborhood info...")
                    r = requests.get(
                        "https://maps.googleapis.com/maps/api/place/details/json",
                        params={
                            "place_id": place_id,
                            "fields": "address_components,formatted_address,business_status,types,geometry",
                            "key": GOOGLE_API_KEY
                        },
                        timeout=10
                    )
                    r.raise_for_status()
                    details_data = r.json()
                    api_status = details_data.get("status")
                    
                    if api_status == "OK":
                        result = details_data.get("result", {})
                        address_components = result.get("address_components", [])
                        # Get business_status to check if permanently closed
                        business_status = result.get("business_status")
                        # Get lat/lon from geometry for strict neighborhood extraction
                        geometry = result.get("geometry", {})
                        location = geometry.get("location", {})
                        latitude = location.get("lat")
                        longitude = location.get("lng")
                        # Get place types from Google Maps (for cuisine categorization)
                        place_types_from_google = result.get("types", [])
                        if place_types_from_google:
                            print(f"   🏷️ Google Maps types: {', '.join(place_types_from_google[:10])}")
                        
                        # Check if venue is a hotel (for special context handling)
                        is_hotel = "lodging" in place_types_from_google or any("hotel" in pt.lower() for pt in place_types_from_google)
                        if is_hotel:
                            print(f"   🏨 Detected hotel/lodging venue: {display_name}")
                        # Also get formatted_address from Place Details (might be more accurate)
                        place_details_address = result.get("formatted_address")
                        if place_details_address and not address:
                            address = place_details_address
                            print(f"   📍 Using address from Place Details API: {address}")
                        # Look for neighborhood, sublocality, or locality in address components
                        # Priority: neighborhood > sublocality > sublocality_level_1 > locality
                        # Define generic locations to filter out
                        generic_locations = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "New York"]
                        
                        # Extract country code/name from address components FIRST (before neighborhood extraction)
                        country_code = None
                        country_name = None
                        for component in address_components:
                            types = component.get("types", [])
                            if "country" in types:
                                country_code = component.get("short_name", "")
                                country_name = component.get("long_name", "")
                                print(f"   🌍 Found country: {country_name} ({country_code})")
                                break
                        
                        # Now extract neighborhood from address components
                        for component in address_components:
                            types = component.get("types", [])
                            neighborhood_name = component.get("long_name")
                            if neighborhood_name:
                                # Skip generic locations immediately
                                if neighborhood_name in generic_locations:
                                    continue
                                
                                if "neighborhood" in types:
                                    google_maps_neighborhood = neighborhood_name
                                    print(f"   📍 Found neighborhood from Place Details (neighborhood): {google_maps_neighborhood}")
                                    break
                                elif "sublocality" in types and not google_maps_neighborhood:
                                    google_maps_neighborhood = neighborhood_name
                                    print(f"   📍 Found neighborhood from Place Details (sublocality): {google_maps_neighborhood}")
                                elif "sublocality_level_1" in types and not google_maps_neighborhood:
                                    google_maps_neighborhood = neighborhood_name
                                    print(f"   📍 Found neighborhood from Place Details (sublocality_level_1): {google_maps_neighborhood}")
                                elif "locality" in types and not google_maps_neighborhood:
                                    # Only use locality if it's not a borough name or generic "Manhattan"
                                    google_maps_neighborhood = neighborhood_name
                                    print(f"   📍 Found neighborhood from Place Details (locality): {google_maps_neighborhood}")
                        # Match Google Maps neighborhood against known neighborhoods list for consistency
                        # Define known neighborhoods list (used for matching)
                        known_neighborhoods = [
                            # Downtown / Below 14th
                            "Downtown", "Lower Manhattan",
                            "Lower East Side", "LES",
                            "East Village", "EV",
                            "Alphabet City",
                            "NoHo", "Noho",
                            "Nolita", "NoLita",
                            "SoHo", "Soho",
                            "Chinatown",
                            "Little Italy",
                            "Two Bridges",
                            "Tribeca", "TriBeCa",
                            "West Village",
                            "Greenwich Village",
                            "Hudson Square",
                            "Battery Park City",
                            "Financial District", "FiDi", "FIDI",
                            # Midtown-ish
                            "Koreatown", "K-Town", "KTown",
                            "Hell's Kitchen", "Hells Kitchen",
                            "Midtown West", "Theater District",
                            "Midtown East",
                            "Murray Hill",
                            "Gramercy",
                            "Flatiron",
                            "Kips Bay",
                            "Chelsea",
                            "Hudson Yards",
                            # Islands / Special Areas
                            "Roosevelt Island",
                            # Uptown
                            "Upper West Side", "UWS",
                            "Upper East Side", "UES",
                            "Harlem",
                            "East Harlem",
                            "Morningside Heights",
                            "Washington Heights",
                            "Inwood",
                            # Brooklyn - Waterfront / North Brooklyn
                            "Williamsburg",
                            "East Williamsburg",
                            "Greenpoint",
                            "Bushwick",
                            # Brooklyn - Brownstone Brooklyn
                            "Brooklyn Heights",
                            "DUMBO",
                            "Cobble Hill",
                            "Carroll Gardens",
                            "Boerum Hill",
                            "Gowanus",
                            "Park Slope",
                            "Prospect Heights",
                            "Fort Greene",
                            "Clinton Hill",
                            # Brooklyn - Further Out
                            "Bedford-Stuyvesant", "Bed-Stuy", "BedStuy",
                            "Crown Heights",
                            "Red Hook",
                            "Sunset Park",
                            "Bay Ridge",
                            # Queens
                            "Astoria",
                            "Long Island City", "LIC",
                            "Sunnyside",
                            "Jackson Heights",
                            "Elmhurst",
                            "Flushing",
                            "Forest Hills",
                            # Bronx
                            "Belmont", "Arthur Avenue",
                            "Mott Haven",
                            # Staten Island
                            "St. George", "St George",
                        ]
                        # Sort by length (longest first) to prioritize more specific matches
                        sorted_known = sorted(known_neighborhoods, key=len, reverse=True)
                        if google_maps_neighborhood:
                            google_neighborhood_lower = google_maps_neighborhood.lower()

                            # Try exact match first, then substring match
                            # But don't match generic locations like "Manhattan" to "Lower Manhattan"
                            generic_locations = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "New York"]
                            is_generic = google_neighborhood_lower in [g.lower() for g in generic_locations]

                            # If it's already generic, skip matching and try fallbacks
                            if is_generic:
                                print(f"   ⚠️ Place Details returned generic location '{google_maps_neighborhood}', will try other sources")
                                google_maps_neighborhood = None
                            else:
                                matched = False
                                for known_neighborhood in sorted_known:
                                    known_lower = known_neighborhood.lower()
                                    if known_lower == google_neighborhood_lower:
                                        google_maps_neighborhood = known_neighborhood
                                        print(f"   📍 Google Maps exact match to known neighborhood: {google_maps_neighborhood}")
                                        matched = True
                                        break
                                    elif known_lower in google_neighborhood_lower or google_neighborhood_lower in known_lower:
                                        # Use substring match if it's a significant portion (>= 4 chars)
                                        # This handles cases like "Lower East Side" matching "Lower East Side, Manhattan"
                                        if len(known_neighborhood) >= 4:
                                            google_maps_neighborhood = known_neighborhood
                                            print(f"   📍 Google Maps matched to known neighborhood: {google_maps_neighborhood}")
                                            matched = True
                                            break

                                # If no match found, keep the original neighborhood name (might be valid but not in our list)
                                if not matched:
                                    print(f"   ⚠️ Google Maps neighborhood '{google_maps_neighborhood}' not in known list, keeping as-is")
                    else:
                        # Handle non-OK API response statuses
                        # Handle non-OK API response statuses
                        error_message = details_data.get("error_message", "No error message provided")
                        
                        if api_status == "REQUEST_DENIED":
                            print(f"   ❌ Place Details API REQUEST_DENIED: {error_message}")
                            print(f"   ⚠️ This usually means:")
                            print(f"      - GOOGLE_API_KEY is missing or invalid")
                            print(f"      - Places API is not enabled for this API key")
                            print(f"      - API key restrictions are blocking the request")
                        elif api_status == "OVER_QUERY_LIMIT":
                            print(f"   ❌ Place Details API OVER_QUERY_LIMIT: {error_message}")
                            print(f"   ⚠️ Google Places API quota exceeded - will use fallback methods")
                        elif api_status == "INVALID_REQUEST":
                            print(f"   ❌ Place Details API INVALID_REQUEST: {error_message}")
                            print(f"   ⚠️ Invalid place_id or request parameters")
                        elif api_status == "ZERO_RESULTS":
                            print(f"   ⚠️ Place Details API ZERO_RESULTS: No data found for place_id")
                        elif api_status == "UNKNOWN_ERROR":
                            print(f"   ❌ Place Details API UNKNOWN_ERROR: {error_message}")
                            print(f"   ⚠️ Google server error - will try fallback methods")
                        else:
                            print(f"   ❌ Place Details API returned status '{api_status}': {error_message}")
                            print(f"   ⚠️ Will use fallback methods for neighborhood extraction")
                            
                except requests.exceptions.RequestException as e:
                    print(f"   ❌ Place Details API network error: {e}")
                    print(f"   ⚠️ Network request failed - will use fallback methods")
                except Exception as e:
                    print(f"   ⚠️ Place Details API failed for neighborhood: {e}")
                    import traceback
                    print(f"   📋 Place Details API traceback: {traceback.format_exc()[:300]}")
        else:
            print(f"   ⚠️ No place_id available for neighborhood extraction")

        # PRIORITY 2: Place name extraction (from parentheses like "(NOMAD)")
        if not final_neighborhood and display_name:
            paren_match = re.search(r'\(([^)]+)\)', display_name)
            if paren_match:
                paren_content = paren_match.group(1).strip()
                paren_lower = paren_content.lower()
                # Check if it matches a known neighborhood
                known_neighborhoods = [
                    "NoMad", "Nomad", "NOMAD", "NoHo", "Noho", "NOHO",
                    "SoHo", "Soho", "SOHO", "Nolita", "NoLita", "NOLITA",
                    "LES", "EV", "UWS", "UES", "FiDi", "FIDI",
                    "Lower East Side", "East Village", "West Village",
                    "Greenwich Village", "Upper West Side", "Upper East Side",
                    "Financial District", "Tribeca", "TriBeCa",
                    "Chelsea", "Flatiron", "Gramercy", "Midtown East", "Midtown West",
                    "Hell's Kitchen", "Hells Kitchen", "Koreatown", "K-Town",
                    "Williamsburg", "Greenpoint", "Bushwick", "DUMBO",
                    "Astoria", "Long Island City", "LIC",
                    "Roosevelt Island"
                ]
                for known in known_neighborhoods:
                    if known.lower() == paren_lower or known.lower() in paren_lower or paren_lower in known.lower():
                        final_neighborhood = known
                        print(f"   📍 Found neighborhood from place name (parentheses): {final_neighborhood}")
                        break

        # Photo priority: 1) TikTok slide photo, 2) Google Maps photo
        # Skip photo if permanently closed
        photo = None

        # Check if permanently closed - if so, skip photo fetching
        is_permanently_closed = business_status == "CLOSED_PERMANENTLY"
        if is_permanently_closed:
            print(f"   ⚠️ {display_name} is permanently closed - skipping photo fetch")
        
        # CRITICAL: Add "Permanently Closed" to good_to_know field if venue is permanently closed
        # This should happen even if Google Maps API fails to return other data
        if is_permanently_closed:
            # Update good_to_know in intel dict if it exists
            if "good_to_know" in intel:
                if intel["good_to_know"]:
                    # Prepend "Permanently Closed" if good_to_know already has content
                    intel["good_to_know"] = "Permanently Closed. " + intel["good_to_know"]
                else:
                    # Set to "Permanently Closed" if good_to_know is empty
                    intel["good_to_know"] = "Permanently Closed"
            else:
                # Create good_to_know field if it doesn't exist
                intel["good_to_know"] = "Permanently Closed"
            print(f"   ✅ Added 'Permanently Closed' to good_to_know for {display_name}")

        # Try to get photo from the specific slide this venue came from (only if not permanently closed)
        if source_slide and photo_urls:
            # Extract slide number from source_slide (e.g., "slide_2" -> 1 for 0-indexed)
            try:
                slide_num = int(source_slide.split('_')[1]) - 1  # Convert to 0-indexed
                if 0 <= slide_num < len(photo_urls):
                    photo = photo_urls[slide_num]
                    print(f"   📸 Using TikTok slide {slide_num + 1} photo for {display_name}")
            except (IndexError, ValueError) as e:
                print(f"   ⚠️ Failed to parse slide number from {source_slide}: {e}")

        # Fallback to Google Maps photo if no TikTok photo (skip if permanently closed)
        if not photo and not is_permanently_closed:
            photo = get_photo_url(display_name, place_id=place_id, photos=photos)
            if photo:
                print(f"   📸 Using Google Maps photo for {display_name}")
        
        # Additional fallback: Try searching by name with NYC if still no photo (skip if permanently closed)
        if not photo and not is_permanently_closed:
            print(f"   🔍 No photo found yet, trying search with NYC for {display_name}...")
            search_name = f"{display_name} NYC" if "NYC" not in display_name.upper() and "New York" not in display_name else display_name
            photo = get_photo_url(search_name, place_id=None, photos=None)
            if photo:
                print(f"   📸 Got photo via NYC search for {display_name}")
        
        # Last resort: Try original venue name if canonical name didn't work (skip if permanently closed)
        if not photo and display_name != venue_name and not is_permanently_closed:
            print(f"   🔍 Trying original venue name for photo: {venue_name}...")
            search_name = f"{venue_name} NYC" if "NYC" not in venue_name.upper() and "New York" not in venue_name else venue_name
            photo = get_photo_url(search_name, place_id=None, photos=None)
            if photo:
                print(f"   📸 Got photo via original name search for {venue_name}")

        # PRIORITY 4: Google Maps address parsing
        if not final_neighborhood and address:
            print(f"   🔍 Trying Google Maps address parsing for neighborhood...")
            parsed_neighborhood = _extract_neighborhood_from_address(address)
            if parsed_neighborhood:
                final_neighborhood = parsed_neighborhood
                print(f"   📍 Found neighborhood from address parsing: {final_neighborhood}")

        # PRIORITY 5: SMART NEIGHBORHOOD FALLBACK: Use NYC geography knowledge if neighborhood still missing
        if not final_neighborhood and address:
            print(f"   🔍 Trying NYC geography inference for neighborhood...")
            inferred_neighborhood = infer_nyc_neighborhood_from_address(address, display_name)
            if inferred_neighborhood:
                final_neighborhood = inferred_neighborhood
                print(f"   🧠 Inferred neighborhood from address: {final_neighborhood}")
        
        # PRIORITY 5.5: Try extracting from address components more directly
        if not final_neighborhood and address:
            # Try to find street names that indicate neighborhoods
            address_parts = address.split(',')
            for part in address_parts:
                part_clean = part.strip()
                # Check if any part matches a known neighborhood
                neighborhood_match = _extract_neighborhood_from_text(part_clean)
                if neighborhood_match and neighborhood_match not in ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]:
                    final_neighborhood = neighborhood_match
                    print(f"   📍 Found neighborhood from address component: {final_neighborhood}")
                    break

        # PRIORITY 6: Extract from venue name itself (e.g., "Soho Wine Bar" -> "SoHo")
        if not final_neighborhood and display_name:
            print(f"   🔍 Trying to extract neighborhood from venue name...")
            name_neighborhood = _extract_neighborhood_from_text(display_name)
            if name_neighborhood and name_neighborhood not in ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]:
                final_neighborhood = name_neighborhood
                print(f"   📍 Found neighborhood from venue name: {final_neighborhood}")
        
        # Final fallback: Extract borough from address if neighborhood still missing
        if not final_neighborhood and address:
            print(f"   🔍 Final fallback: Trying to extract borough from address...")
            address_lower = address.lower()
            boroughs = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]
            for borough in boroughs:
                if borough.lower() in address_lower:
                    final_neighborhood = borough
                    print(f"   📍 Using borough as neighborhood: {final_neighborhood}")
                    break
        
        
        # Final fallback: If we have an address but no neighborhood, try to extract from the full address string
        if not final_neighborhood and address:
            print(f"   🔍 Last resort: Parsing full address string for neighborhood...")
            # Try the full address as one string
            full_address_neighborhood = _extract_neighborhood_from_text(address)
            if full_address_neighborhood and full_address_neighborhood not in ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]:
                final_neighborhood = full_address_neighborhood
                print(f"   📍 Found neighborhood from full address: {final_neighborhood}")
            # Also try infer_nyc_neighborhood_from_address one more time with full address
            elif not final_neighborhood:
                inferred = infer_nyc_neighborhood_from_address(address, display_name)
                if inferred:
                    final_neighborhood = inferred
                    print(f"   📍 Inferred neighborhood from full address: {final_neighborhood}")
        
        # Log if still no neighborhood (but don't set to "NYC" - let frontend handle it)
        if not final_neighborhood:
            print(f"   ⚠️ Could not determine neighborhood for {display_name}")
            print(f"      Address: {address or 'None (Google API may have failed)'}")
            print(f"      Place ID: {place_id or 'None (Google API may have failed)'}")
            print(f"      Title/Caption text: {neighborhood_source_text[:100] if neighborhood_source_text else 'None'}")
            # If we have an address, try one more time to extract neighborhood from it
            if address:
                # Try splitting address and checking each part
                address_parts = address.replace(',', ' ').split()
                for part in address_parts:
                    part_clean = part.strip('.,;')
                    if len(part_clean) > 3:  # Skip short parts
                        test_neighborhood = _extract_neighborhood_from_text(part_clean)
                        if test_neighborhood and test_neighborhood not in ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "New York", "NY"]:
                            final_neighborhood = test_neighborhood
                            print(f"   📍 Found neighborhood from address part '{part_clean}': {final_neighborhood}")
                            break
            # If Google API completely failed, this is a critical issue
            if not address and not place_id:
                print(f"   ❌ CRITICAL: Google Places API failed - no address or place_id available")
                print(f"      Check Google API key configuration and API status")

        # Convert price_level to dollar signs
        price = price_level_to_dollars(price_level)

        # Add cuisine type from Google Maps to vibe_tags (if available)
        # CRITICAL: Only add cuisine tags if place is actually a restaurant (PRIMARY type, not secondary)
        vibe_tags = intel.get("vibe_tags", []).copy()  # Make a copy to avoid modifying original
        if place_types_from_google:
            # Google Maps returns types in order of relevance, so first few types are most important
            primary_types = place_types_from_google[:3]  # Check first 3 types only
            
            # Check if PRIMARY type is a restaurant (exclude cafes, bars, bakeries even if they have restaurant types)
            # CRITICAL: If primary type is cafe/bar/bakery, don't add cuisine tags even if there's a restaurant type
            excluded_primary_types = {"cafe", "bar", "bakery", "store", "establishment", "point_of_interest", "food"}
            
            # First check: if any primary type is cafe/bar/bakery, exclude cuisine tags
            has_excluded_primary = any(pt in excluded_primary_types for pt in primary_types)
            
            is_restaurant = False
            if not has_excluded_primary:
                # Check if any primary type is explicitly a restaurant
                for pt in primary_types:
                    if pt in ["restaurant", "meal_delivery", "meal_takeaway"]:
                        is_restaurant = True
                        break
                    elif pt.endswith("_restaurant"):
                        is_restaurant = True
                        break
            
            # Only add cuisine tags for actual restaurants (not cafes/bars with secondary restaurant types)
            if is_restaurant:
                # Extract cuisine from Google Maps place types (ONLY check primary types)
                # Comprehensive cuisine map covering 50+ cuisine types
                cuisine_map = {
                    "restaurant": None,  # Too generic
                    "bar": None,  # Too generic
                    "cafe": None,  # Too generic
                    "meal_takeaway": None,  # Too generic
                    "food": None,  # Too generic
                    "establishment": None,  # Too generic
                    "point_of_interest": None,  # Too generic
                    # Asian Cuisines
                    "indian_restaurant": "Indian",
                    "chinese_restaurant": "Chinese",
                    "japanese_restaurant": "Japanese",
                    "thai_restaurant": "Thai",
                    "korean_restaurant": "Korean",
                    "vietnamese_restaurant": "Vietnamese",
                    "filipino_restaurant": "Filipino",
                    "indonesian_restaurant": "Indonesian",
                    "malaysian_restaurant": "Malaysian",
                    "singaporean_restaurant": "Singaporean",
                    "pakistani_restaurant": "Pakistani",
                    "bangladeshi_restaurant": "Bangladeshi",
                    "nepalese_restaurant": "Nepalese",
                    "burmese_restaurant": "Burmese",
                    "cambodian_restaurant": "Cambodian",
                    "laotian_restaurant": "Laotian",
                    "asian_restaurant": "Asian",
                    "asian_fusion_restaurant": "Asian Fusion",
                    "sushi_restaurant": "Sushi",
                    "ramen_restaurant": "Ramen",
                    # European Cuisines
                    "italian_restaurant": "Italian",
                    "french_restaurant": "French",
                    "greek_restaurant": "Greek",
                    "spanish_restaurant": "Spanish",
                    "portuguese_restaurant": "Portuguese",
                    "german_restaurant": "German",
                    "british_restaurant": "British",
                    "irish_restaurant": "Irish",
                    "polish_restaurant": "Polish",
                    "russian_restaurant": "Russian",
                    "ukrainian_restaurant": "Ukrainian",
                    "scandinavian_restaurant": "Scandinavian",
                    "dutch_restaurant": "Dutch",
                    "belgian_restaurant": "Belgian",
                    "swiss_restaurant": "Swiss",
                    "austrian_restaurant": "Austrian",
                    # Mediterranean & Middle Eastern
                    "mediterranean_restaurant": "Mediterranean",
                    "middle_eastern_restaurant": "Middle Eastern",
                    "turkish_restaurant": "Turkish",
                    "lebanese_restaurant": "Lebanese",
                    "syrian_restaurant": "Syrian",
                    "israeli_restaurant": "Israeli",
                    "persian_restaurant": "Persian",
                    "moroccan_restaurant": "Moroccan",
                    "egyptian_restaurant": "Egyptian",
                    # Latin American & Caribbean
                    "mexican_restaurant": "Mexican",
                    "brazilian_restaurant": "Brazilian",
                    "peruvian_restaurant": "Peruvian",
                    "argentinian_restaurant": "Argentinian",
                    "colombian_restaurant": "Colombian",
                    "cuban_restaurant": "Cuban",
                    "venezuelan_restaurant": "Venezuelan",
                    "caribbean_restaurant": "Caribbean",
                    "jamaican_restaurant": "Jamaican",
                    "latin_american_restaurant": "Latin American",
                    # African Cuisines
                    "african_restaurant": "African",
                    "ethiopian_restaurant": "Ethiopian",
                    "nigerian_restaurant": "Nigerian",
                    "south_african_restaurant": "South African",
                    # American & Regional
                    "american_restaurant": "American",
                    "southern_restaurant": "Southern",
                    "cajun_restaurant": "Cajun",
                    "creole_restaurant": "Creole",
                    "soul_food_restaurant": "Soul Food",
                    "tex_mex_restaurant": "Tex-Mex",
                    # Specialty Categories
                    "seafood_restaurant": "Seafood",
                    "steak_house": "Steakhouse",
                    "pizza_restaurant": "Pizza",
                    "hamburger_restaurant": "Burgers",
                    "sandwich_shop": "Sandwiches",
                    "barbecue_restaurant": "BBQ",
                    "fast_food_restaurant": "Fast Food",
                    "breakfast_restaurant": "Breakfast",
                    "brunch_restaurant": "Brunch",
                    "vegetarian_restaurant": "Vegetarian",
                    "vegan_restaurant": "Vegan",
                    "fusion_restaurant": "Fusion",
                }
                google_cuisine = None
                # CRITICAL: Only check PRIMARY types for cuisine (not all types)
                for place_type in primary_types:
                    if place_type in cuisine_map and cuisine_map[place_type]:
                        google_cuisine = cuisine_map[place_type]
                        break
                if google_cuisine and google_cuisine not in vibe_tags:
                    vibe_tags.append(google_cuisine)
                    print(f"   ✅ Added Google Maps cuisine tag: {google_cuisine} (from primary types: {primary_types})")
            else:
                print(f"   ⚠️ Skipping cuisine tag - place is not a restaurant (primary types: {primary_types})")

        # Use strict neighborhood extraction function (PRIORITY: static overrides > lat/lon > address)
        strict_neighborhood = get_nyc_neighborhood_strict(
            venue_name=display_name,
            address=address,
            latitude=latitude,
            longitude=longitude
        )

        # Use strict neighborhood if available, otherwise fall back to extracted neighborhood
        final_neighborhood_to_use = strict_neighborhood if strict_neighborhood != "Unknown" else final_neighborhood

        # CRITICAL FIX: If still no neighborhood found, try to extract from address one more time
        if not final_neighborhood_to_use or final_neighborhood_to_use == "Unknown":
            if address:
                # Last attempt: Try to find any borough or well-known area in the address
                address_lower = address.lower()
                # Check for boroughs
                if "manhattan" in address_lower or "new york, ny" in address_lower:
                    final_neighborhood_to_use = "Manhattan"
                elif "brooklyn" in address_lower:
                    final_neighborhood_to_use = "Brooklyn"
                elif "queens" in address_lower:
                    final_neighborhood_to_use = "Queens"
                elif "bronx" in address_lower:
                    final_neighborhood_to_use = "Bronx"
                elif "staten island" in address_lower:
                    final_neighborhood_to_use = "Staten Island"
                else:
                    # If still no neighborhood, use "NYC" as final fallback
                    final_neighborhood_to_use = "NYC"
                print(f"   ⚠️ Using final fallback neighborhood for {display_name}: {final_neighborhood_to_use}")
            else:
                # No address available, use "NYC" as absolute fallback
                final_neighborhood_to_use = "NYC"
                print(f"   ⚠️ No address available for {display_name}, using 'NYC' as neighborhood")

        # CRITICAL FIX: Ensure photo is set to placeholder if missing or empty
        if not photo or photo.strip() == "":
            photo = "https://via.placeholder.com/600x400?text=No+Photo"
            print(f"   ⚠️ No photo found for {display_name}, using placeholder")

        # Store country code/name for NYC filtering
        country_for_filtering = country_code or country_name or ""

        # Create search query with neighborhood for more accurate Google Maps results
        search_query = f"{display_name} {final_neighborhood_to_use}".strip() if final_neighborhood_to_use else display_name

        place_data = {
            "name": display_name,  # Use canonical name from Google Maps
            "maps_url": f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}",
            "photo_url": photo,  # CRITICAL: Ensure photo URL is always set
            "description": intel.get("summary", ""),
            "vibe_tags": vibe_tags,  # Venue-specific vibe tags extracted from filtered context
            "vibe_keywords": intel.get("vibe_keywords", []),  # Add vibe keywords explicitly
            "address": address,  # Also get address while we're at it
            "neighborhood": final_neighborhood_to_use,  # Use strict neighborhood extraction (static overrides > lat/lon > address)
            "price": price,  # Price level from Google Maps ($, $$, $$$, $$$$)
            "place_id": place_id,  # Store place_id for deduplication
            "country": country_for_filtering,  # Store country for NYC filtering
            **{k: v for k, v in intel.items() if k not in ["summary", "vibe_tags", "vibe_keywords"]}
        }
        
        # CRITICAL: Verify photo URL is set (should never be empty at this point)
        if not place_data.get("photo_url") or place_data["photo_url"].strip() == "":
            place_data["photo_url"] = "https://via.placeholder.com/600x400?text=No+Photo"
            print(f"   ⚠️ WARNING: Photo URL was empty for {display_name}, set to placeholder")
        
        # CRITICAL: Ensure vibe_tags are venue-specific - log for debugging
        if vibe_tags:
            print(f"   🏷️ Extracted {len(vibe_tags)} vibe tags for {display_name}: {vibe_tags}")
        else:
            print(f"   ⚠️ No vibe tags extracted for {display_name}")
        
        # CRITICAL: Log all fields being returned to debug missing data
        print(f"   📊 Returning place_data for {display_name}:")
        print(f"      - neighborhood: {place_data.get('neighborhood', 'MISSING')}")
        print(f"      - vibe_tags: {len(place_data.get('vibe_tags', []))} tags")
        print(f"      - description: {len(place_data.get('description', ''))} chars")
        print(f"      - photo_url: {'SET' if place_data.get('photo_url') else 'MISSING'}")
        print(f"      - address: {place_data.get('address', 'MISSING')[:50] if place_data.get('address') else 'MISSING'}")
        
        return place_data
    
    # Run enrichment and photo fetching in parallel (max 5 concurrent to avoid rate limits)
    if len(venues) > 1:
        print(f"⚡ Enriching {len(venues)} places in parallel...")
    
    # Track place_ids to deduplicate venues that Google Maps identifies as the same place
    seen_place_ids = {}  # place_id -> place_data (keep best match)
    seen_venue_names = {}  # venue_name_lower -> place_data (for address-based deduplication)
    
    # Track slide order for each venue to preserve order in final output
    venue_to_order = {}
    if venue_to_slide:
        def get_slide_number(slide_key):
            try:
                return int(slide_key.split('_')[1])
            except (IndexError, ValueError):
                return 999  # Put unknown slides at the end
        
        for venue in venues:
            slide_key = venue_to_slide.get(venue)
            if slide_key:
                venue_to_order[venue.lower()] = get_slide_number(slide_key)
            else:
                venue_to_order[venue.lower()] = 999  # Put venues without slide info at the end
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_venue = {
            executor.submit(enrich_and_fetch_photo, v): v 
            for v in venues
        }
        
        for future in as_completed(future_to_venue):
            venue_name = future_to_venue[future]
            try:
                place_data = future.result()
                # CRITICAL: Log place_data before merge to debug missing fields
                print(f"   📦 place_data BEFORE merge for {venue_name}:")
                print(f"      - neighborhood: {place_data.get('neighborhood', 'MISSING')}")
                print(f"      - vibe_tags: {len(place_data.get('vibe_tags', []))} tags")
                print(f"      - description: {len(place_data.get('description', ''))} chars")
                print(f"      - photo_url: {'SET' if place_data.get('photo_url') else 'MISSING'}")
                
                # Merge with cached places - pass video summary
                merged_place = merge_place_with_cache(place_data, url, username, context_title)
                
                # CRITICAL: Log merged_place after merge to debug missing fields
                print(f"   📦 merged_place AFTER merge for {venue_name}:")
                print(f"      - neighborhood: {merged_place.get('neighborhood', 'MISSING')}")
                print(f"      - vibe_tags: {len(merged_place.get('vibe_tags', []))} tags")
                print(f"      - description: {len(merged_place.get('description', ''))} chars")
                print(f"      - photo_url: {'SET' if merged_place.get('photo_url') else 'MISSING'}")
                
                # Deduplicate by place_id (if Google Maps returned same place_id, it's the same venue)
                place_id = merged_place.get("place_id") or merged_place.get("address")  # Use address as fallback
                place_name_lower = merged_place.get("name", "").lower().strip()
                
                # Check for duplicates
                is_duplicate = False
                
                # Method 1: Check by place_id (most reliable)
                if place_id and place_id in seen_place_ids:
                    existing = seen_place_ids[place_id]
                    print(f"🔄 Duplicate detected by place_id: '{merged_place.get('name')}' matches '{existing.get('name')}' (place_id: {place_id[:20]}...)")
                    # Keep the one with more complete data or better name match
                    if len(merged_place.get("description", "")) > len(existing.get("description", "")):
                        seen_place_ids[place_id] = merged_place
                    is_duplicate = True
                
                # Method 2: Check by venue name (case-insensitive, fuzzy match) and address
                if not is_duplicate:
                    for seen_name_lower, seen_place_data in seen_venue_names.items():
                        seen_name = seen_place_data.get("name", "")
                        # Check if names are very similar (likely same venue with OCR/spelling variations)
                        if place_name_lower and seen_name_lower:
                            # If one name contains the other (e.g., "lingo greenpoint" vs "lingo")
                            if place_name_lower in seen_name_lower or seen_name_lower in place_name_lower:
                                # Check if they're not just generic words
                                if len(place_name_lower) > 4 and len(seen_name_lower) > 4:
                                    print(f"🔄 Duplicate detected by name similarity: '{merged_place.get('name')}' similar to '{seen_name}'")
                                    # Keep the one with more complete data or better name
                                    if len(merged_place.get("description", "")) > len(seen_place_data.get("description", "")):
                                        seen_venue_names[place_name_lower] = merged_place
                                        # Remove old entry
                                        if seen_name_lower in seen_venue_names:
                                            del seen_venue_names[seen_name_lower]
                                    is_duplicate = True
                                    break
                            # Check for garbled OCR variations (e.g., "LA TETE DOR" vs "la tete d'or")
                            # Normalize both names by removing spaces, apostrophes, and special chars for comparison
                            normalized_current = re.sub(r'[\s\'\-\.]', '', place_name_lower)
                            normalized_seen = re.sub(r'[\s\'\-\.]', '', seen_name_lower)
                            # If normalized versions are very similar (80%+ match), they're likely the same venue
                            if len(normalized_current) > 5 and len(normalized_seen) > 5:
                                # Calculate similarity
                                matching_chars = sum(1 for a, b in zip(normalized_current, normalized_seen) if a == b)
                                similarity = matching_chars / max(len(normalized_current), len(normalized_seen))
                                if similarity >= 0.8:  # 80% similarity threshold
                                    print(f"🔄 Duplicate detected by garbled OCR similarity: '{merged_place.get('name')}' ({similarity:.0%} match) similar to '{seen_name}'")
                                    # Prefer the version with apostrophes/spaces (more likely correct)
                                    prefer_current = ("'" in merged_place.get("name", "") or " " in merged_place.get("name", "")) and not ("'" in seen_name or " " in seen_name)
                                    prefer_seen = ("'" in seen_name or " " in seen_name) and not ("'" in merged_place.get("name", "") or " " in merged_place.get("name", ""))
                                    if prefer_current or len(merged_place.get("description", "")) > len(seen_place_data.get("description", "")):
                                        seen_venue_names[place_name_lower] = merged_place
                                        if seen_name_lower in seen_venue_names:
                                            del seen_venue_names[seen_name_lower]
                                        # Also update places_extracted if already added
                                        for i, place in enumerate(places_extracted):
                                            if place.get("name", "").lower() == seen_name_lower:
                                                places_extracted[i] = merged_place
                                                break
                                    is_duplicate = True
                                    break
                            # Check character similarity for short names
                            elif len(place_name_lower) <= 6 and len(seen_name_lower) <= 6:
                                # For short names, check if they're very similar
                                matching_chars = sum(1 for a, b in zip(place_name_lower, seen_name_lower) if a == b)
                                if matching_chars >= min(len(place_name_lower), len(seen_name_lower)) * 0.8:
                                    print(f"🔄 Duplicate detected by name similarity: '{merged_place.get('name')}' similar to '{seen_name}'")
                                    if len(merged_place.get("description", "")) > len(seen_place_data.get("description", "")):
                                        seen_venue_names[place_name_lower] = merged_place
                                        if seen_name_lower in seen_venue_names:
                                            del seen_venue_names[seen_name_lower]
                                    is_duplicate = True
                                    break
                            # Check if addresses match (same address = same venue, even if names differ)
                            # This catches cases like "Walco" vs "Tucci" where OCR misread the name
                            elif merged_place.get("address") and seen_place_data.get("address"):
                                existing_address = seen_place_data.get("address", "").lower()
                                current_address = merged_place.get("address", "").lower()
                                # If addresses are very similar (same street address), likely same venue
                                if existing_address and current_address:
                                    # Extract street address (before first comma)
                                    existing_street = existing_address.split(',')[0].strip()
                                    current_street = current_address.split(',')[0].strip()
                                    # Check if street addresses match (allowing for minor variations)
                                    if existing_street == current_street and len(existing_street) > 10:
                                        print(f"🔄 Duplicate detected by address match: '{merged_place.get('name')}' and '{seen_name}' have same address")
                                        # Prefer the name that appears more complete or is more common
                                        # "Tucci" is likely correct vs "Walco" (OCR error)
                                        prefer_new = (
                                            len(merged_place.get("description", "")) > len(seen_place_data.get("description", "")) or
                                            len(merged_place.get("name", "")) > len(seen_name) or
                                            "tucci" in place_name_lower  # Prefer known correct name
                                        )
                                        if prefer_new:
                                            # Replace with better name/data
                                            seen_venue_names[place_name_lower] = merged_place
                                            # Remove old entry
                                            if seen_name_lower in seen_venue_names:
                                                del seen_venue_names[seen_name_lower]
                                            # Also update places_extracted if already added
                                            for i, place in enumerate(places_extracted):
                                                if place.get("name", "").lower() == seen_name_lower:
                                                    places_extracted[i] = merged_place
                                                    break
                                        is_duplicate = True
                                        break
                                    # Also check if addresses are very similar (fuzzy match)
                                    elif len(existing_street) > 10 and len(current_street) > 10:
                                        # Check if they share most of the address
                                        words_existing = set(existing_street.split())
                                        words_current = set(current_street.split())
                                        common_words = words_existing & words_current
                                        if len(common_words) >= min(len(words_existing), len(words_current)) * 0.7:
                                            print(f"🔄 Duplicate detected by similar address: '{merged_place.get('name')}' and '{seen_name}' have similar addresses")
                                            prefer_new = (
                                                len(merged_place.get("description", "")) > len(seen_place_data.get("description", "")) or
                                                "tucci" in place_name_lower
                                            )
                                            if prefer_new:
                                                seen_venue_names[place_name_lower] = merged_place
                                                if seen_name_lower in seen_venue_names:
                                                    del seen_venue_names[seen_name_lower]
                                                for i, place in enumerate(places_extracted):
                                                    if place.get("name", "").lower() == seen_name_lower:
                                                        places_extracted[i] = merged_place
                                                        break
                                            is_duplicate = True
                                            break
                
                if not is_duplicate:
                    # Store original slide order for sorting later
                    if venue_name.lower() in venue_to_order:
                        merged_place["_slide_order"] = venue_to_order[venue_name.lower()]
                    else:
                        merged_place["_slide_order"] = 999  # Default to end if no slide info
                places_extracted.append(merged_place)
                if place_id:
                    seen_place_ids[place_id] = merged_place
                if place_name_lower:
                    seen_venue_names[place_name_lower] = merged_place
                if len(venues) > 1:
                    print(f"✅ Enriched: {venue_name} (slide order: {merged_place.get('_slide_order', 'unknown')})")
                else:
                    print(f"⏭️  Skipped duplicate: {venue_name}")
                    
            except Exception as e:
                print(f"⚠️ Failed to enrich {venue_name}: {e}")
                import traceback
                print(f"   📋 Traceback: {traceback.format_exc()[:500]}")
                # Add basic place data even if enrichment fails
                # Try to get at least address from Google Maps
                try:
                    canonical_name, address, place_id, photos, neighborhood, price_level = get_place_info_from_google(venue_name, use_cache=True, location_hint="NYC")
                    if not canonical_name:
                        canonical_name = venue_name
                except Exception as ge:
                    print(f"   ⚠️ Failed to get Google Maps info: {ge}")
                    canonical_name = venue_name
                    address = ""
                    place_id = None
                    photos = []
                    neighborhood = ""
                    price_level = None
                
                # Try to get photo even if enrichment failed
                photo_url_fallback = "https://via.placeholder.com/600x400?text=No+Photo"
                if place_id:
                    try:
                        photo_fallback = get_photo_url(canonical_name, place_id=place_id, photos=photos)
                        if photo_fallback:
                            photo_url_fallback = photo_fallback
                    except Exception as pe:
                        print(f"   ⚠️ Failed to get photo fallback: {pe}")

                # Create search query with neighborhood for more accurate Google Maps results
                fallback_neighborhood = neighborhood or "NYC"
                search_query_fallback = f"{canonical_name} {fallback_neighborhood}".strip()

                place_data = {
                    "name": canonical_name,
                    "maps_url": f"https://www.google.com/maps/search/{search_query_fallback.replace(' ', '+')}",
                    "photo_url": photo_url_fallback,
                    "address": address,
                    "neighborhood": fallback_neighborhood,
                    "place_id": place_id,
                    "price": price_level,
                    "summary": "",
                    "description": "",
                    "when_to_go": "",
                    "vibe": "",
                    "must_try": "",
                    "must_try_field": "must_try",
                    "good_to_know": "",
                    "features": "",
                    "specials": "",
                    "comments_summary": "",
                    "vibe_tags": [],
                }
                merged_place = merge_place_with_cache(place_data, url, username, context_title)
                # Check for duplicates before adding
                place_name_lower = venue_name.lower().strip()
                is_duplicate = any(place_name_lower in seen.lower() or seen.lower() in place_name_lower
                                  for seen in seen_venue_names.keys() if len(place_name_lower) > 4 and len(seen) > 4)
                if not is_duplicate:
                    places_extracted.append(merged_place)
                    seen_venue_names[place_name_lower] = merged_place
    
    # Filter to keep only NYC venues (MVP requirement)
    from location_filters import is_nyc_venue

    nyc_places = []
    for place in places_extracted:
        # Get country from place data if available (set during enrichment)
        address = place.get("address") or ""
        country = place.get("country", "")  # Set during enrichment from Google Maps API
        if not country and address:
            # Try to extract country from end of address (usually last component)
            address_parts = [p.strip() for p in address.split(",")]
            if len(address_parts) > 0:
                last_part = address_parts[-1].lower()
                # Check if last part is a country name or code
                if last_part not in ["usa", "us", "united states", "united states of america"]:
                    country = address_parts[-1]
        
        is_nyc, reason = is_nyc_venue(
            address,
            place.get("neighborhood") or "",
            country
        )

        if is_nyc:
            nyc_places.append(place)
            print(f"   ✅ Kept NYC venue: {place.get('name')} - {reason}")
        else:
            print(f"   ❌ Filtered out non-NYC venue: {place.get('name')} - {reason}")

    if len(nyc_places) < len(places_extracted):
        print(f"🗽 NYC Filter: Kept {len(nyc_places)}/{len(places_extracted)} venues")
    
    # Sort by slide order to preserve order of appearance in slides
    # Use _slide_order field if available (more reliable), otherwise fall back to venue_to_order
    def get_venue_order(place):
        # First try _slide_order field (set during enrichment)
        if "_slide_order" in place:
            return place["_slide_order"]
        # Fall back to venue_to_order mapping
        if venue_to_order:
            venue_name = place.get("name", "").lower()
            return venue_to_order.get(venue_name, 999)
        return 999  # Default to end
    
    nyc_places.sort(key=get_venue_order)
    print(f"📋 Sorted {len(nyc_places)} places by slide order")

    return nyc_places

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
    """Extract photo post data from TikTok URL using mobile API first, then fallback to Playwright."""
    # Clean URL - remove query parameters
    clean_url = url.split('?')[0] if '?' in url else url
    if clean_url != url:
        print(f"🔗 Cleaned URL: {url} -> {clean_url}")
        url = clean_url
    
    try:
        print(f"🌐 Extracting TikTok post data from: {url}")
        
        # Try TikTok mobile API FIRST (avoids bot detection)
        try:
            api_data = get_tiktok_post_data(url)
            
            if api_data.get("type") == "photo":
                print("✅ Successfully extracted photo post via TikTok mobile API")
                return {
                    "photos": api_data.get("photo_urls", []),
                    "caption": api_data.get("caption", ""),
                    "author": api_data.get("author", ""),
                }
            elif api_data.get("type") == "video":
                print("⚠️ API returned video post, not photo post")
                # Return empty for photo extraction (video will be handled elsewhere)
                return {
                    "photos": [],
                    "caption": api_data.get("caption", ""),
                    "author": api_data.get("author", ""),
                }
        except Exception as api_error:
            print(f"⚠️ TikTok mobile API failed: {api_error}")
            print("   Trying TikTok API16 fallback...")
            
            # Try TikTok API16 fallback before Playwright
            try:
                api16_data = get_tiktok_media(url)
                if api16_data and (api16_data.get("photo_urls") or api16_data.get("video_url")):
                    print("✅ TikTok API16 fallback succeeded!")
                    # Convert API16 format to our format
                    return {
                        "photos": api16_data.get("photo_urls", []),
                        "caption": api16_data.get("caption", ""),
                        "author": "",
                        "source": "tiktok_api16",
                    }
                else:
                    print("⚠️ TikTok API16 fallback returned no media")
            except Exception as api16_error:
                print(f"⚠️ TikTok API16 fallback failed: {api16_error}")
            
            print("   Falling back to Playwright HTML scraping...")
        
        # Fallback to Playwright HTML scraping if API and SnapTik both fail
        print(f"🌐 Fetching HTML from: {url}")
        html = None
        
        # Try Playwright (for dynamic content) - fallback only
        playwright_used = False
        try:
            from playwright.sync_api import sync_playwright
            print("🎭 Using Playwright to render dynamic content...")
            with sync_playwright() as p:
                # Launch browser with stealth settings to avoid bot detection
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                    ]
                )
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='en-US',
                    timezone_id='America/New_York',
                )
                # Remove webdriver property
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    // Override permissions
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications' ?
                            Promise.resolve({ state: Notification.permission }) :
                            originalQuery(parameters)
                    );
                """)
                page = context.new_page()
                # Set a longer timeout for TikTok to load
                page.set_default_timeout(60000)
                
                # Set extra headers to look like a real browser
                page.set_extra_http_headers({
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Cache-Control': 'max-age=0',
                })
                
                print(f"   Navigating to {url}...")
                # Use networkidle to wait for all resources to load
                page.goto(url, wait_until="networkidle", timeout=60000)
                print("   Waiting for dynamic content to load...")
                # Wait longer for TikTok's JavaScript to load the caption (photo posts need more time)
                page.wait_for_timeout(10000)  # Increased wait time
                
                # Try to wait for actual content, not generic page
                try:
                    # Wait for TikTok content to load (not generic page)
                    page.wait_for_selector('article, [data-e2e="browse-video-desc"], [data-e2e="video-desc"]', timeout=10000)
                    print("   ✅ Found TikTok content elements")
                except:
                    print("   ⚠️ Content elements not found, but continuing...")
                    page.wait_for_timeout(5000)  # Extra wait
                
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
                context.close()
                browser.close()
                playwright_used = True
                print(f"✅ Rendered HTML with Playwright ({len(html)} chars)")
                
                # Check if we got the real caption (not generic TikTok page)
                if "Download TikTok Lite" in html or ("Make Your Day" in html and len(html) < 150000):
                    print("⚠️ Warning: Still seeing generic TikTok page - TikTok may be blocking automated access")
                    print("   Checking for actual content indicators...")
                    # Check if we have actual TikTok content
                    has_real_content = (
                        '__UNIVERSAL_DATA__' in html or 
                        'SIGI_STATE' in html or 
                        'ItemModule' in html or
                        'imagePost' in html or
                        'ImageList' in html
                    )
                    if not has_real_content:
                        print("   ⚠️ No TikTok data structures found - TikTok is likely blocking access")
                        print("   💡 Tip: TikTok may require manual browser access or cookies")
                    else:
                        print("   ✅ Found TikTok data structures despite generic title")
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
        
        # Method 1: Try window.__UNIVERSAL_DATA__ with explicit ItemModule parsing
        match = re.search(r'window\.__UNIVERSAL_DATA__\s*=\s*({.+?});', html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                print("✅ Found window.__UNIVERSAL_DATA__")
                
                # Explicitly check for ItemModule (as per user requirements)
                if "ItemModule" in data and isinstance(data["ItemModule"], dict):
                    print("   Found ItemModule - extracting first post...")
                    item_module = data["ItemModule"]
                    # Get the first post from ItemModule
                    first_post_key = list(item_module.keys())[0] if item_module else None
                    if first_post_key:
                        first_post = item_module[first_post_key]
                        
                        # Extract images array (as per user requirements)
                        if "images" in first_post and isinstance(first_post["images"], list):
                            for img in first_post["images"]:
                                if isinstance(img, dict):
                                    # Try url field
                                    if "url" in img and isinstance(img["url"], str):
                                        photos.append(img["url"])
                                    # Try urlList array
                                    elif "urlList" in img and isinstance(img["urlList"], list) and len(img["urlList"]) > 0:
                                        photos.append(img["urlList"][0])
                                elif isinstance(img, str) and img.startswith("http"):
                                    photos.append(img)
                            print(f"   ✅ Extracted {len(photos)} images from ItemModule.images[]")
                        
                        # Extract desc (caption) from first post
                        if "desc" in first_post and first_post["desc"]:
                            caption = str(first_post["desc"])
                            print(f"   ✅ Extracted caption from ItemModule: {caption[:100]}...")
                
                # Fallback to recursive search if ItemModule parsing didn't work
                if not photos or not caption:
                    print("   ItemModule parsing incomplete, trying recursive search...")
                found_photos, found_caption = find_in_data(data)
                photos.extend(found_photos)
                if found_caption and not caption:
                    caption = found_caption
                    print(f"   Recursive search found {len(found_photos)} photos, caption: {found_caption[:50] if found_caption else 'None'}...")
                else:
                    print(f"   ✅ ItemModule extraction complete: {len(photos)} photos, caption: {caption[:50] if caption else 'None'}...")
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
        
        # If still no photos found, try TikTok API16 as final fallback
        if not photos:
            print("⚠️ All HTML extraction methods failed - trying TikTok API16 fallback...")
            try:
                api16_data = get_tiktok_media(url)
                if api16_data and api16_data.get("photo_urls"):
                    print("✅ TikTok API16 fallback succeeded!")
                    photos = api16_data.get("photo_urls", [])
                    if api16_data.get("caption") and not caption:
                        caption = api16_data.get("caption", "")
                elif api16_data and api16_data.get("caption") and not caption:
                    # Even if no photos, use caption from API16
                    caption = api16_data.get("caption", "")
            except Exception as api16_error:
                print(f"⚠️ TikTok API16 fallback failed: {api16_error}")
        
        return {"photos": photos, "caption": caption}
    except Exception as e:
        print(f"❌ Error extracting photo post: {e}")
        import traceback
        print(traceback.format_exc())
        
        # Try TikTok API16 as last resort even if there's an error
        try:
            print("🔄 Trying TikTok API16 as last resort...")
            api16_data = get_tiktok_media(url)
            if api16_data and (api16_data.get("photo_urls") or api16_data.get("caption")):
                print("✅ TikTok API16 succeeded as last resort!")
                return {
                    "photos": api16_data.get("photo_urls", []),
                    "caption": api16_data.get("caption", ""),
                }
        except:
            pass
        
        return {"photos": [], "caption": ""}


@app.route("/api/healthz", methods=["GET"])
def healthz():
    """
    Detailed health check endpoint with environment variable diagnostics.
    This endpoint does API connectivity tests but should not block health checks.
    API test failures don't fail the health check - they're informational only.
    """
    try:
        # First check if app is ready
        if not is_app_ready():
            return jsonify({
                "status": "initializing",
                "service": "planit-backend",
                "message": "App is still initializing"
            }), 503
        
        # Check critical environment variables
        openai_key_set = bool(os.getenv("OPENAI_API_KEY"))
        google_key_set = bool(os.getenv("GOOGLE_API_KEY"))
        
        # Check if required modules are available
        modules_available = {
            "ocr_processor": False,
            "slideshow_extractor": False,
            "geocoding_service": False,
        }
        try:
            import ocr_processor
            modules_available["ocr_processor"] = True
        except ImportError:
            pass
        try:
            import slideshow_extractor
            modules_available["slideshow_extractor"] = True
        except ImportError:
            pass
        try:
            import geocoding_service
            modules_available["geocoding_service"] = True
        except ImportError:
            pass
        
        # Test OpenAI API connectivity if key is set (with timeout protection)
        openai_test = None
        if openai_key_set:
            try:
                client = get_openai_client()
                # Quick test call with short timeout - don't block health check
                test_response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "Say 'ok'"}],
                    max_tokens=5,
                    timeout=3  # Reduced from 5 to 3 seconds
                )
                openai_test = "success" if test_response.choices else "failed"
            except Exception as e:
                # Don't fail health check if API test fails - just report it
                openai_test = f"error: {str(e)[:100]}"
        
        # Test Google Places API connectivity if key is set (with timeout protection)
        google_api_test = None
        if google_key_set:
            try:
                # Test with a simple Place Details API call using a known NYC place_id
                # Using "ChIJN1t_tDeuEmsRUsoyG83frY4" (Google's NYC office) as test
                test_place_id = "ChIJN1t_tDeuEmsRUsoyG83frY4"
                import requests
                # Reduced timeout to prevent blocking health checks
                r = requests.get(
                    "https://maps.googleapis.com/maps/api/place/details/json",
                    params={
                        "place_id": test_place_id,
                        "fields": "name",
                        "key": GOOGLE_API_KEY
                    },
                    timeout=3  # Reduced from 5 to 3 seconds
                )
                r.raise_for_status()
                test_data = r.json()
                test_status = test_data.get("status")
                
                if test_status == "OK":
                    google_api_test = "success"
                elif test_status == "REQUEST_DENIED":
                    error_msg = test_data.get("error_message", "No error message")
                    google_api_test = f"REQUEST_DENIED: {error_msg[:100]}"
                elif test_status == "OVER_QUERY_LIMIT":
                    google_api_test = "OVER_QUERY_LIMIT: API quota exceeded"
                else:
                    error_msg = test_data.get("error_message", "Unknown error")
                    google_api_test = f"{test_status}: {error_msg[:100]}"
            except requests.exceptions.Timeout:
                # Timeout is OK - don't fail health check
                google_api_test = "timeout: API test timed out (non-critical)"
            except requests.exceptions.RequestException as e:
                # Network errors are OK - don't fail health check
                google_api_test = f"network_error: {str(e)[:100]}"
            except Exception as e:
                # Any other error is OK - don't fail health check
                google_api_test = f"error: {str(e)[:100]}"
        
        # Always return 200 OK - API test failures are informational, not health check failures
        return jsonify({
            "status": "ok",
            "ready": True,
            "openai_api_key_set": openai_key_set,
            "google_api_key_set": google_key_set,
            "openai_api_test": openai_test,
            "google_api_test": google_api_test,
            "ocr_available": OCR_AVAILABLE,
            "advanced_ocr_available": ADVANCED_OCR_AVAILABLE,
            "modules_available": modules_available,
        }), 200
    except Exception as e:
        # Log error but return 200 - don't fail health check due to diagnostics errors
        print(f"⚠️ Health check diagnostics error: {e}")
        import traceback
        return jsonify({
            "status": "ok",
            "ready": True,
            "message": "Health check diagnostics failed but app is running",
            "error": str(e)[:200]
        }), 200

@app.route("/api/status/<extraction_id>", methods=["GET"])
def get_extraction_status(extraction_id):
    """Get status updates for an ongoing extraction."""
    try:
        status_messages = get_status(extraction_id)
        
        # Auto-cleanup: if status is empty or very old, return empty
        # This helps stop unnecessary polling
        if not status_messages:
            return jsonify({
                "extraction_id": extraction_id,
                "messages": [],
                "completed": True
            }), 200
        
        # Check if last message indicates completion
        last_message = status_messages[-1].get("message", "")
        is_complete = "Finalizing" in last_message or "Complete" in last_message
        
        return jsonify({
            "extraction_id": extraction_id,
            "messages": status_messages,
            "completed": is_complete
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/extract", methods=["POST"])
def extract_api():
    """
    NEW EXTRACTION PRIORITY LOGIC:
    1. If voice (non-song): Use voice extraction
    2. If photo slideshow: Use OCR extraction on each slide
    3. Use OCR sparingly (sample frames, not every frame)
    4. Stream results: Show places as they arrive, then supplement with OCR
    """
    url = request.json.get("video_url")

    if not url:
        return jsonify({
            "error": "No video URL provided",
            "message": "Please provide a valid TikTok video URL."
        }), 400

    # Create extraction ID for status tracking
    extraction_id = create_extraction_id()
    update_status(extraction_id, "Analyzing TikTok link...")

    # ===== AUTO-DETECT SLIDESHOW =====
    is_slideshow = "/photo/" in url.lower()

    # ===== PHOTO POST HANDLING =====
    # Detect if the TikTok URL contains /photo/ and use robust extractor
    if is_slideshow:
        print("📸 Detected TikTok photo post - using robust extractor (API16 -> SnapTik -> Playwright)")
        update_status(extraction_id, "Detected photo slideshow...")
        
        # Clean URL - remove query parameters that might interfere
        clean_url = url.split('?')[0] if '?' in url else url
        if clean_url != url:
            print(f"🔗 Cleaned URL: {url} -> {clean_url}")

        update_status(extraction_id, "Downloading slideshow images...")
        # Use robust extractor (API16 -> SnapTik -> Playwright)
        media = robust_tiktok_extractor(clean_url)
        
        print(f"📊 Robust extractor result: source={media.get('source')}, photos={len(media.get('photo_urls', []))}, caption_len={len(media.get('caption', ''))}")
        
        if media.get("photo_urls"):
            photo_urls = media["photo_urls"]
            caption = media.get("caption", "").strip()
            
            print(f"✅ Robust extractor succeeded via {media['source']}: {len(photo_urls)} photos")
        else:
            # Fallback to existing extract_photo_post if robust extractor didn't find photos
            print(f"⚠️ Robust extractor returned no photos (source: {media.get('source')}) - trying extract_photo_post as final fallback...")
            try:
                photo_data = extract_photo_post(clean_url)
                
                if photo_data and photo_data.get("photos"):
                    photo_urls = photo_data["photos"]
                    caption = photo_data.get("caption", "").strip()
                    print(f"✅ extract_photo_post succeeded: {len(photo_urls)} photos")
                else:
                    print("⚠️ extract_photo_post also returned no photos")
                    # Check if we at least got a caption
                    if photo_data and photo_data.get("caption"):
                        caption = photo_data.get("caption", "").strip()
                        print(f"📝 Got caption from extract_photo_post but no photos: {caption[:100]}...")
                        # Return error but include caption info
                        return jsonify({
                            "error": "Photo extraction failed",
                            "message": f"Unable to extract photos from the TikTok photo post. Found caption ({len(caption)} chars) but no images. The post may be private, deleted, or the URL may be invalid.",
                            "video_url": url,
                            "caption_found": True,
                        }), 200
                    else:
                        print("⚠️ All extraction methods failed - no photos and no caption")
                        return jsonify({
                            "error": "Photo extraction failed",
                            "message": "Unable to extract photos or caption from the TikTok photo post. The post may be private, deleted, or the URL may be invalid. Please verify the URL is correct and the post is publicly accessible.",
                            "video_url": url,
                            "extraction_source": media.get("source", "unknown"),
                        }), 200
            except Exception as e:
                print(f"❌ extract_photo_post exception: {e}")
                import traceback
                print(traceback.format_exc())
                return jsonify({
                    "error": "Photo extraction failed",
                    "message": f"Error during photo extraction: {str(e)}. The post may be private or the URL may be invalid.",
                    "video_url": url,
                    "extraction_source": media.get("source", "unknown"),
                }), 200
        
        if photo_urls:
            print(f"✅ Extracted {len(photo_urls)} photos, caption: {caption[:100] if caption else 'None'}...")
            print(f"📸 Photo URLs: {[url[:50] + '...' if len(url) > 50 else url for url in photo_urls]}")
            update_status(extraction_id, f"Scanning {len(photo_urls)} images for text...")

            # Use new advanced OCR pipeline for slideshow extraction
            ocr_text = ""
            slides_with_attribution = []  # NEW: Store slide attribution data

            if ADVANCED_OCR_AVAILABLE and len(photo_urls) > 0:
                print(f"🔍 Processing {len(photo_urls)} images with advanced OCR pipeline...")
                print(f"   📋 Will process ALL {len(photo_urls)} images including the last one")
                update_status(extraction_id, "Reading text from images...")
                try:
                    # Use the new high-quality OCR processor on slideshow images
                    # CRITICAL: Process images in the exact order they appear in photo_urls
                    # This ensures slide numbers match the actual order of images in the TikTok slideshow
                    image_sources = photo_urls  # Process ALL images (no limit)
                    print(f"   🔍 Processing {len(image_sources)} images in order: {[f'Image {i+1}' for i in range(len(image_sources))]}")
                    print(f"   📋 Image order preserved from TikTok: image 1 → image {len(image_sources)}")

                    # NEW: Request attribution data from OCR
                    print(f"🔍 DEBUG: Calling extract_text_from_slideshow with return_attribution=True")
                    ocr_result = extract_text_from_slideshow(image_sources, return_attribution=True)

                    # CRITICAL DEBUG: Log what type was returned
                    print(f"🔍 DEBUG: OCR result type: {type(ocr_result)}")
                    if isinstance(ocr_result, dict):
                        print(f"🔍 DEBUG: OCR returned dict with keys: {ocr_result.keys()}")
                        ocr_text = ocr_result["formatted_text"]
                        slides_with_attribution = ocr_result["slides_with_attribution"]
                        print(f"🔍 DEBUG: slides_with_attribution length: {len(slides_with_attribution)}")
                        print(f"🔍 DEBUG: First 2 slides attribution: {slides_with_attribution[:2] if len(slides_with_attribution) >= 2 else slides_with_attribution}")
                        print(f"✅ Advanced OCR pipeline extracted {len(ocr_text)} chars from {len(image_sources)} slides with attribution")
                        print(f"✅ Got {len(slides_with_attribution)} slides with attribution data")
                    else:
                        # Fallback for backward compatibility
                        print(f"⚠️ DEBUG: OCR returned string instead of dict! Falling back to legacy mode.")
                        print(f"⚠️ DEBUG: String length: {len(ocr_result) if isinstance(ocr_result, str) else 'N/A'}")
                        ocr_text = ocr_result
                        print(f"⚠️ WARNING: slides_with_attribution will be empty (OCR didn't return dict)")
                    print(f"✅ Advanced OCR pipeline extracted {len(ocr_text)} chars from {len(image_sources)} slides")
                    if ocr_text:
                        print(f"📝 OCR preview: {ocr_text[:200]}...")
                        # Check if last slide text is present
                        if "SLIDE" in ocr_text:
                            slide_count = ocr_text.count("SLIDE")
                            print(f"   📊 Found text from {slide_count} slides (expected {len(image_sources)} slides)")
                            if slide_count < len(image_sources):
                                print(f"   ⚠️ WARNING: Only {slide_count} slides have text, but {len(image_sources)} images were processed")
                                print(f"   ⚠️ Last slide may not have been OCR'd successfully")
                except Exception as e:
                    print(f"⚠️ Advanced OCR pipeline failed: {e}")
                    print(f"   Falling back to legacy OCR method...")
                    # Fall back to legacy OCR
                    ocr_text = ""
            
            # If advanced OCR not available or failed, use legacy method
            if not ocr_text and OCR_AVAILABLE:
                tmpdir = tempfile.mkdtemp()
                try:
                    num_images = len(photo_urls)  # Process ALL images (no limit)
                    print(f"🔍 Processing {num_images} images with legacy OCR...")
                    print(f"   📋 Will process ALL {num_images} images including the last one (image {num_images})")
                    
                    for i, img_url in enumerate(photo_urls):
                        try:
                            if OCR_AVAILABLE:
                                is_last = (i == len(photo_urls) - 1)
                                slide_marker = " (LAST SLIDE)" if is_last else ""
                                print(f"🔍 Running OCR on photo {i+1}/{num_images}{slide_marker}...")
                                photo_ocr = run_ocr_on_image(img_url)
                                if photo_ocr and len(photo_ocr.strip()) > 3:
                                    ocr_text += photo_ocr + " "
                                    print(f"✅ OCR extracted text from photo {i+1}{slide_marker} ({len(photo_ocr)} chars): {photo_ocr[:150]}...")
                                else:
                                    print(f'⚠️ OCR found no text in photo {i+1}{slide_marker}')
                                    if is_last:
                                        print(f"   ⚠️ WARNING: Last slide ({i+1}) has no text - venue name might be missing!")
                            else:
                                print(f"⚠️ OCR not available - skipping photo {i+1}")
                        except Exception as e:
                            is_last = (i == len(photo_urls) - 1)
                            slide_marker = " (LAST SLIDE)" if is_last else ""
                            print(f"⚠️ Failed to process photo {i+1}{slide_marker}: {e}")
                            if is_last:
                                print(f"   ⚠️ ERROR: Failed to process last slide - venue name might be missing!")
                            continue
            
                    ocr_text = ocr_text.strip()
                finally:
                    try:
                        shutil.rmtree(tmpdir, ignore_errors=True)
                    except:
                        pass
                
                ocr_text = ocr_text.strip()
                print(f"📊 Total OCR text extracted: {len(ocr_text)} chars")
                if ocr_text:
                    print(f"📝 OCR text preview: {ocr_text[:300]}...")
                    # Check if last slide text is present
                    if "SLIDE" in ocr_text:
                        slide_count = ocr_text.count("SLIDE")
                        print(f"   📊 Found text from {slide_count} slides (expected {len(photo_urls)} slides)")
                        if slide_count < len(photo_urls):
                            print(f"   ⚠️ WARNING: Only {slide_count} slides have text, but {len(photo_urls)} images were processed")
                            print(f"   ⚠️ Last slide may not have been OCR'd successfully")
                    # Check for common last slide patterns
                    ocr_lower = ocr_text.lower()
                    if "last" in ocr_lower or len(photo_urls) > 0:
                        last_slide_marker = f"SLIDE {len(photo_urls)}"
                        if last_slide_marker in ocr_text:
                            print(f"   ✅ Last slide ({last_slide_marker}) text found in OCR")
                        else:
                            print(f"   ⚠️ Last slide marker ({last_slide_marker}) not found in OCR - checking if text is present anyway...")
                else:
                    print("⚠️ No OCR text extracted from any images")
                    print(f"   ⚠️ This means venue names from slides (including last slide) won't be extracted!")
            
            # Combine OCR + caption text using weighted formula
            # OCR prioritized: (ocr * 1.4) + (caption * 1.2) = photo-mode specific weights
            if ocr_text and caption:
                # Weight OCR more heavily than caption for photo posts
                combined_text = f"{ocr_text}\n{caption} {caption}"  # OCR gets more weight
                print(f"📊 Weighted combination: OCR ({len(ocr_text)} chars) prioritized over caption ({len(caption)} chars)")
                print(f"   📋 Full OCR text being sent to GPT (check for last slide content):")
                print(f"   {ocr_text[:500]}..." if len(ocr_text) > 500 else f"   {ocr_text}")
            else:
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
            print(f"📝 Caption preview: {caption[:200] if caption else 'None'}...")
            print(f"📝 OCR preview: {ocr_text[:200] if ocr_text else 'None'}...")
            
            # Pass combined OCR + caption through existing GPT-based place extraction function
            print(f"🤖 Extracting venues from photo post using GPT...")
            update_status(extraction_id, "Identifying venues and locations...")
            transcript = ""  # No audio for photo posts
            comments_text = ""
            print(f"   Input to GPT: transcript={len(transcript)} chars, ocr={len(ocr_text)} chars, caption={len(caption)} chars, comments={len(comments_text)} chars")
            try:
                # NEW: Pass slides_with_attribution to enable explicit attribution tracking
                result = extract_places_and_context(transcript, ocr_text, caption, comments_text, slides_with_attribution=slides_with_attribution)

                # Handle both 4-tuple and 5-tuple returns
                venue_attribution = None
                if len(result) == 5:
                    venues, context_title, venue_to_slide, venue_to_context, venue_attribution = result
                    print(f"✅ Received venue attribution data for {len(venue_attribution)} venues")
                else:
                    venues, context_title, venue_to_slide, venue_to_context = result

                print(f"🤖 GPT returned {len(venues)} venues: {venues}")
                print(f"🤖 GPT returned title: {context_title}")
                venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
                print(f"✅ After filtering: {len(venues)} venues remain: {venues}")
            except Exception as extract_error:
                print(f"❌ extract_places_and_context failed: {extract_error}")
                import traceback
                print(traceback.format_exc())
                venues = []
                context_title = caption or "TikTok Photo Post"
                venue_to_slide = {}
                venue_to_context = {}
                venue_attribution = None
            update_status(extraction_id, f"Found {len(venues)} venues...")
            
            # Build response with same JSON structure as video extraction
            data = {
                "video_url": url,
                "summary_title": context_title or caption or "TikTok Photo Post",
                "context_summary": context_title or caption or "TikTok Photo Post",
                "places_extracted": [],
                "preview_image": photo_urls[0] if photo_urls else None,  # NEW: Include first image as preview
                "source_type": "photo_slideshow",  # NEW: Identify source type
                "photo_urls": photo_urls,  # Include photo URLs in response
                "caption_extracted": caption,  # Include actual caption for debugging
                "ocr_text": ocr_text,  # Include OCR text for debugging/testing
                "transcript": transcript,  # Include transcript (empty for photo posts)
                "_debug_attribution": {
                    "slides_with_attribution_count": len(slides_with_attribution) if slides_with_attribution else 0,
                    "venue_attribution_count": len(venue_attribution) if venue_attribution else 0,
                    "venue_attribution_is_none": venue_attribution is None
                }
            }
            
            # Enrich places if any were found
            if venues:
                print(f"🌟 Enriching {len(venues)} places with Google Maps data...")
                update_status(extraction_id, f"Mapping {len(venues)} venues with details...")
                username = extract_username_from_url(url)
                places_extracted = enrich_places_parallel(
                    venues, transcript, ocr_text, caption, comments_text,
                    url, username, context_title, venue_to_slide=venue_to_slide, venue_to_context=venue_to_context, photo_urls=photo_urls,
                    venue_attribution=venue_attribution  # NEW: Pass attribution data
                )
                print(f"✅ Enriched {len(places_extracted)} places successfully")
                update_status(extraction_id, "Finalizing results...")
                data["places_extracted"] = places_extracted
                data["venue_contexts"] = venue_to_context if venue_to_context else {}  # Include all extracted context for each venue
                # Mark as complete - frontend will stop polling
                update_status(extraction_id, "Complete")
            else:
                print(f"⚠️ No venues found by GPT extraction")
                print(f"   This could mean:")
                print(f"   - The caption/OCR text doesn't contain venue names")
                print(f"   - GPT couldn't identify venues in the text")
                print(f"   - The text was too short or unclear")
                print(f"   - GPT API call failed (check logs above for errors)")
                # Check OpenAI API key
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    print(f"   ⚠️ CRITICAL: OPENAI_API_KEY environment variable is NOT SET!")
                    data["error"] = "No venues found in this video. OpenAI API key is not configured. Please check Render environment variables."
                    data["warning"] = "OpenAI API key is not configured. Please check Render environment variables."
                else:
                    print(f"   ✅ OPENAI_API_KEY is set (first 10 chars: {api_key[:10]}...)")
                    # Check if GPT extraction actually ran
                    if not ocr_text and not caption:
                        data["error"] = "No venues found in this video. No extractable text found (no caption or OCR text)."
                    else:
                        data["error"] = "No venues found in this video. The video might not mention specific venue names, or they couldn't be extracted."
                data["places_extracted"] = []
                data["debug_info"] = {
                    "has_content": bool(ocr_text or caption),
                    "openai_key_set": bool(api_key),
                    "ocr_available": OCR_AVAILABLE,
                    "advanced_ocr_available": ADVANCED_OCR_AVAILABLE,
                    "content_lengths": {
                        "ocr_text": len(ocr_text) if ocr_text else 0,
                        "caption": len(caption) if caption else 0,
                    },
                    "caption_preview": caption[:200] if caption else None,
                    "ocr_preview": ocr_text[:200] if ocr_text else None,
                }

            # Add extraction_id to response
            data["extraction_id"] = extraction_id

            # Cache the result
            vid = get_tiktok_id(url)
            if vid:
                cache = load_cache()
                cache[vid] = data
                save_cache(cache)

            # Don't clear status immediately - let frontend finish polling first
            # Status will be cleared after a timeout or when frontend stops polling
            # clear_status(extraction_id)  # Commented out - let frontend control cleanup

            return jsonify(data), 200
        
        # If HTML extraction failed, try to use caption if we got one
        if photo_data and photo_data.get("caption") and not photo_data.get("photos"):
            caption = photo_data.get("caption", "").strip()
            print(f"📝 HTML extraction found caption but no photos: {caption[:100]}...")
            print("📝 Attempting venue extraction from caption only...")
            
            # Extract venues from caption only
            transcript = ""
            ocr_text = ""
            comments_text = ""
            result = extract_places_and_context(transcript, ocr_text, caption, comments_text)
            # Handle both 4-tuple and 5-tuple returns
            if len(result) == 5:
                venues, context_title, venue_to_slide, venue_to_context, _venue_attribution = result
            else:
                venues, context_title, venue_to_slide, venue_to_context = result
            venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
            
            # Build response
            data = {
                "video_url": url,
                "summary_title": context_title or caption or "TikTok Photo Post",
                "context_summary": context_title or caption or "TikTok Photo Post",
                "places_extracted": [],
                "photo_urls": [],  # No photos found
                "caption_extracted": caption,  # Include actual caption for debugging
                "extraction_id": extraction_id,  # Add extraction_id for status polling
            }
            
            if venues:
                username = extract_username_from_url(url)
                places_extracted = enrich_places_parallel(
                    venues, transcript, ocr_text, caption, comments_text,
                    url, username, context_title, venue_to_slide=venue_to_slide, venue_to_context=venue_to_context
                )
                data["places_extracted"] = places_extracted
                data["venue_contexts"] = venue_to_context if venue_to_context else {}  # Include all extracted context for each venue
            else:
                data["places_extracted"] = []
                data["venue_contexts"] = {}  # No venues, so no contexts

            # Cache the result
            vid = get_tiktok_id(url)
            if vid:
                cache = load_cache()
                cache[vid] = data
                save_cache(cache)
            
            return jsonify(data), 200
        
        # If no caption either, fall through to yt-dlp (though it likely won't work)
        print("📸 HTML extraction failed - no caption found, trying yt-dlp (may fail)...")
    
    # ===== VIDEO POST HANDLING =====
    # Continue with normal video processing (works for both videos and photo posts via yt-dlp fallback)
    
    # Photo URLs are now supported with OCR fallback - no need to block them
    
    # Check if this is a photo post that reached here - yt-dlp doesn't support photo posts
    is_photo_post = "/photo/" in url.lower()
    
    vid = get_tiktok_id(url)
    print(f"\n🟦 Extracting TikTok: {url}")

    # Check for cache bypass flag (useful for testing)
    bypass_cache = request.json.get("bypass_cache", False) or request.args.get("bypass_cache", "false").lower() == "true"
    if bypass_cache:
        print("🔄 Cache bypass enabled - will re-extract even if cached")

    cache = load_cache()
    if vid and vid in cache and not bypass_cache:
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

    # Try TikTok API16 first for video posts (before yt-dlp)
    api_video_url = None
    api_caption = ""
    if not is_photo_post:
        print("🎥 Video post detected - trying TikTok API16 first...")
        try:
            api16_data = get_tiktok_media(url)
            if api16_data:
                api_video_url = api16_data.get("video_url")
                api_caption = api16_data.get("caption", "").strip()
                if api_video_url:
                    print(f"✅ Got video URL from API16: {api_video_url[:100]}...")
                if api_caption:
                    print(f"✅ Got caption from API16 ({len(api_caption)} chars): {api_caption[:200]}...")
        except Exception as e:
            print(f"⚠️ TikTok API16 failed for video: {e}")
            # Try mobile API as fallback
            try:
                api_data = get_tiktok_post_data(url)
                if api_data.get("type") == "video":
                    api_video_url = api_data.get("video_url")
                    api_caption = api_data.get("caption", "").strip()
                    if api_video_url:
                        print(f"✅ Got video URL from mobile API: {api_video_url[:100]}...")
                    if api_caption:
                        print(f"✅ Got caption from mobile API ({len(api_caption)} chars): {api_caption[:200]}...")
            except Exception as e2:
                print(f"⚠️ TikTok mobile API also failed: {e2}")
            # Continue with yt-dlp fallback
    
    # Try API/HTML extraction for photo posts (before yt-dlp)
    html_caption = ""
    if is_photo_post:
        print("📸 Photo post detected - trying mobile API/HTML extraction for caption...")
        try:
            clean_url = url.split('?')[0] if '?' in url else url
            photo_data_html = extract_photo_post(clean_url)
            if photo_data_html:
                html_caption = photo_data_html.get("caption", "").strip()
                if html_caption:
                    print(f"✅ Got caption from API/HTML ({len(html_caption)} chars): {html_caption[:200]}...")
                else:
                    print(f"⚠️ API/HTML extraction found no caption")
                if photo_data_html.get("photos"):
                    print(f"✅ Also found {len(photo_data_html.get('photos', []))} photos")
        except Exception as e:
            print(f"⚠️ API/HTML extraction failed: {e}")
            import traceback
            print(traceback.format_exc())

    try:
        update_status(extraction_id, "Downloading video...")
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
        
        # Extract caption from multiple possible fields (prioritize API caption if available)
        caption = (
            api_caption or  # API caption takes priority
            html_caption or  # HTML caption for photo posts
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
                    update_status(extraction_id, f"Scanning {len(photo_urls)} images for text...")
                    for i, photo_url in enumerate(photo_urls):  # Process ALL images
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
                update_status(extraction_id, "Identifying venues and locations...")
                result = extract_places_and_context(transcript, ocr_text, caption, comments_text)
                # Handle both 4-tuple and 5-tuple returns
                if len(result) == 5:
                    venues, context_title, venue_to_slide, venue_to_context, _venue_attribution = result
                else:
                    venues, context_title, venue_to_slide, venue_to_context = result
                venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
                update_status(extraction_id, f"Found {len(venues)} venues...")
                
                data = {
                    "video_url": url,
                    "summary_title": context_title or caption or "No venues found",
                    "context_summary": context_title or caption or "No venues found",
                    "places_extracted": [],
                    "extraction_id": extraction_id,  # Add extraction_id for status polling
                    "ocr_text": ocr_text,  # Include OCR text for debugging/testing
                    "transcript": transcript,  # Include transcript for debugging/testing
                    "venue_contexts": venue_to_context if venue_to_context else {}  # Include all extracted context for each venue
                }
                
                if venues:
                    update_status(extraction_id, f"Mapping {len(venues)} venues with details...")
                    username = extract_username_from_url(url)
                    places_extracted = enrich_places_parallel(
                        venues, transcript, ocr_text, caption, comments_text,
                        url, username, context_title, venue_to_slide=venue_to_slide, venue_to_context=venue_to_context
                    )
                    data["places_extracted"] = places_extracted
                    update_status(extraction_id, "Finalizing results...")
                    update_status(extraction_id, "Complete")

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
        update_status(extraction_id, "Extracting audio...")
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
            update_status(extraction_id, "Scanning video for text...")
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
            update_status(extraction_id, "Transcribing audio...")
            transcript = transcribe_audio(audio_path)
            print(f"✅ Transcript: {len(transcript)} chars")
            
            # Clean up audio file immediately after transcription
            if audio_path != video_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    print("🗑️ Cleaned up audio file")
                except:
                    pass
            
            # ========== NEW PRIORITY LOGIC ==========
            # Priority 1: If there's good speech (non-music), use transcript only
            # Priority 2: Only run OCR if no transcript or if it's a slideshow
            # Priority 3: Use OCR sparingly - sample frames, not every frame
            
            transcript_length = len(transcript) if transcript else 0
            
            # Check if this is a slideshow (multiple slides with text)
            caption = meta.get("description", "") or meta.get("title", "") if meta else ""
            is_slideshow = "/photo/" in url.lower() or "_is_slideshow" in meta
            
            if is_slideshow:
                print("📸 SLIDESHOW DETECTED - OCR will be primary extraction method")
                # For slideshows, OCR is essential - run it on multiple slides
                update_status(extraction_id, "Scanning video for text...")
                ocr_text = extract_ocr_text(video_path, sample_rate=1)  # Sample every frame
                print(f"✅ Slideshow OCR: {len(ocr_text)} chars extracted")
            elif transcript_length > 50 and not "music" in transcript.lower():
                # Check if transcript actually contains venue-like content
                # If transcript is just generic words or noise, prioritize OCR
                transcript_lower = transcript.lower()
                has_venue_indicators = any(word in transcript_lower for word in ["restaurant", "bar", "cafe", "lounge", "place", "spot", "venue", "nyc", "manhattan", "brooklyn"])
                
                if has_venue_indicators:
                    # Transcript seems useful - run OCR with light sampling as backup
                    print(f"✅ GOOD TRANSCRIPT ({transcript_length} chars) - Running OCR anyway (video may have text overlays)")
                    update_status(extraction_id, "Scanning video for text...")
                    ocr_text = extract_ocr_text(video_path, sample_rate=0.3)  # Light sampling (30% of frames) since we have transcript
                    print(f"✅ Sampled OCR: {len(ocr_text)} chars extracted")
                    print("   Note: Transcript will be prioritized, but OCR available as backup")
                else:
                    # Transcript doesn't seem to contain venue info - prioritize OCR
                    print(f"⚠️ TRANSCRIPT EXISTS ({transcript_length} chars) but doesn't contain venue indicators - Prioritizing OCR")
                    print(f"   Transcript preview: {transcript[:100]}...")
                    update_status(extraction_id, "Scanning video for text...")
                    ocr_text = extract_ocr_text(video_path, sample_rate=0.5)  # More sampling since transcript isn't useful
                    print(f"✅ Sampled OCR: {len(ocr_text)} chars extracted")
                    print("   Note: OCR prioritized over transcript for venue extraction")
            else:
                # No transcript or very short - run OCR with sampling
                print(f"⚠️ LIMITED SPEECH ({transcript_length} chars) - Running sampled OCR")
                update_status(extraction_id, "Scanning video for text...")
                ocr_text = extract_ocr_text(video_path, sample_rate=0.5)  # Sample 50% of frames
                print(f"✅ Sampled OCR: {len(ocr_text)} chars extracted")
            
        if ocr_text:
                print(f"📝 OCR preview: {ocr_text[:200]}...")
        else:
                if not transcript:
                    print("⚠️ No transcript AND no OCR text - extraction will use caption only")

            
        # Clean up video file immediately after processing
        if os.path.exists(video_path):
            try:
                os.remove(video_path)
                print("🗑️ Cleaned up video file")
                gc.collect()
            except:
                pass

        update_status(extraction_id, "Identifying venues and locations...")
        result = extract_places_and_context(transcript, ocr_text, caption, comments_text)
        # Handle both 4-tuple and 5-tuple returns
        if len(result) == 5:
            venues, context_title, venue_to_slide, venue_to_context, _venue_attribution = result
        else:
            venues, context_title, venue_to_slide, venue_to_context = result

        # Filter out any remaining placeholder-like venues
        venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
        update_status(extraction_id, f"Found {len(venues)} venues...")
        
        if not venues:
            print("⚠️ No valid venues extracted from video")
            # Check if we had any content to analyze
            has_content = bool(transcript or ocr_text or caption or comments_text)
            warning_msg = ""
            
            # Check OpenAI API key
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                error_msg = "No venues found in this video. OpenAI API key is not configured. Please check Render environment variables."
                warning_msg = " OpenAI API key is not configured. Please set OPENAI_API_KEY environment variable on Render."
            elif not transcript and not ocr_text:
                error_msg = "No venues found in this video. This appears to be a slideshow/image-only video with no audio. OCR is needed to extract text from images."
                warning_msg = " This appears to be a slideshow/image-only video with no audio. OCR is needed to extract text from images, but tesseract may not be available on Render."
            elif not has_content:
                error_msg = "No venues found in this video. No extractable content found (no transcript, OCR text, or caption)."
                warning_msg = " No extractable content found (no transcript, OCR text, or caption)."
            else:
                error_msg = "No venues found in this video. The video might not mention specific venue names, or they couldn't be extracted."
                warning_msg = " GPT extraction returned no venues. Check Render logs for detailed error messages."
            
            # Return empty result with helpful message
            data = {
                "video_url": url,
                "summary_title": context_title or caption or "No venues found",
                "context_summary": context_title or caption or "No venues found",
                "places_extracted": [],
                "error": error_msg,
                "warning": warning_msg if warning_msg else None,
                "extraction_id": extraction_id,  # Add extraction_id for status polling
                "ocr_text": ocr_text,  # Include OCR text for debugging/testing
                "transcript": transcript,  # Include transcript for debugging/testing
                "debug_info": {
                    "has_content": has_content,
                    "openai_key_set": bool(api_key),
                    "ocr_available": OCR_AVAILABLE,
                    "advanced_ocr_available": ADVANCED_OCR_AVAILABLE,
                    "content_lengths": {
                        "transcript": len(transcript) if transcript else 0,
                        "ocr_text": len(ocr_text) if ocr_text else 0,
                        "caption": len(caption) if caption else 0,
                    },
                    "caption_preview": caption[:200] if caption else None,
                    "ocr_preview": ocr_text[:200] if ocr_text else None,
                }
            }
            return jsonify(data)

        # OPTIMIZATION: Parallelize enrichment and photo fetching for multiple places
        update_status(extraction_id, f"Mapping {len(venues)} venues with details...")
        username = extract_username_from_url(url)
        places_extracted = enrich_places_parallel(
            venues, transcript, ocr_text, caption, comments_text,
            url, username, context_title, venue_to_slide=venue_to_slide, venue_to_context=venue_to_context, photo_urls=None
        )
        update_status(extraction_id, "Finalizing results...")
        update_status(extraction_id, "Complete")

        data = {
            "video_url": url,
            "summary_title": context_title or caption or "TikTok Venues",
            "context_summary": context_title or caption or "TikTok Venues",
            "places_extracted": places_extracted,
            "extraction_id": extraction_id,  # Add extraction_id for status polling
            "ocr_text": ocr_text,  # Include OCR text for debugging/testing
            "transcript": transcript  # Include transcript for debugging/testing
        }

        if vid:
            cache[vid] = data
            save_cache(cache)
            print(f"💾 Cached result for video {vid}")

        print(f"✅ Extraction complete — {len(places_extracted)} places found")
        # Don't clear status immediately - let frontend finish polling first
        return jsonify(data)

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        error_str = str(e)
        
        # Log the full error for debugging
        print(f"❌ Extraction error: {error_str}")
        print(f"📋 Full traceback:\n{error_trace}")
        
        # Special handling for photo posts when yt-dlp fails
        if is_photo_post and ("Unsupported URL" in error_str or "yt-dlp error" in error_str):
            print(f"⚠️ yt-dlp doesn't support photo posts - using HTML-extracted caption")
            if html_caption:
                print(f"📝 Extracting venues from HTML caption: {html_caption[:100]}...")
                # Extract venues from caption only
                transcript = ""
                ocr_text = ""
                comments_text = ""
                result = extract_places_and_context(transcript, ocr_text, html_caption, comments_text)
                # Handle both 4-tuple and 5-tuple returns
                if len(result) == 5:
                    venues, context_title, venue_to_slide, venue_to_context, _venue_attribution = result
                else:
                    venues, context_title, venue_to_slide, venue_to_context = result
                venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
                
                data = {
                    "video_url": url,
                    "summary_title": context_title or html_caption or "TikTok Photo Post",
                    "context_summary": context_title or html_caption or "TikTok Photo Post",
                    "places_extracted": [],
                    "photo_urls": [],
                    "caption_extracted": html_caption,  # Include actual caption for debugging
                    "ocr_text": ocr_text,  # Include OCR text for debugging/testing
                    "transcript": transcript  # Include transcript for debugging/testing
                }
                
                if venues:
                    update_status(extraction_id, f"Mapping {len(venues)} venues with details...")
                    username = extract_username_from_url(url)
                    places_extracted = enrich_places_parallel(
                        venues, transcript, ocr_text, html_caption, comments_text,
                        url, username, context_title, venue_to_slide=venue_to_slide, venue_to_context=venue_to_context
                    )
                    data["places_extracted"] = places_extracted
                    data["venue_contexts"] = venue_to_context if venue_to_context else {}  # Include all extracted context for each venue
                    update_status(extraction_id, "Finalizing results...")
                    update_status(extraction_id, "Complete")

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

@app.route("/api/cache/stats", methods=["GET"])
def cache_stats():
    """Get cache statistics for monitoring cost savings"""
    try:
        if OPTIMIZED_GEOCODING_AVAILABLE:
            from geocoding_service import get_cache_stats
            stats = get_cache_stats()
            return jsonify({
                "success": True,
                "optimized_geocoding": True,
                "stats": stats
            })
        else:
            return jsonify({
                "success": True,
                "optimized_geocoding": False,
                "stats": {
                    "in_memory_cache_size": len(_places_cache) if '_places_cache' in globals() else 0,
                    "message": "Install googlemaps and rapidfuzz for full optimization"
                }
            })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route("/healthz", methods=["GET"])
def health_check():
    """
    Lightweight health check endpoint for Render.com.
    Must be fast and non-blocking - no API calls or heavy operations.
    """
    try:
        # Check if app is ready (initialized)
        if not is_app_ready():
            return jsonify({
                "status": "initializing",
                "service": "planit-backend",
                "message": "App is still initializing"
            }), 503  # Service Unavailable - not ready yet
        
        # Basic health check - just verify app is running
        return jsonify({
            "status": "ok",
            "service": "planit-backend",
            "ready": True
        }), 200
    except Exception as e:
        # Log error but don't crash - return error status
        print(f"⚠️ Health check error: {e}")
        return jsonify({
            "status": "error",
            "service": "planit-backend",
            "message": str(e)[:200]  # Limit message length
        }), 500

# ─────────────────────────────
# Run Server
# ─────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    print(f"Running Flask backend on {port}...")
    app.run(host="0.0.0.0", port=port)
