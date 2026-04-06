from django.contrib import admin
from import_export.admin import ImportExportMixin


class BaseCoreAdmin(ImportExportMixin, admin.ModelAdmin):
    """Base admin class for all Fossilrepo models. Provides audit field handling and import/export."""

    def get_readonly_fields(self, request, obj=None):
        base = tuple(self.readonly_fields or ())
        return base + ("version", "created_at", "created_by", "updated_at", "updated_by", "deleted_at", "deleted_by")

    def get_raw_id_fields(self, request):
        base = tuple(self.raw_id_fields or ())
        return base + ("created_by", "updated_by", "deleted_by")

    def save_model(self, request, obj, form, change):
        if hasattr(obj, "created_by") and not obj.created_by:
            obj.created_by = request.user
        if hasattr(obj, "updated_by"):
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)
