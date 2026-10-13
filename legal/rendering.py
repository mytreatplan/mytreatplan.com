"""Markdown bodies of legal documents to safe HTML.

Python-Markdown renders the body with raw HTML switched off (any HTML typed in the body
shows as text), then nh3 keeps only headings, paragraphs, lists, emphasis, links and
tables: no scripts, styles, attributes other than a link's href and title, and only
http, https and mailto links.
"""
import markdown
import nh3
from django.utils.safestring import mark_safe

ALLOWED_TAGS = {
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'hr',
    'ul', 'ol', 'li', 'strong', 'em', 'b', 'i', 'a',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
}
ALLOWED_ATTRIBUTES = {'a': {'href', 'title'}}
ALLOWED_URL_SCHEMES = {'http', 'https', 'mailto'}


class _NoRawHtml(markdown.Extension):
    """Treat HTML in the Markdown source as text (escaped), not markup."""

    def extendMarkdown(self, md):
        md.preprocessors.deregister('html_block')
        md.inlinePatterns.deregister('html')


def render_markdown(text):
    """`text` (Markdown) as sanitized HTML, marked safe for templates."""
    html = markdown.markdown(text or '', extensions=['tables', _NoRawHtml()],
                             output_format='html')
    return mark_safe(nh3.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES,
                               url_schemes=ALLOWED_URL_SCHEMES,
                               link_rel='noopener noreferrer nofollow'))
