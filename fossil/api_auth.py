"""API authentication for both project-scoped and user-scoped tokens.

Supports:
1. Project-scoped APIToken (tied to a FossilRepository) — permissions enforced
2. User-scoped PersonalAccessToken (tied to a Django User) — scopes enforced
3. Session auth fallback (for browser testing)
"""

from django.http import JsonResponse
from django.utils import timezone


def authenticate_request(request, repository=None, required_scope="read"):
    """Authenticate an API request via Bearer token.

    Args:
        request: Django request object
        repository: FossilRepository instance (for project-scoped token lookup)
        required_scope: "read", "write", or "admin" — the minimum scope needed

    Returns (user_or_none, token_or_none, error_response_or_none).
    If error_response is not None, return it immediately.
    """
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth.startswith("Bearer "):
        # Fall back to session auth — session users have full access
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
            # Enforce token permissions
            if not _token_has_scope(token.permissions, required_scope):
                return None, None, JsonResponse({"error": f"Token lacks required scope: {required_scope}"}, status=403)
            token.last_used_at = timezone.now()
            token.save(update_fields=["last_used_at"])
            return None, token, None
        except APIToken.DoesNotExist:
            pass

    # Try user-scoped PersonalAccessToken
    from accounts.models import PersonalAccessToken

    token_hash = PersonalAccessToken.hash_token(raw_token)
    try:
        pat = PersonalAccessToken.objects.get(token_hash=token_hash, revoked_at__isnull=True)
        if pat.expires_at and pat.expires_at < timezone.now():
            return None, None, JsonResponse({"error": "Token expired"}, status=401)
        # Enforce PAT scopes
        if not _token_has_scope(pat.scopes, required_scope):
            return None, None, JsonResponse({"error": f"Token lacks required scope: {required_scope}"}, status=403)
        pat.last_used_at = timezone.now()
        pat.save(update_fields=["last_used_at"])
        return pat.user, pat, None
    except PersonalAccessToken.DoesNotExist:
        pass

    return None, None, JsonResponse({"error": "Invalid token"}, status=401)


def _token_has_scope(token_scopes: str, required: str) -> bool:
    """Check if a comma-separated scope string includes the required scope.

    Scope hierarchy: admin > write > read
    A token with "write" scope can do "read" operations.
    A token with "*" or "admin" can do everything.
    """
    scopes = {s.strip().lower() for s in token_scopes.split(",") if s.strip()}

    if "*" in scopes or "admin" in scopes:
        return True
    if required == "read":
        return bool(scopes & {"read", "write", "admin", "status:write"})
    if required == "write":
        return "write" in scopes
    if required == "status:write":
        return bool(scopes & {"status:write", "write", "admin", "*"})
    return False
