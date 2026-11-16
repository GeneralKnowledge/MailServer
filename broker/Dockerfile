# Control plane (provisioning broker): the only container that holds the mailcow API key.
# Builds for arm64 and amd64 (pure-Python dependencies plus cryptography wheels for both).
FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && useradd --system --uid 10002 --home /srv broker \
 && mkdir /data && chown broker /data
COPY broker ./broker

USER broker
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=4)"
# One worker: mailbox creation is serialised by an in-process lock (the daily
# cap itself is counted from the audit log, so it survives restarts).
# No --proxy-headers: callers connect directly, so the client address is real.
CMD ["uvicorn", "broker.main:get_app", "--factory", "--host", "0.0.0.0", "--port", "8100", "--workers", "1", "--no-server-header"]
