"""HTML sanitization for user-generated content.

Strips dangerous tags (<script>, <style>, <iframe>, etc.), event handlers (on*),
and dangerous URL protocols (javascript:, data:, vbscript:) while preserving
safe formatting tags used by Fossil wiki, Markdown, and Pikchr diagrams.
"""

import re

# Tags that are safe to render -- covers Markdown/wiki formatting and Pikchr SVG
ALLOWED_TAGS = {
    "a",
    "abbr",
    "acronym",
    "b",
    "blockquote",
    "br",
    "code",
    "dd",
    "del",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "ins",
    "kbd",
    "li",
    "mark",
    "ol",
    "p",
    "pre",
    "q",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "tt",
    "u",
    "ul",
    "var",
    # SVG elements for Pikchr diagrams
    "svg",
    "path",
    "circle",
    "rect",
    "line",
    "polyline",
    "polygon",
    "g",
    "text",
    "defs",
    "use",
    "symbol",
}

# Tags whose entire content (not just the tag) must be removed
_DANGEROUS_CONTENT_TAGS = re.compile(
    r"<\s*(script|style|iframe|object|embed|form|base|meta|link)\b[^>]*>.*?</\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

# Self-closing / unclosed dangerous tags
_DANGEROUS_SELF_CLOSING = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|form|base|meta|link)\b[^>]*/?\s*>",
    re.IGNORECASE,
)

# Event handler attributes (onclick, onload, onerror, etc.)
_EVENT_HANDLERS = re.compile(
    r"""\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""",
    re.IGNORECASE,
)

# Dangerous protocols in href/src values
_DANGEROUS_PROTOCOL = re.compile(r"^\s*(?:javascript|vbscript|data):", re.IGNORECASE)

# href="..." and src="..." attribute pattern
_URL_ATTR = re.compile(r"""(href|src)\s*=\s*(["']?)([^"'>\s]+)\2""", re.IGNORECASE)


def _clean_url_attr(match: re.Match) -> str:
    """Replace dangerous protocol URLs with a safe '#' anchor."""
    attr_name = match.group(1)
    quote = match.group(2) or ""
    url = match.group(3)
    if _DANGEROUS_PROTOCOL.match(url):
        return f"{attr_name}={quote}#{quote}"
    return match.group(0)


def sanitize_html(html: str) -> str:
    """Remove dangerous HTML tags and attributes while preserving safe formatting.

    Strips <script>, <style>, <iframe>, <object>, <embed>, <form>, <base>,
    <meta>, <link> tags and their content.  Removes event handler attributes
    (on*) and replaces dangerous URL protocols (javascript:, data:, vbscript:)
    in href/src with '#'.
    """
    if not html:
        return html

    # 1. Remove dangerous tags WITH their content (e.g. <script>...</script>)
    html = _DANGEROUS_CONTENT_TAGS.sub("", html)

    # 2. Remove any remaining self-closing or orphaned dangerous tags
    html = _DANGEROUS_SELF_CLOSING.sub("", html)

    # 3. Remove event handler attributes (onclick, onload, onerror, etc.)
    html = _EVENT_HANDLERS.sub("", html)

    # 4. Neutralize dangerous URL protocols in href and src attributes
    html = _URL_ATTR.sub(_clean_url_attr, html)

    return html
