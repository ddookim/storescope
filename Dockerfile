FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1  # FIX: without this, CPython buffers stdout/stderr in non-TTY mode; Docker/systemd logs show nothing until buffer flushes or process exits

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000 8501
