# Step-by-Step: Deploy PlanIt to Render

Follow these exact steps to deploy your app to Render.

## Prerequisites
- Your code pushed to GitHub (or GitLab/Bitbucket)
- OpenAI API key
- Google Maps API key

---

## Step 1: Push Your Code to GitHub

1. Open terminal in your project directory
2. Run these commands:

```bash
git add .
git commit -m "Add Render deployment files"
git push origin main
```

Make sure your code is pushed successfully before proceeding.

---

## Step 2: Create Render Account & Connect Repository

1. **Go to Render**: https://dashboard.render.com
2. **Sign up** (or log in if you have an account)
   - You can sign up with GitHub (recommended)
3. **Click the big blue "New +" button** (top right)
4. **Select "Blueprint"** from the dropdown menu
   - This will let you deploy both services at once using `render.yaml`

---

## Step 3: Connect Your Repository

1. **Click "Connect account"** or "Connect repository"
2. **Select GitHub** (or your Git provider)
3. **Authorize Render** to access your repositories
4. **Find and select your repository** (`planit-codex` or whatever it's named)
5. **Click "Connect"**

---

## Step 4: Configure Blueprint

1. Render should automatically detect your `render.yaml` file
2. You'll see a preview showing:
   - `planit-backend` (Python service)
   - `planit-frontend` (Node service)
3. **Review the settings** (they should be correct from `render.yaml`)
4. **Click "Apply"** at the bottom

---

## Step 5: Set Environment Variables for Backend

**IMPORTANT**: Do this AFTER the services are created (during or after first build)

1. **Go to your Render dashboard**
2. **Click on `planit-backend` service**
3. **Click "Environment" tab** (left sidebar)
4. **Add these environment variables**:

   Click "Add Environment Variable" for each:

   **Variable 1:**
   - Key: `OPENAI_API_KEY`
   - Value: `sk-...` (your actual OpenAI API key)
   - Click "Save Changes"

   **Variable 2:**
   - Key: `GOOGLE_API_KEY`
   - Value: `AIza...` (your actual Google Maps API key)
   - Click "Save Changes"

5. **The service will automatically redeploy** after you add variables

---

## Step 6: Wait for Backend to Deploy

1. **Watch the build logs** in the Render dashboard
2. **First build takes 5-10 minutes** (installing dependencies)
3. **Wait until you see**: "Your service is live at https://planit-backend.onrender.com"
4. **Copy the backend URL** - you'll need it for the frontend!

---

## Step 7: Test Backend

1. **Click on the backend URL** or visit: `https://planit-backend.onrender.com/healthz`
2. **You should see**: `{"status":"ok"}`
3. If you see this, backend is working! ✅

---

## Step 8: Set Environment Variable for Frontend

1. **Go back to Render dashboard**
2. **Click on `planit-frontend` service**
3. **Click "Environment" tab**
4. **Add environment variable**:

   Click "Add Environment Variable":
   - Key: `REACT_APP_API_URL`
   - Value: `https://planit-backend.onrender.com` (use YOUR actual backend URL from Step 6)
   - Click "Save Changes"

5. **The frontend will rebuild** automatically

---

## Step 9: Wait for Frontend to Deploy

1. **Watch the build logs**
2. **Build takes 2-5 minutes**
3. **Wait until you see**: "Your service is live at https://planit-frontend.onrender.com"

---

## Step 10: Test Your App!

1. **Visit your frontend URL**: `https://planit-frontend.onrender.com`
2. **Try extracting a TikTok URL** to test everything works
3. **Check browser console** (F12) for any errors

---

## Troubleshooting

### Backend won't start
- Check the "Logs" tab in Render dashboard
- Common issues:
  - Missing environment variables → Add them in "Environment" tab
  - Build timeout → Dependencies are large, wait longer or upgrade plan
  - Import errors → Check requirements.txt is correct

### Frontend can't connect to backend
- Verify `REACT_APP_API_URL` is set correctly (no trailing slash)
- Check backend is actually running (visit `/healthz` endpoint)
- Check CORS is enabled (it is in your code)

### Build fails
- Check "Logs" tab for specific error
- Common fixes:
  - Node version mismatch → Check NODE_VERSION in render.yaml
  - Python version mismatch → Check PYTHON_VERSION in render.yaml
  - Missing dependencies → Check package.json and requirements.txt

### Service is slow to respond
- Free tier services "spin down" after 15 min of inactivity
- First request after spin-down takes 30-60 seconds
- This is normal for free tier

---

## Quick Reference: Service URLs

After deployment, you'll have:
- **Backend**: `https://planit-backend.onrender.com`
- **Frontend**: `https://planit-frontend.onrender.com`

---

## Updating Your App

To update after making changes:

```bash
git add .
git commit -m "Your changes"
git push origin main
```

Render will automatically detect changes and redeploy!

---

## Need Help?

- Check Render logs: Dashboard → Your Service → "Logs" tab
- Render docs: https://render.com/docs
- Render status: https://status.render.com

