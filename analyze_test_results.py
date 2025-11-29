#!/usr/bin/env python3
import json
import sys

with open('test_response.json', 'r') as f:
    data = json.load(f)

places = data.get('places_extracted', [])

print("=" * 80)
print("TEST RESULTS ANALYSIS")
print("=" * 80)
print(f"\n✅ Found {len(places)} venues\n")

# Check 1: Blank Street found
blank_street_found = False
for p in places:
    name = p.get('name', '').lower()
    if 'blank street' in name:
        blank_street_found = True
        print(f"✅ CHECK 1: Blank Street FOUND: {p.get('name')}")
        break

if not blank_street_found:
    print("❌ CHECK 1: Blank Street NOT FOUND")

# Check 2: Thai tag on coffee shops
print("\n" + "=" * 80)
print("CHECK 2: Thai tag on coffee shops (should be 0)")
print("=" * 80)
thai_issues = []
for p in places:
    name = p.get('name', '').lower()
    vibe_tags = p.get('vibe_tags', [])
    if 'Thai' in vibe_tags:
        is_coffee = any(word in name for word in ['coffee', 'cafe', 'caffe', 'latte', 'tea', 'elk', 'sip', 'tearoom', 'paradiso'])
        if is_coffee:
            thai_issues.append({
                'name': p.get('name'),
                'tags': vibe_tags,
                'keywords': p.get('vibe_keywords', [])
            })

if thai_issues:
    print(f"❌ FOUND {len(thai_issues)} coffee shops with Thai tag:")
    for issue in thai_issues:
        print(f"   - {issue['name']}: {issue['tags']} (keywords: {issue['keywords']})")
else:
    print("✅ No coffee shops have Thai tag")

# Check 3: Item bleeding (PB&J matcha appearing multiple times)
print("\n" + "=" * 80)
print("CHECK 3: Item bleeding (PB&J matcha latte mentions)")
print("=" * 80)
pbj_mentions = []
for p in places:
    name = p.get('name', '')
    must_try = str(p.get('must_try', '')).lower()
    if 'pb&j' in must_try or 'pb and j' in must_try:
        pbj_mentions.append({
            'name': name,
            'must_try': p.get('must_try', '')[:100]
        })

if pbj_mentions:
    print(f"⚠️  PB&J matcha latte found in {len(pbj_mentions)} venues:")
    for mention in pbj_mentions:
        print(f"   - {mention['name']}: {mention['must_try']}")
    if len(pbj_mentions) > 2:
        print(f"   ❌ TOO MANY - likely bleeding issue")
    else:
        print(f"   ✅ Reasonable number")
else:
    print("✅ PB&J matcha latte not found (or correctly isolated)")

# Check 4: Neighborhoods (should not be "NYC")
print("\n" + "=" * 80)
print("CHECK 4: Neighborhoods (should not be 'NYC')")
print("=" * 80)
nyc_neighborhoods = []
for p in places:
    name = p.get('name', '')
    neighborhood = p.get('neighborhood', '')
    if neighborhood and neighborhood.upper() == 'NYC':
        nyc_neighborhoods.append({'name': name, 'neighborhood': neighborhood})

if nyc_neighborhoods:
    print(f"⚠️  Found {len(nyc_neighborhoods)} venues with 'NYC' as neighborhood:")
    for item in nyc_neighborhoods:
        print(f"   - {item['name']}: {item['neighborhood']}")
else:
    print("✅ No venues have 'NYC' as neighborhood")

# Check 5: Detailed venue analysis
print("\n" + "=" * 80)
print("DETAILED VENUE ANALYSIS")
print("=" * 80)
for i, p in enumerate(places, 1):
    name = p.get('name', 'Unknown')
    neighborhood = p.get('neighborhood', 'N/A')
    vibe_tags = ', '.join(p.get('vibe_tags', [])) or '(none)'
    must_try = p.get('must_try', '')[:80] or '(none)'
    print(f"\n{i}. {name}")
    print(f"   📍 Neighborhood: {neighborhood}")
    print(f"   🏷️  Vibe Tags: {vibe_tags}")
    print(f"   🍴 Must Try: {must_try}")

# Summary
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"Blank Street found: {'✅' if blank_street_found else '❌'}")
print(f"Thai tag issue: {'❌' if thai_issues else '✅'} ({len(thai_issues)} issues)")
print(f"Item bleeding: {'❌' if len(pbj_mentions) > 2 else '✅'} ({len(pbj_mentions)} mentions)")
print(f"Neighborhoods: {'✅' if not nyc_neighborhoods else '⚠️'} ({len(nyc_neighborhoods)} with NYC)")

