FROM python:3.11-slim

WORKDIR /app

# 라인 끝 주석 = Docker ENV 파서가 값 일부로 인식 → PYTHONUNBUFFERED 환경변수에 주석 텍스트 포함됨.
# without this, CPython buffers stdout/stderr in non-TTY mode; Docker/systemd logs show nothing.
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SEC: non-root user — root container = CVE blast radius 확대.
# 다중 단계 빌드 안 한 이유: gcc 가 런타임 의존성 (psycopg2-binary 는 prebuilt wheel 사용하나
# 미래 의존성 추가 시 컴파일 필요).
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

EXPOSE 8000 8501
