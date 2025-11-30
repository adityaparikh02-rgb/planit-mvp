"""
Google Cloud Vision API OCR for high-quality text extraction.

Much more accurate than Tesseract for TikTok images with:
- Stylized fonts
- Text overlays
- Complex backgrounds
- Low resolution images

Pricing: ~$1.50 per 1,000 images (first 1,000 free per month)

Supports both:
1. API Key authentication (GOOGLE_VISION_API_KEY)
2. Service Account JSON (GOOGLE_VISION_SERVICE_ACCOUNT_JSON or file path)
"""

import os
import json
import logging
import requests
from typing import Optional, List

logger = logging.getLogger(__name__)

# Check if Google Cloud Vision is available
GOOGLE_VISION_AVAILABLE = False
GOOGLE_VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY") or os.getenv("GOOGLE_CLOUD_VISION_API_KEY")
GOOGLE_VISION_SERVICE_ACCOUNT = None

# Try to load service account JSON
service_account_json_str = os.getenv("GOOGLE_VISION_SERVICE_ACCOUNT_JSON")
service_account_path = os.getenv("GOOGLE_VISION_SERVICE_ACCOUNT_PATH")

# Fallback: check for local file if env vars not set
if not service_account_path:
    local_file = os.path.join(os.path.dirname(__file__), "google-vision-service-account.json")
    if os.path.exists(local_file):
        service_account_path = local_file
        logger.info(f"📁 Found local Google Vision credentials at {local_file}")

if service_account_json_str:
    try:
        GOOGLE_VISION_SERVICE_ACCOUNT = json.loads(service_account_json_str)
        GOOGLE_VISION_AVAILABLE = True
        logger.info("✅ Google Cloud Vision service account JSON found - will use for OCR")
    except json.JSONDecodeError as e:
        logger.error(f"⚠️ Failed to parse GOOGLE_VISION_SERVICE_ACCOUNT_JSON: {e}")
elif service_account_path and os.path.exists(service_account_path):
    try:
        with open(service_account_path, 'r') as f:
            GOOGLE_VISION_SERVICE_ACCOUNT = json.load(f)
        GOOGLE_VISION_AVAILABLE = True
        logger.info(f"✅ Google Cloud Vision service account file found at {service_account_path}")
    except Exception as e:
        logger.error(f"⚠️ Failed to load service account file: {e}")
elif GOOGLE_VISION_API_KEY:
    GOOGLE_VISION_AVAILABLE = True
    logger.info("✅ Google Cloud Vision API key found - will use for OCR")
else:
    logger.warning("⚠️ GOOGLE_VISION_API_KEY or GOOGLE_VISION_SERVICE_ACCOUNT_JSON not set - will fall back to Tesseract OCR")


def _get_access_token():
    """
    Get OAuth2 access token using service account credentials.
    
    Returns:
        Access token string, or None if failed
    """
    if not GOOGLE_VISION_SERVICE_ACCOUNT:
        return None
    
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        
        credentials = service_account.Credentials.from_service_account_info(
            GOOGLE_VISION_SERVICE_ACCOUNT,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        
        # Refresh token if needed
        if not credentials.valid:
            credentials.refresh(Request())
        
        return credentials.token
    except ImportError:
        logger.error("⚠️ google-auth library not installed. Install with: pip install google-auth")
        return None
    except Exception as e:
        logger.error(f"Failed to get access token: {e}")
        return None


def extract_text_with_google_vision(image_url: str, detect_language: bool = True) -> Optional[str]:
    """
    Extract text from image using Google Cloud Vision API.

    Supports both API key and service account authentication.
    Google Vision automatically detects language and returns text in original language.

    Args:
        image_url: URL of the image to process
        detect_language: If True, logs the detected language

    Returns:
        Extracted text string, or None if failed
    """
    if not GOOGLE_VISION_AVAILABLE:
        logger.warning("Google Cloud Vision not available - missing credentials")
        return None

    try:
        # Download image
        response = requests.get(image_url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        response.raise_for_status()
        image_content = response.content

        # Encode image as base64
        import base64
        image_base64 = base64.b64encode(image_content).decode('utf-8')

        payload = {
            "requests": [{
                "image": {
                    "content": image_base64
                },
                "features": [{
                    "type": "TEXT_DETECTION",
                    "maxResults": 10
                }]
            }]
        }

        # Use service account if available, otherwise use API key
        headers = {"Content-Type": "application/json"}
        if GOOGLE_VISION_SERVICE_ACCOUNT:
            # Use service account authentication
            access_token = _get_access_token()
            if not access_token:
                logger.error("Failed to get access token from service account")
                return None
            headers["Authorization"] = f"Bearer {access_token}"
            vision_url = "https://vision.googleapis.com/v1/images:annotate"
        else:
            # Use API key authentication
            vision_url = f"https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_API_KEY}"

        api_response = requests.post(vision_url, json=payload, headers=headers, timeout=30)
        api_response.raise_for_status()

        result = api_response.json()

        # Extract text from response
        if "responses" in result and len(result["responses"]) > 0:
            response_data = result["responses"][0]

            # Check for errors
            if "error" in response_data:
                error_msg = response_data["error"].get("message", "Unknown error")
                logger.error(f"Google Vision API error: {error_msg}")
                return None

            # Extract text annotations
            text_annotations = response_data.get("textAnnotations", [])
            if text_annotations:
                # First annotation contains all text
                full_text = text_annotations[0].get("description", "")

                # Detect language from the first text annotation
                if detect_language and "locale" in text_annotations[0]:
                    detected_language = text_annotations[0]["locale"]
                    logger.info(f"🌐 Detected language: {detected_language}")
                    logger.info(f"✅ Google Vision extracted {len(full_text)} chars in {detected_language}")
                else:
                    logger.info(f"✅ Google Vision extracted {len(full_text)} chars")

                return full_text.strip()
            else:
                logger.info("⚠️ Google Vision found no text in image")
                return None
        else:
            logger.warning("⚠️ Google Vision API returned unexpected response format")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Google Vision API request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Google Vision OCR error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def extract_text_from_slideshow_google_vision(image_urls: List[str], return_attribution: bool = False):
    """
    Extract text from multiple images using Google Cloud Vision API.

    Args:
        image_urls: List of image URLs to process
        return_attribution: If True, returns dict with attribution data. If False, returns formatted text string (backward compatible)

    Returns:
        If return_attribution=False (default): Concatenated text from all images with slide markers (str)
        If return_attribution=True: Dict with {
            "formatted_text": str,  # Backward compatible formatted text
            "slides_with_attribution": [  # Attribution data
                {
                    "slide_number": int,
                    "tiktok_photo_index": int,  # Same as slide_number (1-indexed)
                    "ocr_lines": [str],  # Individual lines of text
                    "full_text": str  # Combined text for this slide
                },
                ...
            ]
        }
    """
    if not GOOGLE_VISION_AVAILABLE:
        logger.warning("Google Cloud Vision not available")
        if return_attribution:
            return {"formatted_text": "", "slides_with_attribution": []}
        return ""

    all_text = []
    slides_with_attribution = []

    # CRITICAL: Process images in order and assign slide numbers based on position in list
    # This ensures slide numbers match the actual order of images in the TikTok slideshow
    for idx, image_url in enumerate(image_urls, 1):
        try:
            logger.info(f"🔍 Processing slide {idx}/{len(image_urls)} (image {idx} of {len(image_urls)}) with Google Vision...")
            text = extract_text_with_google_vision(image_url)

            # CRITICAL: Always add slide marker, even if no text detected
            # This ensures slide numbers are sequential and match image order
            if text and len(text.strip()) > 0:
                marked_text = f"SLIDE {idx}: {text}"
                all_text.append(marked_text)
                logger.info(f"✅ Slide {idx}: {len(text)} chars extracted")

                # Build attribution data
                ocr_lines = text.split('\n')
                slides_with_attribution.append({
                    "slide_number": idx,
                    "tiktok_photo_index": idx,  # Same as slide_number (1-indexed, matches TikTok photo position)
                    "ocr_lines": ocr_lines,
                    "full_text": text
                })
            else:
                # Still add slide marker even if no text - preserves slide numbering
                marked_text = f"SLIDE {idx}:"
                all_text.append(marked_text)
                logger.info(f"⚠️ Slide {idx}: No text detected (marked as SLIDE {idx} to preserve order)")

                # Build attribution data for empty slide
                slides_with_attribution.append({
                    "slide_number": idx,
                    "tiktok_photo_index": idx,
                    "ocr_lines": [],
                    "full_text": ""
                })

        except Exception as e:
            # Even on error, add slide marker to preserve numbering
            logger.error(f"Failed to process slide {idx}: {e}")
            marked_text = f"SLIDE {idx}:"
            all_text.append(marked_text)

            # Build attribution data for failed slide
            slides_with_attribution.append({
                "slide_number": idx,
                "tiktok_photo_index": idx,
                "ocr_lines": [],
                "full_text": ""
            })
            continue

    combined = "\n".join(all_text)
    logger.info(f"📊 Google Vision extraction complete: {len(image_urls)} slides, {len(combined)} chars total")

    if return_attribution:
        return {
            "formatted_text": combined,
            "slides_with_attribution": slides_with_attribution
        }
    else:
        # Backward compatible: return just the formatted text string
        return combined

