from django.contrib import admin

from core.admin import BaseCoreAdmin

from .models import Organization, OrganizationMember


class OrganizationMemberInline(admin.TabularInline):
    model = OrganizationMember
    extra = 0
    raw_id_fields = ("member",)


@admin.register(Organization)
class OrganizationAdmin(BaseCoreAdmin):
    list_display = ("name", "slug", "website", "created_at")
    search_fields = ("name", "slug")
    inlines = [OrganizationMemberInline]


@admin.register(OrganizationMember)
class OrganizationMemberAdmin(BaseCoreAdmin):
    list_display = ("member", "organization", "is_active", "created_at")
    list_filter = ("is_active",)
    raw_id_fields = ("member", "organization")
