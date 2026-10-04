import pandas as pd

from portal.config import load_settings
from portal.connector import LinkResult, LinkStatus, check_all
from portal.pipeline import prepare_run
from portal.ui import view

S = load_settings()
A = "1" + "a" * 43
B = "1" + "b" * 43
EDIT = "https://docs.google.com/forms/d/{}/edit"


# ------------------------------------------------------------------ the list of links

def test_add_sources_finds_links_and_skips_repeats():
    srcs, nxt, notes = view.add_sources([], f"{EDIT.format(A)}\n{EDIT.format(B)}, {EDIT.format(A)}", 1)
    assert [s["id"] for s in srcs] == [1, 2] and nxt == 3
    assert notes == ["Added 2 links.", "1 link was already in the list, so skipped."]


def test_same_form_with_different_url_shape_is_a_repeat():
    srcs, _, notes = view.add_sources([], EDIT.format(A), 1)
    srcs, _, notes = view.add_sources(srcs, f"https://docs.google.com/forms/d/{A}/viewform", 5)
    assert len(srcs) == 1 and "already in the list" in notes[0]


def test_bad_links_are_added_so_the_operator_can_see_why():
    srcs, _, notes = view.add_sources([], "https://forms.gle/short https://example.com/x", 1)
    assert len(srcs) == 2 and notes == ["Added 2 links."]
    badge0, note0 = view.source_status(srcs[0], {}, {})
    assert badge0.startswith("⚠️") and "edit link" in note0
    assert view.source_status(srcs[1], {}, {})[0].startswith("❌")


def test_nothing_found_gives_a_helpful_note():
    srcs, nxt, notes = view.add_sources([], "hello there", 1)
    assert srcs == [] and nxt == 1 and "edit link" in notes[0]


def test_remove_and_text():
    srcs, _, _ = view.add_sources([], f"{EDIT.format(A)} {EDIT.format(B)}", 1)
    assert view.sources_text(view.remove_source(srcs, 1)) == EDIT.format(B)


def test_status_priority_fetch_then_check_then_shape():
    src = {"id": 1, "url": EDIT.format(A), "label": ""}
    assert view.source_status(src, {}, {}) == ("🔗 Link looks fine", "Not checked yet")
    checked = LinkResult(url=src["url"], status=LinkStatus.READY, title="Grade 9 Quiz", form_id=A)
    assert view.source_status(src, {src["url"]: checked}, {}) == ("✅ Ready", "Title: Grade 9 Quiz")
    denied = LinkResult(url=src["url"], status=LinkStatus.NO_ACCESS, message="can't open", form_id=A)
    assert view.source_status(src, {src["url"]: denied}, {})[0] == "🔒 No access"
    fetched = LinkResult(url=src["url"], status=LinkStatus.READY, row_count=120, form_id=A)
    assert view.source_status(src, {src["url"]: denied}, {src["url"]: fetched}) == ("✅ Ready · 120 responses", "")


def test_label_hint_uses_form_title_once_checked():
    src = {"id": 1, "url": EDIT.format(A), "label": ""}
    assert "optional" in view.label_hint(src, {})
    checked = LinkResult(url=src["url"], status=LinkStatus.READY, title="Grade 9 Quiz", form_id=A)
    assert view.label_hint(src, {src["url"]: checked}) == "Grade 9 Quiz"


def test_short_url():
    assert view.short_url(EDIT.format(A)) == "Form …" + A[-6:]
    assert view.short_url("https://example.com/" + "x" * 80).endswith("…")


# ------------------------------------------------------------------ access check

class _Req:
    def __init__(self, fn): self.fn = fn
    def execute(self): return self.fn()


class _Err(Exception):
    def __init__(self, status):
        super().__init__("denied")
        import types
        self.resp = types.SimpleNamespace(status=status)


class _Svc:
    def forms(self): return self
    def get(self, formId):
        def go():
            if formId == B:
                raise _Err(403)
            return {"info": {"title": "Grade 9 Quiz"}, "items": [{"title": "Name", "questionItem": {"question": {"questionId": "q1"}}}]}
        return _Req(go)


def test_check_all_gives_titles_and_access_without_downloading_responses():
    res = check_all(_Svc(), f"{EDIT.format(A)} {EDIT.format(B)} https://forms.gle/x", sleep=lambda s: None)
    assert [r.status for r in res] == [LinkStatus.READY, LinkStatus.NO_ACCESS, LinkStatus.PUBLIC_LINK]
    assert res[0].title == "Grade 9 Quiz" and len(res[0].questions) == 1 and res[0].table is None


# ------------------------------------------------------------------ the review

def _raw(rows):
    return pd.DataFrame(rows, columns=["response_id", "submitted_at", "respondent_email", "Full Name", "WhatsApp Contact", "City", "School Name"], dtype=str)


def _result(fid, title, rows, url=None):
    return LinkResult(url=url or EDIT.format(fid), status=LinkStatus.READY if rows else LinkStatus.EMPTY, form_id=fid,
                      title=title, row_count=len(rows), table=_raw(rows))


ROWS = [
    ("r1", "2026-01-01T10:00:00Z", "ali@gmail.com", "aLI raza", "0314 1837972", "islamabad", "city school"),
    ("r2", "2026-01-02T10:00:00Z", "sara@gmial.com", "Sara Khan", "03331234567", "Lahore", "city school"),   # flagged domain
    ("r3", "2026-01-03T10:00:00Z", "junk@gmail.com", "dc", "cd", "cd", "cd"),                               # rejected
]


def _prepared(extra=()):
    sources = [{"id": 1, "url": EDIT.format(A), "label": "Grade 9 Chemistry"}]
    results = [_result(A, "Long Google Title", ROWS)] + list(extra)
    labels = view.labels_map(sources, results)
    return prepare_run(results, S, labels=labels), results, sources


def test_labels_map_only_for_typed_labels():
    sources = [{"id": 1, "url": EDIT.format(A), "label": " Grade 9 "}, {"id": 2, "url": EDIT.format(B), "label": ""}]
    results = [_result(A, "T1", ROWS), _result(B, "T2", ROWS, url=EDIT.format(B))]
    assert view.labels_map(sources, results) == {A: "Grade 9"}


def test_metrics():
    prepared, _, _ = _prepared()
    assert view.run_metrics(prepared) == {"forms": 1, "fetched": 3, "accepted": 2, "rejected": 1, "flagged": 1,
                                          "master_rows": 2, "duplicates_removed": 0}


def test_link_table_is_plain_language_for_ok_and_failed_links():
    denied = LinkResult(url=EDIT.format(B), status=LinkStatus.NO_ACCESS, message="The portal can't open this form.", form_id=B)
    prepared, results, sources = _prepared([denied])
    table = view.link_table(prepared, results, sources)
    assert list(table.columns) == ["Form", "Status", "Fetched", "Accepted", "Rejected", "Note"]
    assert table.iloc[0].tolist() == ["Grade 9 Chemistry", "✅ Ready", 3, 2, 1, "1 kept but flagged for a look."]
    assert table.iloc[1]["Status"] == "🔒 No access" and "can't open" in table.iloc[1]["Note"]


def test_link_table_shows_needs_review_for_unmappable_form():
    odd = _result(B, "Odd", ROWS)
    odd.table = odd.table.drop(columns=["Full Name"])
    prepared, results, sources = _prepared([odd])
    table = view.link_table(prepared, results, sources)
    assert table.iloc[1]["Status"] == "🟡 Needs review" and "Full Name" in table.iloc[1]["Note"]


def test_datasets_have_friendly_headers_and_source():
    prepared, _, _ = _prepared()
    cleaned = view.cleaned_dataset(prepared, S)
    assert list(cleaned.columns)[:2] == ["Source", "Response ID"] and "Full Name" in cleaned.columns and "Flags" in cleaned.columns
    assert set(cleaned["Source"]) == {"Grade 9 Chemistry"} and len(cleaned) == 2
    rejected = view.rejected_dataset(prepared, S)
    assert len(rejected) == 1 and "Reason" in rejected.columns and rejected.iloc[0]["Full Name"] == "dc"
    flagged = view.flagged_dataset(prepared, S)
    assert len(flagged) == 1 and "gmial.com" in flagged.iloc[0]["Flags"]


def test_datasets_empty_when_nothing_included():
    prepared = prepare_run([], S)
    assert view.cleaned_dataset(prepared, S).empty and view.flagged_dataset(prepared, S).empty


def test_csv_bytes_keep_urdu_and_open_in_excel():
    data = view.csv_bytes(pd.DataFrame({"Name": ["علی خان"]}))
    assert data.startswith(b"\xef\xbb\xbf") and "علی خان" in data.decode("utf-8-sig")


# ------------------------------------------------------------------ confirming fields (Milestone 7)

from portal.mapper import MappingStatus, confirm_choices, detect_mapping  # noqa: E402


def _table_for_confirm(with_email=True):
    t = _raw([("r1", "2026-01-01T10:00:00Z", "ali@gmail.com" if with_email else "", "Ali Raza", "03141837972", "Islamabad", "City School")])
    return t


def test_column_options_skip_system_columns_and_empty_email():
    assert view.column_options(_table_for_confirm()) == ["respondent_email", "Full Name", "WhatsApp Contact", "City", "School Name"]
    assert "respondent_email" not in view.column_options(_table_for_confirm(with_email=False))


def test_column_display_shows_examples_in_plain_words():
    t = _table_for_confirm()
    assert view.column_display(t, view.NONE_CHOICE) == view.NONE_TEXT
    assert view.column_display(t, "Full Name") == "Full Name   (e.g. Ali Raza)"
    assert view.column_display(t, "respondent_email").startswith("Email the person used to fill the form")
    long = _raw([("r1", "t", "", "x" * 60, "1", "c", "s")])
    assert view.column_display(long, "Full Name").count("x") < 30


def test_choice_confidence_colours():
    assert view.choice_confidence("Full Name", 1.0, "Full Name", S)[0] == "green"
    assert view.choice_confidence("Father's Name", 0.8, "Father's Name", S)[0] == "orange"
    assert view.choice_confidence(None, 0.0, None, S)[0] == "red"
    assert view.choice_confidence("Full Name", 1.0, "City", S) == ("blue", "Your choice")


def test_validate_choices_blocks_one_question_for_two_fields():
    ok = {"full_name": "Name", "whatsapp_contact": "Phone", "city": None, "school_name": None, "email": None}
    assert view.validate_choices(ok, S) == []
    bad = {**ok, "city": "Name"}
    problems = view.validate_choices(bad, S)
    assert len(problems) == 1 and "Full Name and City" in problems[0] and "only be used once" in problems[0]


def test_missing_required_lists_field_labels():
    assert view.missing_required({"full_name": "Name", "city": None}, S) == ["WhatsApp Contact", "City", "School Name", "Email"]


def test_form_badge_and_pending_forms():
    table = _table_for_confirm()
    auto = detect_mapping(table, S)
    assert view.form_badge(auto) == ("green", "Looks right")
    review = detect_mapping(table.drop(columns=["City"]), S)
    assert view.form_badge(review) == ("orange", "Needs a quick check")
    res_a = _result(A, "A", ROWS)
    res_b = _result(B, "B", ROWS, url=EDIT.format(B))
    empty = _result("C" * 44, "C", [])
    denied = LinkResult(url="u", status=LinkStatus.NO_ACCESS, form_id="D" * 44)
    confirmed = {A: confirm_choices(auto, auto.columns(), S)}
    pending = view.needs_confirmation([res_a, res_b, empty, denied], confirmed)
    assert [r.form_id for r in pending] == [B]          # empty forms and failed links are never asked about


def test_mapping_rows_show_the_question_used_for_each_field():
    table = _table_for_confirm()
    rows = view.mapping_rows(detect_mapping(table, S), table, S)
    assert list(rows["Field"]) == ["Full Name", "WhatsApp Contact", "City", "School Name", "Email"]
    assert rows.iloc[0]["Question used"].startswith("Full Name") and rows.iloc[4]["Question used"].startswith("Email the person used")
