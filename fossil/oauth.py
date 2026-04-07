"""Lightweight OAuth2 flows for GitHub and GitLab.

No dependency on django-allauth — just requests + constance config.
Stores tokens on GitMirror.auth_credential.
"""

import logging
import secrets

import requests

logger = logging.getLogger(__name__)

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

GITLAB_AUTHORIZE_URL = "https://gitlab.com/oauth/authorize"
GITLAB_TOKEN_URL = "https://gitlab.com/oauth/token"


def github_authorize_url(request, slug, mirror_id=None):
    """Build GitHub OAuth authorization URL."""
    from constance import config

    client_id = config.GITHUB_OAUTH_CLIENT_ID
    if not client_id:
        return None

    callback = request.build_absolute_uri("/oauth/callback/github/")
    nonce = secrets.token_urlsafe(32)
    state = f"{slug}:{mirror_id or 'new'}:{nonce}"
    request.session["oauth_state_nonce"] = nonce

    return f"{GITHUB_AUTHORIZE_URL}?client_id={client_id}&redirect_uri={callback}&scope=repo&state={state}"


def github_exchange_token(request, slug):
    """Exchange GitHub OAuth code for access token. Returns {token, username, error}."""
    from constance import config

    code = request.GET.get("code", "")
    if not code:
        return {"token": "", "username": "", "error": "No code received"}

    client_id = config.GITHUB_OAUTH_CLIENT_ID
    client_secret = config.GITHUB_OAUTH_CLIENT_SECRET

    try:
        resp = requests.post(
            GITHUB_TOKEN_URL,
            data={"client_id": client_id, "client_secret": client_secret, "code": code},
            headers={"Accept": "application/json"},
            timeout=15,
        )
        data = resp.json()
        token = data.get("access_token", "")
        if not token:
            return {"token": "", "username": "", "error": data.get("error_description", "Token exchange failed")}

        # Get username
        user_resp = requests.get(GITHUB_USER_URL, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        username = user_resp.json().get("login", "") if user_resp.ok else ""

        return {"token": token, "username": username, "error": ""}
    except Exception as e:
        logger.exception("GitHub OAuth error")
        return {"token": "", "username": "", "error": str(e)}


def gitlab_authorize_url(request, slug, mirror_id=None):
    """Build GitLab OAuth authorization URL."""
    from constance import config

    client_id = config.GITLAB_OAUTH_CLIENT_ID
    if not client_id:
        return None

    callback = request.build_absolute_uri("/oauth/callback/gitlab/")
    nonce = secrets.token_urlsafe(32)
    state = f"{slug}:{mirror_id or 'new'}:{nonce}"
    request.session["oauth_state_nonce"] = nonce

    return f"{GITLAB_AUTHORIZE_URL}?client_id={client_id}&redirect_uri={callback}&response_type=code&scope=api&state={state}"


def gitlab_exchange_token(request, slug):
    """Exchange GitLab OAuth code for access token."""
    from constance import config

    code = request.GET.get("code", "")
    if not code:
        return {"token": "", "error": "No code received"}

    client_id = config.GITLAB_OAUTH_CLIENT_ID
    client_secret = config.GITLAB_OAUTH_CLIENT_SECRET
    callback = request.build_absolute_uri("/oauth/callback/gitlab/")

    try:
        resp = requests.post(
            GITLAB_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": callback,
            },
            timeout=15,
        )
        data = resp.json()
        token = data.get("access_token", "")
        if not token:
            return {"token": "", "error": data.get("error_description", "Token exchange failed")}
        return {"token": token, "error": ""}
    except Exception as e:
        logger.exception("GitLab OAuth error")
        return {"token": "", "error": str(e)}
