FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POLYAGENTS_WEB_HOST=0.0.0.0 \
    POLYAGENTS_WEB_PORT=8000 \
    HOME=/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY polyagents ./polyagents
COPY skills ./skills
COPY README.md ./

RUN mkdir -p /data/.polyagents

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3).read()"

CMD ["python", "-m", "polyagents.web"]
