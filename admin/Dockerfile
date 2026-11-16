# Avedeus admin console (PR 15). Builds for arm64 and amd64 (pure-Python dependencies).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && useradd --system --uid 10004 --home /srv admin
COPY app ./app

USER admin
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/console/healthz', timeout=4)"
# One worker: sessions are kept in this process's memory (a restart signs people out).
# --forwarded-allow-ips trusts X-Forwarded-For only from mailcow's nginx.
CMD ["sh", "-c", "exec uvicorn app.main:get_app --factory --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-172.30.240.2}\" --no-server-header --no-access-log"]
