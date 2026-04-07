from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .forms import LoginForm


@ratelimit(key="ip", rate="10/m", block=True)
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            next_url = request.GET.get("next", "dashboard")
            return redirect(next_url)
    else:
        form = LoginForm()

    return render(request, "auth1/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("auth1:login")


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
        # Each key gets a forced command that identifies the user
        forced_cmd = (
            f'command="/usr/local/bin/fossil-shell {key.user.username}",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty'
        )
        lines.append(f"{forced_cmd} {key.public_key.strip()}")

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
            key_type = _parse_key_type(public_key)
            fingerprint = _compute_fingerprint(public_key)

            UserSSHKey.objects.create(
                user=request.user,
                title=title,
                public_key=public_key,
                key_type=key_type,
                fingerprint=fingerprint,
                created_by=request.user,
            )

            _regenerate_authorized_keys()

            messages.success(request, f'SSH key "{title}" added.')
            return redirect("auth1:ssh_keys")

    return render(request, "auth1/ssh_keys.html", {"keys": keys})


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

    return redirect("auth1:ssh_keys")
