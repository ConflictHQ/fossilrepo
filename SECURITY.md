# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Fossilrepo, please report it responsibly.

**Do not open a public issue.**

Email **security@weareconflict.com** with:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge your report within 48 hours and aim to release a fix within 7 days for critical issues.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| latest  | Yes       |

## Security Model

### Authentication

- Session-based authentication with httpOnly, secure cookies
- CSRF protection on all forms (HTMX includes token via `htmx:configRequest`)
- Rate limiting on login (10 attempts/minute per IP)
- Password validation enforced (Django's built-in validators)

### Authorization

- Group-based permissions via `P` enum (`core/permissions.py`)
- Project-level RBAC: read, write, admin roles via team membership
- Project visibility: public (anonymous read), internal (authenticated), private (team members only)
- All views enforce permission checks before data access

### Data Protection

- SSH keys and OAuth tokens encrypted at rest (Fernet/AES-128-CBC, keyed from `SECRET_KEY`)
- No plaintext credentials stored in the database
- Fossil sync uses `--localauth` only for authenticated users with write access
- Anonymous users get pull-only access on public repos (no `--localauth`)

### Deployment

When deploying Fossilrepo in production:

- Set a strong, unique `DJANGO_SECRET_KEY` (the app refuses to start without one when `DEBUG=False`)
- Change all default database credentials
- Enable HTTPS (`SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` are automatic when `DEBUG=False`)
- Set `DJANGO_ALLOWED_HOSTS` to your domain only
- Set `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` to your domain
- Review Constance settings in Django admin (OAuth secrets, S3 credentials)
- Use a reverse proxy (Caddy/nginx) for SSL termination
- Keep the Fossil binary updated (compiled from source in the Docker image)
