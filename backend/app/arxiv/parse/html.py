"""Parse affiliation metadata from arXiv HTML."""

from __future__ import annotations

from bs4 import BeautifulSoup

from .normalization import clean_text


def parse_html_affiliations(payload: str | bytes) -> list[str]:
    """Extract only explicitly marked affiliations from an arXiv HTML author block."""

    soup = BeautifulSoup(payload, "html.parser")
    values: list[str] = []
    for element in soup.select(".ltx_authors .ltx_role_affiliation"):
        for label in element.select(".ltx_contact_name"):
            label.decompose()
        value = clean_text(element.get_text(" ", strip=True)).strip(" ?:;,.")
        if value and value.casefold() != "affiliation":
            values.append(value)
    return list(dict.fromkeys(values))
