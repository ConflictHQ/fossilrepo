from django.contrib import admin

from .models import PersonalAccessToken, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "handle", "location")
    search_fields = ("user__username", "handle", "location")
    raw_id_fields = ("user",)
    readonly_fields = ("user",)


@admin.register(PersonalAccessToken)
class PersonalAccessTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "token_prefix", "scopes", "created_at", "expires_at", "last_used_at", "revoked_at")
    list_filter = ("scopes",)
    search_fields = ("name", "user__username", "token_prefix")
    raw_id_fields = ("user",)
    readonly_fields = ("token_hash", "token_prefix", "created_at", "last_used_at")
