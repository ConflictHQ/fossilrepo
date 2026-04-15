"""seed_roles — idempotent seed for the four predefined organization roles.

Creates Admin, Manager, Developer, and Viewer roles if they don't exist.
Safe to run multiple times — skips roles that are already present.

Invoked automatically by the entrypoint at container startup, and available
via the "Initialize Roles" button in Admin > Roles.
"""

from django.core.management.base import BaseCommand

ROLES = [
    {
        "name": "Admin",
        "slug": "admin",
        "is_default": False,
        "description": "Full access to everything",
    },
    {
        "name": "Manager",
        "slug": "manager",
        "is_default": False,
        "description": "Manage projects, teams, members, and pages",
    },
    {
        "name": "Developer",
        "slug": "developer",
        "is_default": True,  # New users land here
        "description": "Contribute: view projects, create tickets and wiki pages",
    },
    {
        "name": "Viewer",
        "slug": "viewer",
        "is_default": False,
        "description": "Read-only access to all content",
    },
]


class Command(BaseCommand):
    help = "Seed predefined organization roles (Admin, Manager, Developer, Viewer). Idempotent."

    def handle(self, *args, **options):
        from organization.models import OrgRole

        for spec in ROLES:
            role, created = OrgRole.all_objects.get_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "is_default": spec["is_default"],
                },
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created role: {spec['name']}"))
            else:
                self.stdout.write(f"Role already exists: {spec['name']}")
