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

        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=username)
        except user_model.DoesNotExist:
            logger.warning("TrustedProxyAuthMiddleware: user %r not found in database", username)
            return

        # If a different user is in the session, flush it first.
        if request.user.is_authenticated:
            auth.logout(request)

        user.backend = _BACKEND
        auth.login(request, user)
        logger.debug("TrustedProxyAuthMiddleware: authenticated request as %r", username)
