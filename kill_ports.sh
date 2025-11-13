#!/bin/bash
# Kill processes on ports 5001 and 3000 (backend and frontend)

echo "🔍 Checking for processes on ports 5001 and 3000..."

# Kill port 5001 (backend)
if lsof -ti:5001 > /dev/null 2>&1; then
    lsof -ti:5001 | xargs kill -9
    echo "✅ Killed process on port 5001"
else
    echo "ℹ️  Port 5001 is free"
fi

# Kill port 3000 (frontend)
if lsof -ti:3000 > /dev/null 2>&1; then
    lsof -ti:3000 | xargs kill -9
    echo "✅ Killed process on port 3000"
else
    echo "ℹ️  Port 3000 is free"
fi

echo ""
echo "✅ Ports cleared! Ready to start servers."

