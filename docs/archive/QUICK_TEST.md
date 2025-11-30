# Quick Testing Guide

## Current Status ✅

- ✅ Backend running on `http://localhost:5001`
- ✅ Frontend running on `http://localhost:3000`
- ✅ All features implemented in code

## Quick Test Steps

### 1. Open Frontend
Navigate to: **http://localhost:3000**

### 2. Test Extraction
Paste a TikTok URL and click "Extract"

**Example URLs:**
- Restaurant: `https://www.tiktok.com/@felieats/video/7210172038814895406`
- Bars: `https://www.tiktok.com/@lisaboccuzzi/video/7194940851905219883`
- Slideshow: `https://www.tiktok.com/@beli_eats/video/7570546677053000974`

### 3. Verify Each Place Has:

#### ✅ Location (Neighborhood)
- **Should show:** "Upper East Side", "SoHo", "West Village", etc.
- **NOT:** "Location", "NYC", or raw address
- **Check backend logs for:** `📍 Found neighborhood from Place Details`

#### ✅ Photo
- **Should show:** Actual photo from Google Maps or TikTok
- **NOT:** Placeholder image
- **Check backend logs for:** `📸 Got photo via...`

#### ✅ Commentary/Description
- **Should show:** 2-3 sentence description
- **Should be:** Third person (no "I", "we", "my")
- **Check backend logs for:** GPT extraction completing

#### ✅ Vibe Tags
- **Should show:** Relevant tags like "Cozy", "Trendy", "Italian"
- **For restaurants:** Should include cuisine type ("Indian", "Italian", etc.)
- **Check backend logs for:** `✅ Added Google Maps cuisine tag: [cuisine]`

## Backend Log Monitoring

Watch Terminal 1 (backend) for these success indicators:

```
✅ Extraction complete — X places found
📍 Found neighborhood from Place Details (neighborhood): [neighborhood]
📸 Got photo via...
✅ Added Google Maps cuisine tag: [cuisine]
🤖 GPT raw response: {"summary": "...", "vibe": "...", ...}
```

## Common Issues

| Issue | Solution |
|-------|----------|
| "No venues found" | Check backend logs for GPT errors, verify OPENAI_API_KEY |
| Shows "Location" instead of neighborhood | Check backend logs for Google API errors, verify GOOGLE_API_KEY |
| No photos | Check backend logs, verify Google Places API is enabled |
| No cuisine tags | Check if place is a restaurant, verify Google Maps returns `types` field |
| First person in descriptions | This is a bug - descriptions should be third person only |

## Feature Checklist

- [ ] Location: Specific neighborhood (not "Location" or "NYC")
- [ ] Photo: Actual photo (not placeholder)
- [ ] Commentary: Description populated (not empty)
- [ ] Vibe Tags: Tags present (including cuisine for restaurants)
- [ ] Third Person: No "I", "we", "my" in descriptions

## Test URLs by Category

**Restaurants:**
- `https://www.tiktok.com/@felieats/video/7210172038814895406` (Indian)
- `https://www.tiktok.com/@beli_eats/video/7570546677053000974` (Italian slideshow)

**Bars:**
- `https://www.tiktok.com/@lisaboccuzzi/video/7194940851905219883` (Upscale bars)
- `https://www.tiktok.com/@hudsonyardsny/photo/7355264621512576298` (Hudson Yards bars)

**Slideshows:**
- `https://www.tiktok.com/@beli_eats/video/7570546677053000974` (Multiple Italian restaurants)
- `https://www.tiktok.com/@thedetailedlocal/photo/7573037434431114526` (West Village spots)

