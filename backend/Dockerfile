FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws", "websockets", "--ws-max-size", "4096", "--ws-ping-interval", "20", "--ws-ping-timeout", "15", "--no-proxy-headers", "--timeout-keep-alive", "5", "--limit-concurrency", "200", "--limit-max-requests", "5000", "--backlog", "512", "--no-access-log"]
