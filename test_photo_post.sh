#!/bin/bash
# Test script wrapper that uses venv Python

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d "venv" ]; then
    echo "✅ Using virtual environment..."
    if [ -f "venv/bin/python3" ]; then
        venv/bin/python3 test_photo_post.py "$@"
    else
        echo "❌ venv/bin/python3 not found"
        exit 1
    fi
else
    echo "⚠️  No venv found, using system Python..."
    echo "   (Make sure requests is installed: pip3 install requests)"
    python3 test_photo_post.py "$@"
fi

