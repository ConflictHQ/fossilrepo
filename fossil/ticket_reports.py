"""Custom SQL-based ticket reports.

Fossil supports running SQL queries against its built-in ticket table.
This model lets admins define reusable reports that project members can
execute.  Queries run in read-only mode against the Fossil SQLite file.
"""

import re

from django.db import models

from core.models import ActiveManager, Tracking

# Statements that are never allowed in a report query.
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|REINDEX|VACUUM|PRAGMA)\b",
    re.IGNORECASE,
)


class TicketReport(Tracking):
    """Custom SQL-based ticket report."""

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="ticket_reports")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    sql_query = models.TextField(help_text="SQL query against the Fossil ticket table. Use {status}, {type} as placeholders.")
    is_public = models.BooleanField(default=True, help_text="Visible to all project members")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    @staticmethod
    def validate_sql(sql: str) -> str | None:
        """Return an error message if *sql* is unsafe, or None if it passes.

        Rules:
        - Must start with SELECT (after stripping whitespace/comments).
        - Must not contain any write/DDL keywords.
        - Must not contain multiple statements (semicolons aside from trailing).
        """
        stripped = sql.strip().rstrip(";").strip()
        if not stripped:
            return "Query cannot be empty."

        if not re.match(r"(?i)^\s*SELECT\b", stripped):
            return "Query must start with SELECT."

        if _FORBIDDEN_KEYWORDS.search(stripped):
            return "Query contains forbidden keywords (INSERT, UPDATE, DELETE, DROP, ALTER, etc.)."

        # Reject multiple statements: strip string literals then check for semicolons.
        no_strings = re.sub(r"'[^']*'", "", stripped)
        if ";" in no_strings:
            return "Query must not contain multiple statements."

        return None
