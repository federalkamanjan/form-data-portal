"""Connector: link parsing and Forms API fetch, one raw table per form."""
from .forms_api import extract_questions, build_raw_table, check_all, check_link, fetch_all, fetch_link
from .links import extract_urls, parse_link, parse_links
from .models import LinkResult, LinkStatus, ParsedLink
from .raw_store import save_raw_table

__all__ = [
    "extract_questions", "build_raw_table", "check_all", "check_link", "fetch_all", "fetch_link",
    "extract_urls", "parse_link", "parse_links",
    "LinkResult", "LinkStatus", "ParsedLink", "save_raw_table",
]
