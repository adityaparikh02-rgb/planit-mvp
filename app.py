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
    m = re.search(r"/video/(\d+)", url)
    return m.group(1) if m else None

# ─────────────────────────────
# TikTok Download
# ─────────────────────────────
def download_tiktok(video_url):
    tmpdir = tempfile.mkdtemp()
    video_path = os.path.join(tmpdir, "video.mp4")

    print("🎞 Downloading TikTok + metadata...")
    
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
        f'-o "{tmpdir}/video" "{video_url}"', shell=True, check=False, capture_output=True, text=True, timeout=60)
    if result1.returncode != 0:
        error1 = (result1.stderr or result1.stdout or "Unknown error")[:1000]
        print(f"⚠️ Metadata download warning: {error1}")
    
    result2 = subprocess.run(
        f'{yt_dlp_cmd} {impersonate_flag} {extra_opts} -o "{video_path}" "{video_url}"',
        shell=True, check=False, capture_output=True, text=True, timeout=120)
    if result2.returncode != 0:
        error2 = (result2.stderr or result2.stdout or "Unknown error")
        print(f"⚠️ Video download error (full): {error2}")
        if not os.path.exists(video_path):
            # Extract the actual error message from the traceback
            error_lines = error2.split('\n')
            # Find the last meaningful error line
            actual_error = "Unknown yt-dlp error"
            for line in reversed(error_lines):
                if line.strip() and not line.startswith('File "') and not line.startswith('  File '):
                    actual_error = line.strip()
                    break
            raise Exception(f"Failed to download video. yt-dlp error: {actual_error[:500]}. Full error: {error2[:1000]}")

    meta = {}
    try:
        info_files = [f for f in os.listdir(tmpdir) if f.endswith(".info.json")]
        if info_files:
            with open(os.path.join(tmpdir, info_files[0]), "r") as f:
                meta = json.load(f)
    except Exception as e:
        print("⚠️ Metadata load fail:", e)
    return video_path, meta

# ─────────────────────────────
# Audio + OCR
# ─────────────────────────────
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

def extract_ocr_text(video_path):
    """Extract on-screen text using OCR. Returns empty string if OCR unavailable."""
    if not OCR_AVAILABLE:
        print("⚠️ OCR not available (tesseract not installed) - skipping OCR")
        return ""
    
    try:
        print("🧩 Extracting on-screen text with OCR…")
        vidcap = cv2.VideoCapture(video_path)
        total = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
        # Reduce frames for memory efficiency (5 instead of 10)
        frames = np.linspace(0, total - 1, min(total, 5), dtype=int)
        texts = []
        for n in frames:
            vidcap.set(cv2.CAP_PROP_POS_FRAMES, n)
            ok, img = vidcap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            txt = image_to_string(Image.fromarray(gray))
            if txt.strip():
                texts.append(txt.strip())
            # Clean up image from memory
            del img, gray
        vidcap.release()
        del vidcap
        gc.collect()  # Force garbage collection
        merged = " | ".join(texts)
        print(f"✅ OCR extracted {len(merged)} chars")
        return merged
    except Exception as e:
        print(f"⚠️ OCR extraction failed: {e}")
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
    
    # Check if it's a photo URL (not supported)
    if "/photo/" in url:
        return jsonify({
            "error": "TikTok photos are not supported. Please use a video URL (URLs with /video/ in them).",
            "message": "Only TikTok videos can be processed, not photos."
        }), 400
    
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

        if not os.path.exists(video_path):
            raise Exception("Video file was not downloaded successfully")
        
        video_size = os.path.getsize(video_path)
        print(f"✅ Video downloaded: {video_path} ({video_size} bytes)")
        
        # Check video size - warn if very large
        if video_size > 50 * 1024 * 1024:  # 50MB
            print(f"⚠️ Large video file ({video_size / 1024 / 1024:.1f}MB) - may cause memory issues")
        
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
