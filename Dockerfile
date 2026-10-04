# BitcoinMonetaryView — base image for Docker and future platform packages (StartOS, Umbrel, …)
# For reproducible builds, pin the base image by digest, e.g. python:3.12-slim@sha256:<digest>.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

LABEL org.opencontainers.image.title="BitcoinMonetaryView" \
      org.opencontainers.image.description="Read-only spam analysis for Bitcoin Core/Knots nodes (Monetary Node rules)" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/MarkSierra/BitcoinMonetaryView"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BMV_DATA_DIR=/data \
    BMV_BIND=0.0.0.0 \
    BMV_PORT=8338

RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin bmv \
 && mkdir -p /data && chown bmv:bmv /data && chmod 700 /data

WORKDIR /app
COPY bitcoinmonetaryview/ /app/bitcoinmonetaryview/
COPY LICENSE NOTICE README.md /app/

USER bmv
VOLUME ["/data"]
EXPOSE 8338

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,os,sys; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('BMV_PORT','8338'), timeout=4); sys.exit(0)" || exit 1

ENTRYPOINT ["python3", "-m", "bitcoinmonetaryview"]
