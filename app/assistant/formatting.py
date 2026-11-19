"""Ask O2 replies are shown as plain text, so Markdown symbols would appear literally.

The system prompt asks the model for plain text; this removes what slips through:
**bold**, __bold__, # headings and `code`. "- " list lines stay (they read fine
as text). A lone * or _ is left alone, so amounts and names aren't touched.
"""

import re

_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)
_CODE = re.compile(r"`([^`\n]*)`")
_STAR_BULLET = re.compile(r"^([ \t]*)[*+][ \t]+", re.MULTILINE)


def plain_text(text: str | None) -> str | None:
    """The reply without Markdown formatting symbols."""
    if not text:
        return text
    text = _BOLD.sub(r"\2", text)
    text = text.replace("**", "")
    text = _HEADING.sub("", text)
    text = _CODE.sub(r"\1", text)
    return _STAR_BULLET.sub(r"\1- ", text)
