# Playwright's official Python image ships Chromium + all system deps, so the
# in-container browser "just works" (no apt dance).
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY . .

# Hosted defaults (override via Fly secrets/env). Persistent data on /data.
ENV ARBFINDER_HOSTED=1 \
    ARBFINDER_DATA_DIR=/data \
    PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# ONE worker so the JobStore + single scan-worker thread are process-global;
# threads serve the quick status/results polls while a scan runs off-request.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "180", \
     "--bind", "0.0.0.0:8080", "arbfinder.dashboard:app"]
