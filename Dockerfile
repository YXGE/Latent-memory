FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY src/ /app/src/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz',timeout=3)" || exit 1

# /data must be a persistent Zeabur Volume. The first mkdir lets a fresh empty
# volume boot far enough for the owner to upload the private memory corpus.
CMD ["sh", "-c", "mkdir -p /data/memory && exec python /app/src/chatgpt_action_server.py --runtime-src /app/src --corpus /data/memory --threads /data/threads.jsonl --host 0.0.0.0 --port ${PORT:-8080}"]
