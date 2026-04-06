# fossilrepo backend — Django + HTMX + Fossil binary
#
# Omnibus: bundles Fossil from source for repo init/management.

# ── Stage 1: Build Fossil from source ──────────────────────────────────────

FROM debian:bookworm-slim AS fossil-builder

ARG FOSSIL_VERSION=2.24

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates zlib1g-dev libssl-dev tcl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN curl -sSL "https://fossil-scm.org/home/tarball/version-${FOSSIL_VERSION}/fossil-src-${FOSSIL_VERSION}.tar.gz" \
    -o fossil.tar.gz \
    && tar xzf fossil.tar.gz \
    && cd fossil-src-${FOSSIL_VERSION} \
    && ./configure --prefix=/usr/local --with-openssl=auto --json \
    && make -j$(nproc) \
    && make install

# ── Stage 2: Runtime image ─────────────────────────────────────────────────

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client ca-certificates zlib1g libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Copy Fossil binary from builder
COPY --from=fossil-builder /usr/local/bin/fossil /usr/local/bin/fossil
RUN fossil version

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY . .

RUN python manage.py collectstatic --noinput 2>/dev/null || true

# Create data directory for .fossil files
RUN mkdir -p /data/repos /data/trash

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DJANGO_SETTINGS_MODULE=config.settings

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
