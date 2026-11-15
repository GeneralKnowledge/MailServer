# Invite registration app. Builds for arm64 and amd64 (pure-Python deps + argon2 wheels).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && useradd --system --uid 10001 --home /srv app \
 && mkdir /data && chown app /data
COPY app ./app

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import os,urllib.request; b=os.environ.get('BASE_PATH','/join').strip('/'); urllib.request.urlopen('http://127.0.0.1:8000'+('/'+b if b else '')+'/healthz', timeout=4)"
# One worker: the rate limiter and SQLite writer are per-process by design.
# --no-access-log: invite links carry secret tokens in the URL; don't write them to logs.
# --forwarded-allow-ips trusts X-Forwarded-For only from mailcow's nginx (its avedeus-edge address).
CMD ["sh", "-c", "exec uvicorn app.main:get_app --factory --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-172.30.240.2}\" --no-server-header --no-access-log"]
