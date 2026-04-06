import logging
from enum import Enum

from django.core.exceptions import PermissionDenied

logger = logging.getLogger(__name__)


class P(Enum):
    """Permission enum. Check permissions via P.PERMISSION_NAME.check(user)."""

    # Organization
    ORGANIZATION_VIEW = "organization.view_organization"
    ORGANIZATION_ADD = "organization.add_organization"
    ORGANIZATION_CHANGE = "organization.change_organization"
    ORGANIZATION_DELETE = "organization.delete_organization"

    # Organization Members
    ORGANIZATION_MEMBER_VIEW = "organization.view_organizationmember"
    ORGANIZATION_MEMBER_ADD = "organization.add_organizationmember"
    ORGANIZATION_MEMBER_CHANGE = "organization.change_organizationmember"
    ORGANIZATION_MEMBER_DELETE = "organization.delete_organizationmember"

    # Teams
    TEAM_VIEW = "organization.view_team"
    TEAM_ADD = "organization.add_team"
    TEAM_CHANGE = "organization.change_team"
    TEAM_DELETE = "organization.delete_team"

    # Projects
    PROJECT_VIEW = "projects.view_project"
    PROJECT_ADD = "projects.add_project"
    PROJECT_CHANGE = "projects.change_project"
    PROJECT_DELETE = "projects.delete_project"

    # Fossil
    FOSSIL_VIEW = "fossil.view_fossilrepository"
    FOSSIL_ADD = "fossil.add_fossilrepository"
    FOSSIL_CHANGE = "fossil.change_fossilrepository"
    FOSSIL_DELETE = "fossil.delete_fossilrepository"

    # Pages (docs)
    PAGE_VIEW = "pages.view_page"
    PAGE_ADD = "pages.add_page"
    PAGE_CHANGE = "pages.change_page"
    PAGE_DELETE = "pages.delete_page"

    # Items (example domain)
    ITEM_VIEW = "items.view_item"
    ITEM_ADD = "items.add_item"
    ITEM_CHANGE = "items.change_item"
    ITEM_DELETE = "items.delete_item"

    def check(self, user, raise_error=True):
        """Check if user has this permission. Superusers always pass."""
        if not user or not user.is_authenticated:
            if raise_error:
                raise PermissionDenied("Authentication required.")
            return False

        if user.is_superuser:
            return True

        if user.has_perm(self.value):
            return True

        if raise_error:
            raise PermissionDenied(f"Permission denied: {self.value}")
        return False
