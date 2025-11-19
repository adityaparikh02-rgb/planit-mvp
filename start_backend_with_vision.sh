#!/bin/bash

# Start backend with Google Cloud Vision API enabled

cd "$(dirname "$0")"

# Load service account JSON from file
if [ -f "google-vision-service-account.json" ]; then
    export GOOGLE_VISION_SERVICE_ACCOUNT_JSON=$(cat google-vision-service-account.json | tr -d '\n')
    echo "✅ Google Cloud Vision service account loaded"
else
    echo "⚠️  google-vision-service-account.json not found - Google Vision will not be available"
fi

# Start the backend
echo "🚀 Starting backend..."
python3 app.py

