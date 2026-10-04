"""Quick connection check: fetch one form's title using only the stored secrets.

    python check_auth.py "https://docs.google.com/forms/d/<ID>/edit"

Prints the form title if everything is set up correctly. (The full link parser with
friendly messages arrives in Milestone 2; this script only needs the form ID.)
"""
from __future__ import annotations

import re
import sys

from portal.auth import AuthSetupError, AuthTokenError, get_forms_service

_ID_RE = re.compile(r"/forms/d/(?:e/)?([A-Za-z0-9_-]+)")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit('Usage: python check_auth.py "<form edit link or form ID>"')
    arg = sys.argv[1].strip()
    match = _ID_RE.search(arg)
    form_id = match.group(1) if match else arg

    try:
        service = get_forms_service()
        form = service.forms().get(formId=form_id).execute()
    except (AuthSetupError, AuthTokenError) as exc:
        sys.exit(f"Sign-in problem: {exc}")
    except Exception as exc:  # per-form problems (no access, wrong ID) are handled properly in Milestone 2
        sys.exit(f"Signed in, but could not open that form: {exc}")

    title = form.get("info", {}).get("title") or form.get("info", {}).get("documentTitle") or "(untitled)"
    questions = sum(1 for item in form.get("items", []) if "questionItem" in item or "questionGroupItem" in item)
    print(f"Connected. Form title: {title}  ({questions} questions)")


if __name__ == "__main__":
    main()
