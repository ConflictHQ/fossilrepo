# Configuration

## Environment Variables

All configuration is done through environment variables, loaded from `.env` in development.

### Django Settings

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | -- | Django secret key (required) |
| `DEBUG` | `False` | Enable debug mode |
| `ALLOWED_HOSTS` | `localhost` | Comma-separated list of allowed hosts |
| `TIME_ZONE` | `UTC` | Server timezone |

### Database

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `fossilrepo` | Database name |
| `POSTGRES_USER` | `fossilrepo` | Database user |
| `POSTGRES_PASSWORD` | -- | Database password (required) |
| `POSTGRES_HOST` | `postgres` | Database host |
| `POSTGRES_PORT` | `5432` | Database port |

### Redis & Celery

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |
| `CELERY_BROKER_URL` | `$REDIS_URL` | Celery broker (defaults to Redis URL) |

### Fossil

| Variable | Default | Description |
|---|---|---|
| `FOSSIL_REPO_DIR` | `/data/repos` | Directory where `.fossil` files are stored |
| `FOSSIL_BASE_URL` | -- | Base URL for Fossil web UI (e.g., `https://code.example.com`) |
| `FOSSIL_BINARY` | `fossil` | Path to the Fossil binary |

### Caddy (Production)

| Variable | Default | Description |
|---|---|---|
| `CADDY_DOMAIN` | -- | Your domain (e.g., `example.com`) |
| `CADDY_EMAIL` | -- | Email for Let's Encrypt certificates |

### Litestream (Backups)

| Variable | Default | Description |
|---|---|---|
| `LITESTREAM_ACCESS_KEY_ID` | -- | S3 access key |
| `LITESTREAM_SECRET_ACCESS_KEY` | -- | S3 secret key |
| `LITESTREAM_BUCKET` | -- | S3 bucket name |
| `LITESTREAM_ENDPOINT` | -- | S3 endpoint (for MinIO/B2) |
| `LITESTREAM_REGION` | `us-east-1` | S3 region |

### Sync Bridge

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | -- | GitHub personal access token (for mirroring) |
| `GITLAB_TOKEN` | -- | GitLab personal access token (for mirroring) |

## Caddy Configuration

The Caddyfile controls SSL termination and subdomain routing. Each Fossil repo gets its own subdomain:

```
{$CADDY_DOMAIN} {
    reverse_proxy django:8000
}

*.{$CADDY_DOMAIN} {
    reverse_proxy fossil:8080
}
```

Caddy automatically provisions Let's Encrypt certificates for all subdomains.

## Litestream Configuration

Litestream continuously replicates every `.fossil` SQLite file to S3:

```yaml
dbs:
  - path: /data/repos/*.fossil
    replicas:
      - type: s3
        bucket: ${LITESTREAM_BUCKET}
        endpoint: ${LITESTREAM_ENDPOINT}
        region: ${LITESTREAM_REGION}
```

!!! tip "Point-in-time recovery"
    Litestream replicates WAL frames continuously. You can restore any `.fossil` file to any point in time, not just the latest snapshot.
