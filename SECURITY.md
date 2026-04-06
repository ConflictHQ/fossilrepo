# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Fossilrepo, please report it responsibly.

**Do not open a public issue.**

Instead, email **security@weareconflict.com** with:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge your report within 48 hours and aim to release a fix within 7 days for critical issues.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| latest  | Yes       |

## Security Best Practices

When deploying Fossilrepo:

- Change all default credentials (database, MinIO, session secret)
- Use HTTPS in production
- Set `NODE_ENV=production`
- Configure `CORS_ORIGINS` to your domain only
- Use strong Auth0 credentials
- Review the security hardening in `bootstrap.md`
