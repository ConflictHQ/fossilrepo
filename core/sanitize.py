"""HTML sanitization for user-generated content.

Uses Python's html.parser to properly parse HTML and enforce an allowlist
of tags and attributes. Strips everything not explicitly allowed.
"""

import html
import re
from html.parser import HTMLParser
from io import StringIO

# Tags that are safe to render — covers Markdown/wiki formatting and Pikchr SVG
ALLOWED_TAGS = frozenset(
    {
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
)

# Attributes allowed per tag (all others stripped)
ALLOWED_ATTRS = {
    "a": {"href", "title", "class", "id", "name"},
    "img": {"src", "alt", "title", "width", "height", "class"},
    "div": {"class", "id"},
    "span": {"class", "id"},
    "td": {"class", "colspan", "rowspan"},
    "th": {"class", "colspan", "rowspan"},
    "table": {"class"},
    "code": {"class"},
    "pre": {"class"},
    "ol": {"class", "start", "type"},
    "ul": {"class"},
    "li": {"class", "value"},
    "details": {"open", "class"},
    "summary": {"class"},
    "h1": {"id", "class"},
    "h2": {"id", "class"},
    "h3": {"id", "class"},
    "h4": {"id", "class"},
    "h5": {"id", "class"},
    "h6": {"id", "class"},
    # SVG attributes
    "svg": {"viewbox", "width", "height", "class", "xmlns", "fill", "stroke"},
    "path": {"d", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "class"},
    "circle": {"cx", "cy", "r", "fill", "stroke", "class"},
    "rect": {"x", "y", "width", "height", "fill", "stroke", "rx", "ry", "class"},
    "line": {"x1", "y1", "x2", "y2", "stroke", "stroke-width", "class"},
    "text": {"x", "y", "font-size", "text-anchor", "fill", "class"},
    "g": {"transform", "class"},
    "polyline": {"points", "fill", "stroke", "class"},
    "polygon": {"points", "fill", "stroke", "class"},
}

# Global attributes allowed on any tag
GLOBAL_ATTRS = frozenset()

# Protocols allowed in href/src — everything else is stripped
ALLOWED_PROTOCOLS = frozenset({"http", "https", "mailto", "ftp", "#", ""})

# Regex to detect protocol in a URL (after HTML entity decoding)
_PROTOCOL_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+\-.]*):.*", re.DOTALL)


def _is_safe_url(url: str) -> bool:
    """Check if a URL uses a safe protocol.

    Decodes HTML entities, then strips ASCII control characters (tabs, CRs, NULs,
    etc.) that browsers silently ignore but can be used to bypass protocol checks
    (e.g. ``jav&#9;ascript:`` or ``java&#x0D;script:``).
    """
    decoded = html.unescape(url)
    # Strip all ASCII control characters (0x00-0x1F, 0x7F) — browsers ignore them
    # in URL scheme parsing, so "jav\tascript:" is treated as "javascript:"
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", decoded).strip()
    m = _PROTOCOL_RE.match(cleaned)
    if m:
        return m.group(1).lower() in ALLOWED_PROTOCOLS
    return True


class _SanitizingParser(HTMLParser):
    """HTML parser that only emits allowed tags/attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = StringIO()
        self._skip_depth = 0  # Track depth inside dangerous tags to skip content

    # Void elements that are dangerous but never have content/closing tags
    _DANGEROUS_VOID = frozenset({"base", "meta", "link"})
    # Dangerous container tags — skip both the tag and all content inside
    _DANGEROUS_CONTAINER = frozenset({"script", "style", "iframe", "object", "embed", "form"})

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()

        # Dangerous void tags — just drop the tag (no content to skip)
        if tag_lower in self._DANGEROUS_VOID:
            return

        # Dangerous content tags — skip tag AND all content inside
        if tag_lower in self._DANGEROUS_CONTAINER:
            self._skip_depth += 1
            return

        if self._skip_depth > 0:
            return

        if tag_lower not in ALLOWED_TAGS:
            return  # Strip unknown tag (but keep its text content)

        # Filter attributes
        allowed = ALLOWED_ATTRS.get(tag_lower, set()) | GLOBAL_ATTRS
        safe_attrs = []
        for name, value in attrs:
            name_lower = name.lower()
            # Block event handlers
            if name_lower.startswith("on"):
                continue
            if name_lower not in allowed:
                continue
            # Sanitize URLs in href/src
            if name_lower in ("href", "src") and value and not _is_safe_url(value):
                value = "#"
            safe_attrs.append((name, value))

        # Build the tag
        attr_str = ""
        for name, value in safe_attrs:
            if value is None:
                attr_str += f" {name}"
            else:
                escaped = value.replace("&", "&amp;").replace('"', "&quot;")
                attr_str += f' {name}="{escaped}"'

        self.out.write(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in self._DANGEROUS_VOID:
            return
        if tag_lower in self._DANGEROUS_CONTAINER:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth > 0:
            return
        if tag_lower in ALLOWED_TAGS:
            self.out.write(f"</{tag}>")

    def handle_data(self, data):
        if self._skip_depth > 0:
            return  # Inside a dangerous tag — skip content
        self.out.write(data)

    def handle_entityref(self, name):
        if self._skip_depth > 0:
            return
        self.out.write(f"&{name};")

    def handle_charref(self, name):
        if self._skip_depth > 0:
            return
        self.out.write(f"&#{name};")

    def handle_comment(self, data):
        pass  # Strip all HTML comments

    def handle_startendtag(self, tag, attrs):
        # Self-closing tags like <br/>, <img/>
        self.handle_starttag(tag, attrs)


def sanitize_html(html_content: str) -> str:
    """Sanitize HTML using a proper parser with tag/attribute allowlists.

    - Only tags in ALLOWED_TAGS are kept (all others stripped, text preserved)
    - Only attributes in ALLOWED_ATTRS per tag are kept
    - Event handlers (on*) are always stripped
    - URLs in href/src are checked after HTML entity decoding — javascript:,
      data:, vbscript: (including entity-encoded variants) are neutralized
    - Content inside <script>, <style>, <iframe>, etc. is completely removed
    - HTML comments are stripped
    """
    if not html_content:
        return html_content

    parser = _SanitizingParser()
    parser.feed(html_content)
    return parser.out.getvalue()
