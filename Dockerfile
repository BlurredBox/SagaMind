# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────
# Stage 1 — build dependencies
# ─────────────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .
RUN pip install --user --no-cache-dir --require-hashes -r requirements.lock

# ─────────────────────────────────────────────────────────────────────
# Stage 2 — minimal runtime image (non-root)
# ─────────────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runner

# Create an unprivileged user to run the service.
RUN groupadd --system sagamind && useradd --system --gid sagamind --create-home sagamind

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    z3 \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from the builder; place on PATH for the runtime user.
COPY --from=builder /root/.local /home/sagamind/.local
ENV PATH=/home/sagamind/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --chown=sagamind:sagamind . .

USER sagamind

EXPOSE 8000 50051

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready').status==200 else 1)"

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
