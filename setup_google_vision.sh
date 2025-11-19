#!/bin/bash

# Setup script for Google Cloud Vision API with Service Account

echo "🔧 Setting up Google Cloud Vision API..."

# Check if service account JSON is provided as argument
if [ -z "$1" ]; then
    echo "Usage: ./setup_google_vision.sh <path-to-service-account.json>"
    echo ""
    echo "Or set environment variable:"
    echo "export GOOGLE_VISION_SERVICE_ACCOUNT_JSON='{\"type\":\"service_account\",...}'"
    exit 1
fi

SERVICE_ACCOUNT_FILE="$1"

if [ ! -f "$SERVICE_ACCOUNT_FILE" ]; then
    echo "❌ Error: Service account file not found: $SERVICE_ACCOUNT_FILE"
    exit 1
fi

# Read the JSON file and set as environment variable
export GOOGLE_VISION_SERVICE_ACCOUNT_JSON=$(cat "$SERVICE_ACCOUNT_FILE")

echo "✅ Service account JSON loaded from: $SERVICE_ACCOUNT_FILE"
echo ""
echo "To use this in your backend, run:"
echo "export GOOGLE_VISION_SERVICE_ACCOUNT_JSON='$(cat "$SERVICE_ACCOUNT_FILE" | tr -d '\n')'"
echo "python3 app.py"

