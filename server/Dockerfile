# ─── Dockerfile ─────────────────────────────────────────────────────────────
# Optimized for Oracle Ampere A1 (ARM64 / aarch64).
# Uses the official multi-arch Python slim image; Docker will automatically
# pull the linux/arm64 variant when building on your ARM64 server.
# ─────────────────────────────────────────────────────────────────────────────

# --- Stage 1: dependency builder -------------------------------------------
FROM python:3.11-slim AS builder

# Install build tools needed by some wheels on ARM64
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .

# Install into a local directory so we can copy only the wheels to the runner
RUN pip install --upgrade pip \
 && pip install --prefix=/install --no-cache-dir -r requirements.txt


# --- Stage 2: minimal runtime image ----------------------------------------
FROM python:3.11-slim AS runner

# Create a non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY database.py main.py ./

# Data volume – SQLite database lives here (mounted by docker-compose)
RUN mkdir -p /app/data && chown appuser:appgroup /app/data

USER appuser

EXPOSE 8000

# uvicorn with a single worker is sufficient for this use-case.
# Increase --workers if you anticipate concurrent captures.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
