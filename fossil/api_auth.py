"""API authentication for both project-scoped and user-scoped tokens.

Supports:
1. Project-scoped APIToken (tied to a FossilRepository)
2. User-scoped PersonalAccessToken (tied to a Django User)
3. Session auth fallback (for browser testing)
"""

from django.http import JsonResponse
from django.utils import timezone


def authenticate_request(request, repository=None):
    """Authenticate an API request via Bearer token.

    Returns (user_or_none, token_or_none, error_response_or_none).
    If error_response is not None, return it immediately.
    """
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth.startswith("Bearer "):
        # Fall back to session auth
        if request.user.is_authenticated:
            return request.user, None, None
        return None, None, JsonResponse({"error": "Authentication required"}, status=401)

    raw_token = auth[7:]

    # Try project-scoped APIToken first (only if repository is provided)
    if repository:
        from fossil.api_tokens import APIToken

        token_hash = APIToken.hash_token(raw_token)
        try:
            token = APIToken.objects.get(token_hash=token_hash, repository=repository, deleted_at__isnull=True)
            if token.expires_at and token.expires_at < timezone.now():
                return None, None, JsonResponse({"error": "Token expired"}, status=401)
            token.last_used_at = timezone.now()
            token.save(update_fields=["last_used_at"])
            return None, token, None  # No user, but valid project token
        except APIToken.DoesNotExist:
            pass

    # Try user-scoped PersonalAccessToken
    from accounts.models import PersonalAccessToken

    token_hash = PersonalAccessToken.hash_token(raw_token)
    try:
        pat = PersonalAccessToken.objects.get(token_hash=token_hash, revoked_at__isnull=True)
        if pat.expires_at and pat.expires_at < timezone.now():
            return None, None, JsonResponse({"error": "Token expired"}, status=401)
        pat.last_used_at = timezone.now()
        pat.save(update_fields=["last_used_at"])
        return pat.user, pat, None
    except PersonalAccessToken.DoesNotExist:
        pass

    return None, None, JsonResponse({"error": "Invalid token"}, status=401)
