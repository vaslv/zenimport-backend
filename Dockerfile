# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS python-base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app

FROM python-base AS config
WORKDIR /app
COPY backend/__init__.py backend/config.py ./backend/
COPY docker/init_env.py ./docker/init_env.py
COPY .env.example ./.env.example
ENV PYTHONPATH=/app
CMD ["python", "/app/docker/init_env.py"]

FROM python-base AS dependencies
WORKDIR /app
COPY requirements.runtime.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements.runtime.lock.txt
COPY requirements.sdk.lock.txt ./
RUN python -m pip --isolated install --no-cache-dir --no-deps --only-binary=:all: \
       --index-url https://pypi.org/simple --require-hashes -r requirements.sdk.lock.txt \
    && python -m pip check

FROM dependencies AS runtime
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir /data && chown app:app /data
COPY backend ./backend
COPY LICENSE ./LICENSE
COPY .env.example ./.env.example
COPY .env.server.example ./.env.server.example
COPY docker/init_server_env.py ./docker/init_server_env.py
ARG VCS_REF=unknown
ARG SOURCE_URL=https://github.com/vaslv/zenimport-backend
LABEL org.opencontainers.image.title="ZenImport Backend" \
      org.opencontainers.image.description="Bank history synchronization for ZenMoney" \
      org.opencontainers.image.version="0.2.1" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source=$SOURCE_URL
USER 10001:10001
ENV DATABASE_PATH=/data/zenimport.sqlite3
EXPOSE 8000
HEALTHCHECK --interval=3s --timeout=3s --start-period=5s --retries=10 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"
CMD ["python", "-m", "uvicorn", "backend.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

FROM runtime AS tests
USER root
COPY requirements.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements.lock.txt
COPY pyproject.toml ./
COPY tests ./tests
COPY scripts/migration_lab.py scripts/migration_history.py scripts/migration_replay.py ./scripts/
COPY scripts/update_sdk.py ./scripts/update_sdk.py
COPY fixtures/sync-v1 ./fixtures/sync-v1
COPY fixtures/legacy-v12 ./fixtures/legacy-v12
USER 10001:10001
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

# Explicit acceptance target only. Normal builds never include the live runner.
FROM runtime AS migration-lab
COPY scripts/migration_lab.py scripts/migration_history.py scripts/migration_replay.py ./scripts/
ENTRYPOINT ["python", "/app/scripts/migration_lab.py"]
CMD ["inspect"]

# A default build always produces the service, not the test/configuration image.
FROM runtime AS final
