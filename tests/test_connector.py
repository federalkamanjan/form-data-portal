import os
import tempfile
import types

from portal.connector import (
    LinkStatus, build_raw_table, extract_questions, extract_urls, fetch_all, parse_link, parse_links, save_raw_table,
)

ID_A = "1" + "a" * 43
ID_B = "1" + "b" * 43
ID_C = "1" + "c" * 43


# ------------------------------------------------------------------ fakes

class FakeHttpError(Exception):
    def __init__(self, status, msg="boom"):
        super().__init__(msg)
        self.resp = types.SimpleNamespace(status=status)


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Responses:
    def __init__(self, svc):
        self.svc = svc

    def list(self, formId, pageToken=None):
        return _Req(lambda: self.svc._list(formId, pageToken))


class FakeService:
    """data[form_id] = {"form": {...}, "pages": [{"responses": [...], "nextPageToken": "x"}],
                        "error": exc (always), "get_errors": [exc, ...] (raised once each, in order)}"""

    def __init__(self, data):
        self.data = data

    def forms(self):
        return self

    def get(self, formId):
        return _Req(lambda: self._get(formId))

    def responses(self):
        return _Responses(self)

    def _get(self, form_id):
        entry = self.data[form_id]
        if entry.get("error"):
            raise entry["error"]
        if entry.get("get_errors"):
            raise entry["get_errors"].pop(0)
        return entry["form"]

    def _list(self, form_id, token):
        pages = self.data[form_id]["pages"]
        index = 0 if token is None else int(token)
        page = dict(pages[index])
        if index + 1 < len(pages):
            page["nextPageToken"] = str(index + 1)
        return page


def _form(title="Registration"):
    return {
        "info": {"title": title},
        "items": [
            {"title": "Full Name", "questionItem": {"question": {"questionId": "q1"}}},
            {"title": "Section break"},
            {"title": "Subjects", "questionItem": {"question": {"questionId": "q2"}}},
            {"title": "Rate us", "questionGroupItem": {"questions": [
                {"questionId": "q3", "rowQuestion": {"title": "Teaching"}},
                {"questionId": "q4", "rowQuestion": {"title": "Facilities"}},
            ]}},
            {"title": "Full Name", "questionItem": {"question": {"questionId": "q5"}}},
        ],
    }


def _resp(rid, name, subjects=("Math",), email=None):
    r = {
        "responseId": rid,
        "createTime": "2026-09-01T10:00:00Z",
        "lastSubmittedTime": "2026-09-01T10:05:00Z",
        "answers": {
            "q1": {"textAnswers": {"answers": [{"value": name}]}},
            "q2": {"textAnswers": {"answers": [{"value": s} for s in subjects]}},
            "q3": {"textAnswers": {"answers": [{"value": "5"}]}},
        },
    }
    if email:
        r["respondentEmail"] = email
    return r


# ------------------------------------------------------------------ links

def test_edit_link_parsed():
    p = parse_link(f"https://docs.google.com/forms/d/{ID_A}/edit")
    assert p.form_id == ID_A and p.problem is None


def test_user_account_prefix_and_viewform_on_edit_id_accepted():
    assert parse_link(f"https://docs.google.com/forms/u/0/d/{ID_A}/edit").form_id == ID_A
    assert parse_link(f"https://docs.google.com/forms/d/{ID_A}/viewform").form_id == ID_A


def test_published_link_gets_edit_link_message():
    p = parse_link("https://docs.google.com/forms/d/e/1FAIpQLSdXXXXXXXXXXXXXXXX/viewform")
    assert p.problem == LinkStatus.PUBLIC_LINK and "edit link" in p.message


def test_short_link_is_public_link():
    p = parse_link("https://forms.gle/AbCdEf123")
    assert p.problem == LinkStatus.PUBLIC_LINK


def test_sheet_link_explained():
    p = parse_link("https://docs.google.com/spreadsheets/d/" + ID_A + "/edit")
    assert p.problem == LinkStatus.INVALID and "Sheets" in p.message


def test_random_link_invalid():
    assert parse_link("https://example.com/hello").problem == LinkStatus.INVALID


def test_urls_extracted_from_messy_paste():
    text = f"""Grade 9: https://docs.google.com/forms/d/{ID_A}/edit,
    docs.google.com/forms/d/{ID_B}/edit; and (https://forms.gle/xyz).  random words"""
    urls = extract_urls(text)
    assert len(urls) == 3 and urls[2] == "https://forms.gle/xyz"


def test_duplicates_flagged_with_first_position():
    text = f"https://docs.google.com/forms/d/{ID_A}/edit https://docs.google.com/forms/d/{ID_B}/edit https://docs.google.com/forms/d/{ID_A}/viewform"
    parsed = parse_links(text)
    assert [p.problem for p in parsed] == [None, None, LinkStatus.DUPLICATE]
    assert "#1" in parsed[2].message


# ------------------------------------------------------------------ table building

def test_questions_in_order_with_grid_rows_and_no_headers():
    qs = extract_questions(_form())
    assert [q["question_id"] for q in qs] == ["q1", "q2", "q3", "q4", "q5"]
    assert qs[2]["title"] == "Rate us [Teaching]"


def test_duplicate_question_titles_made_unique():
    qs = extract_questions(_form())
    df = build_raw_table(qs, [_resp("r1", "Ali")])
    assert "Full Name" in df.columns and "Full Name (2)" in df.columns


def test_multi_answers_joined_and_missing_answers_blank():
    qs = extract_questions(_form())
    df = build_raw_table(qs, [_resp("r1", "Ali", subjects=("Math", "Physics"), email="a@x.com")])
    assert df.loc[0, "Subjects"] == "Math; Physics"
    assert df.loc[0, "Rate us [Facilities]"] == ""
    assert df.loc[0, "respondent_email"] == "a@x.com"
    assert df.loc[0, "submitted_at"] == "2026-09-01T10:05:00Z"


def test_values_kept_untouched():
    qs = extract_questions(_form())
    df = build_raw_table(qs, [_resp("r1", "  aLI   khan ")])
    assert df.loc[0, "Full Name"] == "  aLI   khan "


# ------------------------------------------------------------------ fetching

def _url(i):
    return f"https://docs.google.com/forms/d/{i}/edit"


def test_paging_collects_all_pages():
    svc = FakeService({ID_A: {"form": _form(), "pages": [
        {"responses": [_resp("r1", "A"), _resp("r2", "B")]},
        {"responses": [_resp("r3", "C")]},
    ]}})
    [res] = fetch_all(svc, _url(ID_A), sleep=lambda s: None)
    assert res.status == LinkStatus.READY and res.row_count == 3 and res.title == "Registration"


def test_empty_form_returns_columns_but_no_rows():
    svc = FakeService({ID_A: {"form": _form(), "pages": [{}]}})
    [res] = fetch_all(svc, _url(ID_A), sleep=lambda s: None)
    assert res.status == LinkStatus.EMPTY and res.has_table and list(res.table.columns)[:3] == ["response_id", "submitted_at", "respondent_email"]


def test_one_bad_link_does_not_stop_the_others():
    svc = FakeService({
        ID_A: {"form": _form(), "pages": [{"responses": [_resp("r1", "A")]}]},
        ID_B: {"error": FakeHttpError(403, "forbidden")},
        ID_C: {"form": _form("Second"), "pages": [{"responses": [_resp("r9", "Z")]}]},
    })
    text = "\n".join(_url(i) for i in (ID_A, ID_B, ID_C)) + "\nhttps://forms.gle/short"
    results = fetch_all(svc, text, sleep=lambda s: None)
    assert [r.status for r in results] == [LinkStatus.READY, LinkStatus.NO_ACCESS, LinkStatus.READY, LinkStatus.PUBLIC_LINK]


def test_404_is_no_access_with_plain_message():
    svc = FakeService({ID_A: {"error": FakeHttpError(404, "not found")}})
    [res] = fetch_all(svc, _url(ID_A), sleep=lambda s: None)
    assert res.status == LinkStatus.NO_ACCESS and "can't open this form" in res.message


def test_temporary_error_retries_then_succeeds():
    sleeps = []
    svc = FakeService({ID_A: {"form": _form(), "pages": [{"responses": [_resp("r1", "A")]}],
                              "get_errors": [FakeHttpError(429), FakeHttpError(503)]}})
    [res] = fetch_all(svc, _url(ID_A), sleep=sleeps.append)
    assert res.status == LinkStatus.READY and sleeps == [1.0, 2.0]


def test_persistent_temporary_error_ends_as_error_status():
    svc = FakeService({ID_A: {"error": FakeHttpError(503)}})
    [res] = fetch_all(svc, _url(ID_A), sleep=lambda s: None)
    assert res.status == LinkStatus.ERROR and "try this link again" in res.message


def test_permission_error_not_retried():
    sleeps = []
    svc = FakeService({ID_A: {"error": FakeHttpError(403)}})
    fetch_all(svc, _url(ID_A), sleep=sleeps.append)
    assert sleeps == []


def test_forms_api_not_enabled_gives_admin_message():
    svc = FakeService({ID_A: {"error": FakeHttpError(403, "accessNotConfigured: Google Forms API has not been used in project")}})
    [res] = fetch_all(svc, _url(ID_A), sleep=lambda s: None)
    assert res.status == LinkStatus.ERROR and "enable" in res.message


def test_unexpected_exception_is_contained():
    svc = FakeService({ID_A: {"error": RuntimeError("weird")}})
    [res] = fetch_all(svc, _url(ID_A), sleep=lambda s: None)
    assert res.status == LinkStatus.ERROR


def test_progress_callback_called_per_link():
    svc = FakeService({ID_A: {"form": _form(), "pages": [{"responses": [_resp("r1", "A")]}]}})
    seen = []
    fetch_all(svc, _url(ID_A) + " https://example.com/x", sleep=lambda s: None, progress=lambda i, n, r: seen.append((i, n)))
    assert seen == [(1, 2), (2, 2)]


# ------------------------------------------------------------------ raw store

def test_raw_table_saved_as_csv_per_form():
    svc = FakeService({ID_A: {"form": _form(), "pages": [{"responses": [_resp("r1", "علی خان")]}]}})
    [res] = fetch_all(svc, _url(ID_A), sleep=lambda s: None)
    with tempfile.TemporaryDirectory() as d:
        path = save_raw_table(res, d)
        assert os.path.basename(path) == f"{ID_A}.csv"
        assert "علی خان" in open(path, encoding="utf-8-sig").read()
