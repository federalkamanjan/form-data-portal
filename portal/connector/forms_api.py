"""Fetch one form's structure and responses from the Google Forms API.

Raw means raw: values are kept exactly as respondents typed them. Cleaning happens later.
Every link is fetched in isolation, so one failure never stops the others.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import pandas as pd

from .links import parse_links
from .models import LinkResult, LinkStatus, ParsedLink

SYSTEM_COLUMNS = ["response_id", "submitted_at", "respondent_email"]
TRANSIENT_HTTP = {429, 500, 502, 503, 504}
MULTI_ANSWER_JOINER = "; "


# ---------------------------------------------------------------- error handling

def _http_status(exc: Exception) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None) or getattr(exc, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _is_transient(exc: Exception) -> bool:
    status = _http_status(exc)
    if status is not None:
        return status in TRANSIENT_HTTP
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def call_with_retry(request_fn: Callable[[], Any], *, retries: int = 3, base_delay: float = 1.0,
                    sleep: Callable[[float], None] = time.sleep) -> Any:
    """Run a Google call, retrying temporary failures with growing pauses."""
    for attempt in range(retries + 1):
        try:
            return request_fn()
        except Exception as exc:
            if attempt == retries or not _is_transient(exc):
                raise
            sleep(base_delay * (2 ** attempt))


def _classify_failure(exc: Exception) -> tuple[LinkStatus, str]:
    status = _http_status(exc)
    text = str(exc)
    if "accessNotConfigured" in text or "has not been used" in text or "is disabled" in text:
        return LinkStatus.ERROR, (
            "The Google Forms API is not switched on for the portal's Google Cloud project. "
            "The administrator needs to enable it."
        )
    if status in (403, 404):
        return LinkStatus.NO_ACCESS, (
            "The portal can't open this form. Check the link is right, and that the form belongs to "
            "(or is shared as editor with) the Google account the portal was set up with."
        )
    if status == 400:
        return LinkStatus.INVALID, "Google didn't recognise this form link. Please copy it again from the form's edit page."
    if status in TRANSIENT_HTTP or isinstance(exc, (ConnectionError, TimeoutError)):
        return LinkStatus.ERROR, "Google was busy or the connection dropped. Please try this link again in a minute."
    return LinkStatus.ERROR, f"Something unexpected went wrong with this link ({text[:150]})."


# ---------------------------------------------------------------- form structure

def extract_questions(form: dict) -> list[dict]:
    """Questions in form order. Grid questions become one entry per row."""
    questions: list[dict] = []
    for item in form.get("items", []):
        title = (item.get("title") or "").strip()
        if "questionItem" in item:
            q = item["questionItem"].get("question", {})
            if q.get("questionId"):
                questions.append({"question_id": q["questionId"], "title": title or "(untitled question)"})
        elif "questionGroupItem" in item:
            for q in item["questionGroupItem"].get("questions", []):
                row_title = (q.get("rowQuestion", {}).get("title") or "").strip()
                if title and row_title:
                    label = f"{title} [{row_title}]"
                else:
                    label = title or row_title or "(untitled question)"
                if q.get("questionId"):
                    questions.append({"question_id": q["questionId"], "title": label})
    return questions


def _unique_titles(questions: list[dict]) -> list[str]:
    """Column names: unique within the form and never clashing with the system columns."""
    used = set(SYSTEM_COLUMNS)
    names = []
    for q in questions:
        base, name, n = q["title"], q["title"], 1
        while name in used:
            n += 1
            name = f"{base} ({n})"
        used.add(name)
        names.append(name)
    return names


def _answer_text(answer: dict) -> str:
    text_answers = answer.get("textAnswers")
    if text_answers:
        return MULTI_ANSWER_JOINER.join(a.get("value", "") for a in text_answers.get("answers", []))
    file_answers = answer.get("fileUploadAnswers")
    if file_answers:
        return MULTI_ANSWER_JOINER.join(a.get("fileName") or a.get("fileId", "") for a in file_answers.get("answers", []))
    return ""


def build_raw_table(questions: list[dict], responses: list[dict]) -> pd.DataFrame:
    """One row per response; columns are the system columns then each question."""
    names = _unique_titles(questions)
    rows = []
    for resp in responses:
        row = {
            "response_id": resp.get("responseId", ""),
            "submitted_at": resp.get("lastSubmittedTime") or resp.get("createTime", ""),
            "respondent_email": resp.get("respondentEmail", ""),
        }
        answers = resp.get("answers", {})
        for q, name in zip(questions, names):
            row[name] = _answer_text(answers.get(q["question_id"], {}))
        rows.append(row)
    return pd.DataFrame(rows, columns=SYSTEM_COLUMNS + names, dtype=str).fillna("")


# ---------------------------------------------------------------- fetching

def _fetch_all_responses(service, form_id: str, sleep) -> list[dict]:
    responses: list[dict] = []
    token = None
    while True:
        kwargs = {"formId": form_id}
        if token:
            kwargs["pageToken"] = token
        page = call_with_retry(lambda: service.forms().responses().list(**kwargs).execute(), sleep=sleep)
        responses.extend(page.get("responses", []))
        token = page.get("nextPageToken")
        if not token:
            return responses


def fetch_link(service, link: ParsedLink, sleep: Callable[[float], None] = time.sleep) -> LinkResult:
    """Fetch one valid link. Never raises: failures come back as a status and message."""
    try:
        form = call_with_retry(lambda: service.forms().get(formId=link.form_id).execute(), sleep=sleep)
        questions = extract_questions(form)
        responses = _fetch_all_responses(service, link.form_id, sleep)
        table = build_raw_table(questions, responses)
        info = form.get("info", {})
        title = info.get("title") or info.get("documentTitle") or "(untitled form)"
    except Exception as exc:
        status, message = _classify_failure(exc)
        return LinkResult(url=link.url, form_id=link.form_id, status=status, message=message)

    if table.empty:
        return LinkResult(url=link.url, form_id=link.form_id, status=LinkStatus.EMPTY,
                          message="This form has no responses yet.", title=title,
                          table=table, questions=questions)
    return LinkResult(url=link.url, form_id=link.form_id, status=LinkStatus.READY,
                      title=title, row_count=len(table), table=table, questions=questions)


def fetch_all(service, pasted_text: str, *, sleep: Callable[[float], None] = time.sleep,
              progress: Callable[[int, int, LinkResult], None] | None = None) -> list[LinkResult]:
    """Parse everything pasted and fetch each usable form, one link at a time."""
    parsed = parse_links(pasted_text)
    results: list[LinkResult] = []
    for i, link in enumerate(parsed, start=1):
        if link.problem:
            result = LinkResult(url=link.url, form_id=link.form_id, status=link.problem, message=link.message)
        else:
            result = fetch_link(service, link, sleep=sleep)
        results.append(result)
        if progress:
            progress(i, len(parsed), result)
    return results


def check_link(service, link: ParsedLink, sleep: Callable[[float], None] = time.sleep) -> LinkResult:
    """Quick access check: opens the form (title and questions only, no responses). Never raises."""
    try:
        form = call_with_retry(lambda: service.forms().get(formId=link.form_id).execute(), sleep=sleep)
    except Exception as exc:
        status, message = _classify_failure(exc)
        return LinkResult(url=link.url, form_id=link.form_id, status=status, message=message)
    info = form.get("info", {})
    title = info.get("title") or info.get("documentTitle") or "(untitled form)"
    return LinkResult(url=link.url, form_id=link.form_id, status=LinkStatus.READY, message="The portal can open this form.",
                      title=title, questions=extract_questions(form))


def check_all(service, pasted_text: str, *, sleep: Callable[[float], None] = time.sleep,
              progress: Callable[[int, int, LinkResult], None] | None = None) -> list[LinkResult]:
    """Check access for every pasted link without downloading any responses."""
    parsed = parse_links(pasted_text)
    results: list[LinkResult] = []
    for i, link in enumerate(parsed, start=1):
        if link.problem:
            result = LinkResult(url=link.url, form_id=link.form_id, status=link.problem, message=link.message)
        else:
            result = check_link(service, link, sleep=sleep)
        results.append(result)
        if progress:
            progress(i, len(parsed), result)
    return results
