FROM python:3.10-slim

# Install base system dependencies (git, curl, ffmpeg, openssh-client, and docker-cli are required)
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ffmpeg \
    openssh-client \
    && curl -fsSL https://get.docker.com | sh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Use Playwright's built-in CLI to automatically install Chromium AND
# all of its exact OS-level system dependencies (highly robust on any Debian/Ubuntu version!)
RUN playwright install chromium
RUN playwright install-deps chromium

# Create directories for persistent storage and cloned projects
RUN mkdir -p /app/data /app/projects

# Copy the application code
COPY app/ /app/app/

# Environment variable to run python in unbuffered mode
ENV PYTHONUNBUFFERED=1

# Command to run the bot
CMD ["python", "-m", "app.bot"]
