"""Turn small HTML fragments from source APIs into plain text."""

from __future__ import annotations

import re
from html.parser import HTMLParser


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._parts)).strip()


def html_to_text(value: str) -> str:
    parser = _HTMLText()
    parser.feed(value or "")
    parser.close()
    return parser.text()
