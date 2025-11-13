#!/bin/bash
# Quick deployment script - commits and pushes all changes to Render
# Usage: ./quick-deploy.sh [commit message]

set -e  # Exit on error

echo "🚀 Deploying PlanIt to Render..."
echo ""

# Check if we're in a git repo
if [ ! -d .git ]; then
    echo "❌ Not a git repository. Initialize with: git init"
    exit 1
fi

# Get commit message from argument or use default
COMMIT_MSG="${1:-Deploy: Update PlanIt app}"

# Show current status
echo "📋 Current changes:"
git status --short
echo ""

# Check if there are changes to commit
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    echo "✅ No changes to commit. Everything is up to date!"
    exit 0
fi

# Add all changes
echo "📦 Adding all changes..."
git add .

# Commit
echo "💾 Committing changes..."
git commit -m "$COMMIT_MSG"

# Push
echo "📤 Pushing to GitHub..."
git push origin main

echo ""
echo "✅ Changes pushed to GitHub!"
echo ""
echo "🔄 Render will automatically deploy your changes..."
echo "   - Backend: https://dashboard.render.com → planit-backend"
echo "   - Frontend: https://dashboard.render.com → planit-frontend"
echo ""
echo "⏱️  Deployment usually takes 5-10 minutes"
echo "📱 Your app will be live at your Render frontend URL"
echo ""
echo "💡 Tip: Check deployment status in Render dashboard"

