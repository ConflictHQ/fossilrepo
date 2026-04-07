# Installation

## Clone the Repository

```bash
git clone https://github.com/ConflictHQ/fossilrepo.git
cd fossilrepo
```

## Environment Configuration

Copy the example environment file and configure it:

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```ini
# Django
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
POSTGRES_DB=fossilrepo
POSTGRES_USER=fossilrepo
POSTGRES_PASSWORD=your-db-password

# Redis
REDIS_URL=redis://redis:6379/0

# Fossil
FOSSIL_REPO_DIR=/data/repos
FOSSIL_BASE_URL=https://your-domain.com
```

## Start the Stack

### Development

```bash
# Build and start all services
make build

# Run database migrations
make migrate

# Create an admin user
make superuser

# Load sample data (optional)
make seed
```

The development stack includes:

- Django dev server on `http://localhost:8000`
- PostgreSQL 16
- Redis
- Celery worker + beat
- Mailpit on `http://localhost:8025`

### Production

For production, you'll also configure Caddy and Litestream:

```bash
# Copy production configs
cp docker/Caddyfile.example docker/Caddyfile
cp docker/litestream.yml.example docker/litestream.yml

# Edit with your domain and S3 credentials
# Then start with the production compose file
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

## Verify Installation

```bash
# Check all services are running
docker compose ps

# Hit the health endpoint
curl http://localhost:8000/health/

# Open the dashboard
open http://localhost:8000
```

!!! success "You should see"
    The fossilrepo dashboard with navigation, login page, and (after seeding) sample repositories.

## Common Issues

??? question "Port 8000 already in use"
    Change the Django port mapping in `docker-compose.yml`:
    ```yaml
    ports:
      - "8001:8000"
    ```

??? question "Database connection refused"
    Ensure PostgreSQL has started before Django:
    ```bash
    docker compose logs postgres
    ```
    The Django container waits for Postgres to be ready, but network issues on some Docker Desktop versions can cause timeouts. Restart with `make down && make up`.

??? question "Permission denied on /data/repos"
    The Fossil repo directory needs to be writable by the container user:
    ```bash
    sudo chown -R 1000:1000 /data/repos
    ```
