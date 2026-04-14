from django.urls import path

from . import api_views, views

app_name = "fossil"

urlpatterns = [
    # JSON API
    path("api/", api_views.api_docs, name="api_docs"),
    path("api/project", api_views.api_project, name="api_project"),
    path("api/timeline", api_views.api_timeline, name="api_timeline"),
    path("api/tickets", api_views.api_tickets, name="api_tickets"),
    # Unclaimed must be before <str:ticket_uuid> to avoid matching "unclaimed" as a UUID
    path("api/tickets/unclaimed", api_views.api_tickets_unclaimed, name="api_tickets_unclaimed"),
    path("api/tickets/<str:ticket_uuid>", api_views.api_ticket_detail, name="api_ticket_detail"),
    path("api/wiki", api_views.api_wiki_list, name="api_wiki_list"),
    path("api/wiki/<path:page_name>", api_views.api_wiki_page, name="api_wiki_page"),
    path("api/branches", api_views.api_branches, name="api_branches"),
    path("api/tags", api_views.api_tags, name="api_tags"),
    path("api/releases", api_views.api_releases, name="api_releases"),
    path("api/search", api_views.api_search, name="api_search"),
    # Batch API
    path("api/batch", api_views.api_batch, name="api_batch"),
    # Agent Workspaces
    path("api/workspaces", api_views.api_workspace_list, name="api_workspace_list"),
    path("api/workspaces/create", api_views.api_workspace_create, name="api_workspace_create"),
    path("api/workspaces/<str:workspace_name>", api_views.api_workspace_detail, name="api_workspace_detail"),
    path("api/workspaces/<str:workspace_name>/commit", api_views.api_workspace_commit, name="api_workspace_commit"),
    path("api/workspaces/<str:workspace_name>/merge", api_views.api_workspace_merge, name="api_workspace_merge"),
    path("api/workspaces/<str:workspace_name>/abandon", api_views.api_workspace_abandon, name="api_workspace_abandon"),
    # Task Claiming
    path("api/tickets/<str:ticket_uuid>/claim", api_views.api_ticket_claim, name="api_ticket_claim"),
    path("api/tickets/<str:ticket_uuid>/release", api_views.api_ticket_release, name="api_ticket_release"),
    path("api/tickets/<str:ticket_uuid>/submit", api_views.api_ticket_submit, name="api_ticket_submit"),
    # Server-Sent Events
    path("api/events", api_views.api_events, name="api_events"),
    # Code Reviews
    path("api/reviews", api_views.api_review_list, name="api_review_list"),
    path("api/reviews/create", api_views.api_review_create, name="api_review_create"),
    path("api/reviews/<int:review_id>", api_views.api_review_detail, name="api_review_detail"),
    path("api/reviews/<int:review_id>/comment", api_views.api_review_comment, name="api_review_comment"),
    path("api/reviews/<int:review_id>/approve", api_views.api_review_approve, name="api_review_approve"),
    path("api/reviews/<int:review_id>/request-changes", api_views.api_review_request_changes, name="api_review_request_changes"),
    path("api/reviews/<int:review_id>/merge", api_views.api_review_merge, name="api_review_merge"),
    #
    path("code/", views.code_browser, name="code"),
    path("code/tree/<path:dirpath>/", views.code_browser, name="code_dir"),
    path("code/file/<path:filepath>", views.code_file, name="code_file"),
    path("timeline/", views.timeline, name="timeline"),
    path("checkin/<str:checkin_uuid>/", views.checkin_detail, name="checkin_detail"),
    path("tickets/", views.ticket_list, name="tickets"),
    path("tickets/create/", views.ticket_create, name="ticket_create"),
    path("tickets/export/", views.tickets_csv, name="tickets_csv"),
    # Custom Ticket Fields (must be before tickets/<str:ticket_uuid>/ to avoid str match)
    path("tickets/fields/", views.ticket_fields_list, name="ticket_fields"),
    path("tickets/fields/create/", views.ticket_fields_create, name="ticket_field_create"),
    path("tickets/fields/<int:pk>/edit/", views.ticket_fields_edit, name="ticket_field_edit"),
    path("tickets/fields/<int:pk>/delete/", views.ticket_fields_delete, name="ticket_field_delete"),
    # Custom Ticket Reports (must be before tickets/<str:ticket_uuid>/ to avoid str match)
    path("tickets/reports/", views.ticket_reports_list, name="ticket_reports"),
    path("tickets/reports/create/", views.ticket_report_create, name="ticket_report_create"),
    path("tickets/reports/<int:pk>/", views.ticket_report_run, name="ticket_report_run"),
    path("tickets/reports/<int:pk>/edit/", views.ticket_report_edit, name="ticket_report_edit"),
    path("tickets/<str:ticket_uuid>/", views.ticket_detail, name="ticket_detail"),
    path("tickets/<str:ticket_uuid>/edit/", views.ticket_edit, name="ticket_edit"),
    path("tickets/<str:ticket_uuid>/comment/", views.ticket_comment, name="ticket_comment"),
    path("wiki/", views.wiki_list, name="wiki"),
    path("wiki/create/", views.wiki_create, name="wiki_create"),
    path("wiki/page/<path:page_name>", views.wiki_page, name="wiki_page"),
    path("wiki/edit/<path:page_name>", views.wiki_edit, name="wiki_edit"),
    path("forum/", views.forum_list, name="forum"),
    path("forum/create/", views.forum_create, name="forum_create"),
    path("forum/<str:thread_uuid>/", views.forum_thread, name="forum_thread"),
    path("forum/<int:post_id>/reply/", views.forum_reply, name="forum_reply"),
    # Webhooks
    path("webhooks/", views.webhook_list, name="webhooks"),
    path("webhooks/create/", views.webhook_create, name="webhook_create"),
    path("webhooks/<int:webhook_id>/edit/", views.webhook_edit, name="webhook_edit"),
    path("webhooks/<int:webhook_id>/delete/", views.webhook_delete, name="webhook_delete"),
    path("webhooks/<int:webhook_id>/deliveries/", views.webhook_deliveries, name="webhook_deliveries"),
    path("user/<str:username>/", views.user_activity, name="user_activity"),
    path("branches/", views.branch_list, name="branches"),
    path("tags/", views.tag_list, name="tags"),
    path("technotes/", views.technote_list, name="technotes"),
    path("technotes/create/", views.technote_create, name="technote_create"),
    path("technotes/<str:technote_id>/", views.technote_detail, name="technote_detail"),
    path("technotes/<str:technote_id>/edit/", views.technote_edit, name="technote_edit"),
    # Unversioned content
    path("files/", views.unversioned_list, name="unversioned"),
    path("files/upload/", views.unversioned_upload, name="unversioned_upload"),
    path("files/download/<path:filename>", views.unversioned_download, name="unversioned_download"),
    path("search/", views.search, name="search"),
    path("stats/", views.repo_stats, name="stats"),
    path("compare/", views.compare_checkins, name="compare"),
    path("settings/", views.repo_settings, name="repo_settings"),
    path("sync/", views.sync_pull, name="sync"),
    path("sync/git/", views.git_mirror_config, name="git_mirror"),
    path("sync/git/<int:mirror_id>/edit/", views.git_mirror_config, name="git_mirror_edit"),
    path("sync/git/<int:mirror_id>/delete/", views.git_mirror_delete, name="git_mirror_delete"),
    path("sync/git/<int:mirror_id>/run/", views.git_mirror_run, name="git_mirror_run"),
    path("sync/git/connect/github/", views.oauth_github_start, name="oauth_github"),
    path("sync/git/connect/gitlab/", views.oauth_gitlab_start, name="oauth_gitlab"),
    # Per-project OAuth callbacks removed — global /oauth/callback/ handlers
    # enforce nonce/state validation. Keeping these would bypass that check.
    path("code/raw/<path:filepath>", views.code_raw, name="code_raw"),
    path("code/blame/<path:filepath>", views.code_blame, name="code_blame"),
    path("code/history/<path:filepath>", views.file_history, name="file_history"),
    path("watch/", views.toggle_watch, name="toggle_watch"),
    path("timeline/rss/", views.timeline_rss, name="timeline_rss"),
    path("docs/", views.fossil_docs, name="docs"),
    path("docs/<path:doc_path>", views.fossil_doc_page, name="doc_page"),
    path("xfer", views.fossil_xfer, name="xfer"),
    # Releases
    path("releases/", views.release_list, name="releases"),
    path("releases/create/", views.release_create, name="release_create"),
    path("releases/<str:tag_name>/", views.release_detail, name="release_detail"),
    path("releases/<str:tag_name>/edit/", views.release_edit, name="release_edit"),
    path("releases/<str:tag_name>/delete/", views.release_delete, name="release_delete"),
    path("releases/<str:tag_name>/upload/", views.release_asset_upload, name="release_asset_upload"),
    path("releases/<str:tag_name>/assets/<int:asset_id>/", views.release_asset_download, name="release_asset_download"),
    path("releases/<str:tag_name>/source.<str:fmt>", views.release_source_archive, name="release_source_archive"),
    # CI Status API
    path("api/status", views.status_check_api, name="status_check_api"),
    path("api/status/<str:checkin_uuid>/badge.svg", views.status_badge, name="status_badge"),
    # API Tokens
    path("tokens/", views.api_token_list, name="api_tokens"),
    path("tokens/create/", views.api_token_create, name="api_token_create"),
    path("tokens/<int:token_id>/delete/", views.api_token_delete, name="api_token_delete"),
    # Branch Protection
    path("branches/protect/", views.branch_protection_list, name="branch_protections"),
    path("branches/protect/create/", views.branch_protection_create, name="branch_protection_create"),
    path("branches/protect/<int:pk>/edit/", views.branch_protection_edit, name="branch_protection_edit"),
    path("branches/protect/<int:pk>/delete/", views.branch_protection_delete, name="branch_protection_delete"),
    # Artifact Shunning
    path("admin/shun/", views.shun_list_view, name="shun_list"),
    path("admin/shun/add/", views.shun_artifact, name="shun_artifact"),
    # SQLite Explorer
    path("explorer/", views.repo_explorer, name="explorer"),
    path("explorer/table/<str:table_name>/", views.repo_explorer_table, name="explorer_table"),
    path("explorer/query/", views.repo_explorer_query, name="explorer_query"),
    # Bundle export/import
    path("bundle/export/", views.bundle_export, name="bundle_export"),
    path("bundle/import/", views.bundle_import, name="bundle_import"),
]
