FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

WORKDIR /app

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA

COPY --from=builder /install /usr/local

RUN useradd --create-home --uid 10001 appuser

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
