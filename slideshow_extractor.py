"""
Slideshow OCR extractor for TikTok photo posts.

Extracts and concatenates text from all images in a slideshow.
"""

import logging
from ocr_processor import get_ocr_processor

logger = logging.getLogger(__name__)


def extract_text_from_slideshow(image_sources):
    """
    Extract text from all images in a slideshow.
    
    Concatenates OCR output from each image with slide markers.
    
    Args:
        image_sources: List of image URLs, file paths, or bytes
        
    Returns:
        Concatenated OCR text from all slides
    """
    if not image_sources:
        logger.warning("No images provided to slideshow extractor")
        return ""
    
    processor = get_ocr_processor()
    all_text = []
    
    for idx, image_source in enumerate(image_sources, 1):
        try:
            logger.debug(f"Extracting text from slide {idx}/{len(image_sources)}...")
            text = processor.run(image_source, use_inverted_secondary=True)
            
            if text and len(text.strip()) > 0:
                # Add slide marker for context (helps with multi-slide analysis)
                marked_text = f"SLIDE {idx}: {text}"
                all_text.append(marked_text)
                logger.info(f"✅ Slide {idx}: {len(text)} chars extracted")
            else:
                logger.info(f"⚠️ Slide {idx}: No readable text detected")
        
        except Exception as e:
            logger.error(f"Failed to process slide {idx}: {e}")
            continue
    
    # Concatenate all text with newlines for clarity
    combined = "\n".join(all_text)
    
    logger.info(f"📊 Slideshow extraction complete: {len(image_sources)} slides, {len(combined)} chars total")
    return combined


def extract_text_from_slideshow_weighted(
    image_sources,
    caption_text="",
    transcript_text="",
    weight_ocr=1.4,
    weight_caption=1.2,
    weight_transcript=0.6
):
    """
    Extract and weight text from slideshow with caption and transcript.
    
    Formula:
        final_text = (ocr * weight_ocr) + (caption * weight_caption) + (transcript * weight_transcript)
    
    Args:
        image_sources: List of image URLs/paths/bytes
        caption_text: TikTok caption text
        transcript_text: Audio transcript (usually empty for photo posts)
        weight_ocr: Weight multiplier for OCR text (default 1.4 = prioritized)
        weight_caption: Weight multiplier for caption (default 1.2)
        weight_transcript: Weight multiplier for transcript (default 0.6)
        
    Returns:
        Weighted and combined text
    """
    # Extract OCR from all slides
    ocr_text = extract_text_from_slideshow(image_sources)
    
    # Build weighted text
    text_parts = []
    
    if ocr_text:
        # Repeat OCR text by weight to prioritize it
        ocr_repetitions = int(weight_ocr)
        for _ in range(ocr_repetitions):
            text_parts.append(ocr_text)
        # Add fractional part if any
        if weight_ocr % 1 > 0:
            ocr_lines = ocr_text.split('\n')
            partial = int(len(ocr_lines) * (weight_ocr % 1))
            text_parts.append('\n'.join(ocr_lines[:max(1, partial)]))
    
    if caption_text:
        caption_repetitions = int(weight_caption)
        for _ in range(caption_repetitions):
            text_parts.append(caption_text)
        if weight_caption % 1 > 0:
            text_parts.append(caption_text[:int(len(caption_text) * (weight_caption % 1))])
    
    if transcript_text:
        transcript_repetitions = int(weight_transcript)
        for _ in range(transcript_repetitions):
            text_parts.append(transcript_text)
        if weight_transcript % 1 > 0:
            text_parts.append(transcript_text[:int(len(transcript_text) * (weight_transcript % 1))])
    
    weighted_text = " ".join(text_parts)
    
    logger.info(
        f"📝 Weighted text created: "
        f"OCR={len(ocr_text)} * {weight_ocr}, "
        f"Caption={len(caption_text)} * {weight_caption}, "
        f"Transcript={len(transcript_text)} * {weight_transcript} "
        f"= {len(weighted_text)} chars total"
    )
    
    return weighted_text
