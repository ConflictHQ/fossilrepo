"""Per-user SSH public keys for Fossil clone/push over SSH."""

from django.contrib.auth.models import User
from django.db import models

from core.fields import EncryptedTextField
from core.models import ActiveManager, Tracking


class UserSSHKey(Tracking):
    """SSH public key uploaded by a user for Fossil access."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ssh_keys")
    title = models.CharField(max_length=200, help_text="Label for this key (e.g. 'Work laptop')")
    public_key = EncryptedTextField(help_text="SSH public key (ssh-ed25519, ssh-rsa, etc.)")
    fingerprint = models.CharField(max_length=100, blank=True, default="")
    key_type = models.CharField(max_length=20, blank=True, default="")  # ed25519, rsa, etc.
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "User SSH Key"

    def __str__(self):
        return f"{self.user.username}: {self.title}"
