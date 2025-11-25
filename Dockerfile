# Use Python 3.11 slim image
FROM python:3.11-slim

# Install system dependencies including tesseract-ocr and opencv dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    ffmpeg \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY ocr_processor.py .
COPY slideshow_extractor.py .
COPY geocoding_service.py .
COPY Procfile .

# Expose port (Render will set PORT env var)
EXPOSE 10000

# Use gunicorn to run the app
CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 120 --access-logfile - --error-logfile -

