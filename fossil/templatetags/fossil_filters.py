from django import template

register = template.Library()


@register.filter
def display_user(value):
    """Convert email-style Fossil usernames to display names.

    lmata@weareconflict.com -> lmata
    ragelink -> ragelink
    """
    if not value:
        return ""
    if "@" in str(value):
        return str(value).split("@")[0]
    return str(value)
