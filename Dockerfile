# syntax=docker/dockerfile:1

# ── Stage 1: Build React frontend ───────────────────────────────────────────
FROM node:20-alpine AS web-builder
WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY web/ ./
RUN npm run build

# ── Stage 2: Python API + serve frontend ────────────────────────────────────
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      antiword \
      curl \
      libgl1 \
      libglib2.0-0 \
      poppler-utils \
      tesseract-ocr \
      tesseract-ocr-eng \
      unrtf \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 library \
    && useradd --uid 10001 --gid library --create-home --shell /usr/sbin/nologin library

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=library:library api/ .
COPY --chown=library:library --from=web-builder /app/web/dist /app/static

RUN mkdir -p /home/library/.cache && chown -R library:library /home/library /app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=8s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)" || exit 1

USER library

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
