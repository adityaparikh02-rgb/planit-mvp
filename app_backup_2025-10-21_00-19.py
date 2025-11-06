import os, tempfile, subprocess, requests, openai
from flask import Flask, request, jsonify
from flask_cors import CORS
import whisper

app = Flask(__name__)
CORS(app)

openai.api_key = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

def log(msg):
    print(f"\n🟦 {msg}\n", flush=True)

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "message": "AI extractor ready"}), 200

@app.route("/api/extract", methods=["POST"])
def extract():
    data = request.get_json()
    video_url = data.get("video_url")
    if not video_url:
        return jsonify({"error": "Missing video_url"}), 400

    try:
        tmp = tempfile.mkdtemp()
        audio_path = os.path.join(tmp, "audio.mp3")

        # 1️⃣ Download TikTok audio
        log(f"Downloading TikTok audio from: {video_url}")
        subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "mp3", "-o", audio_path, video_url],
            check=False,
        )
        if not os.path.exists(audio_path):
            return jsonify({"error": "Failed to download audio"}), 400
        log("✅ Audio downloaded")

        # 2️⃣ Transcribe with Whisper
        log("Loading Whisper model (base)...")
        model = whisper.load_model("base")
        log("Transcribing audio...")
        transcript = model.transcribe(audio_path)["text"]
        log("✅ Transcription complete")

        # 3️⃣ Detect the main city mentioned
        city_prompt = f"""
        From this TikTok transcript, identify the most likely city or metro area being discussed or shown.
        Return ONLY the city name (e.g. 'New York', 'Miami', 'London'). If unclear, guess based on context.
        Transcript:
        {transcript}
        """
        log("🧭 Detecting city from transcript...")
        city_resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": city_prompt}],
            temperature=0
        )
        detected_city = city_resp.choices[0].message.content.strip().split("\n")[0]
        log(f"✅ Detected city: {detected_city}")

        # 4️⃣ Extract place names using that city context
        prompt = f"""
        The TikTok transcript below is from a creator talking about places in {detected_city}.
        Extract the names of all specific venues (bars, pubs, lounges, cafes, restaurants, parks, nightclubs, 
        rooftop bars, or landmarks) explicitly mentioned. Exclude generic neighborhoods or businesses that
        are clearly offices, real estate firms, law firms, or unrelated commercial entities.

        Transcript:
        {transcript}

        Respond strictly as a JSON list, e.g. ["Joe's Pizza", "Old Mates Pub"].
        """

        log("Asking GPT to identify venues...")
        gpt_response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        text = gpt_response.choices[0].message.content
        try:
            places = eval(text)
        except Exception:
            places = [p.strip("-• \n") for p in text.splitlines() if p.strip()]
        log(f"✅ GPT found {len(places)} potential venues: {places}")

        # 5️⃣ Verify via Google Places, filtering by category
        allowed_types = {
            "bar", "cafe", "restaurant", "night_club", "park", "meal_takeaway",
            "tourist_attraction", "food", "point_of_interest", "establishment",
            "lodging", "museum", "art_gallery"
        }

        verified = []
        for p in places:
            r = requests.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={"query": f"{p} {detected_city}", "key": GOOGLE_API_KEY},
            )
            js = r.json()
            if not js.get("results"):
                log(f"❌ No Google match for {p}")
                continue

            place = js["results"][0]
            address = place.get("formatted_address", "")
            types = set(place.get("types", []))

            # filter out irrelevant business types
            if not types.intersection(allowed_types):
                log(f"⚠️ Skipping {p} — not a venue type ({types})")
                continue
            if detected_city.lower() not in address.lower():
                log(f"⚠️ Skipping {p} — address not in {detected_city} ({address})")
                continue

            verified.append({
                "name": place.get("name"),
                "address": address,
                "lat": place["geometry"]["location"]["lat"],
                "lng": place["geometry"]["location"]["lng"],
                "types": list(types)
            })
            log(f"✅ Verified {p}: {address}")

        log(f"🎯 Final verified venues: {len(verified)} in {detected_city}")

        return jsonify({
            "video_url": video_url,
            "detected_city": detected_city,
            "transcript_excerpt": transcript[:300],
            "places_extracted": verified,
            "count": len(verified),
        }), 200

    except Exception as e:
        import traceback
        print("---- ERROR ----")
        traceback.print_exc()
        print("----------------")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
