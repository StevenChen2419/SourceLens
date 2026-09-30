# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.lock build-requirements.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes -r requirements.lock \
    && python -m pip install --require-hashes -r build-requirements.lock
COPY pyproject.toml ./
COPY app/ ./app/
RUN python -m pip wheel --no-deps --no-build-isolation --wheel-dir /wheels . \
    && /opt/venv/bin/pip install --no-deps /wheels/*.whl \
    && /opt/venv/bin/pip check
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
RUN /opt/venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"

FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache \
    KNOWLEDGEOPS_ENVIRONMENT=production \
    AZURE_TOKEN_CREDENTIALS=ManagedIdentityCredential
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/tiktoken-cache /opt/tiktoken-cache
WORKDIR /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
