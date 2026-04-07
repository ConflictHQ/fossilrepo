import re

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .forms import LoginForm

# Allowed SSH key type prefixes
_SSH_KEY_PREFIXES = ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-", "ssh-dss")


def _sanitize_ssh_key(public_key: str) -> tuple[str | None, str]:
    """Validate and sanitize an SSH public key.

    Returns (sanitized_key, error_message).  On success error_message is "".
    Rejects keys containing newlines, carriage returns, or null bytes (which
    would allow injection of extra authorized_keys entries).  Validates format:
    known type prefix, 2-3 space-separated parts.
    """
    # Strip dangerous injection characters -- newlines let an attacker add
    # a second authorized_keys line outside the forced-command wrapper
    if "\n" in public_key or "\r" in public_key or "\x00" in public_key:
        return None, "SSH key must be a single line. Newlines, carriage returns, and null bytes are not allowed."

    key = public_key.strip()
    if not key:
        return None, "SSH key cannot be empty."

    # SSH keys are: <type> <base64-data> [optional comment]
    parts = key.split()
    if len(parts) < 2 or len(parts) > 3:
        return None, "Invalid SSH key format. Expected: <key-type> <key-data> [comment]"

    key_type = parts[0]
    if not any(key_type.startswith(prefix) for prefix in _SSH_KEY_PREFIXES):
        return None, f"Unsupported key type '{key_type}'. Allowed: ssh-ed25519, ssh-rsa, ecdsa-sha2-*, ssh-dss."

    # Validate base64 data is plausible (only base64 chars + padding)
    if not re.match(r"^[A-Za-z0-9+/=]+$", parts[1]):
        return None, "Invalid SSH key data encoding."

    return key, ""


@ratelimit(key="ip", rate="10/m", block=True)
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            next_url = request.GET.get("next", "")
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect("dashboard")
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("accounts:login")


# ---------------------------------------------------------------------------
# SSH key management
# ---------------------------------------------------------------------------


def _parse_key_type(public_key):
    """Extract key type from public key string."""
    parts = public_key.strip().split()
    if parts:
        key_prefix = parts[0]
        type_map = {
            "ssh-ed25519": "ed25519",
            "ssh-rsa": "rsa",
            "ecdsa-sha2-nistp256": "ecdsa",
            "ecdsa-sha2-nistp384": "ecdsa",
            "ecdsa-sha2-nistp521": "ecdsa",
            "ssh-dss": "dsa",
        }
        return type_map.get(key_prefix, key_prefix)
    return ""


def _compute_fingerprint(public_key):
    """Compute SSH key fingerprint (SHA256)."""
    import base64
    import hashlib

    parts = public_key.strip().split()
    if len(parts) >= 2:
        try:
            key_data = base64.b64decode(parts[1])
            digest = hashlib.sha256(key_data).digest()
            return "SHA256:" + base64.b64encode(digest).rstrip(b"=").decode()
        except Exception:
            pass
    return ""


def _regenerate_authorized_keys():
    """Regenerate the authorized_keys file from all active user SSH keys."""
    from pathlib import Path

    from constance import config

    from fossil.user_keys import UserSSHKey

    ssh_dir = Path(config.FOSSIL_DATA_DIR).parent / "ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    authorized_keys_path = ssh_dir / "authorized_keys"

    keys = UserSSHKey.objects.filter(deleted_at__isnull=True).select_related("user")

    lines = []
    for key in keys:
        # Defense in depth: strip newlines/CR/null from stored keys so a
        # compromised DB value cannot inject extra authorized_keys entries.
        clean_key = key.public_key.strip().replace("\n", "").replace("\r", "").replace("\x00", "")
        if not clean_key:
            continue
        # Each key gets a forced command that identifies the user
        forced_cmd = (
            f'command="/usr/local/bin/fossil-shell {key.user.username}",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty'
        )
        lines.append(f"{forced_cmd} {clean_key}")

    authorized_keys_path.write_text("\n".join(lines) + "\n" if lines else "")
    authorized_keys_path.chmod(0o600)


@login_required
def ssh_keys(request):
    """List and add SSH keys."""
    from fossil.user_keys import UserSSHKey

    keys = UserSSHKey.objects.filter(user=request.user)

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        public_key = request.POST.get("public_key", "").strip()

        if title and public_key:
            sanitized_key, error = _sanitize_ssh_key(public_key)
            if error:
                messages.error(request, error)
                return render(request, "accounts/ssh_keys.html", {"keys": keys})

            key_type = _parse_key_type(sanitized_key)
            fingerprint = _compute_fingerprint(sanitized_key)

            UserSSHKey.objects.create(
                user=request.user,
                title=title,
                public_key=sanitized_key,
                key_type=key_type,
                fingerprint=fingerprint,
                created_by=request.user,
            )

            _regenerate_authorized_keys()

            messages.success(request, f'SSH key "{title}" added.')
            return redirect("accounts:ssh_keys")

    return render(request, "accounts/ssh_keys.html", {"keys": keys})


@login_required
@require_POST
def ssh_key_delete(request, pk):
    """Delete an SSH key."""
    from fossil.user_keys import UserSSHKey

    key = get_object_or_404(UserSSHKey, pk=pk, user=request.user)
    key.soft_delete(user=request.user)
    _regenerate_authorized_keys()

    messages.success(request, f'SSH key "{key.title}" removed.')

    if request.headers.get("HX-Request"):
        return HttpResponse(status=200, headers={"HX-Redirect": "/auth/ssh-keys/"})

    return redirect("accounts:ssh_keys")
