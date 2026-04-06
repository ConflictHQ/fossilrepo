from django.contrib import admin

from core.admin import BaseCoreAdmin

from .models import FossilRepository, FossilSnapshot


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
