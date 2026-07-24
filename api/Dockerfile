FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      antiword \
      build-essential \
      curl \
      libffi-dev \
      libgl1 \
      libglib2.0-0 \
      poppler-utils \
      tesseract-ocr \
      tesseract-ocr-eng \
      unrtf \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG TYPECHECK=0
RUN if [ "$TYPECHECK" = "1" ]; then pip install --no-cache-dir "mypy>=1.11" "types-redis" "types-requests" ruff; fi

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=8s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)" || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
