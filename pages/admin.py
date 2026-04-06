from django.contrib import admin

from core.admin import BaseCoreAdmin

from .models import Page


@admin.register(Page)
class PageAdmin(BaseCoreAdmin):
    list_display = ("name", "slug", "is_published", "created_at")
    list_filter = ("is_published",)
    search_fields = ("name", "slug", "content")
