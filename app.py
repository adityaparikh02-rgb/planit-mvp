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
import tempfile, re, subprocess, json, cv2, numpy as np, requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from pytesseract import image_to_string
from PIL import Image
from moviepy.editor import VideoFileClip
from openai import OpenAI
from httpx import Client as HttpxClient

# ─────────────────────────────
# Setup
# ─────────────────────────────
app = Flask(__name__)
# Allow all origins for now - can restrict later if needed
CORS(app, resources={r"/api/*": {"origins": "*"}})

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
YT_IMPERSONATE = "chrome-131:macos-14"
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
    result1 = subprocess.run(
        f'yt-dlp --skip-download --write-info-json --impersonate "{YT_IMPERSONATE}" '
        f'-o "{tmpdir}/video" "{video_url}"', shell=True, check=False, capture_output=True, text=True)
    if result1.returncode != 0:
        print(f"⚠️ Metadata download warning: {result1.stderr[:200]}")
    
    result2 = subprocess.run(
        f'yt-dlp --impersonate "{YT_IMPERSONATE}" -o "{video_path}" "{video_url}"',
        shell=True, check=False, capture_output=True, text=True)
    if result2.returncode != 0:
        print(f"⚠️ Video download warning: {result2.stderr[:200]}")
        if not os.path.exists(video_path):
            raise Exception(f"Failed to download video: {result2.stderr[:500]}")

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
    """Extract audio from video as WAV for Whisper."""
    try:
        audio_path = video_path.replace(".mp4", ".wav")
        clip = VideoFileClip(video_path)
        clip.audio.write_audiofile(audio_path, verbose=False, logger=None)
        return audio_path
    except Exception as e:
        print("⚠️ Audio extraction failed:", e)
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
    print("🧩 Extracting on-screen text with OCR…")
    vidcap = cv2.VideoCapture(video_path)
    total = int(vidcap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = np.linspace(0, total - 1, min(total, 10), dtype=int)
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
    vidcap.release()
    merged = " | ".join(texts)
    print(f"✅ OCR extracted {len(merged)} chars")
    return merged

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
    combined_text = "\n".join(x for x in [ocr_text, transcript, caption, comments] if x)
    prompt = f"""
You are analyzing a TikTok video about NYC venues.

1️⃣ Extract every **specific** bar, restaurant, café, or food/drink venue mentioned.
   • Use on-screen text, speech, or captions.
   • Ignore broad neighborhoods like "SoHo" or "Brooklyn."
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
        
        print(f"📝 Analyzing content ({len(combined_text)} chars): {combined_text[:200]}...")
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt + combined_text[:3500]}],
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
        caption = meta.get("description", "") or meta.get("title", "")
        comments_text = ""
        if "comments" in meta and isinstance(meta["comments"], list):
            comments_text = " | ".join(c.get("text", "") for c in meta["comments"][:10])

        if not os.path.exists(video_path):
            raise Exception("Video file was not downloaded successfully")
        
        print(f"✅ Video downloaded: {video_path} ({os.path.getsize(video_path)} bytes)")
        
        audio_path = extract_audio(video_path)
        print(f"✅ Audio extracted: {audio_path}")
        
        transcript = transcribe_audio(audio_path)
        print(f"✅ Transcript: {len(transcript)} chars")
        
        ocr_text = extract_ocr_text(video_path)
        print(f"✅ OCR text: {len(ocr_text)} chars")

        venues, context_title = extract_places_and_context(transcript, ocr_text, caption, comments_text)

        # Filter out any remaining placeholder-like venues
        venues = [v for v in venues if not re.search(r"<.*venue.*\d+.*>|^venue\s*\d+$|placeholder", v, re.I)]
        
        if not venues:
            print("⚠️ No valid venues extracted from video")
            # Return empty result instead of placeholder data
            data = {
                "video_url": url,
                "context_summary": context_title,
                "places_extracted": [],
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
