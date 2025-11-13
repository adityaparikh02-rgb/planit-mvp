#!/bin/bash
# Start PlanIt frontend (React) for local development

cd client

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "📦 Installing npm dependencies..."
    npm install
    echo "✅ Dependencies installed"
fi

# Check for API URL environment variable
if [ -z "$REACT_APP_API_URL" ]; then
    echo "⚠️  WARNING: REACT_APP_API_URL not set"
    echo "   Defaulting to http://localhost:5001"
    echo "   Set it with: export REACT_APP_API_URL='http://localhost:5001'"
    export REACT_APP_API_URL="http://localhost:5001"
fi

# Auto-set Google Maps API key from GOOGLE_API_KEY if not set
if [ -z "$REACT_APP_GOOGLE_MAPS_API_KEY" ] && [ ! -z "$GOOGLE_API_KEY" ]; then
    export REACT_APP_GOOGLE_MAPS_API_KEY="$GOOGLE_API_KEY"
    echo "✅ Using GOOGLE_API_KEY for map view"
elif [ -z "$REACT_APP_GOOGLE_MAPS_API_KEY" ]; then
    echo "⚠️  WARNING: REACT_APP_GOOGLE_MAPS_API_KEY not set"
    echo "   Map view will not work. Set it with:"
    echo "   export REACT_APP_GOOGLE_MAPS_API_KEY='your-google-maps-api-key'"
    echo "   (You can use the same key as GOOGLE_API_KEY)"
fi

echo ""
echo "🚀 Starting PlanIt frontend on http://localhost:3000"
echo "   Backend API: $REACT_APP_API_URL"
if [ ! -z "$REACT_APP_GOOGLE_MAPS_API_KEY" ]; then
    echo "   Google Maps API: ✅ Set"
else
    echo "   Google Maps API: ❌ Not set (map view disabled)"
fi
echo "   Press Ctrl+C to stop"
echo ""

# Start React development server
npm start

