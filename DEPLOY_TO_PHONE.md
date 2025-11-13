# 🚀 Deploy PlanIt to Render - Step by Step Guide

## 🎯 Quick Deploy (Keep Your App Always Updated!)

**To deploy your latest localhost changes to Render (and your phone):**

```bash
# Option 1: Use the quick deploy script (easiest!)
./quick-deploy.sh "Your commit message here"

# Option 2: Manual deploy
git add .
git commit -m "Your commit message"
git push origin main
```

**That's it!** Render automatically detects the push and redeploys both services. Your app will be live in 5-10 minutes.

### 🔄 How Auto-Deployment Works

1. **Push to GitHub** → Render detects the change
2. **Render builds** → Both backend and frontend rebuild automatically
3. **Deployment completes** → Your Render URL is updated with latest code
4. **Share with friends** → They always see the latest version!

### ✅ Verify Your Deployment

- **Check status**: https://dashboard.render.com → Your services → Logs
- **Test backend**: `https://your-backend.onrender.com/healthz` → Should return `{"status":"ok"}`
- **Test frontend**: `https://your-frontend.onrender.com` → Should show your app

### 📱 Sharing with Friends

Once deployed, share your **frontend URL** with friends:
- They can access it on any device (phone, tablet, computer)
- No installation needed - works in any browser
- Always shows the latest version you've deployed
- Can be added to home screen as a PWA

---

## ✅ Pre-Deployment Checklist

Before deploying, make sure all your changes are committed:

```bash
# Check what files have changed
git status

# Add all new files and changes
git add .

# Commit everything
git commit -m "Add user auth, photo post support, OCR improvements, and mobile share target"

# Push to GitHub
git push origin main
```

## 📋 Step 1: Deploy to Render

### Option A: Using Blueprint (Easiest - Recommended)

1. **Go to Render Dashboard**: https://dashboard.render.com
2. **Click "New +" → "Blueprint"**
3. **Connect your GitHub repository** (select the repo with PlanIt)
4. **Render will auto-detect `render.yaml`** and create both services
5. **Wait for initial build** (takes 5-10 minutes)

### Option B: Manual Setup

If Blueprint doesn't work, follow the manual steps in `DEPLOYMENT.md`.

## 🔐 Step 2: Set Environment Variables

After services are created, set these environment variables in Render dashboard:

### Backend Service (`planit-backend`)

Go to: **Dashboard → planit-backend → Environment**

Add these variables:

1. **`OPENAI_API_KEY`**: Your OpenAI API key
   - Get from: https://platform.openai.com/api-keys

2. **`GOOGLE_API_KEY`**: Your Google Maps API key  
   - Get from: https://console.cloud.google.com/apis/credentials

3. **`JWT_SECRET_KEY`**: Generate a secure random string
   - Run this command to generate one:
     ```bash
     python -c "import secrets; print(secrets.token_urlsafe(32))"
     ```
   - Or use: https://randomkeygen.com/ (use "CodeIgniter Encryption Keys")

### Frontend Service (`planit-frontend`)

Go to: **Dashboard → planit-frontend → Environment**

1. **`REACT_APP_API_URL`**: Your backend URL
   - Format: `https://planit-backend.onrender.com` (NO trailing slash!)
   - Find your backend URL in the Render dashboard (it will be something like `https://planit-backend-xxxx.onrender.com`)

## ⏱️ Step 3: Wait for Deployment

- **Backend**: Usually takes 5-10 minutes (installing Python dependencies, OCR, etc.)
- **Frontend**: Usually takes 2-5 minutes (installing npm packages, building React app)

## ✅ Step 4: Verify Deployment

1. **Check Backend Health**:
   - Visit: `https://your-backend-url.onrender.com/healthz`
   - Should return: `{"status":"ok"}`

2. **Check Frontend**:
   - Visit: `https://your-frontend-url.onrender.com`
   - Should show the PlanIt app

3. **Test on Phone**:
   - Open the frontend URL on your phone's browser
   - Try logging in and extracting a TikTok

## 📱 Step 5: Access on Your Phone

Once deployed, you can:

1. **Open the frontend URL** on your phone's browser
2. **Add to Home Screen** (for PWA features):
   - **iOS**: Safari → Share → "Add to Home Screen"
   - **Android**: Chrome → Menu → "Add to Home screen"

3. **Test Share Target** (if installed as PWA):
   - Share a TikTok URL → Look for "PlanIt" in share options

## 🔄 Step 6: Update Existing Deployment

**To keep your Render deployment in sync with localhost:**

### Quick Update (Recommended)
```bash
./quick-deploy.sh "Update with latest features"
```

### Manual Update
```bash
git add .
git commit -m "Update with latest features"
git push origin main
```

**Render will automatically:**
- ✅ Detect the push to GitHub
- ✅ Trigger a new build for both services
- ✅ Deploy the latest version
- ✅ Update your live URL (no downtime)

**Deployment takes 5-10 minutes.** Check status in Render dashboard.

### Ensure Auto-Deploy is Enabled

1. Go to Render Dashboard → Your service → Settings
2. Under "Build & Deploy", make sure **"Auto-Deploy"** is set to **"Yes"**
3. Branch should be set to `main` (or your default branch)

### Update Environment Variables (if needed)
- Go to each service → Environment → Update variables
- Changes take effect on next deployment

## 🐛 Troubleshooting

### Backend Issues

- **Build fails**: Check logs in Render dashboard → planit-backend → Logs
- **Timeout errors**: Video processing can take time; consider upgrading plan
- **Memory issues**: Starter plan has 512MB RAM; upgrade if needed
- **OCR not working**: Check logs - tesseract should be installed in Dockerfile

### Frontend Issues

- **Can't connect to backend**: 
  - Verify `REACT_APP_API_URL` is set correctly (no trailing slash!)
  - Check backend URL is accessible: `https://your-backend.onrender.com/healthz`
  - Make sure CORS is enabled (already configured in app.py)

- **Build fails**: Check logs in Render dashboard → planit-frontend → Logs

### Mobile Issues

- **Not loading on phone**: 
  - Make sure you're using HTTPS (not HTTP)
  - Check phone's internet connection
  - Try clearing browser cache

- **Share Target not working**:
  - Must be installed as PWA (Add to Home Screen)
  - Requires HTTPS (localhost won't work)
  - Check `manifest.json` is being served correctly

## 📝 Important Notes

- **Free Tier**: Services spin down after 15 min of inactivity (first request will be slow)
- **Cold Starts**: First request after spin-down takes 30-60 seconds
- **Upgrade**: Consider upgrading to paid plan for better performance and no spin-downs

## 🔗 Quick Links

- **Render Dashboard**: https://dashboard.render.com
- **Backend Logs**: Dashboard → planit-backend → Logs
- **Frontend Logs**: Dashboard → planit-frontend → Logs
- **Health Check**: `https://your-backend.onrender.com/healthz`

## ✨ What's Included in This Deployment

✅ User authentication (Sign Up / Log In / Log Out)  
✅ User-specific saved places and history  
✅ Shared place caching across TikToks  
✅ Photo post support (scraping + OCR)  
✅ Enhanced OCR with multiple preprocessing methods  
✅ Music detection (skips audio transcription when music detected)  
✅ Mobile share target (PWA)  
✅ Context-aware "Must Try" fields  
✅ "Also featured in" videos with links  

---

**Need Help?** Check the logs in Render dashboard or see `DEPLOYMENT.md` for more details.

