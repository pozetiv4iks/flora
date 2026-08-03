FROM python:3.10-slim

# Install system dependencies (git is required for GitPython, docker-cli to manage docker, ffmpeg for voice processing, and curl for debug)
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create directories for persistent storage and cloned projects
RUN mkdir -p /app/data /app/projects

# Copy the application code
COPY app/ /app/app/

# Environment variable to run python in unbuffered mode
ENV PYTHONUNBUFFERED=1

# Command to run the bot
CMD ["python", "-m", "app.bot"]
