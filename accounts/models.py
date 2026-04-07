"""User profile and personal access token models.

UserProfile extends Django's built-in User with optional profile fields.
PersonalAccessToken provides user-scoped tokens for API/CLI authentication,
separate from project-scoped APITokens.
"""

import hashlib
import re
import secrets

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    """Extended profile information for users."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    handle = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        default=None,
        unique=True,
        help_text="@handle for mentions (alphanumeric and hyphens only)",
    )
    bio = models.TextField(blank=True, default="", max_length=500)
    location = models.CharField(max_length=100, blank=True, default="")
    website = models.URLField(blank=True, default="")

    def __str__(self):
        return f"@{self.handle or self.user.username}"

    @staticmethod
    def sanitize_handle(raw: str) -> str:
        """Slugify a handle: lowercase, alphanumeric + hyphens, strip leading/trailing hyphens."""
        cleaned = re.sub(r"[^a-z0-9-]", "", raw.lower().strip())
        return cleaned.strip("-")


class PersonalAccessToken(models.Model):
    """User-scoped personal access token for API/CLI authentication.

    Tokens are stored as SHA-256 hashes -- the raw value is shown once on
    creation and never stored in plaintext.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="personal_tokens")
    name = models.CharField(max_length=200)
    token_hash = models.CharField(max_length=64, unique=True)
    token_prefix = models.CharField(max_length=12)
    scopes = models.CharField(max_length=500, default="read", help_text="Comma-separated: read, write, admin")
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

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

    @property
    def is_expired(self):
        return bool(self.expires_at and self.expires_at < timezone.now())

    @property
    def is_revoked(self):
        return self.revoked_at is not None

    @property
    def is_active(self):
        return not self.is_expired and not self.is_revoked

    def __str__(self):
        return f"{self.name} ({self.token_prefix}...)"
