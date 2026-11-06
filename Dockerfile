FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-worker.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-worker.txt \
    && python -m playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml app.py reset_sandbox.py ./
COPY linda_worker ./linda_worker
COPY tests ./tests
COPY data ./data
RUN pip install --no-cache-dir --no-deps .
CMD ["python", "-m", "linda_worker"]
