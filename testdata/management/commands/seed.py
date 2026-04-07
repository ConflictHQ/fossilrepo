import logging

from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand

from organization.models import Organization, OrganizationMember, OrgRole, Team
from pages.models import Page
from projects.models import Project, ProjectTeam

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Seed the database with initial data for development."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Flush non-system tables before seeding.")

    def handle(self, *args, **options):
        if options["flush"]:
            self.stdout.write("Flushing data...")
            Page.all_objects.all().delete()
            ProjectTeam.all_objects.all().delete()
            Project.all_objects.all().delete()
            Team.all_objects.all().delete()
            OrganizationMember.all_objects.all().delete()
            Organization.all_objects.all().delete()

        # Groups and permissions
        admin_group, _ = Group.objects.get_or_create(name="Administrators")
        viewer_group, _ = Group.objects.get_or_create(name="Viewers")

        # Admin group gets all permissions for org, projects, and pages
        for app_label in ["organization", "projects", "pages"]:
            perms = Permission.objects.filter(content_type__app_label=app_label)
            admin_group.permissions.add(*perms)

        # Viewer group gets view permissions for org, projects, and pages
        view_perms = Permission.objects.filter(
            content_type__app_label__in=["organization", "projects", "pages"],
            codename__startswith="view_",
        )
        viewer_group.permissions.set(view_perms)

        # Superuser
        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@fossilrepo.local", "is_staff": True, "is_superuser": True},
        )
        if created:
            admin_user.set_password("admin")
            admin_user.save()
            self.stdout.write(self.style.SUCCESS("Created superuser: admin / admin"))

        # Regular user
        viewer_user, created = User.objects.get_or_create(
            username="viewer",
            defaults={"email": "viewer@fossilrepo.local", "is_staff": False, "is_superuser": False},
        )
        if created:
            viewer_user.set_password("viewer")
            viewer_user.save()
            viewer_user.groups.add(viewer_group)
            self.stdout.write(self.style.SUCCESS("Created viewer user: viewer / viewer"))

        # Organization
        org, _ = Organization.objects.get_or_create(name="Fossilrepo HQ", defaults={"description": "Default organization"})
        OrganizationMember.objects.get_or_create(member=admin_user, organization=org)
        OrganizationMember.objects.get_or_create(member=viewer_user, organization=org)

        # Teams
        core_devs, _ = Team.objects.get_or_create(name="Core Devs", defaults={"organization": org, "description": "Core development team"})
        core_devs.members.add(admin_user)

        contributors, _ = Team.objects.get_or_create(
            name="Contributors", defaults={"organization": org, "description": "Community contributors"}
        )
        contributors.members.add(viewer_user)

        reviewers, _ = Team.objects.get_or_create(name="Reviewers", defaults={"organization": org, "description": "Code review team"})
        reviewers.members.add(admin_user, viewer_user)

        # Projects
        projects_data = [
            {"name": "Frontend App", "description": "User-facing web application", "visibility": "internal"},
            {"name": "Backend API", "description": "Core API service", "visibility": "private"},
            {"name": "Documentation", "description": "Project documentation and guides", "visibility": "public"},
            {"name": "Infrastructure", "description": "Deployment and infrastructure tooling", "visibility": "private"},
        ]
        for pdata in projects_data:
            project, _ = Project.objects.get_or_create(
                name=pdata["name"],
                defaults={**pdata, "organization": org, "created_by": admin_user},
            )

        # Team-project assignments
        frontend = Project.objects.filter(name="Frontend App").first()
        backend = Project.objects.filter(name="Backend API").first()
        docs = Project.objects.filter(name="Documentation").first()

        if frontend:
            ProjectTeam.objects.get_or_create(project=frontend, team=core_devs, defaults={"role": "admin"})
            ProjectTeam.objects.get_or_create(project=frontend, team=contributors, defaults={"role": "write"})
        if backend:
            ProjectTeam.objects.get_or_create(project=backend, team=core_devs, defaults={"role": "admin"})
            ProjectTeam.objects.get_or_create(project=backend, team=reviewers, defaults={"role": "read"})
        if docs:
            ProjectTeam.objects.get_or_create(project=docs, team=contributors, defaults={"role": "write"})
            ProjectTeam.objects.get_or_create(project=docs, team=reviewers, defaults={"role": "write"})

        # Sample docs pages
        pages_data = [
            {
                "name": "Getting Started",
                "content": "# Getting Started\n\nWelcome to Fossilrepo. This guide covers initial setup and configuration.\n\n## Prerequisites\n\n- Docker and Docker Compose\n- A domain name (for SSL)\n- S3-compatible storage (for backups)\n\n## Quick Start\n\n1. Clone the repository\n2. Copy `.env.example` to `.env`\n3. Run `fossilrepo-ctl reconfigure`\n4. Run `fossilrepo-ctl start`\n",
            },
            {
                "name": "Admin Guide",
                "content": "# Admin Guide\n\nThis guide covers day-to-day administration of your Fossilrepo instance.\n\n## Managing Users\n\nUsers can be added through the Django admin or the Settings > Members page.\n\n## Backups\n\nLitestream continuously replicates all `.fossil` files to S3. Manual backups can be created with `fossilrepo-ctl backup create`.\n\n## Monitoring\n\nCheck `/health/` for service status and `/status/` for an overview page.\n",
            },
            {
                "name": "Architecture Overview",
                "content": "# Architecture Overview\n\n## Stack\n\n| Component | Technology |\n|-----------|------------|\n| Backend | Django 5 + HTMX |\n| Database | PostgreSQL 16 |\n| SCM | Fossil |\n| Proxy | Caddy |\n| Backups | Litestream → S3 |\n| Jobs | Celery + Redis |\n\n## How It Works\n\nEach Fossil repository is a single `.fossil` SQLite file. Caddy routes subdomain requests to the Fossil server. Django provides the management UI. Litestream continuously replicates repo files to S3.\n",
            },
        ]
        for pdata in pages_data:
            Page.objects.get_or_create(
                name=pdata["name"],
                defaults={**pdata, "organization": org, "created_by": admin_user},
            )

        # --- Seed sample users per role ---
        roles = OrgRole.objects.all()
        if not roles.exists():
            from django.core.management import call_command

            call_command("seed_roles")
            roles = OrgRole.objects.all()

        role_users = {
            "admin": {"email": "admin-role@fossilrepo.local", "first_name": "Admin", "last_name": "User"},
            "manager": {"email": "manager@fossilrepo.local", "first_name": "Manager", "last_name": "User"},
            "developer": {"email": "developer@fossilrepo.local", "first_name": "Dev", "last_name": "User"},
            "viewer": {"email": "viewer-role@fossilrepo.local", "first_name": "Viewer", "last_name": "RoleUser"},
        }

        for role in roles:
            slug = role.slug
            if slug not in role_users:
                continue
            info = role_users[slug]
            username = f"role-{slug}"
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": info["email"],
                    "first_name": info["first_name"],
                    "last_name": info["last_name"],
                    "is_active": True,
                },
            )
            if created:
                user.set_password(username)
                user.save()

            membership, _ = OrganizationMember.objects.get_or_create(
                member=user,
                organization=org,
                defaults={"created_by": admin_user},
            )
            if membership.role != role:
                membership.role = role
                membership.save()
                role.apply_to_user(user)

            self.stdout.write(f"  User: {username} / {username} (role: {role.name})")

        self.stdout.write(self.style.SUCCESS("Seed complete."))
