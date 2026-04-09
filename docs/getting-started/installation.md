# Setup Guide

## Quick Start (Docker)

=== "Fossil (recommended)"

    ```bash
    fossil clone https://fossilrepo.io/fossilrepo fossilrepo.fossil
    fossil open fossilrepo.fossil --workdir fossilrepo
    cd fossilrepo
    docker compose up -d --build
    docker compose exec backend python manage.py migrate
    docker compose exec backend python manage.py seed
    docker compose exec backend python manage.py seed_roles
    ```

=== "Git (mirror)"

    ```bash
    git clone https://github.com/ConflictHQ/fossilrepo.git
    cd fossilrepo
    docker compose up -d --build
    docker compose exec backend python manage.py migrate
    docker compose exec backend python manage.py seed
    docker compose exec backend python manage.py seed_roles
    ```

Visit http://localhost:8000. Login: `admin` / `admin`.

## Default Users

| Username | Password | Role |
|----------|----------|------|
| admin | admin | Superuser |
| viewer | viewer | View-only |
| role-admin | role-admin | Admin role |
| role-manager | role-manager | Manager role |
| role-developer | role-developer | Developer role |
| role-viewer | role-viewer | Viewer role |

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and customize. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| DJANGO_SECRET_KEY | change-me | **Required in production** |
| DJANGO_DEBUG | false | Enable debug mode |
| DJANGO_ALLOWED_HOSTS | localhost | Comma-separated hostnames |
| POSTGRES_DB | fossilrepo | Database name |
| POSTGRES_USER | dbadmin | Database user |
| POSTGRES_PASSWORD | Password123 | Database password |
| REDIS_URL | redis://localhost:6379/1 | Redis connection |
| EMAIL_HOST | localhost | SMTP server |
| CORS_ALLOWED_ORIGINS | http://localhost:8000 | CORS origins |
| SENTRY_DSN | (empty) | Sentry error tracking |

### Runtime Settings (Constance)

Configurable via Django admin at `/admin/constance/config/`:

| Setting | Default | Description |
|---------|---------|-------------|
| SITE_NAME | Fossilrepo | Display name |
| FOSSIL_DATA_DIR | /data/repos | Where .fossil files live |
| FOSSIL_BINARY_PATH | fossil | Path to fossil binary |
| FOSSIL_STORE_IN_DB | false | Store snapshots via Django file storage |
| FOSSIL_S3_TRACKING | false | Track S3 replication |
| GIT_SYNC_MODE | disabled | Default sync mode |
| GIT_SYNC_SCHEDULE | */15 * * * * | Default cron for git sync |

### OAuth (GitHub/GitLab)

For Git mirror sync via OAuth:
1. Create an OAuth App on GitHub/GitLab
2. Set Client ID and Secret in Constance (Django admin)
3. Callback URLs are handled automatically

## Adding Repositories

### Create Empty

1. Go to Projects > + New
2. Fill in name and description
3. Select "Create empty repository"
4. Done — empty .fossil file created

### Clone from Fossil URL

1. Go to Projects > + New
2. Select "Clone from Fossil URL"
3. Enter the URL (e.g., `https://fossil-scm.org/home`)
4. FossilRepo clones the repo and links it

### Clone Fossil SCM (Example)

```bash
# Clone the official Fossil SCM repo
docker compose exec backend fossil clone https://fossil-scm.org/home /data/repos/fossil-scm.fossil
```

Then create a Project in the UI and link it to the file.

## Production Deployment

See `.env.production.example` for production settings. Key steps:

1. Set a strong `DJANGO_SECRET_KEY`
2. Set `DJANGO_DEBUG=false`
3. Configure `DJANGO_ALLOWED_HOSTS` to your domain
4. Use a proper database password
5. Configure email (SES, SMTP)
6. Set up HTTPS via Caddy or reverse proxy
7. Configure S3 for Litestream backups (optional)

## Ports

| Port | Service |
|------|---------|
| 8000 | Django (HTTP) |
| 2222 | SSH (Fossil sync) |
| 5432 | PostgreSQL |
| 6379 | Redis |
| 1025 | Mailpit SMTP (dev) |
| 8025 | Mailpit UI (dev) |
