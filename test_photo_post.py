#!/usr/bin/env python3
"""
Test script for TikTok photo post extraction.
Run this to test the photo post functionality.

Usage:
    ./test_photo_post.py <TikTok_photo_post_URL>
    OR
    python3 test_photo_post.py <TikTok_photo_post_URL>
"""

import sys
import os

# Add venv to path if it exists
if os.path.exists("venv"):
    venv_site_packages = os.path.join("venv", "lib", "python3.11", "site-packages")
    if os.path.exists(venv_site_packages):
        sys.path.insert(0, venv_site_packages)
    # Also try python3.12, python3.10, etc.
    for py_version in ["3.12", "3.10", "3.9"]:
        alt_path = os.path.join("venv", "lib", f"python{py_version}", "site-packages")
        if os.path.exists(alt_path):
            sys.path.insert(0, alt_path)
            break

try:
    import requests
    import json
except ImportError:
    print("❌ Error: 'requests' module not found")
    print("\n💡 Solutions:")
    print("   1. Use the wrapper script: ./test_photo_post.sh <URL>")
    print("   2. Activate venv first: source venv/bin/activate")
    print("   3. Use venv Python directly: venv/bin/python3 test_photo_post.py <URL>")
    sys.exit(1)

# Configuration
API_BASE = "http://localhost:5001"  # Change if your backend runs on different port

def test_photo_post(url):
    """Test photo post extraction."""
    print(f"🧪 Testing photo post extraction...")
    print(f"📸 URL: {url}")
    print(f"🌐 API: {API_BASE}/api/extract")
    print("-" * 60)
    
    try:
        response = requests.post(
            f"{API_BASE}/api/extract",
            json={"video_url": url},
            headers={"Content-Type": "application/json"},
            timeout=300  # 5 minutes timeout
        )
        
        print(f"📊 Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if "error" in data:
                print(f"❌ Error: {data.get('error')}")
                print(f"💬 Message: {data.get('message')}")
                return False
            
            print(f"✅ Success!")
            print(f"📝 Summary: {data.get('summary_title') or data.get('context_summary', 'N/A')}")
            print(f"📍 Places Found: {len(data.get('places_extracted', []))}")
            
            # Show actual caption that was extracted
            if data.get('caption_extracted'):
                print(f"\n📋 Caption Extracted ({len(data.get('caption_extracted', ''))} chars):")
                print(f"   {data.get('caption_extracted', '')[:200]}{'...' if len(data.get('caption_extracted', '')) > 200 else ''}")
            
            if data.get('places_extracted'):
                print("\n🏢 Extracted Places:")
                for i, place in enumerate(data.get('places_extracted', []), 1):
                    print(f"  {i}. {place.get('name', 'Unknown')}")
                    if place.get('address'):
                        print(f"     📍 {place.get('address')}")
            else:
                print("\n⚠️ No places found. Check the caption above to see if it contains venue names.")
            
            if data.get('photo_urls'):
                print(f"\n📸 Photo URLs: {len(data.get('photo_urls', []))} photos")
            
            return True
        else:
            print(f"❌ Failed with status {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ Request timed out (exceeded 5 minutes)")
        return False
    except requests.exceptions.ConnectionError:
        print(f"❌ Connection error - is the backend running at {API_BASE}?")
        print("   Start it with: ./run_local.sh")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # Example TikTok photo post URL (replace with a real one)
    # Format: https://www.tiktok.com/@username/photo/1234567890
    test_url = None
    
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
    else:
        print("📝 Usage: python test_photo_post.py <TikTok_photo_post_URL>")
        print("\nExample:")
        print("  python test_photo_post.py 'https://www.tiktok.com/@username/photo/1234567890'")
        print("\nOr provide URL as argument:")
        test_url = input("\nEnter TikTok photo post URL: ").strip()
    
    if not test_url:
        print("❌ No URL provided")
        sys.exit(1)
    
    if "/photo/" not in test_url.lower():
        print("⚠️  Warning: URL doesn't contain '/photo/' - this might not be a photo post")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            sys.exit(1)
    
    success = test_photo_post(test_url)
    sys.exit(0 if success else 1)

