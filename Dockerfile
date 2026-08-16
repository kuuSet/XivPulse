FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    XIVPULSE_DATA_DIR=/app/data \
    XIVPULSE_HOST=0.0.0.0 \
    XIVPULSE_PORT=8765 \
    PYTHONPATH=/app/backend

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/backend/requirements.txt \
    && addgroup --system xivpulse \
    && adduser --system --ingroup xivpulse --home /app xivpulse \
    && mkdir -p /app/data /app/frontend/dist \
    && chown -R xivpulse:xivpulse /app

COPY --chown=xivpulse:xivpulse backend/ /app/backend/
COPY --from=frontend-build --chown=xivpulse:xivpulse /build/frontend/dist/ /app/frontend/dist/

USER xivpulse
EXPOSE 8765
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8765"]
