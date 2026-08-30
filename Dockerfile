FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for psycopg2 and audio tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose Web port (8000) and Hardware Bridge port (5566)
EXPOSE 8000 5566

# Default command
CMD ["python", "-m", "feishu_meeting_tool", "web", "--host", "0.0.0.0", "--port", "8000"]
