FROM python:3.12-slim

LABEL maintainer="PruneMate"
LABEL description="Docker image & resource cleanup helper, on a schedule!"

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ .

# Set default environment variables
ENV TZ=Europe/Amsterdam
ENV SCHEDULE_FREQUENCY=daily
ENV SCHEDULE_TIME=03:00
ENV SCHEDULE_DAY=*
ENV RUN_ON_STARTUP=false
ENV PRUNE_CONTAINERS=true
ENV PRUNE_IMAGES=true
ENV PRUNE_IMAGES_ALL=true
ENV PRUNE_NETWORKS=true
ENV PRUNE_VOLUMES=false
ENV NOTIFICATIONS_ENABLED=false
ENV NOTIFY_ONLY_WHEN_PRUNED=true
ENV NTFY_URL=
ENV NTFY_TOPIC=
# Note: NTFY_TOKEN should be passed at runtime for security

# Run the application
CMD ["python", "main.py"]
