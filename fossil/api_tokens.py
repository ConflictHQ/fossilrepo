"""API tokens scoped to a repository for CI/CD and automation.

Tokens are stored as SHA-256 hashes -- the raw value is shown once on creation
and never stored in plaintext.
"""

import hashlib
import secrets

from django.db import models
from django.utils import timezone

from core.models import ActiveManager, Tracking


class APIToken(Tracking):
    """API token scoped to a repository for CI/CD and automation."""

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(max_length=200)
    token_hash = models.CharField(max_length=64, unique=True, help_text="SHA-256 hash of the token")
    token_prefix = models.CharField(max_length=12, help_text="First 12 chars for identification")
    permissions = models.CharField(max_length=200, default="status:write", help_text="Comma-separated permissions")
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]

    @staticmethod
    def generate():
        """Generate a new token. Returns (raw_token, token_hash, prefix)."""
        raw = f"frp_{secrets.token_urlsafe(32)}"
        hash_val = hashlib.sha256(raw.encode()).hexdigest()
        prefix = raw[:12]
        return raw, hash_val, prefix

    @staticmethod
    def hash_token(raw_token):
        return hashlib.sha256(raw_token.encode()).hexdigest()

    def has_permission(self, permission):
        """Check if this token has a specific permission."""
        perms = [p.strip() for p in self.permissions.split(",")]
        return permission in perms or "*" in perms

    def __str__(self):
        return f"{self.name} ({self.token_prefix}...)"


def authenticate_api_token(request, repository):
    """Check Bearer token auth. Returns APIToken or None."""
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth.startswith("Bearer "):
        return None
    raw_token = auth[7:]
    token_hash = APIToken.hash_token(raw_token)
    try:
        token = APIToken.objects.get(token_hash=token_hash, repository=repository, deleted_at__isnull=True)
        # Check expiry
        if token.expires_at and token.expires_at < timezone.now():
            return None
        token.last_used_at = timezone.now()
        token.save(update_fields=["last_used_at"])
        return token
    except APIToken.DoesNotExist:
        return None
