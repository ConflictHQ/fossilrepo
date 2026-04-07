"""Shared pagination helpers used across all list views."""

import math

PER_PAGE_OPTIONS = [25, 50, 100]


def get_per_page(request, default=25):
    """Get per_page from request, constrained to PER_PAGE_OPTIONS."""
    try:
        per_page = int(request.GET.get("per_page", default))
    except (ValueError, TypeError):
        per_page = default
    return per_page if per_page in PER_PAGE_OPTIONS else default


def manual_paginate(items, request, per_page=None):
    """Paginate a plain list and return (sliced_items, pagination_dict).

    The pagination dict has keys compatible with the _pagination_manual.html partial:
    has_previous, has_next, previous_page_number, next_page_number, number, num_pages, count.
    """
    if per_page is None:
        per_page = get_per_page(request)
    total = len(items)
    num_pages = max(1, math.ceil(total / per_page))
    try:
        page = int(request.GET.get("page", 1))
    except (ValueError, TypeError):
        page = 1
    page = max(1, min(page, num_pages))
    offset = (page - 1) * per_page
    sliced = items[offset : offset + per_page]
    pagination = {
        "has_previous": page > 1,
        "has_next": offset + per_page < total,
        "previous_page_number": page - 1,
        "next_page_number": page + 1,
        "number": page,
        "num_pages": num_pages,
        "count": total,
    }
    return sliced, pagination
