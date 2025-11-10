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
    if shutil.which("tesseract"):
        OCR_AVAILABLE = True
    else:
        print("⚠️ pytesseract installed but tesseract binary not found - OCR will be skipped")
except ImportError:
    print("⚠️ pytesseract not available - OCR will be skipped")
except Exception as e:
    print(f"⚠️ OCR check failed: {e} - OCR will be skipped")

# ─────────────────────────────
# Setup
# ─────────────────────────────
app = Flask(__name__)
# Allow all origins for CORS - needed for frontend to connect
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# Create a proxy-safe HTTP client
safe_httpx = HttpxClient(proxies=None, trust_env=False, timeout=30.0)

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
# Cache Setup
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
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Try to extract JSON data from script tags (TikTok embeds data in JSON)
        meta = {}
        caption = ""
        photo_urls = []
        
        # Look for JSON data in script tags - TikTok uses various formats
        scripts = soup.find_all('script', type='application/json')
        for script in scripts:
            if script.string:
                try:
                    data = json.loads(script.string)
                    # Recursively search for caption/description fields
                    def find_caption(obj, depth=0):
                        if depth > 5:  # Limit recursion depth
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
                    if found_caption:
                        caption = found_caption
                except:
                    pass
        
        # Also check script tags with text content (not JSON)
        if not caption:
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    # Try to find JSON data containing post information
                    # TikTok often embeds data in window.__UNIVERSAL_DATA_FOR_REHYDRATION__ or similar
                    if '__UNIVERSAL_DATA_FOR_REHYDRATION__' in script.string or 'SIGI_STATE' in script.string:
                        try:
                            # Extract JSON from script - look for window.__UNIVERSAL_DATA_FOR_REHYDRATION__ = {...}
                            json_match = re.search(r'window\.__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*({.+?});', script.string, re.DOTALL)
                            if not json_match:
                                json_match = re.search(r'({.+})', script.string, re.DOTALL)
                            if json_match:
                                data = json.loads(json_match.group(1))
                                # Navigate through the JSON structure to find caption
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
                                if found_caption:
                                    caption = found_caption
                        except Exception as e:
                            print(f"⚠️ JSON parsing error: {e}")
                            pass
        
        # Fallback: Try to find caption in meta tags
        if not caption:
            meta_desc = soup.find('meta', property='og:description')
            if meta_desc and meta_desc.get('content'):
                caption = meta_desc['content']
        
        # Fallback: Try to find caption in title or other meta tags
        if not caption:
            meta_title = soup.find('meta', property='og:title')
            if meta_title and meta_title.get('content'):
                caption = meta_title['content']
        
        # Extract photo URLs from img tags or JSON
        images = soup.find_all('img')
        for img in images:
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if src:
                # Clean up the URL
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    src = 'https://www.tiktok.com' + src
                
                if src.startswith('http') and ('tiktok' in src.lower() or 'cdn' in src.lower() or 'image' in src.lower()):
                    photo_urls.append(src)
        
        # Also try to extract from JSON data in scripts
        for script in scripts:
            if script.string:
                # Try to find image URLs in the script
                url_matches = re.findall(r'https?://[^\s"\'<>\)]+\.(?:jpg|jpeg|png|webp)', script.string, re.I)
                photo_urls.extend(url_matches)
        
        # Remove duplicates and filter
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
    
    # For photo URLs, use HTML parsing instead of yt-dlp
    if is_photo_url:
        print("🖼️ Photo URL detected - using HTML parsing method (skipping yt-dlp)")
        try:
            meta, photo_urls = fetch_tiktok_photo_post(video_url)
            
            # Optionally download the first photo for OCR
            file_path = None
            if photo_urls and OCR_AVAILABLE:
                try:
                    print(f"📥 Downloading photo for OCR: {photo_urls[0][:100]}...")
                    tmpdir = tempfile.mkdtemp()
                    response = requests.get(photo_urls[0], headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    }, timeout=30)
                    response.raise_for_status()
                    
                    # Determine file extension from URL or content type
                    ext = '.jpg'
                    if '.png' in photo_urls[0].lower():
                        ext = '.png'
                    elif '.webp' in photo_urls[0].lower():
                        ext = '.webp'
                    elif 'image/png' in response.headers.get('content-type', ''):
                        ext = '.png'
                    
                    file_path = os.path.join(tmpdir, f"photo{ext}")
                    with open(file_path, 'wb') as f:
                        f.write(response.content)
                    print(f"✅ Photo downloaded: {file_path}")
                except Exception as e:
                    print(f"⚠️ Failed to download photo for OCR: {e}")
                    file_path = None
            
            return file_path, meta
        except Exception as e:
            print(f"❌ HTML parsing failed for photo URL: {e}")
            import traceback
            print(traceback.format_exc())
            # Return empty metadata but don't raise - let the extraction flow handle it
            return None, {}
    
    # For video URLs, use yt-dlp as before
    # IMPORTANT: Double-check this is NOT a photo URL (should never reach here for photo URLs)
    assert not is_photo_url, "Photo URLs should be handled above - this should never execute for photo URLs"
    
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
    
    # Add extra options to avoid TikTok blocking (403 errors)
    # Use better headers and retry logic
    extra_opts = '--user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" --referer "https://www.tiktok.com/" --retries 3 --fragment-retries 3'
    
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
        
        # For slideshow videos, process more frames to catch all slides
        # Process 1 frame per second, or at least 10 frames, up to 20 frames
        num_frames = min(max(int(duration), 10), 20) if duration > 0 else 15
        frames = np.linspace(0, total - 1, min(total, num_frames), dtype=int)
        
        print(f"📹 Processing {len(frames)} frames from {total} total frames (duration: {duration:.1f}s)")
        
        texts = []
        seen_texts = set()  # Deduplicate similar text
        
        # OCR config for better accuracy on stylized text
        ocr_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,!?;:()[]{}-\'"&@#$% '
        
        for n in frames:
        vidcap.set(cv2.CAP_PROP_POS_FRAMES, n)
        ok, img = vidcap.read()
        if not ok:
            continue
            
            # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Image preprocessing to improve OCR accuracy
            # 1. Increase contrast
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
            enhanced = clahe.apply(gray)
            
            # 2. Apply thresholding to make text more distinct
            _, thresh = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            
            # 3. Try OCR on both original and processed images
            for processed_img in [gray, enhanced, thresh]:
                try:
                    txt = image_to_string(Image.fromarray(processed_img), config=ocr_config)
                    txt_clean = txt.strip()
                    if txt_clean and len(txt_clean) > 2:
                        # Deduplicate - only add if significantly different
                        txt_lower = txt_clean.lower()
                        is_duplicate = any(
                            txt_lower in seen or seen in txt_lower 
                            for seen in seen_texts 
                            if len(seen) > 5 and len(txt_lower) > 5
                        )
                        if not is_duplicate:
                            texts.append(txt_clean)
                            seen_texts.add(txt_lower)
                            print(f"   Frame {n}: Found text: {txt_clean[:50]}...")
                            break  # Found text, move to next frame
                except:
                    continue
            
            # Clean up image from memory
            del img, gray, enhanced, thresh
            gc.collect()
        
    vidcap.release()
        del vidcap
        gc.collect()  # Force garbage collection
        
    merged = " | ".join(texts)
        print(f"✅ OCR extracted {len(merged)} chars from {len(texts)} unique text blocks")
        if merged:
            print(f"📝 OCR text preview: {merged[:200]}...")
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
    prompt = f"""
You are analyzing a TikTok video about NYC venues. Extract venue names from ANY available source.

1️⃣ Extract every **specific** bar, restaurant, café, or food/drink venue mentioned.
   • IMPORTANT: Check the CAPTION/DESCRIPTION carefully - venue names are often listed there
   • Also check on-screen text (OCR), speech (transcript), and comments
   • Look for venue names even if they appear in lists, hashtags, or casual mentions
   • Ignore broad neighborhoods like "SoHo" or "Brooklyn" unless they're part of a venue name
   • ONLY list actual venue names that are mentioned. Do NOT use placeholders like "venue 1" or "<venue 1>".
   • If no venues are found, return an empty list (no venues, just the Summary line).

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
        
        # Increase context window to 6000 chars to capture more content
        content_to_analyze = combined_text[:6000]
        print(f"📝 Analyzing content ({len(content_to_analyze)} chars): {content_to_analyze[:300]}...")
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt + "\n\nContent to analyze:\n" + content_to_analyze}],
            temperature=0.5,
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
  "must_try": "If TikTok mentions or implies a must-try item",
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
        data = {
            "summary": j.get("summary", "").strip(),
            "when_to_go": j.get("when_to_go", "").strip(),
            "vibe": j.get("vibe", "").strip(),
            "must_try": j.get("must_try", "").strip(),
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
# API Endpoint
# ─────────────────────────────
@app.route("/api/extract", methods=["POST"])
def extract_api():
    url = request.json.get("video_url")
    
    if not url:
        return jsonify({
            "error": "No video URL provided",
            "message": "Please provide a valid TikTok video URL."
        }), 400
    
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
            # For photo URLs, try to extract from caption only
            if "/photo/" in url.lower():
                print("🖼️ Photo URL - no file downloaded, extracting from caption only")
                transcript = ""
                ocr_text = ""
                
                if not caption:
                    return jsonify({
                        "error": "Static photo with no extractable text",
                        "message": "The photo post has no caption and the image could not be downloaded. Unable to extract venue information.",
                        "video_url": url,
                        "places_extracted": []
                    }), 200
                
                # Extract from caption only
                venues, context_title = extract_places_and_context(transcript, ocr_text, caption, comments_text)
                venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
                
                data = {
                    "video_url": url,
                    "context_summary": context_title or "No venues found",
                    "places_extracted": []
                }
                
                if venues:
                    places_extracted = []
                    for v in venues:
                        intel = enrich_place_intel(v, transcript, ocr_text, caption, comments_text)
                        photo = get_photo_url(v)
                        places_extracted.append({
                            "name": v,
                            "description": intel.get("summary", ""),
                            "vibe_tags": intel.get("vibe_tags", []),
                            "photo": photo,
                        })
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

        audio_path = extract_audio(video_path)
            print(f"✅ Audio extracted: {audio_path}")
            
        transcript = transcribe_audio(audio_path)
            print(f"✅ Transcript: {len(transcript)} chars")
            
            # Clean up audio file immediately after transcription
            if audio_path != video_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    print("🗑️ Cleaned up audio file")
                except:
                    pass
            
            # Try OCR (especially important for slideshow videos without audio)
            # OCR will try to run even on Render (will fail gracefully if tesseract not available)
        ocr_text = extract_ocr_text(video_path)
            if ocr_text:
                print(f"✅ OCR text: {len(ocr_text)} chars")
            else:
                print("⚠️ OCR returned no text (tesseract may not be available)")
            
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
        for v in venues:
            intel = enrich_place_intel(v, transcript, ocr_text, caption, comments_text)
            photo = get_photo_url(v)
            places_extracted.append({
                "name": v,
                "maps_url": f"https://www.google.com/maps/search/{v.replace(' ', '+')}",
                "photo_url": photo or "https://via.placeholder.com/600x400?text=No+Photo",
                **intel
            })

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
