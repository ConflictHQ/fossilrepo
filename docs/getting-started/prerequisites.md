# Prerequisites

Before installing fossilrepo, ensure your server meets these requirements.

## System Requirements

| Requirement | Minimum |
|---|---|
| **OS** | Linux (Ubuntu 22.04+, Debian 12+, RHEL 9+) or macOS 13+ |
| **CPU** | 1 vCPU |
| **RAM** | 1 GB |
| **Disk** | 10 GB (scales with repo count) |
| **Python** | 3.12+ |

## Required Software

### Docker & Docker Compose

Fossilrepo runs its infrastructure stack via Docker Compose.

=== "Ubuntu/Debian"

    ```bash
    # Install Docker
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER

    # Verify
    docker compose version
    ```

=== "macOS"

    ```bash
    # Install Docker Desktop
    brew install --cask docker

    # Verify
    docker compose version
    ```

### Git

Required for the sync bridge (mirroring to GitHub/GitLab).

```bash
git --version  # 2.30+
```

### Make

Used for running common commands.

```bash
make --version
```

## Optional: S3-Compatible Storage

For continuous backups via Litestream, you need an S3-compatible bucket:

- **AWS S3**
- **MinIO** (self-hosted)
- **Backblaze B2**
- **DigitalOcean Spaces**

!!! info "Local development"
    S3 is not required for local development. Litestream is disabled by default in the dev Docker Compose configuration.

## Ports

The following ports are used by the stack:

| Port | Service |
|---|---|
| `8000` | Django (management UI) |
| `443` | Caddy (HTTPS, production) |
| `80` | Caddy (HTTP redirect, production) |
| `5432` | PostgreSQL |
| `6379` | Redis |
| `8025` | Mailpit (dev only) |
