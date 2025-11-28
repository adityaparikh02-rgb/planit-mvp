#!/usr/bin/env node
/**
 * Generate PlanIt favicon PNG files from SVG
 * Requires: npm install sharp
 * Usage: node generate-favicons.js
 */

const fs = require('fs');
const path = require('path');

// Check if sharp is installed
let sharp;
try {
  sharp = require('sharp');
} catch (e) {
  console.error('❌ Error: sharp is not installed.');
  console.log('📦 Install it with: npm install sharp');
  console.log('   Or: cd client && npm install sharp');
  process.exit(1);
}

const svgPath = path.join(__dirname, 'client', 'public', 'favicon.svg');
const outputDir = path.join(__dirname, 'client', 'public');

// Sizes to generate
const sizes = [
  { name: 'favicon-16x16.png', size: 16 },
  { name: 'favicon-32x32.png', size: 32 },
  { name: 'apple-touch-icon.png', size: 180 },
  { name: 'logo192.png', size: 192 },
  { name: 'logo512.png', size: 512 }
];

async function generateFavicons() {
  if (!fs.existsSync(svgPath)) {
    console.error(`❌ SVG file not found: ${svgPath}`);
    process.exit(1);
  }

  console.log('🎨 Generating PlanIt favicon files...\n');

  for (const { name, size } of sizes) {
    try {
      const outputPath = path.join(outputDir, name);
      await sharp(svgPath)
        .resize(size, size)
        .png()
        .toFile(outputPath);
      console.log(`✅ Generated ${name} (${size}x${size})`);
    } catch (error) {
      console.error(`❌ Failed to generate ${name}:`, error.message);
    }
  }

  console.log('\n✨ All favicon files generated successfully!');
  console.log('📱 Your app will now show the PlanIt logo on mobile devices and browser tabs.');
}

generateFavicons().catch(console.error);

