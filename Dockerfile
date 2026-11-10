# BitcoinMonetaryView — base image for Docker and future platform packages (StartOS, Umbrel, …)
# Base image pinned by digest: every build uses exactly this image. Dependabot opens a PR
# when python:3.15-slim gets a new digest (Python or Debian security updates).
FROM python:3.15-slim@sha256:e97b88e416c3c01eb427b21fd8cd3ebf006e6ee38afcfcc3cae1c54ee2ab0a75

LABEL org.opencontainers.image.title="Monetary View" \
      org.opencontainers.image.description="Read-only spam analysis for Bitcoin Core/Knots/Roots nodes (Monetary Node rules)" \
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
  CMD python3 -c "import http.client,os,sys; c=http.client.HTTPConnection('127.0.0.1', int(os.environ.get('BMV_PORT','8338')), timeout=4); c.request('GET','/api/health'); sys.exit(c.getresponse().status != 200)" || exit 1

ENTRYPOINT ["python3", "-m", "bitcoinmonetaryview"]
