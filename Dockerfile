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
    postgresql-client ca-certificates zlib1g libssl3 openssh-server \
    && rm -rf /var/lib/apt/lists/*

# Copy Fossil binary from builder
COPY --from=fossil-builder /usr/local/bin/fossil /usr/local/bin/fossil
RUN fossil version

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY . .

RUN DJANGO_SECRET_KEY=build-placeholder DJANGO_DEBUG=true python manage.py collectstatic --noinput

# Create data directories
RUN mkdir -p /data/repos /data/trash /data/ssh

# SSH setup — restricted fossil user + sshd for clone/push
RUN useradd -r -m -d /home/fossil -s /bin/bash fossil \
    && mkdir -p /run/sshd /home/fossil/.ssh \
    && chown fossil:fossil /home/fossil/.ssh \
    && chmod 700 /home/fossil/.ssh

COPY docker/sshd_config /etc/ssh/sshd_config
COPY docker/fossil-shell /usr/local/bin/fossil-shell
RUN chmod +x /usr/local/bin/fossil-shell

# Generate host keys if they don't exist (entrypoint will handle persistent keys)
RUN ssh-keygen -A

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DJANGO_SETTINGS_MODULE=config.settings

EXPOSE 8000 2222

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

CMD ["/usr/local/bin/entrypoint.sh"]
