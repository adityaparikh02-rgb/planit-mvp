# Google Cloud Vision API Setup

## Service Account JSON Provided ✅

Your service account JSON has been saved to: `google-vision-service-account.json`

## Setup Options

### Option 1: Set Environment Variable (Recommended)

```bash
export GOOGLE_VISION_SERVICE_ACCOUNT_JSON='{"type":"service_account","project_id":"planit-475303",...}'
```

Or load from file:
```bash
export GOOGLE_VISION_SERVICE_ACCOUNT_JSON=$(cat google-vision-service-account.json)
```

### Option 2: Use File Path

```bash
export GOOGLE_VISION_SERVICE_ACCOUNT_PATH="./google-vision-service-account.json"
```

## Install Dependencies

```bash
pip3 install --break-system-packages google-auth google-auth-oauthlib
```

Or if using a virtual environment:
```bash
pip install google-auth google-auth-oauthlib
```

## Test

After setting the environment variable and installing dependencies, restart your backend:

```bash
python3 app.py
```

You should see:
```
✅ Google Cloud Vision service account JSON found - will use for OCR
```

## Usage

The code will automatically:
1. Use Google Cloud Vision API if service account is set (most accurate)
2. Fall back to Tesseract OCR if not set

## Pricing

- First 1,000 images/month: **FREE**
- After that: ~$1.50 per 1,000 images

