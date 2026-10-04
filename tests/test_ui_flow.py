import copy
import tempfile
from pathlib import Path

import pandas as pd

from fake_streamlit import fake_streamlit, run_script
from portal.auth import AuthSetupError
from portal.config import load_settings
from portal.connector import LinkStatus, parse_link
from portal.mapper import MappingStore
from portal.ui.exports import cleaned_all, csv_bytes, link_summary, mapping_rows, rejected_all
from portal.ui.gate import configured_password, password_ok
from portal.ui.sources import add_sources, problem_count, remove_source
from portal.ui.runner import fetch_sources

S = load_settings()
ID_A, ID_B = "1" + "a" * 43, "1" + "b" * 43


def edit(i):
    return f"https://docs.google.com/forms/d/{i}/edit"


# ------------------------------------------------------------------ a minimal Forms API stand-in

class _Req:
    def __init__(self, fn): self.fn = fn
    def execute(self): return self.fn()


class FakeService:
    def __init__(self, data): self.data = data
    def forms(self): return self
    def get(self, formId): return _Req(lambda: self.data[formId]["form"])
    def responses(self): return self
    def list(self, formId, pageToken=None): return _Req(lambda: {"responses": self.data[formId]["responses"]})


def form_def(title):
    names = ["Full Name", "WhatsApp Contact", "City", "School Name"]
    return {"info": {"title": title}, "items": [
        {"title": n, "questionItem": {"question": {"questionId": f"q{i}"}}} for i, n in enumerate(names)]}


def resp(rid, name, phone, city, school, email):
    vals = [name, phone, city, school]
    return {"responseId": rid, "lastSubmittedTime": "2026-09-01T10:00:00Z", "respondentEmail": email,
            "answers": {f"q{i}": {"textAnswers": {"answers": [{"value": v}]}} for i, v in enumerate(vals)}}


def service():
    return FakeService({
        ID_A: {"form": form_def("Grade 9 Chemistry"), "responses": [
            resp("r1", "aLI raza", "0314 1837972", "islamabad", "imcb pakistan town", "ali@gmail.com"),
            resp("r2", "dc", "cd", "cd", "cd", "junk@gmail.com"),
        ]},
        ID_B: {"form": form_def("Grade 10 Physics"), "responses": [
            resp("r3", "Hina Noor", "0300 1234567", "Quetta", "Boys College", "hina@gmail.com"),
        ]},
    })


# ------------------------------------------------------------------ helpers without Streamlit

def test_add_sources_skips_repeats_and_flags_wrong_links():
    sources, nid, notes = add_sources([], f"{edit(ID_A)}  {edit(ID_B)} https://forms.gle/xyz {edit(ID_A)}/viewform", 1)
    assert [s.form_id for s in sources] == [ID_A, ID_B, None]
    assert sources[2].status == LinkStatus.PUBLIC_LINK and problem_count(sources) == 1
    assert nid == 4 and notes == ["Skipped a link that is already in your list."]
    assert add_sources(sources, "just words", nid)[2] == ["No links were found in what you pasted."]
    assert [s.id for s in remove_source(sources, 2)] == [1, 3]


def test_fetch_sources_updates_badges_in_order():
    sources, _, _ = add_sources([], f"{edit(ID_A)} {edit(ID_B)} https://forms.gle/xyz", 1)
    seen = []
    results = fetch_sources(service(), sources, sleep=lambda s: None, progress=lambda i, n, s, r: seen.append((i, n)))
    assert [r.status for r in results] == [LinkStatus.READY, LinkStatus.READY, LinkStatus.PUBLIC_LINK]
    assert [s.rows for s in sources] == [2, 1, 0] and sources[0].badge == "✅ Ready" and seen[-1] == (3, 3)


def test_password_helpers():
    assert configured_password({"app": {"password": "secret"}}) == "secret"
    assert configured_password({}) is None and configured_password({"app": {}}) is None
    assert password_ok("secret", "secret") and not password_ok("Secret", "secret")


# ------------------------------------------------------------------ driving the real screens with the fake Streamlit

def _setup(d, settings=S, svc=None):
    ctx = fake_streamlit()
    fake, screens = ctx.__enter__()
    screens.get_forms_service = lambda: (svc or service())
    # reads in this test avoid real sleeping if Google asks to retry
    script = lambda: screens.main(settings, Path(d))
    return ctx, fake, screens, script


def test_full_flow_from_paste_to_download():
    with tempfile.TemporaryDirectory() as d:
        ctx, fake, screens, script = _setup(d)
        try:
            run_script(fake, script)
            assert fake.session_state["stage"] == "sources" and any("empty" in t for t in fake.texts("caption"))

            run_script(fake, script, pressed={"add_btn"}, inputs={"paste_box": f"{edit(ID_A)} {edit(ID_B)} https://forms.gle/short {edit(ID_A)}/viewform"})
            assert len(fake.session_state["sources"]) == 3
            assert any("already in your list" in t for t in fake.texts("info"))
            assert any("can't be used as pasted" in t for t in fake.texts("warning"))
            assert fake.session_state["paste_box"] == ""

            run_script(fake, script, pressed={"fetch_btn"})
            assert fake.session_state["stage"] == "review"
            assert any("edit link" in t for t in fake.texts("error"))          # the forms.gle link is explained
            assert fake.buttons["save_btn"] is True                              # saving is blocked until the operator opts in
            assert any(t.startswith("Forms included=2") for t in fake.texts("metric"))
            summary = fake.dataframes[0]
            assert list(summary["Form"])[:2] == ["Grade 9 Chemistry", "Grade 10 Physics"]

            run_script(fake, script, pressed={"save_btn"})
            assert fake.session_state["stage"] == "review" and not (Path(d) / "master.csv").exists()

            run_script(fake, script, pressed={"save_btn"}, inputs={"allow_partial_cb": True})
            assert fake.session_state["stage"] == "done"
            assert (Path(d) / "master.csv").exists()
            assert any("2 rows from 2 form" in t for t in fake.texts("success"))

            label, data, name = fake.downloads["dl_master"]
            assert name == "master.csv" and data.startswith(b"\xef\xbb\xbf")
            assert "Full Name,WhatsApp Contact,City,School Name,Email,Source" in data.decode("utf-8-sig")
            assert "+923141837972" in data.decode("utf-8-sig") and "junk@gmail.com" not in data.decode("utf-8-sig")
            assert "Reason Rejected" in fake.downloads["dl_rejected"][1].decode("utf-8-sig")
            assert "Source" in fake.downloads["dl_cleaned"][1].decode("utf-8-sig")
            assert any("storage is temporary" in t for t in fake.texts("warning"))

            assert MappingStore(Path(d) / "mappings.json").get(ID_A) is not None   # confident mappings remembered

            run_script(fake, script, pressed={"again_btn"})
            assert fake.session_state["stage"] == "sources" and len(fake.session_state["sources"]) == 3
        finally:
            ctx.__exit__(None, None, None)


def test_remove_and_clear_buttons():
    with tempfile.TemporaryDirectory() as d:
        ctx, fake, screens, script = _setup(d)
        try:
            run_script(fake, script, pressed={"add_btn"}, inputs={"paste_box": f"{edit(ID_A)} {edit(ID_B)}"})
            first_id = fake.session_state["sources"][0].id
            run_script(fake, script, pressed={f"rm_{first_id}"})
            assert [s.form_id for s in fake.session_state["sources"]] == [ID_B]
            run_script(fake, script, pressed={"clear_btn"})
            assert fake.session_state["sources"] == []
        finally:
            ctx.__exit__(None, None, None)


def test_clean_run_saves_without_the_partial_checkbox_and_labels_are_used():
    with tempfile.TemporaryDirectory() as d:
        ctx, fake, screens, script = _setup(d)
        try:
            run_script(fake, script, pressed={"add_btn"}, inputs={"paste_box": f"{edit(ID_A)} {edit(ID_B)}"})
            ids = [s.id for s in fake.session_state["sources"]]
            run_script(fake, script, inputs={f"label_{ids[0]}": "Grade 9 - Chemistry"})
            run_script(fake, script, pressed={"fetch_btn"})
            assert fake.session_state["stage"] == "review" and not fake.texts("error")
            assert fake.buttons["save_btn"] is False
            run_script(fake, script, pressed={"save_btn"})
            assert fake.session_state["stage"] == "done"
            master = pd.read_csv(Path(d) / "master.csv", dtype=str, encoding="utf-8-sig")
            assert set(master["Source"]) == {"Grade 9 - Chemistry", "Grade 10 Physics"}
        finally:
            ctx.__exit__(None, None, None)


def test_sign_in_problem_shown_in_plain_language():
    with tempfile.TemporaryDirectory() as d:
        ctx, fake, screens, script = _setup(d)
        try:
            def broken():
                raise AuthSetupError("The refresh token is missing.")
            screens.get_forms_service = broken
            run_script(fake, script, pressed={"add_btn"}, inputs={"paste_box": edit(ID_A)})
            run_script(fake, script, pressed={"fetch_btn"})
            assert fake.session_state["stage"] == "sources"
            assert any("can't sign in to Google" in t and "refresh token" in t for t in fake.texts("error"))
        finally:
            ctx.__exit__(None, None, None)


def test_preview_switched_off_saves_straight_away():
    s2 = copy.deepcopy(S)
    s2["workflow"]["preview_enabled"] = False
    with tempfile.TemporaryDirectory() as d:
        ctx, fake, screens, script = _setup(d, s2)
        try:
            run_script(fake, script, pressed={"add_btn"}, inputs={"paste_box": f"{edit(ID_A)} {edit(ID_B)}"})
            run_script(fake, script, pressed={"fetch_btn"})
            assert fake.session_state["stage"] == "done" and (Path(d) / "master.csv").exists()
        finally:
            ctx.__exit__(None, None, None)


def test_form_with_uncertain_fields_is_held_back_until_included():
    odd = FakeService({ID_A: {"form": {"info": {"title": "Odd form"}, "items": [
        {"title": "Zed", "questionItem": {"question": {"questionId": "q0"}}},
        {"title": "Phone", "questionItem": {"question": {"questionId": "q1"}}}]},
        "responses": [{"responseId": "z1", "lastSubmittedTime": "2026-09-01T10:00:00Z", "respondentEmail": "z@x.com",
                       "answers": {"q0": {"textAnswers": {"answers": [{"value": "Zed"}]}}, "q1": {"textAnswers": {"answers": [{"value": "03001234567"}]}}}}]}})
    with tempfile.TemporaryDirectory() as d:
        ctx, fake, screens, script = _setup(d, svc=odd)
        try:
            run_script(fake, script, pressed={"add_btn"}, inputs={"paste_box": edit(ID_A)})
            run_script(fake, script, pressed={"fetch_btn"})
            assert any("need to be confirmed" in t or "confirmed" in t for t in fake.texts("error"))
            assert "include_unreviewed_cb" in fake.session_state
            run_script(fake, script, inputs={"include_unreviewed_cb": True})
            assert fake.session_state["prepared"].outcomes[0].status == "Processed"
            assert not any("confirmed before" in t for t in fake.texts("error"))
        finally:
            ctx.__exit__(None, None, None)


# ------------------------------------------------------------------ downloads and tables

def test_export_tables():
    from portal.connector import fetch_all
    from portal.pipeline import prepare_run
    sources, _, _ = add_sources([], f"{edit(ID_A)}", 1)
    results = fetch_sources(service(), sources, sleep=lambda s: None)
    prepared = prepare_run(results, S)
    cleaned, rejected = cleaned_all(prepared, S), rejected_all(prepared, S)
    assert list(cleaned.columns)[:4] == ["Source", "Response ID", "Submitted At", "Full Name"] and len(cleaned) == 1
    assert "Reason Rejected" in rejected.columns and len(rejected) == 1
    rows = mapping_rows(prepared.outcomes[0], S)
    assert list(rows["Field"]) == ["Full Name", "WhatsApp Contact", "City", "School Name", "Email"]
    assert rows.iloc[4]["Taken from"] == "The email used to fill the form" and set(rows["Confidence"]) == {"High"}
    summary = link_summary(sources, results, prepared)
    assert summary.iloc[0]["Accepted"] == "1" and summary.iloc[0]["Rejected"] == "1"
    assert csv_bytes(cleaned).startswith(b"\xef\xbb\xbf")
