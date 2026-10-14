# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS python-base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

FROM python-base AS config
WORKDIR /app
COPY docker/init_env.py /app/init_env.py
COPY backend/config.py /app/backend/config.py
COPY .env.example /app/.env.example
CMD ["python", "/app/init_env.py"]

FROM python-base AS dependencies
WORKDIR /app
COPY requirements.runtime.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements.runtime.lock.txt

COPY vendor ./vendor
RUN cd vendor && sha256sum -c SHA256SUMS \
    && python -m pip install --no-cache-dir --no-deps ./zenmoney_api_client-0.6.0-py3-none-any.whl

FROM dependencies AS runtime
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir /data && chown app:app /data
COPY backend ./backend
COPY .env.example ./.env.example
COPY fixtures/demo-bank.html ./fixtures/demo-bank.html
USER 10001:10001
ENV DATABASE_PATH=/data/zenimport.sqlite3
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"
CMD ["python", "-m", "uvicorn", "backend.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM runtime AS backend-tests
USER root
COPY requirements.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements.lock.txt
COPY pyproject.toml ./
COPY tests ./tests
COPY fixtures/import.json ./fixtures/import.json
USER 10001:10001
CMD ["sh", "-c", "python -m pytest -q -p no:cacheprovider && python tests/http_smoke.py"]

FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS parser-tests
WORKDIR /app
COPY extension ./extension
COPY tests/parser.test.mjs ./tests/parser.test.mjs
USER node
CMD ["node", "--test", "tests/parser.test.mjs"]


FROM mcr.microsoft.com/playwright/python:v1.58.0-noble@sha256:678457c4c323b981d8b4befc57b95366bb1bb6aa30057b1269f6b171e8d9975a AS browser-tests
WORKDIR /app
COPY requirements.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements.lock.txt playwright==1.58.0
COPY extension ./extension
COPY tests/browser ./tests/browser
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/browser/extension_flow.py"]

# Default builds produce the application, not a test image.
FROM runtime AS final
