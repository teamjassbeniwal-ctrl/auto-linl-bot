# ─────────────────────────────────────────────
# Telegram Auto-Link Bot — Docker image
# Base: official slim Python 3.11
# ─────────────────────────────────────────────
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set working directory
WORKDIR /app

# Install system dependencies required by some Python packages
# (kept minimal for a small image)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (better Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash botuser \
    && chown -R botuser:botuser /app
USER botuser

# Default port (Koyeb injects PORT at runtime)
ENV PORT=8080
EXPOSE 8080

# Health check (used by Docker, Koyeb uses its own HTTP probe)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8080\")}/health', timeout=3)" || exit 1

# Start the bot
CMD ["python", "main.py"]
