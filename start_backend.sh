#!/bin/bash

# Start PlanIt backend with Google Cloud Vision API

cd "$(dirname "$0")"

# Load Google Cloud Vision service account if available
if [ -f "google-vision-service-account.json" ]; then
    export GOOGLE_VISION_SERVICE_ACCOUNT_JSON=$(cat google-vision-service-account.json | tr -d '\n')
    echo "✅ Google Cloud Vision service account loaded"
else
    echo "ℹ️  Google Vision: Service account file not found - will use Tesseract OCR"
fi

echo ""
echo "🚀 Starting PlanIt backend..."
echo "   Press Ctrl+C to stop"
echo ""

python3 app.py

