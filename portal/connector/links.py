"""Find and classify the links an operator pastes.

Only form *edit* links work with the Forms API. Respondent links (forms.gle short links
and /d/e/... published links) carry a different ID that the API cannot use, so they get
a friendly "paste the edit link instead" message.
"""
from __future__ import annotations

import re

from .models import LinkStatus, ParsedLink

_URL_FINDER = re.compile(r"(?:https?://|forms\.gle/|docs\.google\.com/)\S+", re.IGNORECASE)
_FORM_ID = re.compile(r"docs\.google\.com/forms/(?:u/\d+/)?d/(?!e/)([A-Za-z0-9_-]{15,})", re.IGNORECASE)
_PUBLISHED = re.compile(r"docs\.google\.com/forms/(?:u/\d+/)?d/e/", re.IGNORECASE)
_SHORT = re.compile(r"forms\.gle/", re.IGNORECASE)
_SHEET = re.compile(r"docs\.google\.com/spreadsheets/", re.IGNORECASE)

EDIT_LINK_HELP = (
    "Open the form while signed in, and copy the address from the browser's address bar "
    "(it ends in /edit)."
)


def extract_urls(text: str) -> list[str]:
    """Pull every link out of a pasted block, tolerating commas, spaces and extra words."""
    urls = []
    for raw in _URL_FINDER.findall(text or ""):
        url = raw.rstrip(".,;:)>\"'")
        if url:
            urls.append(url)
    return urls


def parse_link(url: str) -> ParsedLink:
    """Classify one link: usable (form_id set) or a problem with a plain-language message."""
    match = _FORM_ID.search(url)
    if match:
        return ParsedLink(url=url, form_id=match.group(1))

    if _PUBLISHED.search(url) or _SHORT.search(url):
        return ParsedLink(
            url=url,
            problem=LinkStatus.PUBLIC_LINK,
            message="This is the link respondents use to fill in the form. Paste the form's edit link instead. "
            + EDIT_LINK_HELP,
        )
    if _SHEET.search(url):
        return ParsedLink(
            url=url,
            problem=LinkStatus.INVALID,
            message="This is a Google Sheets link. Paste the Google Form's edit link instead. " + EDIT_LINK_HELP,
        )
    return ParsedLink(
        url=url,
        problem=LinkStatus.INVALID,
        message="This doesn't look like a Google Form link. " + EDIT_LINK_HELP,
    )


def parse_links(text: str) -> list[ParsedLink]:
    """Parse every link in the pasted text and flag repeats of the same form."""
    parsed: list[ParsedLink] = []
    first_seen: dict[str, int] = {}
    for position, url in enumerate(extract_urls(text), start=1):
        link = parse_link(url)
        if link.form_id:
            if link.form_id in first_seen:
                link = ParsedLink(
                    url=url,
                    form_id=link.form_id,
                    problem=LinkStatus.DUPLICATE,
                    message=f"Same form as link #{first_seen[link.form_id]}, so it was skipped.",
                )
            else:
                first_seen[link.form_id] = position
        parsed.append(link)
    return parsed
