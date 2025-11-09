# Deployment Guide for Render

This guide will help you deploy PlanIt to Render.

## Prerequisites

1. A Render account (sign up at https://render.com)
2. Your repository pushed to GitHub/GitLab/Bitbucket
3. API keys ready:
   - `OPENAI_API_KEY`
   - `GOOGLE_API_KEY`

## Deployment Steps

### Option 1: Using render.yaml (Recommended)

1. **Push your code to GitHub** (if not already done)
   ```bash
   git add .
   git commit -m "Add Render deployment configuration"
   git push origin main
   ```

2. **Connect to Render**:
   - Go to https://dashboard.render.com
   - Click "New +" → "Blueprint"
   - Connect your repository
   - Render will automatically detect `render.yaml` and create both services

3. **Set Environment Variables**:
   - For the backend service (`planit-backend`):
     - `OPENAI_API_KEY`: Your OpenAI API key
     - `GOOGLE_API_KEY`: Your Google Maps API key
   - The frontend will automatically get the backend URL via `REACT_APP_API_URL`

4. **Deploy**:
   - Render will automatically build and deploy both services
   - The frontend will be available at: `https://planit-frontend.onrender.com`
   - The backend will be available at: `https://planit-backend.onrender.com`

### Option 2: Manual Deployment

#### Backend Service

1. **Create a new Web Service**:
   - Click "New +" → "Web Service"
   - Connect your repository
   - Settings:
     - **Name**: `planit-backend`
     - **Environment**: `Python 3`
     - **Build Command**: `pip install -r requirements.txt`
     - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
     - **Plan**: Starter (or higher for better performance)

2. **Set Environment Variables**:
   - `OPENAI_API_KEY`: Your OpenAI API key
   - `GOOGLE_API_KEY`: Your Google Maps API key
   - `PYTHON_VERSION`: `3.11.9`

3. **Deploy**: Click "Create Web Service"

#### Frontend Service

1. **Create a new Web Service**:
   - Click "New +" → "Web Service"
   - Connect your repository
   - Settings:
     - **Name**: `planit-frontend`
     - **Root Directory**: `client`
     - **Environment**: `Node`
     - **Build Command**: `npm install && npm run build`
     - **Start Command**: `npx serve -s build -l $PORT`
     - **Plan**: Starter

2. **Set Environment Variables**:
   - `NODE_VERSION`: `18.18.0`
   - `REACT_APP_API_URL`: `https://planit-backend.onrender.com` (use your actual backend URL)

3. **Deploy**: Click "Create Web Service"

## Important Notes

### Backend Considerations

- **Timeout**: The backend has a 120-second timeout for long-running video processing
- **Workers**: Using 2 workers for better concurrency
- **Health Check**: The `/healthz` endpoint is used for health checks
- **File Storage**: Temporary files are cleaned up automatically, but consider using Render's disk storage for cache if needed

### Frontend Considerations

- **API URL**: The frontend uses `REACT_APP_API_URL` environment variable
- **Build**: The React app is built during deployment and served statically
- **CORS**: Make sure CORS is enabled in your Flask backend (already configured)

### Environment Variables

Make sure to set these in Render's dashboard:

**Backend:**
- `OPENAI_API_KEY` (required)
- `GOOGLE_API_KEY` (required)
- `PORT` (automatically set by Render)

**Frontend:**
- `REACT_APP_API_URL` (should point to your backend URL)
- `PORT` (automatically set by Render)

## Troubleshooting

### Backend Issues

1. **Build fails**: Check that all dependencies in `requirements.txt` are correct
2. **Timeout errors**: Increase timeout in Procfile or Render settings
3. **Memory issues**: Upgrade to a higher plan (Starter has 512MB RAM)

### Frontend Issues

1. **API connection fails**: Verify `REACT_APP_API_URL` is set correctly
2. **Build fails**: Check Node version compatibility
3. **CORS errors**: Ensure Flask CORS is properly configured

### Common Issues

- **Cold starts**: Render free tier services spin down after 15 minutes of inactivity
- **Build timeouts**: Large dependencies (like opencv, moviepy) may take time to install
- **Memory limits**: Video processing can be memory-intensive; consider upgrading plan

## Updating Your Deployment

After making changes:

```bash
git add .
git commit -m "Your commit message"
git push origin main
```

Render will automatically detect changes and redeploy.

## Monitoring

- Check logs in Render dashboard for both services
- Monitor health check endpoint: `https://planit-backend.onrender.com/healthz`
- Set up alerts in Render dashboard for service failures

