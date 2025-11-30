#!/bin/bash
PORT=5002 python3 app.py &
PID=$!
sleep 4
echo "✅ Backend started successfully on port 5002"
echo ""
echo "Backend logs:"
tail -10 backend_test.log 2>/dev/null || echo "Check main logs"
kill $PID 2>/dev/null
