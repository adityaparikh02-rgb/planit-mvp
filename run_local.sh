#!/bin/bash
# Start PlanIt backend (Flask) for local development

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "⚠️  Virtual environment not found. Creating one..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/update dependencies if needed
if [ ! -f "venv/.deps_installed" ]; then
    echo "📦 Installing Python dependencies..."
    pip install -r requirements.txt
    touch venv/.deps_installed
    echo "✅ Dependencies installed"
fi

# Check for required environment variables
if [ -z "$OPENAI_API_KEY" ]; then
    echo "⚠️  WARNING: OPENAI_API_KEY not set"
    echo "   Set it with: export OPENAI_API_KEY='your-key-here'"
fi

if [ -z "$GOOGLE_API_KEY" ]; then
    echo "⚠️  WARNING: GOOGLE_API_KEY not set"
    echo "   Set it with: export GOOGLE_API_KEY='your-key-here'"
fi

if [ -z "$JWT_SECRET_KEY" ]; then
    echo "⚠️  WARNING: JWT_SECRET_KEY not set (generating random one for local dev)"
    export JWT_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
fi

# Set the backend port and enable debug mode
export PORT=5001
export FLASK_DEBUG=true

echo ""
echo "🚀 Starting PlanIt backend on http://localhost:5001"
echo "   Debug mode: ENABLED (auto-reload on file changes)"
echo "   Press Ctrl+C to stop"
echo ""

# Run the Flask app
python3 app.py
