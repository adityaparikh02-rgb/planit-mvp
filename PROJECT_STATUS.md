# PlanIt Project Status

**Last Updated:** November 9, 2025

## 🚀 Deployment Status

### Backend
- **Service Name:** `planit-backend`
- **URL:** `https://planit-backend-fbm5.onrender.com`
- **Health Check:** `https://planit-backend-fbm5.onrender.com/healthz`
- **Status:** ✅ Live and working
- **Plan:** Starter (512MB RAM)

### Frontend
- **Service Name:** `planit-frontend`
- **Status:** ✅ Live and working
- **Plan:** Starter

### Environment Variables

**Backend (planit-backend):**
- `OPENAI_API_KEY` - Required for Whisper transcription and GPT extraction
- `GOOGLE_API_KEY` - Required for Google Places photos
- `PYTHON_VERSION` - 3.11.9

**Frontend (planit-frontend):**
- `REACT_APP_API_URL` - Set to: `https://planit-backend-fbm5.onrender.com`

## ✅ What's Working

1. **Video Extraction:**
   - Downloads TikTok videos using yt-dlp
   - Extracts audio and transcribes with Whisper
   - Extracts venues using GPT-4o-mini
   - Enriches venue data with descriptions, vibe tags, etc.
   - Fetches photos from Google Places API

2. **Frontend:**
   - React app with Home, History, and Saved tabs
   - List/Grid view toggle
   - Save venues to custom lists
   - Mobile responsive

3. **Caching:**
   - Results are cached to avoid reprocessing
   - Cache stored in `cache.json`

## ⚠️ Known Limitations

### 1. OCR / Tesseract
- **Status:** Not available on Render
- **Impact:** Slideshow/image-only videos (no audio) won't extract venues well
- **Workaround:** App shows helpful message explaining the limitation
- **Future Fix:** Would need Docker setup or upgraded plan to install tesseract

### 2. Memory Limits
- **Current:** 512MB RAM (Starter plan)
- **Impact:** Large videos may cause out-of-memory errors
- **Optimizations Applied:**
  - Uses ffmpeg instead of MoviePy for audio extraction
  - Immediate cleanup of temp files
  - Garbage collection
  - OCR skipped on Render
- **Future Fix:** Upgrade to Standard plan ($7/month, 2GB RAM)

### 3. TikTok 403 Errors
- **Status:** Some videos may fail with 403 Forbidden
- **Cause:** TikTok blocking automated requests
- **Mitigations Applied:**
  - Better User-Agent headers
  - Retry logic
  - Updated yt-dlp version
- **Note:** Some videos may still fail due to TikTok's anti-bot measures

### 4. Photo URLs
- **Status:** Not supported (only `/video/` URLs work)
- **Error:** Clear message shown to user

## 📁 Key Files

### Backend
- `app.py` - Main Flask application
- `requirements.txt` - Python dependencies
- `Procfile` - Gunicorn startup command
- `render.yaml` - Render deployment configuration
- `cache.json` - Cached extraction results

### Frontend
- `client/src/App.js` - Main React component
- `client/src/App.css` - Styles
- `client/package.json` - Node dependencies

## 🔧 Quick Commands

### Local Development
```bash
# Backend
python app.py

# Frontend
cd client && npm start
```

### Deploy Updates
```bash
git add .
git commit -m "Your changes"
git push origin main
# Render will auto-deploy
```

## 🐛 Common Issues & Fixes

### Backend won't start
- Check environment variables are set in Render dashboard
- Check logs for specific errors

### Frontend can't connect
- Verify `REACT_APP_API_URL` is set correctly
- Make sure frontend has rebuilt after env var changes

### Memory errors
- Try shorter videos (< 1 minute)
- Check if video file is very large
- Consider upgrading plan

### 403 errors from TikTok
- Try a different video URL
- Some videos may be blocked by TikTok
- Wait a few minutes and retry

## 🚧 Future Improvements

1. **OCR Support:**
   - Set up Dockerfile to install tesseract
   - Or upgrade to plan that allows system packages

2. **Memory Optimization:**
   - Upgrade to Standard plan for more RAM
   - Or further optimize video processing

3. **Better Error Handling:**
   - More specific error messages
   - Retry logic for failed downloads

4. **Features:**
   - Export lists
   - Share lists
   - Map view of venues
   - Filter/search venues

## 📝 Notes

- OCR is optional and gracefully skipped if unavailable
- Results are cached per video ID
- Frontend uses localStorage for saved lists
- Backend uses lazy initialization for OpenAI client

## 🔗 Important URLs

- Backend: `https://planit-backend-fbm5.onrender.com`
- Backend Health: `https://planit-backend-fbm5.onrender.com/healthz`
- Frontend: (Check Render dashboard for your frontend URL)

## 📞 To Resume Work

1. Check this file for current status
2. Review `render.yaml` for deployment config
3. Check Render dashboard for service status
4. Review recent git commits for changes
5. Check `app.py` and `client/src/App.js` for current implementation

