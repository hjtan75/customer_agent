"""Render model output for a plain terminal.

The model is told to write plain text (see prompts.py), but LLMs reliably drift
back into markdown. This is the deterministic safety net: a prompt rule is a
request, a render pass is a guarantee. Strips the markup that renders as literal
noise in a CLI (**bold**, ### headings, `code`, [text](url)).
"""

from __future__ import annotations

import re

# [text](url) -> text (url). Keeps the URL visible and clickable in most terminals.
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# ### Heading -> Heading
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
# **bold** / __bold__ -> bold
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
# *italic* -> italic. Underscore italics are deliberately NOT stripped: `_` is
# common inside emails, SKUs, and URLs, and unwrapping them would mangle data.
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(?=\S)(.+?)(?<=\S)\*(?!\*)", re.DOTALL)
# `code` -> code (leave ``` fences alone; the agent has no reason to emit them)
_CODE = re.compile(r"`([^`\n]+)`")


def to_terminal(text: str) -> str:
    """Strip markdown markup that a plain CLI can't render."""
    if not text:
        return ""
    out = _LINK.sub(lambda m: f"{m.group(1).strip()} ({m.group(2).strip()})", text)
    out = _HEADING.sub("", out)
    out = _BOLD.sub(lambda m: m.group(2), out)
    out = _ITALIC.sub(lambda m: m.group(1), out)
    out = _CODE.sub(lambda m: m.group(1), out)
    # Collapse the blank-line runs markdown tends to leave behind.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
