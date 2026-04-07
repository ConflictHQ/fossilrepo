from django.contrib import admin

from core.admin import BaseCoreAdmin

from .api_tokens import APIToken
from .branch_protection import BranchProtection
from .ci import StatusCheck
from .forum import ForumPost
from .models import FossilRepository, FossilSnapshot
from .notifications import Notification, ProjectWatch
from .releases import Release, ReleaseAsset
from .sync_models import GitMirror, SSHKey, SyncLog
from .user_keys import UserSSHKey
from .webhooks import Webhook, WebhookDelivery


class FossilSnapshotInline(admin.TabularInline):
    model = FossilSnapshot
    extra = 0
    readonly_fields = ("file", "file_size_bytes", "fossil_hash")


@admin.register(FossilRepository)
class FossilRepositoryAdmin(BaseCoreAdmin):
    list_display = ("filename", "project", "file_size_bytes", "checkin_count", "last_checkin_at")
    search_fields = ("filename", "project__name")
    raw_id_fields = ("project",)
    inlines = [FossilSnapshotInline]


@admin.register(FossilSnapshot)
class FossilSnapshotAdmin(BaseCoreAdmin):
    list_display = ("repository", "file_size_bytes", "fossil_hash", "created_at")
    raw_id_fields = ("repository",)


class SyncLogInline(admin.TabularInline):
    model = SyncLog
    extra = 0
    readonly_fields = ("started_at", "completed_at", "status", "artifacts_synced", "triggered_by")


@admin.register(GitMirror)
class GitMirrorAdmin(BaseCoreAdmin):
    list_display = ("repository", "git_remote_url", "sync_mode", "sync_direction", "last_sync_status", "last_sync_at")
    list_filter = ("sync_mode", "sync_direction", "auth_method")
    raw_id_fields = ("repository",)
    inlines = [SyncLogInline]


@admin.register(SSHKey)
class SSHKeyAdmin(BaseCoreAdmin):
    list_display = ("name", "fingerprint", "created_at")
    readonly_fields = ("public_key", "fingerprint")


@admin.register(UserSSHKey)
class UserSSHKeyAdmin(BaseCoreAdmin):
    list_display = ("title", "user", "key_type", "fingerprint", "last_used_at", "created_at")
    list_filter = ("key_type",)
    search_fields = ("title", "user__username", "fingerprint")
    readonly_fields = ("fingerprint", "key_type")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "project", "event_type", "read", "emailed", "created_at")
    list_filter = ("event_type", "read", "emailed")
    search_fields = ("title", "user__username", "project__name")
    raw_id_fields = ("user", "project")


@admin.register(ProjectWatch)
class ProjectWatchAdmin(BaseCoreAdmin):
    list_display = ("user", "project", "event_filter", "email_enabled", "created_at")
    list_filter = ("event_filter", "email_enabled")
    search_fields = ("user__username", "project__name")
    raw_id_fields = ("user", "project")


class ReleaseAssetInline(admin.TabularInline):
    model = ReleaseAsset
    extra = 0


@admin.register(Release)
class ReleaseAdmin(BaseCoreAdmin):
    list_display = ("tag_name", "name", "repository", "is_prerelease", "is_draft", "published_at")
    list_filter = ("is_prerelease", "is_draft")
    search_fields = ("tag_name", "name")
    inlines = [ReleaseAssetInline]


@admin.register(ReleaseAsset)
class ReleaseAssetAdmin(BaseCoreAdmin):
    list_display = ("name", "release", "file_size_bytes", "download_count")


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ("mirror", "status", "started_at", "completed_at", "artifacts_synced", "triggered_by")
    list_filter = ("status", "triggered_by")
    search_fields = ("mirror__repository__filename", "message")
    raw_id_fields = ("mirror",)


@admin.register(ForumPost)
class ForumPostAdmin(BaseCoreAdmin):
    list_display = ("title", "repository", "parent", "created_by", "created_at")
    search_fields = ("title", "body")
    raw_id_fields = ("repository", "parent", "thread_root")


class WebhookDeliveryInline(admin.TabularInline):
    model = WebhookDelivery
    extra = 0
    readonly_fields = ("event_type", "response_status", "success", "delivered_at", "duration_ms", "attempt")


@admin.register(Webhook)
class WebhookAdmin(BaseCoreAdmin):
    list_display = ("url", "repository", "events", "is_active", "created_at")
    list_filter = ("is_active", "events")
    search_fields = ("url", "repository__filename")
    raw_id_fields = ("repository",)
    inlines = [WebhookDeliveryInline]


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ("webhook", "event_type", "response_status", "success", "delivered_at", "duration_ms")
    list_filter = ("success", "event_type")
    raw_id_fields = ("webhook",)


@admin.register(StatusCheck)
class StatusCheckAdmin(BaseCoreAdmin):
    list_display = ("context", "state", "checkin_uuid", "repository", "created_at")
    list_filter = ("state",)
    search_fields = ("context", "checkin_uuid")
    raw_id_fields = ("repository",)


@admin.register(APIToken)
class APITokenAdmin(BaseCoreAdmin):
    list_display = ("name", "token_prefix", "repository", "permissions", "last_used_at", "expires_at", "created_at")
    search_fields = ("name", "token_prefix")
    raw_id_fields = ("repository",)
    readonly_fields = ("token_hash", "token_prefix")


@admin.register(BranchProtection)
class BranchProtectionAdmin(BaseCoreAdmin):
    list_display = ("branch_pattern", "repository", "require_status_checks", "restrict_push", "created_at")
    list_filter = ("require_status_checks", "restrict_push")
    search_fields = ("branch_pattern",)
    raw_id_fields = ("repository",)
