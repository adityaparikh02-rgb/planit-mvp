#!/usr/bin/env bash
# Build script for Render deployment
# This installs Python dependencies and Playwright browsers

set -o errexit  # Exit on error

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browsers (chromium only to save space and time)
playwright install --with-deps chromium

echo "✅ Build complete - Python packages and Playwright browsers installed"
