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
