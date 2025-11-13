#!/bin/bash
# Quick deployment script - commits and pushes all changes to Render

echo "🚀 Preparing PlanIt for deployment..."
echo ""

# Check if we're in a git repo
if [ ! -d .git ]; then
    echo "❌ Not a git repository. Initialize with: git init"
    exit 1
fi

# Show current status
echo "📋 Current changes:"
git status --short
echo ""

# Ask for confirmation
read -p "Commit and push all changes? (y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Cancelled"
    exit 1
fi

# Add all changes
echo "📦 Adding all changes..."
git add .

# Commit
echo "💾 Committing changes..."
git commit -m "Deploy: Add user auth, photo post support, OCR improvements, and mobile features"

# Push
echo "📤 Pushing to GitHub..."
git push origin main

echo ""
echo "✅ Changes pushed to GitHub!"
echo ""
echo "📋 Next steps:"
echo "1. Go to https://dashboard.render.com"
echo "2. Click 'New +' → 'Blueprint'"
echo "3. Connect your repository"
echo "4. Set environment variables (see DEPLOY_TO_PHONE.md)"
echo ""
echo "Or if you already have services deployed:"
echo "- Render will auto-deploy your changes"
echo "- Make sure to set REACT_APP_API_URL in frontend service"
echo ""

