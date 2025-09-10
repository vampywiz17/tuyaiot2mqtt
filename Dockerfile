# Build a slim, production-ready image for the Tuya–MQTT bridge
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System deps (optional but nice-to-have: curl for debug/health)
RUN apt-get update -y && apt-get install -y --no-install-recommends \
      ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged user
RUN useradd -m -u 10001 appuser
WORKDIR /app

# Install Python deps
# tuya_connector import is provided by the PyPI package "tuya-connector-python"
RUN pip install --no-cache-dir \
      paho-mqtt \
      tuya-connector-python

# Copy your script into the image
# Rename the file here if your local filename differs
COPY tuya_bridge.py /app/tuya_bridge.py

USER appuser

# (Optional) Basic liveness check: process must be up
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,sys; sys.exit(0)"

CMD ["python", "-u", "/app/tuya_bridge.py"]
