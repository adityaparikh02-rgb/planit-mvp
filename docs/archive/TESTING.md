# PlanIt Testing Instructions

## Prerequisites

1. **Backend dependencies installed:**
   ```bash
   cd /Users/aditya/planit-codex
   source venv/bin/activate  # or create venv if needed
   pip install -r requirements.txt
   ```

2. **Environment variables set:**
   - `OPENAI_API_KEY` - Required for GPT extraction
   - `GOOGLE_API_KEY` - Required for Google Maps/Places API
   - `JWT_SECRET_KEY` - Optional (auto-generated if missing)

3. **Frontend dependencies installed:**
   ```bash
   cd client
   npm install
   ```

## Step 1: Start Backend

In Terminal 1:
```bash
cd /Users/aditya/planit-codex
bash run_local.sh
```

**Expected output:**
- ✅ Backend starts on `http://localhost:5001`
- ✅ Health check endpoint responds: `{"status": "ok", ...}`
- ✅ No syntax errors

**Verify backend is running:**
```bash
curl http://localhost:5001/api/healthz
```

Should return JSON with `"status": "ok"` and API test results.

## Step 2: Start Frontend

In Terminal 2:
```bash
cd /Users/aditya/planit-codex
bash run_frontend.sh
```

**Expected output:**
- ✅ Frontend starts on `http://localhost:3000`
- ✅ Browser opens automatically (or navigate to `http://localhost:3000`)
- ✅ Console shows: `🔧 API_BASE: http://localhost:5001`

## Step 3: Test Extraction

### Test Case 1: Basic Video Extraction

1. Open `http://localhost:3000` in your browser
2. Paste a TikTok URL (e.g., `https://www.tiktok.com/@hudsonyardsny/photo/7355264621512576298`)
3. Click "Extract"
4. Wait for extraction to complete (watch backend logs for progress)

**What to verify:**
- ✅ Places are extracted (not "No venues found")
- ✅ Each place shows a **specific neighborhood** (not "Location" or "NYC")
- ✅ Each place has a **photo** (not placeholder)
- ✅ Each place has **commentary/description** (summary field populated)
- ✅ Each place has **vibe tags** (including cuisine type for restaurants)

### Test Case 2: Verify Features

For each extracted place, check:

#### ✅ Location Feature
- **Expected:** Specific neighborhood like "Upper East Side", "SoHo", "West Village"
- **NOT:** "Location", "NYC", or raw address
- **Backend log should show:** `📍 Found neighborhood from Place Details` or similar

#### ✅ Photo Feature
- **Expected:** Actual photo from Google Maps or TikTok
- **NOT:** Placeholder image
- **Backend log should show:** `📸 Got photo via...` or `📸 Using photo from...`

#### ✅ Commentary/Description Feature
- **Expected:** 2-3 sentence description about the place
- **NOT:** Empty or generic text
- **Backend log should show:** GPT extraction completing successfully
- **Check:** Description should be in **third person** (no "I", "we", "my")

#### ✅ Vibe Tags Feature
- **Expected:** Relevant vibe tags like "Cozy", "Trendy", "Italian", etc.
- **For restaurants:** Should include cuisine type (e.g., "Indian", "Italian", "Chinese")
- **Backend log should show:** `✅ Added Google Maps cuisine tag: [cuisine]` for restaurants

## Step 4: Check Backend Logs

While testing, watch Terminal 1 (backend) for:

### Success Indicators:
- `✅ Extraction complete — X places found`
- `📍 Found neighborhood from Place Details`
- `📸 Got photo via...`
- `✅ Added Google Maps cuisine tag: [cuisine]`
- `🤖 GPT raw response:` (should show JSON with summary, vibe, etc.)

### Error Indicators:
- `❌` or `⚠️` warnings
- `REQUEST_DENIED` (Google API issue)
- `No venues found` (extraction failed)

## Step 5: Test Specific Scenarios

### Scenario A: Restaurant with Cuisine Type
**Test URL:** Any TikTok video about a restaurant

**Verify:**
- Cuisine type appears in vibe tags (e.g., "Indian", "Italian")
- Description mentions food/cuisine
- Location is specific neighborhood

### Scenario B: Bar/Cocktail Spot
**Test URL:** Any TikTok video about a bar

**Verify:**
- Vibe tags include bar-related tags (e.g., "Cocktail Bar", "Wine Bar")
- Description mentions drinks/atmosphere
- Location is specific neighborhood

### Scenario C: Slideshow with Multiple Places
**Test URL:** TikTok photo slideshow with multiple venues

**Verify:**
- All places from slideshow are extracted
- Each place has correct context (no mixing between venues)
- Last slide venues are included

## Troubleshooting

### Backend won't start:
```bash
# Check for syntax errors
python3 -m py_compile app.py

# Check if port is in use
lsof -i :5001

# Kill process if needed
kill -9 $(lsof -t -i:5001)
```

### Frontend can't connect:
- Verify backend is running: `curl http://localhost:5001/api/healthz`
- Check `REACT_APP_API_URL` is set to `http://localhost:5001`
- Check browser console for errors

### No venues extracted:
- Check backend logs for GPT errors
- Verify `OPENAI_API_KEY` is set
- Check if video URL is valid and accessible

### No photos:
- Check backend logs for photo fetching attempts
- Verify `GOOGLE_API_KEY` is set
- Check Google Places API quota/status

### No neighborhood:
- Check backend logs for neighborhood extraction attempts
- Verify `GOOGLE_API_KEY` is set and Places API is enabled
- Check if address is in NYC

### No cuisine tags:
- Check backend logs for `place_types_from_google`
- Verify Google Places API returns `types` field
- Check if place is a restaurant (not all places have cuisine types)

## Quick Test Checklist

- [ ] Backend running on port 5001
- [ ] Frontend running on port 3000
- [ ] Health check endpoint returns success
- [ ] Can extract venues from TikTok URL
- [ ] Each venue has specific neighborhood (not "Location")
- [ ] Each venue has photo
- [ ] Each venue has description/commentary
- [ ] Each venue has vibe tags
- [ ] Restaurants have cuisine type in vibe tags
- [ ] Descriptions are in third person (no first person)

## Example Test URLs

1. **Restaurant:** `https://www.tiktok.com/@felieats/video/7210172038814895406` (Indian restaurant)
2. **Bars:** `https://www.tiktok.com/@lisaboccuzzi/video/7194940851905219883` (Upscale bars)
3. **Slideshow:** `https://www.tiktok.com/@beli_eats/video/7570546677053000974` (Italian restaurants)

