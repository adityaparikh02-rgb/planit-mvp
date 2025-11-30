# Issues: Missing Vibe Tags, Photos, and Neighborhoods

## Overview
This document tracks the ongoing issues with missing vibe tags, photos, and neighborhoods in the PlanIt extraction results, along with attempted fixes and current status.

---

## Issue 1: Missing Vibe Tags

### Problem
Extracted places are showing up without vibe tags in the frontend, even though the extraction pipeline includes vibe tag generation logic.

### Current Implementation

#### Vibe Tags Extraction Flow
1. **Primary Method**: `extract_vibe_tags()` function (line ~4052)
   - Uses GPT-4o-mini to extract vibe tags from context text
   - Returns JSON list of 3-6 positive vibe tags
   - Called in `enrich_place_intel()` at line 3956

2. **Fallback Method**: `extract_vibe_keywords()` function (line ~3988)
   - Keyword-based extraction from text
   - Returns list of matching keywords from predefined list
   - Currently NOT used as fallback if GPT extraction fails

3. **Google Maps Supplement**: Place types from Google Maps API
   - Cuisine types are added to vibe_tags (line ~4718-4753)
   - Only adds cuisine tags, not general vibe tags

### What We've Tried

1. **Enhanced GPT Prompt** (line ~4057-4073)
   - Made prompt more strict about extracting positive tags only
   - Added examples and specific instructions
   - Limited to 3-6 tags

2. **Added Vegan Tag Detection** (line ~3958-3964)
   - Automatically adds "Vegan" tag if mentioned in context
   - Works correctly

3. **Google Maps Place Types Integration** (line ~4718-4753)
   - Extracts cuisine types from Google Maps place types
   - Maps specific restaurant types (e.g., "indian_restaurant" → "Indian")
   - Only adds cuisine tags, not general vibe tags like "Bar", "Rooftop", etc.

### Known Issues

1. **No Fallback When GPT Fails**
   - If `extract_vibe_tags()` returns empty list (GPT failure or empty context), no fallback is used
   - `vibe_keywords` are extracted but not used as fallback
   - Result: Empty vibe_tags array

2. **Context May Be Empty**
   - If `slide_context` is None and transcript/OCR/caption are empty, `context` variable may be empty
   - Empty context → GPT returns empty list → No vibe tags

3. **Silent Failures**
   - GPT API failures are caught but only logged, not handled
   - No retry logic or fallback mechanism

### Root Cause Analysis

**Primary Issue**: `extract_vibe_tags()` can fail silently or return empty list, and there's no fallback to `vibe_keywords` or Google Maps place types.

**Secondary Issue**: Context building may result in empty string, causing GPT extraction to fail.

---

## Issue 2: Missing Photos

### Problem
Extracted places are showing up without photos in the frontend, even though multiple photo fetching methods exist.

### Current Implementation

#### Photo Fetching Priority (in `enrich_and_fetch_photo()`)
1. **TikTok Slide Photo** (line ~4595-4603)
   - Uses photo from the specific slide where venue was found
   - Only works for slideshow posts with `source_slide` info

2. **Google Maps Photo** (line ~4606-4609)
   - Uses `get_photo_url()` with place_id from Place Details API
   - Requires successful Google Maps API call

3. **Fallback Search with NYC** (line ~4612-4617)
   - Searches Google Maps with "{venue_name} NYC"
   - Calls `get_photo_url()` without place_id

4. **Original Venue Name Search** (line ~4620-4625)
   - Uses original venue name (before canonicalization)
   - Last resort before placeholder

5. **Placeholder** (line ~4769)
   - `"https://via.placeholder.com/600x400?text=No+Photo"`
   - Should be set if all above fail

### What We've Tried

1. **Multiple Photo Fetch Fallbacks** (lines 4595-4625)
   - Added 4 different photo fetching strategies
   - Each has logging to track which method succeeded

2. **Permanently Closed Handling** (line ~4590-4592)
   - Skips photo fetching for permanently closed places
   - Sets `is_permanently_closed` flag

3. **Enhanced Place Details API** (line ~4369-4370)
   - Fetches `photos` array from Place Details API
   - Passes photos to `get_photo_url()`

### Known Issues

1. **Placeholder May Not Be Set**
   - If all photo fetch attempts fail but don't explicitly set `photo = None`, placeholder might not be used
   - Need explicit check: `if not photo: photo = placeholder`

2. **Google Maps API Failures**
   - If Google Maps API fails (REQUEST_DENIED, OVER_QUERY_LIMIT, etc.), no photos are fetched
   - Fallback searches may also fail if API key is invalid

3. **TikTok Photo URLs May Expire**
   - TikTok photo URLs have expiration signatures
   - URLs may be valid at extraction time but expire before frontend displays

4. **Silent Failures**
   - Photo fetch failures are logged but don't guarantee placeholder is set
   - Need explicit fallback to placeholder

### Root Cause Analysis

**Primary Issue**: Photo fetching has multiple fallbacks but no guarantee that placeholder is set if all fail.

**Secondary Issue**: Google Maps API failures prevent photo fetching, and fallback searches may also fail.

---

## Issue 3: Missing Neighborhoods

### Problem
Extracted places are showing up with addresses instead of specific neighborhood names (e.g., "35 W 19th St" instead of "Chelsea").

### Current Implementation

#### Neighborhood Extraction Priority (in `enrich_and_fetch_photo()`)
1. **Strict Neighborhood Function** (`get_nyc_neighborhood_strict()`) - line ~4756-4761
   - RULE 1: Static overrides (venue name exact matches)
   - RULE 2: Lat/lon grid matching (if coordinates available)
   - RULE 3: Address parsing (if only address available)
   - Returns "Unknown" if no match

2. **Title/Caption Extraction** (line ~4331-4340)
   - Extracts neighborhood from video title/caption
   - Uses `_extract_neighborhood_from_text()`

3. **Place Details API** (line ~4373-4555)
   - Fetches neighborhood from Google Maps Place Details API
   - Filters out generic locations (Manhattan, Brooklyn, etc.)

4. **Place Name Parentheses** (line ~4560-4583)
   - Extracts neighborhood from parentheses in venue name (e.g., "Bar (NOMAD)")

5. **Address Parsing** (line ~4628-4633)
   - Uses `_extract_neighborhood_from_address()`

6. **NYC Geography Inference** (line ~4636-4641)
   - Uses `infer_nyc_neighborhood_from_address()`
   - Infers from street numbers and directions

7. **Address Components** (line ~4644-4654)
   - Parses address parts individually

8. **Venue Name Extraction** (line ~4657-4662)
   - Extracts neighborhood from venue name itself

9. **Borough Fallback** (line ~4665-4672)
   - Extracts borough from address as last resort

### What We've Tried

1. **Strict Neighborhood Function** (`get_nyc_neighborhood_strict()`) - line ~2163
   - Implemented strict priority-based extraction
   - Added static overrides for specific venues
   - Added lat/lon grid matching for Manhattan, Brooklyn, Queens
   - Enhanced address parsing to handle full Google Maps addresses

2. **Improved Address Parsing** (line ~2337-2390)
   - Handles full addresses like "35 W 19th St, New York, NY 10011"
   - Extracts street number and direction
   - Maps to neighborhoods based on street ranges

3. **Enhanced Place Details API** (line ~4373-4555)
   - Filters out generic locations early
   - Matches to known neighborhood list
   - Handles API errors gracefully

4. **Multiple Fallback Methods** (lines 4628-4672)
   - 6 different fallback methods for neighborhood extraction
   - Each has logging to track which method succeeded

### Known Issues

1. **Google Maps API May Return Generic Locations**
   - Place Details API may return "Manhattan" or "Brooklyn" instead of specific neighborhood
   - Filtering logic exists but may not catch all cases

2. **Address Parsing May Fail**
   - Complex addresses may not match regex patterns
   - Street number extraction may fail for non-standard formats

3. **Lat/Lon May Not Be Available**
   - If Place Details API fails, lat/lon are not available
   - Strict function falls back to address parsing, which may also fail

4. **"Unknown" May Be Returned**
   - If all methods fail, `get_nyc_neighborhood_strict()` returns "Unknown"
   - Frontend may display address instead of "Unknown"

### Root Cause Analysis

**Primary Issue**: Neighborhood extraction has many fallbacks, but if Google Maps API fails and address parsing fails, "Unknown" is returned and frontend shows address.

**Secondary Issue**: Address parsing regex may not handle all address formats correctly.

---

## Common Patterns Across All Issues

### 1. Silent Failures
- All three issues involve silent failures where errors are logged but not handled
- No explicit fallback guarantees (e.g., vibe_tags fallback, photo placeholder, neighborhood fallback)

### 2. Google Maps API Dependency
- All three issues depend on Google Maps API working correctly
- API failures cascade to all three issues

### 3. Missing Explicit Checks
- No explicit checks to ensure values are set before returning
- Need: `if not vibe_tags: vibe_tags = fallback`
- Need: `if not photo: photo = placeholder`
- Need: `if not neighborhood: neighborhood = address_fallback`

---

## Proposed Solutions

### For Vibe Tags
1. Add fallback to `vibe_keywords` when GPT extraction fails
2. Ensure context is always populated from available sources
3. Enhance Google Maps place types to add general vibe tags (Bar, Rooftop, etc.)
4. Add explicit check: `if not vibe_tags: vibe_tags = vibe_keywords[:6]`

### For Photos
1. Add explicit placeholder check: `if not photo: photo = placeholder`
2. Improve error handling for each photo fetch attempt
3. Add retry logic for Google Maps API failures
4. Consider caching photo URLs to avoid expiration issues

### For Neighborhoods
1. Improve address parsing regex to handle more formats
2. Add more static overrides for common venues
3. Enhance lat/lon grid matching with more granular boundaries
4. Ensure frontend displays address if neighborhood is "Unknown"

---

## Testing Checklist

- [ ] Test with TikTok URL that has no vibe tags → Verify fallback works
- [ ] Test with TikTok URL that has no photos → Verify placeholder appears
- [ ] Test with TikTok URL that has addresses but no neighborhoods → Verify neighborhood extraction works
- [ ] Test with Google Maps API disabled → Verify fallbacks work
- [ ] Test with empty context → Verify vibe_tags fallback works
- [ ] Test with permanently closed places → Verify no photo but flag is set

---

## Related Files

- `app.py`: Main backend file with all extraction logic
  - `enrich_place_intel()`: Vibe tags extraction (line ~3514)
  - `enrich_and_fetch_photo()`: Photo and neighborhood extraction (line ~4258)
  - `extract_vibe_tags()`: GPT-based vibe tag extraction (line ~4052)
  - `extract_vibe_keywords()`: Keyword-based vibe extraction (line ~3988)
  - `get_nyc_neighborhood_strict()`: Strict neighborhood extraction (line ~2163)
  - `get_photo_url()`: Photo fetching from Google Maps (line ~1940)

- `client/src/App.js`: Frontend display logic
  - Vibe tags rendering (line ~1614)
  - Photo display (line ~1200+)
  - Neighborhood display (line ~1200+)

---

## Last Updated
November 28, 2025


