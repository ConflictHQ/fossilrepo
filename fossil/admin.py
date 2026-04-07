from django.contrib import admin

from core.admin import BaseCoreAdmin

from .models import FossilRepository, FossilSnapshot
from .sync_models import GitMirror, SSHKey, SyncLog
from .user_keys import UserSSHKey


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
