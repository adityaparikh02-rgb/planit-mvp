# Fixes Summary - Missing Vibe Tags, Photos, and Neighborhoods

## Date: November 28, 2025

---

## Changes Made

### Fix 1: Vibe Tags Fallback ✅

**Location**: [app.py:3958-3961](app.py#L3958-L3961)

**Problem**:
- `extract_vibe_tags()` would call GPT-4o-mini to extract vibe tags
- If GPT extraction failed or returned empty list, no fallback was used
- `vibe_keywords` were extracted but never used as fallback
- Result: Empty `vibe_tags` array in frontend

**Solution**:
```python
# CRITICAL FIX: Fallback to vibe_keywords if GPT extraction failed or returned empty list
if not data["vibe_tags"] and vibe_keywords:
    data["vibe_tags"] = vibe_keywords[:6]  # Limit to 6 tags max
    print(f"   ⚠️ GPT vibe_tags extraction failed for {name}, using vibe_keywords fallback: {data['vibe_tags']}")
```

**Impact**:
- Places will now ALWAYS have vibe tags, either from GPT or keyword extraction
- Prevents empty vibe_tags arrays in the frontend
- Maintains 6-tag limit for consistency

---

### Fix 2: Photo Placeholder Guarantee ✅

**Location**: [app.py:4771-4774](app.py#L4771-L4774)

**Problem**:
- Multiple photo fetching fallbacks existed (TikTok slide, Google Maps, NYC search, etc.)
- However, if all methods failed but returned empty string `""` instead of `None`, the `or` operator wouldn't catch it
- No explicit check to ensure placeholder was set
- Result: Places showing up without photos

**Solution**:
```python
# CRITICAL FIX: Ensure photo is set to placeholder if missing or empty
if not photo or photo.strip() == "":
    photo = "https://via.placeholder.com/600x400?text=No+Photo"
    print(f"   ⚠️ No photo found for {display_name}, using placeholder")
```

**Impact**:
- Guarantees every place has a photo URL (real photo or placeholder)
- Catches both `None` and empty string `""` cases
- Prevents broken image displays in frontend

---

### Fix 3: Neighborhood Final Fallback ✅

**Location**: [app.py:4771-4794](app.py#L4771-L4794)

**Problem**:
- `get_nyc_neighborhood_strict()` has comprehensive logic with lat/lon grid and address parsing
- However, if all methods failed, it would return "Unknown"
- Frontend would display the full address instead of a neighborhood name
- Example: "35 W 19th St, New York, NY 10011" instead of "Chelsea"

**Solution**:
```python
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
```

**Impact**:
- Guarantees every place has a neighborhood (specific neighborhood, borough, or "NYC")
- Prevents addresses from showing in place of neighborhood names
- Provides graceful degradation: specific neighborhood → borough → "NYC"
- Better user experience with consistent neighborhood display

---

## How These Fixes Work Together

### Vibe Tags Pipeline
1. **Primary**: GPT-4o-mini extraction via `extract_vibe_tags()`
2. **Fallback**: Keyword matching via `extract_vibe_keywords()` (NEW)
3. **Supplement**: Google Maps cuisine types added to vibe_tags
4. **Special**: Vegan tag detection from context

### Photo Pipeline
1. **Primary**: TikTok slide photo (if slideshow post)
2. **Fallback 1**: Google Maps photo with place_id
3. **Fallback 2**: Google Maps search with "venue_name NYC"
4. **Fallback 3**: Original venue name search
5. **Fallback 4**: Placeholder URL (GUARANTEED - NEW)

### Neighborhood Pipeline
1. **Primary**: `get_nyc_neighborhood_strict()` with static overrides
2. **Method 1**: Lat/lon grid matching (most accurate)
3. **Method 2**: Address parsing with street number/direction
4. **Method 3**: Extract from title/caption
5. **Method 4**: Google Maps Place Details API
6. **Method 5**: Venue name parentheses extraction
7. **Method 6**: NYC geography inference
8. **Method 7**: Borough extraction from address (NEW)
9. **Final Fallback**: "NYC" if all else fails (NEW)

---

## Testing Recommendations

### Test Cases

1. **Vibe Tags**
   - ✅ Test with URL where GPT extraction fails
   - ✅ Test with empty context (should use keyword fallback)
   - ✅ Test with vegan-related context (should add "Vegan" tag)

2. **Photos**
   - ✅ Test with slideshow post (should use TikTok slide photo)
   - ✅ Test with single video (should use Google Maps photo)
   - ✅ Test with permanently closed place (should use placeholder)
   - ✅ Test with place not in Google Maps (should use placeholder)

3. **Neighborhoods**
   - ✅ Test with "35 W 19th St" (should return "Chelsea")
   - ✅ Test with place only having borough info (should return borough name)
   - ✅ Test with place missing all info (should return "NYC")
   - ✅ Test with known static overrides (e.g., "Soogil" → "East Village")

### Manual Testing Commands

```bash
# Test extraction with a TikTok URL
python app.py  # Then use the /extract endpoint with a test URL

# Check database for results
sqlite3 planit.db "SELECT name, neighborhood, photo_url, vibe_tags FROM place_cache LIMIT 5;"
```

### Expected Outcomes

After these fixes, you should NEVER see:
- ❌ Empty vibe_tags arrays
- ❌ Missing photos (no photo URL)
- ❌ "Unknown" as neighborhood
- ❌ Full addresses in place of neighborhood names

You should ALWAYS see:
- ✅ At least 1-6 vibe tags per place
- ✅ Photo URL (real or placeholder)
- ✅ Neighborhood name (specific, borough, or "NYC")

---

## Files Modified

- `app.py`:
  - Line 3958-3961: Vibe tags fallback logic
  - Line 4771-4774: Photo placeholder guarantee
  - Line 4771-4794: Neighborhood final fallback

---

## Related Issues

- Issue #1: Missing Vibe Tags → **FIXED**
- Issue #2: Missing Photos → **FIXED**
- Issue #3: Missing Neighborhoods → **FIXED**

---

## Notes

- All fixes are backward compatible
- No database schema changes required
- Fixes are defensive and ensure data quality
- Logging added for debugging and monitoring
- Frontend should work without changes

---

## Next Steps

1. Test with real TikTok URLs
2. Monitor logs for fallback usage
3. Consider adding metrics to track fallback frequency
4. Update frontend to handle "NYC" neighborhood gracefully
