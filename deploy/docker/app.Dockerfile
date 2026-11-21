# Enterprise Fabric AI: the Python images (M17b; CLAUDE.md section 14).
#
#   docker build -f deploy/docker/app.Dockerfile --target api    -t enterprise-fabric/api:dev .
#   docker build -f deploy/docker/app.Dockerfile --target worker -t enterprise-fabric/worker:dev .
#
# One file, two targets: the API (uvicorn on 8000) and the ingestion worker (Kafka consumer,
# metrics on 9464) run the same code. Built from the repository root; .dockerignore keeps
# secrets, local indexes, the licensed ICAI material and development files out.
#
# - CPU-only PyTorch (the CPU wheel index), as on the laptop.
# - The embedding, re-ranking and NLI models are downloaded at BUILD time into /opt/models,
#   and the containers run with HF_HUB_OFFLINE=1: a pod never reaches the internet for them.
# - The committed SYNTHETIC corpus is included, so the lite profile works out of the box
#   (the ICAI chapters are licensed and never in an image).
# - Runs as an unprivileged user (uid 10001); everything it writes goes to /var/lib/fabric.

FROM python:3.13.18-slim-trixie AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/models

WORKDIR /app
COPY requirements.txt requirements-full.txt ./
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0 \
 && pip install -r requirements.txt -r requirements-full.txt

# Models (app/config.py defaults), fetched once at build time at the exact revisions used
# on the development laptop, only the files the app loads, then loaded once offline so a
# broken cache fails the build (deploy/docker/fetch_models.py).
COPY deploy/docker/fetch_models.py /tmp/fetch_models.py
RUN python /tmp/fetch_models.py && rm /tmp/fetch_models.py
# The hub writes its small tree-cache files root-only; make just those readable for uid 10001
# (a chmod over all of /opt/models would copy 1.5 GB of weights into a new layer).
RUN chmod a+r /opt/models/hub/*/trees/*.json

# Security (M17c, found by the Trivy scan in CI): the OS packages' security updates, a
# setuptools without known vulnerabilities (torch declares it), and NO pip in the runtime
# image (pip vendors its own copies of urllib3 and msgpack, and nothing at run time
# installs packages). Placed after the model download so that layer stays cached.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir setuptools==84.2.1 \
 && pip uninstall -y pip

COPY alembic.ini ./
COPY app/ app/
COPY eval/ eval/
COPY scripts/ scripts/
COPY policy/ policy/
COPY data/ data/

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin fabric \
 && mkdir -p /var/lib/fabric \
 && chown -R fabric:fabric /var/lib/fabric

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    SQLITE_PATH=/var/lib/fabric/fabric.db \
    QDRANT_PATH=/var/lib/fabric/qdrant \
    LANDING_DIR=/var/lib/fabric/landing

USER 10001

# ----------------------------------------------------------------------------------- api
FROM base AS api
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# -------------------------------------------------------------------------------- worker
FROM base AS worker
EXPOSE 9464
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9464/metrics', timeout=4)"]
CMD ["python", "-m", "app.ingest.worker"]
