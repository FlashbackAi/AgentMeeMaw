FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README-step-1.md ./
COPY src ./src

RUN python -m venv /venv \
    && /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install .

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH" \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8000

WORKDIR /app

RUN groupadd --system flashback \
    && useradd --system --gid flashback --home-dir /app flashback

COPY --from=builder /venv /venv
COPY migrations ./migrations
COPY scripts ./scripts

USER flashback

EXPOSE 8000

CMD ["sh", "-c", "uvicorn flashback.http.app:create_app --factory --host ${HTTP_HOST} --port ${HTTP_PORT}"]
