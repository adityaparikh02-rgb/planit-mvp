# 📋 Where Logs Come From & How to View Them

## Where Logs Come From

All logs come from **`print()` statements** in `app.py`. They output to:
- **stdout** (standard output) - normal log messages
- **stderr** (standard error) - error messages

## How to See Logs

### ✅ **Best Way: Run Backend in Foreground**

1. **Open a terminal**
2. **Navigate to project:**
   ```bash
   cd /Users/aditya/planit-codex
   ```

3. **Start backend (logs will appear in this terminal):**
   ```bash
   ./run_local.sh
   ```

4. **Keep this terminal open** - all logs will print here in real-time!

5. **Test your photo post** - logs will appear immediately in this terminal

### 📝 What You'll See

When you test a photo post, logs will appear like this:

```
📸 Detected TikTok photo post - skipping yt-dlp, using HTML extraction
🌐 Fetching HTML from: https://www.tiktok.com/@user/photo/123
🎭 Using Playwright to render dynamic content...
✅ Found window.__UNIVERSAL_DATA__
   Found ItemModule - extracting first post...
   ✅ Extracted 3 images from ItemModule.images[]
   ✅ Extracted caption from ItemModule: My favorite NYC spots...
🔍 Downloading 3 images for OCR...
✅ OCR extracted text from photo 1 (150 chars): Joe's Pizza...
📊 Total OCR text extracted: 450 chars
🤖 Extracting venues from photo post using GPT...
🤖 GPT returned 3 venues: ["Joe's Pizza", "Lombardi's", "Grimaldi's"]
✅ After filtering: 3 venues remain
🌟 Enriching 3 places with Google Maps data...
✅ Enriched 3 places successfully
```

## 🔍 Quick Test

To see logs immediately:

1. **Terminal 1** (keep open for logs):
   ```bash
   cd /Users/aditya/planit-codex
   ./run_local.sh
   ```

2. **Terminal 2** (test photo post):
   ```bash
   cd /Users/aditya/planit-codex
   ./test_photo_post.sh "https://www.tiktok.com/@stephanieinthecity/photo/7491478906856295711"
   ```

3. **Watch Terminal 1** - logs will appear there!

## ⚠️ Important Notes

- **Logs only appear in the terminal where Flask is running**
- If you run Flask in background, logs won't be visible
- **Always run `./run_local.sh` in foreground** to see logs
- Logs print in **real-time** as requests come in

## 🐛 Debugging

If you don't see logs:
1. Make sure Flask is running: `curl http://localhost:5001/healthz`
2. Make sure you're looking at the right terminal (where Flask is running)
3. Check if port 5001 is in use: `lsof -ti:5001`
4. Kill old processes: `lsof -ti:5001 | xargs kill -9`

