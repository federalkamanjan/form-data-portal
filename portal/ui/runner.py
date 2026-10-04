"""Fetch every link in the operator's list, keeping results in the same order as the list."""
from __future__ import annotations

import time
from typing import Callable

from portal.connector import LinkResult, ParsedLink, fetch_link, parse_link

from .sources import Source


def fetch_sources(service, sources: list[Source], *, sleep: Callable[[float], None] = time.sleep,
                  progress: Callable[[int, int, Source, LinkResult], None] | None = None) -> list[LinkResult]:
    """One result per source, in order. Each source's status, message and row count are updated for the badges."""
    results: list[LinkResult] = []
    for i, src in enumerate(sources, start=1):
        if src.form_id is None:
            parsed = parse_link(src.url)
            res = LinkResult(url=src.url, status=parsed.problem, message=parsed.message)
        else:
            res = fetch_link(service, ParsedLink(url=src.url, form_id=src.form_id), sleep=sleep)
        src.status, src.message, src.rows = res.status, res.message, res.row_count
        results.append(res)
        if progress:
            progress(i, len(sources), src, res)
    return results
