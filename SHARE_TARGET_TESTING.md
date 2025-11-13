# Testing "Share to PlanIt" Functionality

## How Share Target Works

The "Share to PlanIt" feature allows users to share TikTok URLs directly from the TikTok app to PlanIt, which will automatically extract venues.

## Testing Methods

### Method 1: Test with URL Parameters (Easiest)

1. **Open PlanIt in your browser**: `http://localhost:3000`
2. **Add URL parameter**: Append `?url=<TIKTOK_URL>` to the URL
   - Example: `http://localhost:3000?url=https://www.tiktok.com/@user/video/1234567890`
3. **The app should**:
   - Auto-fill the URL in the input field
   - If logged in, automatically start extraction

### Method 2: Test as PWA on Mobile (Android/iOS)

#### Android:
1. **Build the app**: `cd client && npm run build`
2. **Serve the build**: Use a local server or deploy to a URL
3. **Install as PWA**:
   - Open the app in Chrome on Android
   - Tap the menu (3 dots) → "Add to Home screen"
   - The app will install as a PWA
4. **Test sharing**:
   - Open TikTok app
   - Find a video with venues
   - Tap "Share" → Look for "PlanIt" in the share menu
   - Tap "PlanIt" → App opens with URL pre-filled

#### iOS:
1. **Build and deploy** the app to a URL (can't test localhost easily)
2. **Install as PWA**:
   - Open in Safari
   - Tap Share button → "Add to Home Screen"
3. **Test sharing**:
   - Open TikTok app
   - Share → Look for PlanIt option
   - Tap PlanIt → App opens

### Method 3: Manual Deep Link Test

You can manually test the deep linking by:

1. **Open PlanIt**: `http://localhost:3000`
2. **In browser console**, run:
   ```javascript
   // Simulate share with URL parameter
   window.location.href = 'http://localhost:3000?url=https://www.tiktok.com/@test/video/1234567890'
   ```

### Method 4: Test Share Target Parameters

The manifest.json has `share_target` configured. To test:

1. **Install as PWA** (see Method 2)
2. **Share from TikTok**:
   - TikTok app → Share button
   - Look for PlanIt in share options
   - Tap PlanIt

The app will receive:
- `?url=<tiktok_url>` or
- `?text=<tiktok_url>` 

And automatically extract.

## Current Implementation

- ✅ Manifest.json has `share_target` configured
- ✅ Frontend checks for `?url=`, `?tiktok_url=`, or `?text=` parameters
- ✅ Auto-extracts when URL is detected and user is logged in
- ✅ Works on Android PWA (when installed)
- ⚠️ iOS requires HTTPS and proper URL scheme configuration

## Troubleshooting

**If Share Target doesn't appear:**
1. Make sure the app is installed as PWA (not just bookmarked)
2. Check that manifest.json is being served correctly
3. On iOS, you need HTTPS (localhost won't work for native sharing)
4. Try clearing browser cache and reinstalling PWA

**To test locally:**
- Use Method 1 (URL parameters) - works immediately
- Or use Method 3 (manual deep link test)

## Next Steps for Production

For full Share Target support in production:
1. Deploy to HTTPS URL (required for PWA share target)
2. Test on actual devices (Android/iOS)
3. For native apps, configure:
   - Android: `AndroidManifest.xml` with intent-filter
   - iOS: URL scheme in `Info.plist`

