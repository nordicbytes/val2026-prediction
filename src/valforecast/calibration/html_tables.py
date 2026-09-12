from __future__ import annotations

import re
from html.parser import HTMLParser


class HtmlTableParser(HTMLParser):
    def __init__(self, *, require_class: str | None = "wikitable") -> None:
        super().__init__()
        self.require_class = require_class
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._in_table = False
        self._capture = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = dict(attrs)
        if tag == "table":
            classes = mapping.get("class") or ""
            if self.require_class is None or self.require_class in classes:
                self._table = []
                self._in_table = True
        elif self._in_table and tag == "tr":
            self._row = []
        elif self._in_table and tag in {"td", "th"}:
            self._cell = []
            self._capture = True
        elif tag == "br" and self._capture and self._cell is not None:
            self._cell.append(" ")
        elif tag == "a" and self._capture and self._cell is not None:
            href = mapping.get("href") or ""
            if href.startswith("http") and "wikipedia.org" not in href.lower():
                self._cell.append(f" {href} ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_table and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            if self._row is not None:
                self._row.append(text)
            self._cell = None
            self._capture = False
        elif tag == "tr" and self._in_table and self._row:
            if self._table is not None:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._in_table:
            if self._table:
                self.tables.append(self._table)
            self._table = None
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._capture and self._cell is not None:
            self._cell.append(data)


def parse_tables(html: str, *, require_class: str | None = "wikitable") -> list[list[list[str]]]:
    parser = HtmlTableParser(require_class=require_class)
    parser.feed(html)
    return parser.tables


def parse_all_tables(html: str) -> list[list[list[str]]]:
    return parse_tables(html, require_class=None)
