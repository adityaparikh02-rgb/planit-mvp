#!/bin/bash

# Test script for 7-venue slideshow URL
# Tests the fixes for: Thai tag issue, context bleeding, missing Blank Street

URL="https://www.tiktok.com/@bitsof.nyc/photo/7515207485737717034?lang=en"
BACKEND_URL="${1:-http://localhost:5002}"

echo "🧪 Testing 7-venue slideshow extraction..."
echo "URL: $URL"
echo "Backend: $BACKEND_URL"
echo ""

# Make the extraction request (with cache bypass for testing)
echo "📤 Sending extraction request (bypass_cache=true)..."
RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/extract" \
  -H "Content-Type: application/json" \
  -d "{\"video_url\": \"$URL\", \"bypass_cache\": true}")

# Check if we got a response
if [ $? -eq 0 ]; then
  echo "✅ Request successful"
  echo ""
  
  # Save response to file for inspection
  echo "$RESPONSE" > test_response.json
  echo "📄 Full response saved to test_response.json"
  echo ""
  
  # Extract key information
  echo "📊 Extracted venues:"
  echo "$RESPONSE" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    if 'places' in data:
        places = data['places']
        print(f'Found {len(places)} venues:')
        for i, p in enumerate(places, 1):
            name = p.get('name', 'Unknown')
            neighborhood = p.get('neighborhood', 'NYC')
            vibe_tags = ', '.join(p.get('vibe_tags', []))
            must_try = p.get('must_try', '')
            print(f'{i}. {name}')
            print(f'   📍 {neighborhood}')
            print(f'   🏷️  {vibe_tags if vibe_tags else \"(no tags)\"}')
            if must_try:
                print(f'   🍴 {must_try[:80]}...')
            print()
        
        # Check for issues
        print('🔍 Checking for issues:')
        blank_street_found = any('blank street' in p.get('name', '').lower() for p in places)
        print(f'   ✓ Blank Street found: {blank_street_found}')
        
        thai_on_coffee = sum(1 for p in places if 'Thai' in p.get('vibe_tags', []) and any(word in p.get('name', '').lower() for word in ['coffee', 'cafe', 'caffe', 'latte']))
        print(f'   ✓ Coffee shops with Thai tag: {thai_on_coffee} (should be 0)')
        
        # Check for item bleeding (PB&J matcha appearing multiple times)
        pbj_count = sum(1 for p in places if 'PB&J' in str(p.get('must_try', '')))
        print(f'   ✓ PB&J matcha latte mentions: {pbj_count} (should be 1-2 max)')
        
    else:
        print('❌ No places found in response')
        if 'error' in data:
            print(f'Error: {data.get(\"error\")}')
except Exception as e:
    print(f'Error parsing response: {e}')
    print('Raw response:')
    sys.stdin.seek(0)
    print(sys.stdin.read()[:500])
"
else
  echo "❌ Request failed"
  exit 1
fi

echo ""
echo "✅ Test complete! Check test_response.json for full details."

