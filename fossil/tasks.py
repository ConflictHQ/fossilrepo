"""Celery tasks for Fossil repository management."""

import hashlib
import logging

from celery import shared_task
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


@shared_task(name="fossil.sync_metadata")
def sync_repository_metadata():
    """Update metadata for all FossilRepository records from disk."""
    from fossil.models import FossilRepository
    from fossil.reader import FossilReader

    for repo in FossilRepository.objects.all():
        if not repo.exists_on_disk:
            continue
        try:
            repo.file_size_bytes = repo.full_path.stat().st_size
            with FossilReader(repo.full_path) as reader:
                repo.checkin_count = reader.get_checkin_count()
                timeline = reader.get_timeline(limit=1)
                if timeline:
                    repo.last_checkin_at = timeline[0].timestamp
                repo.fossil_project_code = reader.get_project_code()
            repo.save(update_fields=["file_size_bytes", "checkin_count", "last_checkin_at", "fossil_project_code", "updated_at", "version"])
        except Exception:
            logger.exception("Failed to sync metadata for %s", repo.filename)


@shared_task(name="fossil.create_snapshot")
def create_snapshot(repository_id: int, note: str = ""):
    """Create a FossilSnapshot if FOSSIL_STORE_IN_DB is enabled."""
    from constance import config

    if not config.FOSSIL_STORE_IN_DB:
        return

    from fossil.models import FossilRepository, FossilSnapshot

    try:
        repo = FossilRepository.objects.get(pk=repository_id)
    except FossilRepository.DoesNotExist:
        return

    if not repo.exists_on_disk:
        return

    data = repo.full_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()

    # Skip if latest snapshot has same hash
    latest = repo.snapshots.first()
    if latest and latest.fossil_hash == sha:
        return

    snapshot = FossilSnapshot(
        repository=repo,
        file_size_bytes=len(data),
        fossil_hash=sha,
        note=note,
        created_by=repo.created_by,
    )
    snapshot.file.save(f"{repo.filename}_{sha[:8]}.fossil", ContentFile(data), save=True)
    logger.info("Created snapshot for %s (hash: %s)", repo.filename, sha[:8])


@shared_task(name="fossil.check_upstream")
def check_upstream_updates():
    """Check all repos with remote URLs for available updates."""
    from fossil.cli import FossilCLI
    from fossil.models import FossilRepository

    cli = FossilCLI()
    if not cli.is_available():
        return

    from django.utils import timezone

    for repo in FossilRepository.objects.exclude(remote_url=""):
        if not repo.exists_on_disk:
            continue
        try:
            result = cli.pull(repo.full_path)
            if result["success"] and result["artifacts_received"] > 0:
                repo.upstream_artifacts_available = result["artifacts_received"]
                repo.last_sync_at = timezone.now()
                # Update metadata after pull
                from fossil.reader import FossilReader

                with FossilReader(repo.full_path) as reader:
                    repo.checkin_count = reader.get_checkin_count()
                    timeline = reader.get_timeline(limit=1, event_type="ci")
                    if timeline:
                        repo.last_checkin_at = timeline[0].timestamp
                    repo.file_size_bytes = repo.full_path.stat().st_size
                repo.save(
                    update_fields=[
                        "upstream_artifacts_available",
                        "last_sync_at",
                        "checkin_count",
                        "last_checkin_at",
                        "file_size_bytes",
                        "updated_at",
                        "version",
                    ]
                )
                logger.info("Pulled %d artifacts for %s (new count: %d)", result["artifacts_received"], repo.filename, repo.checkin_count)
            else:
                repo.upstream_artifacts_available = 0
                repo.last_sync_at = timezone.now()
                repo.save(update_fields=["upstream_artifacts_available", "last_sync_at", "updated_at", "version"])
        except Exception:
            logger.exception("Failed to check upstream for %s", repo.filename)


@shared_task(name="fossil.git_sync")
def run_git_sync(mirror_id: int | None = None):
    """Run Git export/push for configured mirrors."""
    from pathlib import Path

    from constance import config
    from django.utils import timezone

    from fossil.cli import FossilCLI
    from fossil.sync_models import GitMirror, SyncLog

    cli = FossilCLI()
    if not cli.is_available():
        return

    mirrors = GitMirror.objects.filter(deleted_at__isnull=True).exclude(sync_mode="disabled")
    if mirror_id:
        mirrors = mirrors.filter(pk=mirror_id)

    mirror_dir = Path(config.GIT_MIRROR_DIR)

    for mirror in mirrors:
        repo = mirror.repository
        if not repo.exists_on_disk:
            continue

        log = SyncLog.objects.create(mirror=mirror, triggered_by="schedule" if not mirror_id else "manual")

        try:
            # Ensure default user
            cli.ensure_default_user(repo.full_path)

            # Git export directory for this mirror
            export_dir = mirror_dir / f"{repo.filename.replace('.fossil', '')}-git"

            # Pass clean URL and token separately -- never embed credentials in URLs
            push_url = mirror.git_remote_url
            auth_token = ""
            if mirror.auth_method == "token" and mirror.auth_credential and push_url.startswith("https://"):
                auth_token = mirror.auth_credential

            result = cli.git_export(repo.full_path, export_dir, autopush_url=push_url, auth_token=auth_token)

            # Scrub any credential from output before persisting
            message = result.get("message", "")
            if mirror.auth_credential:
                message = message.replace(mirror.auth_credential, "[REDACTED]")

            log.status = "success" if result["success"] else "failed"
            log.message = message
            log.completed_at = timezone.now()
            log.save()

            mirror.last_sync_at = timezone.now()
            mirror.last_sync_status = log.status
            mirror.last_sync_message = message[:500]
            mirror.total_syncs += 1
            mirror.save(
                update_fields=[
                    "last_sync_at",
                    "last_sync_status",
                    "last_sync_message",
                    "total_syncs",
                    "updated_at",
                    "version",
                ]
            )

            if result["success"]:
                logger.info("Git sync success for %s → %s", repo.filename, mirror.git_remote_url)
            else:
                logger.warning("Git sync failed for %s: %s", repo.filename, result["message"][:200])

        except Exception:
            logger.exception("Git sync error for %s", repo.filename)
            log.status = "failed"
            log.message = "Unexpected error"
            log.completed_at = timezone.now()
            log.save()


@shared_task(name="fossil.send_digest")
def send_digest(mode="daily"):
    """Send digest emails to users who prefer batch delivery.

    Collects unread notifications for users with the given delivery mode
    and sends a single summary email with HTML template. Marks those
    notifications as read after sending.
    """
    from django.conf import settings
    from django.core.mail import send_mail
    from django.template.loader import render_to_string

    from fossil.notifications import Notification, NotificationPreference

    prefs = NotificationPreference.objects.filter(delivery_mode=mode).select_related("user")
    for pref in prefs:
        unread = Notification.objects.filter(user=pref.user, read=False).select_related("project")
        if not unread.exists():
            continue

        count = unread.count()
        notifications_list = list(unread[:50])
        overflow_count = count - 50 if count > 50 else 0

        # Plain text fallback
        lines = [f"You have {count} new notification{'s' if count != 1 else ''}:\n"]
        for notif in notifications_list:
            lines.append(f"- [{notif.event_type}] {notif.project.name}: {notif.title}")
        if overflow_count:
            lines.append(f"\n... and {overflow_count} more.")

        # HTML version
        html_body = render_to_string("email/digest.html", {
            "digest_type": mode,
            "count": count,
            "notifications": notifications_list,
            "overflow_count": overflow_count,
            "dashboard_url": "/",
            "preferences_url": "/auth/notifications/",
        })

        try:
            send_mail(
                subject=f"Fossilrepo {mode.title()} Digest - {count} update{'s' if count != 1 else ''}",
                message="\n".join(lines),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[pref.user.email],
                html_message=html_body,
                fail_silently=True,
            )
        except Exception:
            logger.exception("Failed to send %s digest to %s", mode, pref.user.email)
            continue

        unread.update(read=True)


@shared_task(name="fossil.dispatch_webhook", bind=True, max_retries=3)
def dispatch_webhook(self, webhook_id, event_type, payload):
    """Deliver a webhook with retry and logging."""
    import hashlib
    import hmac
    import json
    import time

    import requests

    from fossil.webhooks import Webhook, WebhookDelivery

    try:
        webhook = Webhook.objects.get(id=webhook_id)
    except Webhook.DoesNotExist:
        logger.warning("Webhook %s not found, skipping delivery", webhook_id)
        return

    headers = {"Content-Type": "application/json", "X-Fossilrepo-Event": event_type}
    body = json.dumps(payload)

    if webhook.secret:
        sig = hmac.new(webhook.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-Fossilrepo-Signature"] = f"sha256={sig}"

    start = time.monotonic()
    try:
        resp = requests.post(webhook.url, data=body, headers=headers, timeout=30)
        duration = int((time.monotonic() - start) * 1000)

        WebhookDelivery.objects.create(
            webhook=webhook,
            event_type=event_type,
            payload=payload,
            response_status=resp.status_code,
            response_body=resp.text[:5000],
            success=200 <= resp.status_code < 300,
            duration_ms=duration,
            attempt=self.request.retries + 1,
        )

        if not (200 <= resp.status_code < 300):
            raise self.retry(countdown=60 * (2**self.request.retries))
    except requests.RequestException as exc:
        duration = int((time.monotonic() - start) * 1000)
        WebhookDelivery.objects.create(
            webhook=webhook,
            event_type=event_type,
            payload=payload,
            response_status=0,
            response_body=str(exc)[:5000],
            success=False,
            duration_ms=duration,
            attempt=self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


@shared_task(name="fossil.dispatch_notifications")
def dispatch_notifications():
    """Check for new Fossil events and send notifications to watchers."""
    import datetime

    from django.utils import timezone

    from fossil.models import FossilRepository
    from fossil.notifications import ProjectWatch, notify_project_event
    from fossil.reader import FossilReader

    watched_project_ids = set(ProjectWatch.objects.filter(deleted_at__isnull=True).values_list("project_id", flat=True))
    if not watched_project_ids:
        return

    cutoff = timezone.now() - datetime.timedelta(minutes=5)

    for repo in FossilRepository.objects.filter(project_id__in=watched_project_ids, deleted_at__isnull=True):
        if not repo.exists_on_disk:
            continue
        try:
            with FossilReader(repo.full_path) as reader:
                entries = reader.get_timeline(limit=10)
                for entry in entries:
                    if entry.timestamp < cutoff:
                        break
                    event_type = {"ci": "checkin", "w": "wiki", "t": "ticket", "f": "forum"}.get(entry.event_type, "other")
                    url = f"/projects/{repo.project.slug}/fossil/"
                    if entry.event_type == "ci":
                        url += f"checkin/{entry.uuid}/"
                    notify_project_event(
                        project=repo.project,
                        event_type=event_type,
                        title=f"{entry.user}: {entry.comment[:100]}" if entry.comment else f"New {event_type}",
                        body=entry.comment or "",
                        url=url,
                    )
        except Exception:
            logger.exception("Notification dispatch error for %s", repo.filename)
