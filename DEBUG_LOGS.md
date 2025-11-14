# 🔍 How to Check Logs for Photo Post Extraction

## Where to Find Logs

When you run the backend locally with `./run_local.sh`, **all logs print directly to the terminal** where the Flask app is running.

### Step 1: Start Backend
```bash
./run_local.sh
```

### Step 2: Keep Terminal Open
The terminal where you ran `./run_local.sh` will show all logs in real-time.

### Step 3: Test Photo Post
When you test a photo post URL, watch the terminal for these log messages:

## 📋 What to Look For

### 1. Photo Post Detection
```
📸 Detected TikTok photo post - skipping yt-dlp, using HTML extraction
```

### 2. HTML Extraction
```
🌐 Fetching HTML from: [URL]
🎭 Using Playwright to render dynamic content...
✅ Found window.__UNIVERSAL_DATA__
   Found ItemModule - extracting first post...
   ✅ Extracted X images from ItemModule.images[]
   ✅ Extracted caption from ItemModule: [caption preview]...
```

### 3. Image Download & OCR
```
🔍 Downloading X images for OCR...
📥 Downloading photo 1/X...
🔍 Running OCR on photo 1...
✅ OCR extracted text from photo 1 (X chars): [text preview]...
📊 Total OCR text extracted: X chars
📝 OCR text preview: [preview]...
```

### 4. GPT Extraction
```
📋 Text sources: Caption=X chars, OCR=X chars
📝 Caption preview: [caption]...
📝 OCR preview: [ocr text]...
🤖 Extracting venues from photo post using GPT...
   Input to GPT: transcript=0 chars, ocr=X chars, caption=X chars, comments=0 chars
🤖 GPT returned X venues: [venue list]
🤖 GPT returned title: [title]
✅ After filtering: X venues remain: [filtered list]
```

### 5. Success or Failure
**If venues found:**
```
🌟 Enriching X places with Google Maps data...
✅ Enriched X places successfully
```

**If no venues found:**
```
⚠️ No venues found by GPT extraction
   This could mean:
   - The caption/OCR text doesn't contain venue names
   - GPT couldn't identify venues in the text
   - The text was too short or unclear
```

## 🐛 Common Issues to Check

1. **No photos extracted?**
   - Look for: `⚠️ HTML extraction failed`
   - Check: `Found X photos` should be > 0

2. **No OCR text?**
   - Look for: `⚠️ No OCR text extracted from any images`
   - Check: `Total OCR text extracted: 0 chars`

3. **No caption?**
   - Look for: `Caption preview: None...`
   - Check: `Caption=X chars` should be > 0

4. **GPT found no venues?**
   - Look for: `🤖 GPT returned 0 venues: []`
   - Check the caption/OCR preview to see if venue names are actually there

## 📸 Example Log Output

Here's what a successful extraction looks like:

```
📸 Detected TikTok photo post - skipping yt-dlp, using HTML extraction
🌐 Fetching HTML from: https://www.tiktok.com/@user/photo/123
🎭 Using Playwright to render dynamic content...
✅ Found window.__UNIVERSAL_DATA__
   Found ItemModule - extracting first post...
   ✅ Extracted 3 images from ItemModule.images[]
   ✅ Extracted caption from ItemModule: My favorite NYC spots...
🔍 Downloading 3 images for OCR...
✅ OCR extracted text from photo 1 (150 chars): Joe's Pizza, Lombardi's...
📊 Total OCR text extracted: 450 chars
🤖 Extracting venues from photo post using GPT...
🤖 GPT returned 3 venues: ["Joe's Pizza", "Lombardi's", "Grimaldi's"]
✅ After filtering: 3 venues remain
🌟 Enriching 3 places with Google Maps data...
✅ Enriched 3 places successfully
```

## 💡 Tips

- **Keep the terminal scrolled to the bottom** to see the latest logs
- **Look for emoji indicators** (📸, 🔍, 🤖, ✅, ⚠️) to quickly find important messages
- **Copy the log output** if you need help debugging - the logs show exactly what happened at each step

