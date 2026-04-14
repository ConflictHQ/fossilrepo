"""Trusted-network authentication middleware.

Two modes, controlled via Django settings (set via env vars):

AUTO_AUTH_USERNAME
    Every request is automatically authenticated as this user.
    Use for single-tenant / homelab installs where you just want to
    skip the login form entirely.  The user must already exist in the
    database (run `manage.py seed` or create a superuser first).

TRUSTED_PROXY_AUTH + TRUSTED_PROXY_USER_HEADER
    Reads a header injected by an upstream authentication proxy
    (Cloudflare Access, Google IAP, Tailscale, nginx auth_request,
    Authentik, etc.) and authenticates as the user named in that header.
    The user must already exist in the database.

In both modes the middleware is a no-op when the request already has
an authenticated session that matches the expected user.

SECURITY WARNING
    Both modes assume the Django application is only reachable *through*
    the trusted proxy or on a trusted network.  If the app is directly
    internet-accessible with these settings enabled, any client can
    authenticate as any user by crafting the right header or env.
    TRUSTED_PROXY_AUTH should never be used without SSL termination that
    strips and re-sets the user header before forwarding.
"""

import contextlib
import logging

from django.conf import settings
from django.contrib import auth
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

_BACKEND = "django.contrib.auth.backends.ModelBackend"


class TrustedProxyAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

        self.auto_user = getattr(settings, "AUTO_AUTH_USERNAME", None) or None
        self.proxy_auth = getattr(settings, "TRUSTED_PROXY_AUTH", False)
        self.user_header = getattr(settings, "TRUSTED_PROXY_USER_HEADER", "HTTP_X_REMOTE_USER")

        active = bool(self.auto_user or self.proxy_auth)
        if active and not settings.DEBUG:
            logger.warning(
                "TrustedProxyAuthMiddleware is active in a non-DEBUG environment. "
                "Ensure the app is only reachable through a trusted auth proxy."
            )

    def __call__(self, request):
        self._maybe_authenticate(request)
        return self.get_response(request)

    def _maybe_authenticate(self, request):
        if self.auto_user:
            self._authenticate_as(request, self.auto_user)
        elif self.proxy_auth:
            username = request.META.get(self.user_header, "").strip()
            if username:
                self._authenticate_as(request, username)

    def _authenticate_as(self, request, username):
        # Already the right user — nothing to do.
        if request.user.is_authenticated and request.user.get_username() == username:
            return

        user = self._get_user(username)
        if user is None:
            return

        # If a different user is in the session, flush it first.
        if request.user.is_authenticated:
            auth.logout(request)

        user.backend = _BACKEND
        auth.login(request, user)
        logger.debug("TrustedProxyAuthMiddleware: authenticated request as %r", username)

    def _get_user(self, username):
        """Return the User for username.

        In AUTO_AUTH_USERNAME mode: create the user (superuser, unusable password)
        if they don't exist yet.  This is the safety net for the first request
        when the entrypoint hasn't run ensure_user (local dev, manual starts, etc).

        In TRUSTED_PROXY_AUTH mode: look up only — we don't auto-create accounts
        for arbitrary proxy-supplied usernames.
        """
        from django.contrib.auth.models import Group

        user_model = get_user_model()

        if self.auto_user:
            user, created = user_model.objects.get_or_create(
                username=username,
                defaults={"is_staff": True, "is_superuser": True},
            )
            if created:
                user.set_unusable_password()
                user.save()
                with contextlib.suppress(Group.DoesNotExist):
                    user.groups.add(Group.objects.get(name="Administrators"))
                logger.info("TrustedProxyAuthMiddleware: auto-created user %r", username)
            return user

        try:
            return user_model.objects.get(username=username)
        except user_model.DoesNotExist:
            logger.warning("TrustedProxyAuthMiddleware: user %r not found in database", username)
            return None
