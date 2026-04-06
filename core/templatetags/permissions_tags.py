from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def has_perm(context, perm_string):
    """Check if the current user has a specific permission. Usage: {% has_perm 'items.view_item' as can_view %}"""
    user = context.get("user") or context["request"].user
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm(perm_string)
