# Quick Start: Deploy to Render

## 🚀 Fastest Way (Using Blueprint)

1. **Push your code to GitHub**:
   ```bash
   git add .
   git commit -m "Ready for Render deployment"
   git push origin main
   ```

2. **Go to Render Dashboard**:
   - Visit https://dashboard.render.com
   - Click "New +" → "Blueprint"
   - Connect your GitHub repository
   - Render will auto-detect `render.yaml`

3. **Set Environment Variables** (after services are created):
   
   **Backend (`planit-backend`)**:
   - `OPENAI_API_KEY`: Your OpenAI API key
   - `GOOGLE_API_KEY`: Your Google Maps API key
   
   **Frontend (`planit-frontend`)**:
   - `REACT_APP_API_URL`: `https://planit-backend.onrender.com` (replace with your actual backend URL)

4. **Deploy**: Render will automatically build and deploy both services!

## 📝 Important Notes

- **First deployment takes 5-10 minutes** (installing dependencies)
- **Free tier services** spin down after 15 min of inactivity (first request will be slow)
- **Backend URL**: After backend deploys, copy its URL and set it as `REACT_APP_API_URL` in frontend
- **Health Check**: Backend health endpoint is at `/healthz`

## 🔧 Manual Setup (Alternative)

If Blueprint doesn't work, see `DEPLOYMENT.md` for manual step-by-step instructions.

## ✅ Verify Deployment

1. Backend: Visit `https://planit-backend.onrender.com/healthz` → Should return `{"status":"ok"}`
2. Frontend: Visit `https://planit-frontend.onrender.com` → Should show your app

## 🐛 Troubleshooting

- **Build fails**: Check logs in Render dashboard
- **CORS errors**: Make sure frontend `REACT_APP_API_URL` matches backend URL exactly
- **Timeout errors**: Video processing can take time; consider upgrading plan
- **Memory issues**: Starter plan has 512MB RAM; upgrade if processing fails

