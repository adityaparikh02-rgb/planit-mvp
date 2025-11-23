# Slideshow Extraction Improvements - TODO

## Current Status

✅ **FIXED**: OCR garble detection too aggressive
- Changed threshold from 60% to 90% for slideshow OCR
- OCR with SLIDE markers is now preserved

## Remaining Issues

### 1. Photo Attribution to Venues ⚠️

**Problem**: All venues get the same photo (from Google Maps), but each venue should use the photo from its corresponding TikTok slide.

**Current Flow**:
```
TikTok Slideshow (7 slides) → OCR extraction → Venue extraction → Photo enrichment (Google Maps)
```

**Desired Flow**:
```
TikTok Slideshow (7 slides with URLs) → OCR extraction → Venue extraction →
   Photo attribution: Venue from Slide 2 gets Slide 2's image URL
```

**What Needs to Change**:

1. **In `extract_photo_post()` or slideshow handler**: Pass `photo_urls` list (from TikTok) to enrichment
2. **In `extract_places_and_context()`**: Already returns `venue_to_slide` mapping ✅
3. **In enrichment**: Map `venue_to_slide[venue_name]` → `photo_urls[slide_index]`

**Code Location**: Around line 3200-4100 in `extract_photo_post()` endpoint

**Pseudocode**:
```python
# After getting photo_urls from TikTok
photo_urls = [...]  # List of TikTok slide image URLs

# After extracting venues
venues, summary, venue_to_slide = extract_places_and_context(...)

# During enrichment
for venue in venues:
    slide_key = venue_to_slide.get(venue)  # e.g., "slide_2"
    if slide_key:
        slide_num = int(slide_key.split('_')[1]) - 1  # Convert to 0-indexed
        if 0 <= slide_num < len(photo_urls):
            venue['tiktok_photo_url'] = photo_urls[slide_num]

    # Fallback to Google Maps photo if TikTok photo not available
    if not venue.get('tiktok_photo_url'):
        venue['google_maps_photo_url'] = get_google_photo(...)
```

### 2. Vibe/Context Extraction Per Slide ⚠️

**Problem**: "Vibe" field is empty or generic, but should contain slide-specific content for each venue.

**Current**: Vibe is extracted from combined text
**Desired**: Vibe is extracted from the specific slide where the venue appears

**Example**:
- Slide 2 mentions "Ten11 Lounge" with text: "Great cocktails, rooftop vibes, happy hour 5-7pm"
- Slide 3 has no venue name, but continues with: "DJ on weekends, reservations recommended"
- Ten11's vibe should be: "Great cocktails, rooftop vibes, happy hour 5-7pm. DJ on weekends, reservations recommended"

**What Needs to Change**:

1. **In `extract_places_and_context()`**: When extracting venues per slide, also extract the context/vibe
2. **Store slide-to-content mapping**: `{"slide_2": "Great cocktails...", "slide_3": "DJ on weekends..."}`
3. **Attribute content to venues**: If venue is on slide 2, include slides 2-3 content until next venue appears

**Code Location**: Lines 2270-2372 in `extract_places_and_context()`

**Enhanced Logic**:
```python
# For each venue, collect content from its slide + following slides until next venue
venue_contexts = {}
slides_sorted = sorted(slide_dict.items())

for i, (slide_key, venues) in enumerate(all_venues_per_slide.items()):
    for venue in venues:
        # Get current slide content
        current_content = slide_dict[slide_key]

        # Get following slides until next venue appears
        next_slides = []
        for j in range(i+1, len(slides_sorted)):
            next_key, next_text = slides_sorted[j]
            # If next slide has no venues, attribute it to current venue
            if next_key not in all_venues_per_slide:
                next_slides.append(next_text)
            else:
                break  # Stop at next venue

        # Combine content
        full_context = current_content + "\n".join(next_slides)
        venue_contexts[venue] = full_context

# Later, when creating venue objects:
venue_obj['vibe'] = extract_vibe_from_context(venue_contexts[venue_name])
```

### 3. Extract Structured Information from Slides

**Enhance vibe extraction to look for**:
- Hours: "Open 5pm-2am", "Happy hour 5-7pm"
- Special events: "DJ on weekends", "Live music Thursdays"
- When to go: "Best for dinner", "Great for brunch"
- Notes: "Reservations recommended", "Cash only"
- Menu items: "Try the Old Fashioned", "Best pizza in NYC"

**Use GPT to structure this**:
```python
def extract_slide_context(slide_text, venue_name):
    prompt = f'''
Extract structured information about {venue_name} from this slide:

{slide_text}

Return JSON:
{{
    "vibe": "Overall description",
    "when_to_go": "Best times/occasions",
    "menu_highlights": ["item1", "item2"],
    "special_notes": ["note1", "note2"]
}}
'''
    # Call GPT and parse JSON
    return structured_info
```

## Implementation Priority

1. **HIGH**: Fix OCR garble detection ✅ DONE
2. **HIGH**: Photo attribution (Issue #1)
3. **MEDIUM**: Basic vibe extraction (Issue #2)
4. **LOW**: Structured information extraction (Issue #3)

## Testing

After implementing:
1. Test with the Ariana slideshow: https://www.tiktok.com/@arianaa.n/photo/7572976217981308215
2. Verify:
   - 6 venues extracted (not just 2)
   - Each venue has photo from its corresponding slide
   - Each venue has vibe text from its slide + following contextual slides
   - Structured info like hours, notes, menu items are captured

## Files to Modify

- `app.py` lines 2234-2372: `extract_places_and_context()`
- `app.py` lines 3200-4100: `extract_photo_post()` endpoint
- Possibly add new function: `map_venues_to_photos(venues, photo_urls, venue_to_slide)`
