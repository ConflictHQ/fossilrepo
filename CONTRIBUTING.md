# Contributing to Fossilrepo

Thanks for your interest in contributing. This document covers how to get set up, our coding standards, and the PR process.

## Development Setup

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Ruff](https://docs.astral.sh/ruff/) (linter/formatter)

### Running Locally

```bash
git clone https://github.com/ConflictHQ/fossilrepo.git
cd fossilrepo

# Start infrastructure
docker compose up -d postgres redis mailpit

# Install dependencies
uv sync --all-extras

# Run migrations and seed data
DJANGO_DEBUG=true uv run python manage.py migrate
DJANGO_DEBUG=true uv run python manage.py seed

# Start the dev server
DJANGO_DEBUG=true POSTGRES_HOST=localhost uv run python manage.py runserver
```

Or use Docker for everything:

```bash
docker compose up -d --build
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed
```

### Default Users

- `admin` / `admin` — superuser, full access
- `viewer` / `viewer` — read-only permissions

## Code Style

We use **Ruff** for linting and formatting. No debates, no custom configs.

```bash
# Check
ruff check .
ruff format --check .

# Fix
ruff check --fix .
ruff format .
```

Key conventions:

- **Max line length:** 140 characters
- **Imports:** sorted by Ruff (isort rules)
- **Quote style:** double quotes
- **Target:** Python 3.12+

## Codebase Conventions

Read [`bootstrap.md`](bootstrap.md) before writing code. It covers:

- Model base classes (`Tracking`, `BaseCoreModel`)
- Soft deletes (never call `.delete()`)
- Permission system (`P` enum + project-level RBAC)
- View patterns (HTMX partials, auth checks)
- Template conventions (dark theme, Tailwind classes)

## Testing

Tests run against a real PostgreSQL database. No mocked databases.

```bash
# Run all tests
DJANGO_DEBUG=true uv run pytest

# Run specific test file
DJANGO_DEBUG=true uv run pytest tests/test_releases.py

# Run with coverage
DJANGO_DEBUG=true uv run pytest --cov
```

Every PR should:

- Include tests for new features (happy path + permission denied cases)
- Not decrease test coverage
- Pass all existing tests

## Pull Request Process

1. **Fork and branch** from `main`. Branch naming: `feature/short-description` or `fix/short-description`.

2. **Write code** following the conventions in `bootstrap.md`.

3. **Write tests.** Both allowed and denied permission cases. Assert against database state, not just status codes.

4. **Lint and test locally.** CI will catch it anyway, but save yourself a round trip.

5. **Open a PR** with a clear description:
   - What changed and why
   - How to test it
   - Link to any related issues

6. **Address review feedback** in new commits (don't amend/squash during review).

7. **Merge** when CI is green and review is approved.

## Reporting Issues

Use [GitHub Issues](https://github.com/ConflictHQ/fossilrepo/issues). Include:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Browser/OS/version if relevant

## Architecture Decisions

Fossilrepo has some non-obvious design choices worth understanding:

- **No Fossil HTTP server.** We read `.fossil` files directly via SQLite (`FossilReader`) and use `fossil http` in CGI mode for sync. No persistent Fossil process, stateless containers.
- **Django-backed forum posts** supplement Fossil's native forum because Fossil forum posts don't sync via clone/pull.
- **Encrypted fields** use Fernet (AES-128-CBC + HMAC) keyed from `SECRET_KEY` for SSH keys and OAuth tokens at rest.
- **Single org model.** Multi-org is possible but not implemented — fossilrepo targets self-hosted single-team deployments.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
