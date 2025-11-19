"""
High-quality OCR processor for TikTok text overlays.

Uses Tesseract OCR with aggressive preprocessing optimized for:
- White text on dark backgrounds (TikTok caption style)
- Black text on light backgrounds (photo overlays)
- Small and compressed video frames
- High confidence text extraction
"""

import cv2
import numpy as np
from PIL import Image
import io
import logging

logger = logging.getLogger(__name__)

# Check OCR availability
OCR_AVAILABLE = False
try:
    import pytesseract
    from pytesseract import image_to_string
    import shutil
    if shutil.which("tesseract"):
        OCR_AVAILABLE = True
except (ImportError, Exception):
    pass


class OCRProcessor:
    """
    High-quality OCR processor with adaptive preprocessing.
    
    Optimized for TikTok photo overlays with multiple preprocessing strategies.
    """
    
    def __init__(self, enable_inverted_pass=True, min_confidence=0.3):
        """
        Initialize OCR processor.
        
        Args:
            enable_inverted_pass: Try inverted image as second pass
            min_confidence: Minimum confidence threshold for results
        """
        self.enable_inverted_pass = enable_inverted_pass
        self.min_confidence = min_confidence
        self.ocr_available = OCR_AVAILABLE
    
    def _load_image(self, image_source):
        """
        Load image from URL, file path, or bytes.
        
        Args:
            image_source: URL (str), file path (str), or bytes
            
        Returns:
            cv2 image (BGR) or None if failed
        """
        try:
            if isinstance(image_source, bytes):
                # Bytes to numpy array
                nparr = np.frombuffer(image_source, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                return img
            elif isinstance(image_source, str):
                if image_source.startswith(('http://', 'https://')):
                    # Download from URL
                    import requests
                    resp = requests.get(image_source, timeout=10)
                    nparr = np.frombuffer(resp.content, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    return img
                else:
                    # Local file path
                    img = cv2.imread(image_source)
                    return img
            else:
                logger.warning(f"Unsupported image source type: {type(image_source)}")
                return None
        except Exception as e:
            logger.error(f"Failed to load image: {e}")
            return None
    
    def _preprocess_image(self, img):
        """
        Apply preprocessing chain for optimal OCR.
        
        Steps:
        1. Convert to grayscale
        2. Upscale if small (min 1000px)
        3. Apply Gaussian blur (light)
        4. Apply adaptive thresholding
        5. Optional CLAHE contrast boost
        
        Args:
            img: CV2 image (BGR)
            
        Returns:
            Preprocessed grayscale image
        """
        if img is None:
            return None
        
        # Convert to grayscale
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img
        
        height, width = gray.shape
        
        # Upscale if too small (critical for OCR accuracy)
        if width < 1000:
            scale = 1000 / width
            new_size = (int(width * scale), int(height * scale))
            gray = cv2.resize(gray, new_size, interpolation=cv2.INTER_CUBIC)
            logger.debug(f"📏 Upscaled image {scale:.1f}x to {new_size}")
        
        # Light Gaussian blur to reduce noise
        gray = cv2.GaussianBlur(gray, (3, 3), 0.5)
        
        # Adaptive thresholding (handles varying lighting)
        binary = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11, 2
        )
        
        # Optional: CLAHE contrast boost for very dark/light images
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(binary)
        
        return enhanced
    
    def _clean_ocr_text(self, text):
        """
        Clean OCR output by removing noise and normalizing whitespace.
        
        Args:
            text: Raw OCR text
            
        Returns:
            Cleaned text
        """
        if not text:
            return ""
        
        # Normalize whitespace
        text = " ".join(text.split())
        
        # Remove common OCR artifacts
        text = text.replace("(", " ").replace(")", " ")
        text = text.replace("[", " ").replace("]", " ")
        
        # Normalize spacing again
        text = " ".join(text.split())
        
        return text.strip()
    
    def _calculate_text_quality(self, text):
        """
        Calculate quality score for OCR output (0-1).
        
        Higher = more likely to be real text.
        
        Metrics:
        - Consonant clustering (avoid excessive consonants)
        - Word length distribution
        - Uppercase/lowercase balance
        - Space distribution
        
        Args:
            text: OCR output text
            
        Returns:
            Quality score (0.0 - 1.0)
        """
        if len(text) < 5:
            return 0.0
        
        words = [w for w in text.split() if w]
        if not words:
            return 0.0
        
        # 1. Word length check
        word_lengths = [len(w) for w in words]
        avg_word_len = sum(word_lengths) / len(words)
        
        # Real words: 3-12 chars average
        if avg_word_len < 2 or avg_word_len > 18:
            return 0.1  # Likely garbled
        
        word_len_quality = 1.0 - abs(avg_word_len - 6.5) / 10
        
        # 2. Consonant clustering (detect "RRR yy oe ST" pattern)
        max_consonant_run = 0
        current_run = 0
        for c in text.lower():
            if c.isalpha() and c not in 'aeiou':
                current_run += 1
                max_consonant_run = max(max_consonant_run, current_run)
            else:
                current_run = 0
        
        # Max 5 consonants in a row is reasonable ("string", "spring")
        consonant_quality = 1.0 if max_consonant_run <= 5 else max(0.1, 1.0 - (max_consonant_run - 5) / 10)
        
        # 3. Alphanumeric ratio
        alpha_count = sum(1 for c in text if c.isalnum() or c in ' ,.-')
        alpha_ratio = alpha_count / len(text) if text else 0
        alpha_quality = alpha_ratio
        
        # 4. Space distribution
        space_ratio = text.count(' ') / len(text) if text else 0
        space_quality = 1.0 if 0.08 < space_ratio < 0.45 else 0.5
        
        # Combine scores
        quality = (
            word_len_quality * 0.25 +
            consonant_quality * 0.35 +
            alpha_quality * 0.25 +
            space_quality * 0.15
        )
        
        return max(0.0, min(1.0, quality))
    
    def run(self, image_source, use_inverted_secondary=True):
        """
        Run OCR on image with adaptive preprocessing.
        
        Strategy:
        1. Load image
        2. Apply standard preprocessing
        3. Run Tesseract
        4. If poor quality AND use_inverted_secondary: try inverted version
        5. Return best result
        
        Args:
            image_source: URL (str), file path (str), or bytes
            use_inverted_secondary: Try inverted as fallback
            
        Returns:
            Cleaned OCR text (str)
        """
        if not self.ocr_available:
            logger.warning("OCR not available - tesseract not installed")
            return ""
        
        # Load image
        img = self._load_image(image_source)
        if img is None:
            logger.warning("Failed to load image")
            return ""
        
        # Standard preprocessing
        preprocessed = self._preprocess_image(img)
        if preprocessed is None:
            return ""
        
        # Run Tesseract
        try:
            text = pytesseract.image_to_string(
                preprocessed,
                config="--oem 3 --psm 6"
            )
        except Exception as e:
            logger.error(f"Tesseract failed: {e}")
            return ""
        
        text = self._clean_ocr_text(text)
        quality = self._calculate_text_quality(text)
        
        logger.debug(f"OCR standard pass: {len(text)} chars, quality: {quality:.2f}")
        
        best_text = text
        best_quality = quality
        
        # Try inverted pass if quality is poor
        if use_inverted_secondary and quality < 0.6 and len(text) < 100:
            logger.debug("Quality low, trying inverted pass...")
            
            try:
                inverted = cv2.bitwise_not(preprocessed)
                inverted_text = pytesseract.image_to_string(
                    inverted,
                    config="--oem 3 --psm 6"
                )
                inverted_text = self._clean_ocr_text(inverted_text)
                inverted_quality = self._calculate_text_quality(inverted_text)
                
                logger.debug(f"OCR inverted pass: {len(inverted_text)} chars, quality: {inverted_quality:.2f}")
                
                if inverted_quality > best_quality:
                    best_text = inverted_text
                    best_quality = inverted_quality
                    logger.debug("Inverted pass better, using that result")
            
            except Exception as e:
                logger.warning(f"Inverted pass failed: {e}")
        
        if best_quality < self.min_confidence:
            logger.warning(f"OCR quality below threshold ({best_quality:.2f} < {self.min_confidence})")
            return ""
        
        logger.debug(f"Final OCR result: {len(best_text)} chars, quality: {best_quality:.2f}")
        return best_text


# Singleton instance
_ocr_processor = None


def get_ocr_processor():
    """Get or create singleton OCR processor."""
    global _ocr_processor
    if _ocr_processor is None:
        _ocr_processor = OCRProcessor()
    return _ocr_processor
