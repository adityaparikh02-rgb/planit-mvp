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
    # Try /photo/ format
    m = re.search(r"/photo/(\d+)", url)
    if m:
        return m.group(1)
    # Shortened URLs (/t/ format) will be handled by extracting ID from metadata
    return None

def get_tiktok_post_data(url):
    """Fetch TikTok post data using mobile API endpoint. Returns dict with type, caption, photo_urls/video_url."""
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
            
            photo_urls = []
            for img in images:
                image_url_obj = img.get("imageURL", {})
                url_list = image_url_obj.get("urlList", [])
                if url_list and len(url_list) > 0:
                    photo_urls.append(url_list[0])
            
            print(f"✅ Extracted {len(photo_urls)} photo URLs from API")
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
                
                photo_urls = []
                for img in images:
                    display_image = img.get("display_image", {})
                    url_list = display_image.get("url_list", [])
                    if url_list and len(url_list) > 0:
                        photo_urls.append(url_list[0])
                
                media["photo_urls"] = photo_urls
                if photo_urls:
                    print(f"✅ Extracted {len(photo_urls)} photo URLs from API16")
            
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
                if "image_post_info" in aweme:
                    images = aweme["image_post_info"].get("images", [])
                    result["photo_urls"] = [
                        img["display_image"]["url_list"][0]
                        for img in images
                        if "display_image" in img and "url_list" in img["display_image"] and len(img["display_image"]["url_list"]) > 0
                    ]
                
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
    Run OCR on a single static image with improved preprocessing for better text detection.
    Downloads image if path is a URL, converts formats, applies multiple preprocessing methods.
    Returns cleaned extracted text.
    """
    if not OCR_AVAILABLE:
        print("⚠️ OCR not available (tesseract not installed) - skipping OCR")
        return ""
    
    try:
        print(f"🖼️ Running OCR on image: {image_path[:60]}...")
        
        # If image_path is a URL, download it first
        if image_path.startswith("http://") or image_path.startswith("https://"):
            print(f"📥 Downloading image for OCR: {image_path[:60]}...")
            r = requests.get(image_path, timeout=10)
            r.raise_for_status()
            
            # Save to temp file
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp.write(r.content)
                image_path = tmp.name
        
        # Read image with OpenCV
        img = cv2.imread(image_path)
        if img is None:
            print("⚠️ OpenCV failed to read image, trying PIL fallback...")
            # Fallback to PIL if OpenCV fails (handles WebP, HEIC, etc.)
            pil_img = Image.open(image_path)
            # Convert PIL image to RGB numpy array
            img = np.array(pil_img.convert("RGB"))
            # Convert RGB to BGR for OpenCV
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        
        # Resize if image is too large (improves OCR accuracy)
        height, width = img.shape[:2]
        if width > 2000 or height > 2000:
            scale = min(2000 / width, 2000 / height)
            new_width = int(width * scale)
            new_height = int(height * scale)
            img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_AREA)
            print(f"📏 Resized image from {width}x{height} to {new_width}x{new_height} for better OCR")
        
        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Upscale for better OCR of small text
        height, width = gray.shape
        if width < 1000:
            scale = 1000 / width
            new_size = (int(width * scale), int(height * scale))
            gray = cv2.resize(gray, new_size, interpolation=cv2.INTER_CUBIC)
            print(f"📏 Upscaled image by {scale:.1f}x for better text detection")
        
        # Try multiple preprocessing methods and pick the best result
        texts = []
        
        # Method 0: PURE INVERSION (for white text on dark - TikTok captions)
        # This is often the BEST method for TikTok Sans white text
        inverted_raw = cv2.bitwise_not(gray)
        text0 = pytesseract.image_to_string(inverted_raw, config="--oem 3 --psm 11")
        if text0.strip():
            texts.append(("inverted_raw", text0))
        
        # Method 0b: INVERTED + OTSU (aggressive for white text)
        _, inverted_otsu = cv2.threshold(inverted_raw, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        text0b = pytesseract.image_to_string(inverted_otsu, config="--oem 3 --psm 11")
        if text0b.strip():
            texts.append(("inverted_otsu", text0b))
        
        # Method 0c: INVERTED + MORPHOLOGICAL cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 1))
        inverted_morphed = cv2.morphologyEx(inverted_raw, cv2.MORPH_CLOSE, kernel)
        inverted_morphed = cv2.morphologyEx(inverted_morphed, cv2.MORPH_OPEN, kernel)
        text0c = pytesseract.image_to_string(inverted_morphed, config="--oem 3 --psm 11")
        if text0c.strip():
            texts.append(("inverted_morphed", text0c))
        
        # Method 0d: INVERTED + BILATERAL (smooth noise while preserving edges)
        inverted_bilateral = cv2.bilateralFilter(inverted_raw, 5, 50, 50)
        text0d = pytesseract.image_to_string(inverted_bilateral, config="--oem 3 --psm 11")
        if text0d.strip():
            texts.append(("inverted_bilateral", text0d))
        
        # Method 1: Adaptive thresholding (best for varying lighting)
        processed1 = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, 10
        )
        text1 = pytesseract.image_to_string(processed1, config="--oem 3 --psm 6")
        if text1.strip():
            texts.append(("adaptive_thresh", text1))
        
        # Method 2: Otsu thresholding (automatic contrast)
        _, processed2 = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        text2 = pytesseract.image_to_string(processed2, config="--oem 3 --psm 6")
        if text2.strip():
            texts.append(("otsu_thresh", text2))
        
        # Method 3: Denoising + adaptive (best for TikTok quality)
        denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
        processed3 = cv2.adaptiveThreshold(
            denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        text3 = pytesseract.image_to_string(processed3, config="--oem 3 --psm 6")
        if text3.strip():
            texts.append(("denoised_adaptive", text3))
        
        # Method 4: INVERTED adaptive (critical for white text on dark background!)
        # TikTok captions are usually WHITE TEXT - inverted helps a LOT
        inverted = cv2.bitwise_not(processed3)  # Invert denoised adaptive for best results
        text4 = pytesseract.image_to_string(inverted, config="--oem 3 --psm 11")  # PSM 11 for sparse text
        if text4.strip():
            texts.append(("inverted_denoised", text4))
        
        # Method 5: MORPHOLOGICAL cleaning (removes noise, keeps text)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        processed5 = cv2.morphologyEx(processed3, cv2.MORPH_CLOSE, kernel)  # Close small holes
        processed5 = cv2.morphologyEx(processed5, cv2.MORPH_OPEN, kernel)   # Remove small noise
        text5 = pytesseract.image_to_string(processed5, config="--oem 3 --psm 6")
        if text5.strip():
            texts.append(("morphology", text5))
        
        # Method 6: CONTRAST BOOST (for low-contrast images)
        # Use CLAHE to enhance local contrast
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        processed6 = cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        text6 = pytesseract.image_to_string(processed6, config="--oem 3 --psm 6")
        if text6.strip():
            texts.append(("contrast_enhanced", text6))
        
        # Method 6b: INVERTED + CONTRAST BOOST (for dark images with white text)
        clahe_inv = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced_inv = clahe_inv.apply(inverted_raw)
        text6b = pytesseract.image_to_string(enhanced_inv, config="--oem 3 --psm 11")
        if text6b.strip():
            texts.append(("inverted_contrast", text6b))
        
        # Method 7: Bilateral filtering + threshold (smooth while keeping edges)
        bilateral = cv2.bilateralFilter(gray, 9, 75, 75)
        processed7 = cv2.adaptiveThreshold(
            bilateral, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        text7 = pytesseract.image_to_string(processed7, config="--oem 3 --psm 11")
        if text7.strip():
            texts.append(("bilateral", text7))
        
        # Pick the best result with SMART legibility requirements
        best_text = ""
        best_method = ""
        best_score = 0
        
        for method, text in texts:
            cleaned = clean_ocr_text(text)
            
            if len(cleaned) < 10:  # Too short to be meaningful
                continue
            
            # Calculate detailed legibility metrics
            words = [w for w in cleaned.split() if w]
            
            if not words:
                continue
            
            # 1. WORD LENGTH CHECK (critical for detecting garbled text)
            # Real English words: 3-11 chars average
            # Garbled: very short (1-2) or very long (20+) "words"
            word_lengths = [len(w) for w in words]
            avg_word_len = sum(word_lengths) / len(words)
            
            # Penalize if words are too short OR too long
            if avg_word_len < 2 or avg_word_len > 18:
                continue  # Skip obviously garbled
            
            # 2. CONSONANT CLUSTER CHECK (better than vowel ratio for OCR)
            # Real text: consonant clusters max 3-4 ("str", "scr", "spring")
            # Garbled: "RRR yy oe ST", "ee JE LEE ESS" = excessive consonants
            max_consonant_run = 0
            current_consonant_run = 0
            for c in cleaned.lower():
                if c.isalpha() and c not in 'aeiou':
                    current_consonant_run += 1
                    max_consonant_run = max(max_consonant_run, current_consonant_run)
                else:
                    current_consonant_run = 0
            
            # Reject if more than 5 consonants in a row (that's garbled for sure)
            if max_consonant_run > 5:
                continue
            
            # 3. ALPHANUMERIC RATIO
            # Real text: mostly letters/numbers/spaces/punctuation
            alpha_count = sum(1 for c in cleaned if c.isalnum() or c in ' ,-.')
            alpha_ratio = alpha_count / len(cleaned)
            
            if alpha_ratio < 0.65:  # More lenient: 65% instead of 70%
                continue
            
            # 4. SPACE DISTRIBUTION (check for clustering)
            # Real text: spaces distributed throughout
            # Garbled: clumped spaces or none
            space_ratio = cleaned.count(' ') / max(len(cleaned), 1)
            if space_ratio < 0.08 or space_ratio > 0.45:  # More lenient ranges
                continue
            
            # 5. UPPERCASE/LOWERCASE BALANCE
            # Real OCR: mostly lowercase with some uppercase (names, starts of sentences)
            # Garbled: ALL UPPERCASE like "RRR YY OE ST HE" or chaotic
            upper_count = sum(1 for c in cleaned if c.isupper())
            lower_count = sum(1 for c in cleaned if c.islower())
            
            if upper_count > 0 and lower_count > 0:
                upper_ratio = upper_count / (upper_count + lower_count)
                # Reject if >50% uppercase (likely garbled)
                if upper_ratio > 0.5:
                    continue
            
            # All checks passed! Calculate final score
            # Prefer longer, cleaner text with better word distribution
            length_factor = min(len(cleaned) / 200, 1.0)  # Prefer 200+ chars
            consonant_factor = 1.0 - (max_consonant_run / 10)  # Reward shorter consonant clusters
            alpha_factor = alpha_ratio
            
            quality_score = (alpha_factor * 0.4) + (length_factor * 0.35) + (consonant_factor * 0.25)
            
            if quality_score > best_score:
                best_score = quality_score
                best_text = cleaned
                best_method = method
        
        if best_text:
            print(f"✅ OCR detected text via {best_method} ({len(best_text)} chars, quality: {best_score:.2f}): {best_text[:100]}...")
            return best_text
        else:
            print("⚠️ No readable text detected after all preprocessing methods (all text was too garbled).")
            return ""
            
    except Exception as e:
        print(f"❌ OCR failed on image: {e}")
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
                params={"place_id": place_id, "fields": "photos,name", "key": GOOGLE_API_KEY},
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
    
    # Fallback: search by name
    try:
        print(f"🔍 Fallback: Searching for photo by name: {name}")
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": name, "key": GOOGLE_API_KEY}, 
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
# GPT: Extract Venues + Summary
# ─────────────────────────────
def _is_ocr_garbled(text):
    """
    Detect if OCR text is too garbled/corrupted to be useful.
    Garbled text has too many special characters, random letters, and few real words.
    """
    if not text or len(text) < 50:
        return False
    
    # Count different character types
    total_chars = len(text)
    alphanumeric = sum(1 for c in text if c.isalnum() or c.isspace())
    special_chars = total_chars - alphanumeric
    
    # Calculate garbling ratio
    garble_ratio = special_chars / total_chars if total_chars > 0 else 0
    
    # If more than 40% special characters, it's probably garbled
    if garble_ratio > 0.4:
        print(f"   Garble detection: {garble_ratio:.1%} special chars (threshold: 40%)")
        return True
    
    # Also check for common words - if barely any, it's garbled
    words = text.split()
    common_words = ['the', 'and', 'a', 'to', 'of', 'in', 'is', 'at', 'for', 'nyc', 'restaurant', 'food']
    word_matches = sum(1 for w in words if w.lower() in common_words)
    
    if len(words) > 20 and word_matches < 2:
        print(f"   Garble detection: Found {word_matches} common words in {len(words)} words")
        return True
    
    return False


def _parse_slide_text(ocr_text):
    """
    Parse OCR text that contains SLIDE markers and return a dict of slides.
    Returns: {"slide_1": "text from slide 1", "slide_2": "text from slide 2", ...}
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


def extract_places_and_context(transcript, ocr_text, caption, comments):
    # Clean OCR text before processing
    original_ocr_len = len(ocr_text) if ocr_text else 0
    if ocr_text:
        ocr_text = clean_ocr_text(ocr_text)
        if original_ocr_len != len(ocr_text):
            print(f"🧹 Cleaned OCR text: {len(ocr_text)} chars (was {original_ocr_len} chars before cleaning)")
    
    # Parse OCR into slides for slide-aware extraction
    slide_dict = _parse_slide_text(ocr_text)
    is_slideshow = len(slide_dict) > 1
    
    if is_slideshow:
        print(f"📖 SLIDE-AWARE EXTRACTION: Detected {len(slide_dict)} slides")
        print(f"   Extracting places per-slide to maintain context (like reading a book)")
    
    # CRITICAL: Detect if OCR is too garbled to be useful
    # If OCR has too many special characters/random letters, it's probably corrupted
    if ocr_text and _is_ocr_garbled(ocr_text):
        print("⚠️ OCR text appears to be heavily garbled/corrupted - deprioritizing it")
        print(f"   Reason: Too many non-alphanumeric characters or random text")
        print(f"   Will prioritize caption instead")
        # If OCR is garbled but caption exists, use caption as primary
        if caption:
            ocr_text = ""  # Ignore garbled OCR, use caption instead
            slide_dict = {}  # Clear slide dict
    
    # Log what we have for debugging
    print(f"📋 Content sources:")
    print(f"   - Caption: {len(caption)} chars - {caption[:100] if caption else 'None'}...")
    print(f"   - Transcript: {len(transcript)} chars - {transcript[:100] if transcript else 'None'}...")
    print(f"   - OCR: {len(ocr_text)} chars - {ocr_text[:100] if ocr_text else 'None'}...")
    print(f"   - Comments: {len(comments)} chars - {comments[:100] if comments else 'None'}...")
    
    # If we have slides, extract per-slide to maintain context
    if is_slideshow:
        print(f"\n🔄 Extracting places per-slide (slide-aware mode)...")
        all_venues_per_slide = {}
        overall_summary = ""
        
        # Analyze each slide independently
        for slide_key, slide_text in sorted(slide_dict.items()):
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

Slide content:
{slide_text[:1000]}

Caption context: {caption[:200] if caption else '(none)'}

Output format: One venue name per line, or empty if none found.
VenueName1
VenueName2
...

If no venues found, output: (none)
"""
                
                client = get_openai_client()
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": slide_prompt}],
                    temperature=0.2,  # Very low temperature for consistent extraction
                )
                
                slide_response = response.choices[0].message.content.strip()
                
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
        unique_venues = []
        seen = set()
        for v_dict in all_venues_with_slides:
            v_lower = v_dict["name"].lower().strip()
            if v_lower not in seen and len(v_lower) >= 3:
                seen.add(v_lower)
                unique_venues.append(v_dict["name"])  # Return just names for compatibility
        
        print(f"\n📖 Slide-aware extraction complete:")
        print(f"   Total unique venues: {len(unique_venues)}")
        print(f"   Summary: {overall_summary}")
        
        # Return tuple: (venue_names, summary, venue_to_slide_mapping)
        # Build mapping for enrichment later
        venue_to_slide = {}
        for v_dict in all_venues_with_slides:
            v_lower = v_dict["name"].lower().strip()
            if v_lower not in venue_to_slide and len(v_lower) >= 3:
                venue_to_slide[v_dict["name"]] = v_dict["source_slide"]
        
        return unique_venues, overall_summary, venue_to_slide
    
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
   • IMPORTANT: OCR text may contain garbled characters or errors. Look for REAL venue names, not random words.
   • If OCR text is mostly garbled (lots of special characters, random letters), rely MORE on the caption.
   • Only extract venue names that look like REAL restaurant/bar/café names (e.g., "Joe's Pizza", "Lombardi's").
   • Do NOT extract random words from garbled OCR text (e.g., "Danny's" or "Ballerina" if they don't appear in context).
   • If OCR text is too garbled or unclear, prioritize the caption for venue names.
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
        return unique, summary, {}  # Empty venue_to_slide for non-slideshow videos
    except Exception as e:
        print("❌ GPT extraction failed:", e)
        return [], "TikTok Venues", {}

# ─────────────────────────────
# GPT: Enrichment + Vibe Tags
# ─────────────────────────────
def enrich_place_intel(name, transcript, ocr_text, caption, comments, source_slide=None):
    """
    Enrich place information with slide-aware context.
    If source_slide is provided (e.g., "slide_1"), only use context from that slide.
    """
    # Parse slides if OCR has them
    slide_dict = _parse_slide_text(ocr_text) if ocr_text else {}
    
    # If we have slide info and this place came from a specific slide, use only that slide's context
    if source_slide and source_slide in slide_dict:
        print(f"   🔍 Enriching {name} using context from {source_slide} only (slide-aware)")
        slide_specific_text = slide_dict[source_slide]
        context = "\n".join(x for x in [slide_specific_text, caption] if x)
    else:
        # Fallback: use full context (for non-slideshow videos or if source_slide not found)
        context = "\n".join(x for x in [caption, ocr_text, transcript, comments] if x)
    
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
    """Get canonical name, address, place_id, and photos from Google Maps API."""
    if not GOOGLE_API_KEY:
        print(f"⚠️ GOOGLE_API_KEY not set - cannot get place info for {place_name}")
        return None, None, None, None
    try:
        # Try without location first (more flexible)
        print(f"🔍 Searching Google Places for: {place_name}")
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": place_name, "key": GOOGLE_API_KEY},
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        
        if data.get("status") != "OK":
            print(f"⚠️ Google Places search error for {place_name}: {data.get('status')} - {data.get('error_message', 'Unknown error')}")
            return None, None, None, None
        
        res = data.get("results", [])
        if res and len(res) > 0:
            place_info = res[0]
            canonical_name = place_info.get("name", place_name)  # Use Google's canonical name
            address = place_info.get("formatted_address")
            place_id = place_info.get("place_id")
            photos = place_info.get("photos", [])
            
            print(f"✅ Found place: {canonical_name} (place_id: {place_id[:20] if place_id else 'None'}..., photos: {len(photos) if photos else 0})")
            return canonical_name, address, place_id, photos
        else:
            print(f"⚠️ No results found for {place_name}")
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Failed to get place info from Google for {place_name} - Request error: {e}")
    except Exception as e:
        print(f"⚠️ Failed to get place info from Google for {place_name} - Unexpected error: {e}")
        import traceback
        print(traceback.format_exc())
    return None, None, None, None

def get_place_address(place_name):
    """Get formatted address for a place name using Google Maps API."""
    _, address, _, _ = get_place_info_from_google(place_name)
    return address

def merge_place_with_cache(place_data, video_url, username=None, video_summary=None):
    """Merge a place with cached places if name+address match. Returns merged place data."""
    place_name = place_data.get("name", "")
    original_name = place_name
    
    # If address not already set, get it (and ensure canonical name)
    if not place_data.get("address"):
        canonical_name, address, _, _ = get_place_info_from_google(place_name)
        if canonical_name and canonical_name.lower() != place_name.lower():
            place_name = canonical_name  # Update to canonical name
            place_data["name"] = canonical_name
            print(f"✏️  Corrected spelling in cache: '{original_name}' → '{canonical_name}'")
        place_address = address or ""
    else:
        place_address = place_data.get("address")
        # Still check canonical name even if address exists
        canonical_name, _, _, _ = get_place_info_from_google(place_name)
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

def enrich_places_parallel(venues, transcript, ocr_text, caption, comments_text, url, username, context_title, venue_to_slide=None):
    """Enrich multiple places in parallel for better performance.
    
    Args:
        venues: List of venue names to enrich
        venue_to_slide: Optional dict mapping venue names to their source slides
    """
    if venue_to_slide is None:
        venue_to_slide = {}
    
    places_extracted = []
    
    def enrich_and_fetch_photo(venue_name):
        """Enrich a single venue and fetch its photo - runs in parallel."""
        # Get canonical name, address, place_id, and photos from Google Maps (correct spelling)
        canonical_name, address, place_id, photos = get_place_info_from_google(venue_name)
        # Use canonical name if available, otherwise use original
        display_name = canonical_name if canonical_name else venue_name
        
        # Log if spelling was corrected
        if canonical_name and canonical_name.lower() != venue_name.lower():
            print(f"✏️  Corrected spelling: '{venue_name}' → '{canonical_name}'")
        
        # Get source slide for this venue if available
        source_slide = venue_to_slide.get(venue_name)
        
        # Pass source_slide to enrichment for slide-aware context
        intel = enrich_place_intel(display_name, transcript, ocr_text, caption, comments_text, source_slide=source_slide)
        # Use place_id and photos if available for more reliable photo fetching
        photo = get_photo_url(display_name, place_id=place_id, photos=photos)
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
    """Extract photo post data from TikTok URL using mobile API first, then fallback to Playwright."""
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
    
    # ===== AUTO-DETECT SLIDESHOW =====
    is_slideshow = "/photo/" in url.lower()
    
    # ===== PHOTO POST HANDLING =====
    # Detect if the TikTok URL contains /photo/ and use robust extractor
    if is_slideshow:
        print("📸 Detected TikTok photo post - using robust extractor (API16 -> SnapTik -> Playwright)")
        
        # Clean URL - remove query parameters that might interfere
        clean_url = url.split('?')[0] if '?' in url else url
        if clean_url != url:
            print(f"🔗 Cleaned URL: {url} -> {clean_url}")
        
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
            
            # Download first few images locally (e.g. temp_photo_1.jpg, temp_photo_2.jpg)
            ocr_text = ""
            tmpdir = tempfile.mkdtemp()
            
            try:
                # Process first few images with OCR (limit to 5 for performance)
                num_images = min(5, len(photo_urls))
                print(f"🔍 Processing {num_images} images with OCR...")
                
                for i, img_url in enumerate(photo_urls[:num_images]):
                    try:
                        # Run OCR directly on the URL - the function will download and process it
                        if OCR_AVAILABLE:
                            print(f"🔍 Running OCR on photo {i+1}/{num_images}...")
                            photo_ocr = run_ocr_on_image(img_url)
                            if photo_ocr and len(photo_ocr.strip()) > 3:
                                ocr_text += photo_ocr + " "
                                print(f"✅ OCR extracted text from photo {i+1} ({len(photo_ocr)} chars): {photo_ocr[:150]}...")
                            else:
                                print(f'⚠️ OCR found no text in photo {i+1} (OCR returned: {photo_ocr[:50] if photo_ocr else "None"}...)')
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
            print(f"📝 Caption preview: {caption[:200] if caption else 'None'}...")
            print(f"📝 OCR preview: {ocr_text[:200] if ocr_text else 'None'}...")
            
            # Pass combined OCR + caption through existing GPT-based place extraction function
            print(f"🤖 Extracting venues from photo post using GPT...")
            transcript = ""  # No audio for photo posts
            comments_text = ""
            print(f"   Input to GPT: transcript={len(transcript)} chars, ocr={len(ocr_text)} chars, caption={len(caption)} chars, comments={len(comments_text)} chars")
            venues, context_title, venue_to_slide = extract_places_and_context(transcript, ocr_text, caption, comments_text)
            print(f"🤖 GPT returned {len(venues)} venues: {venues}")
            print(f"🤖 GPT returned title: {context_title}")
            venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
            print(f"✅ After filtering: {len(venues)} venues remain: {venues}")
            
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
                print(f"🌟 Enriching {len(venues)} places with Google Maps data...")
                username = extract_username_from_url(url)
                places_extracted = enrich_places_parallel(
                    venues, transcript, ocr_text, caption, comments_text,
                    url, username, context_title, venue_to_slide=venue_to_slide
                )
                print(f"✅ Enriched {len(places_extracted)} places successfully")
                data["places_extracted"] = places_extracted
            else:
                print(f"⚠️ No venues found by GPT extraction")
                print(f"   This could mean:")
                print(f"   - The caption/OCR text doesn't contain venue names")
                print(f"   - GPT couldn't identify venues in the text")
                print(f"   - The text was too short or unclear")
                data["places_extracted"] = []
            
            # Cache the result
            vid = get_tiktok_id(url)
            if vid:
                cache = load_cache()
                cache[vid] = data
                save_cache(cache)
            
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
            venues, context_title, venue_to_slide = extract_places_and_context(transcript, ocr_text, caption, comments_text)
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
                    url, username, context_title, venue_to_slide=venue_to_slide
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
                venues, context_title, venue_to_slide = extract_places_and_context(transcript, ocr_text, caption, comments_text)
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
                        url, username, context_title, venue_to_slide=venue_to_slide
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
                ocr_text = extract_ocr_text(video_path, sample_rate=1)  # Sample every frame
                print(f"✅ Slideshow OCR: {len(ocr_text)} chars extracted")
            elif transcript_length > 50 and not "music" in transcript.lower():
                # Good transcript exists and it's speech (not music) - skip OCR
                print(f"✅ GOOD TRANSCRIPT ({transcript_length} chars) - SKIPPING OCR for speed")
                ocr_text = ""
                print("   Reason: Voice content detected and transcript is substantial")
            else:
                # No transcript or very short - run OCR with sampling
                print(f"⚠️ LIMITED SPEECH ({transcript_length} chars) - Running sampled OCR")
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

        venues, context_title, venue_to_slide = extract_places_and_context(transcript, ocr_text, caption, comments_text)

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
            url, username, context_title, venue_to_slide=venue_to_slide
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
                venues, context_title, venue_to_slide = extract_places_and_context(transcript, ocr_text, html_caption, comments_text)
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
                        url, username, context_title, venue_to_slide=venue_to_slide
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
