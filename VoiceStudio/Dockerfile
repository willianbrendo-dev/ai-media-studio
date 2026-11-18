FROM python:3.11-slim-bookworm

ENV PORT=3030 \
    HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    espeak-ng \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    edge-tts \
    httpx \
    pydantic \
    aiofiles \
    python-multipart

COPY app.py ./
RUN mkdir -p /app/output

EXPOSE 3030

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3030"]
