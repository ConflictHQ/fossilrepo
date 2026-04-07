"""Custom model fields — encrypted storage using Fernet symmetric encryption."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _get_fernet():
    """Derive a Fernet key from Django's SECRET_KEY."""
    key_bytes = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


class EncryptedTextField(models.TextField):
    """TextField that encrypts data at rest using Fernet (AES-128-CBC + HMAC).

    Values are transparently encrypted on save and decrypted on read.
    Stored as base64-encoded ciphertext in the database.
    """

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        f = _get_fernet()
        return f.encrypt(value.encode("utf-8")).decode("utf-8")

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        f = _get_fernet()
        try:
            return f.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            # Value may not be encrypted (e.g. pre-existing data).
            return value

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        return name, path, args, kwargs
