"""Drives app.py through the full flow (add links, check, fetch, review, save, download) using a stand-in for
Streamlit, so the page logic is exercised even where Streamlit is not installed. It does not test how the page
looks; that needs a real `streamlit run app.py`."""
import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
A = "1" + "a" * 43
B = "1" + "b" * 43
EDIT = "https://docs.google.com/forms/d/{}/edit"


class Rerun(Exception):
    pass


class Stop(Exception):
    pass


class State(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Col:
    def __init__(self, st): self._st = st
    def __getattr__(self, name): return getattr(self._st, name)
    def __enter__(self): return self
    def __exit__(self, *a): return False


def make_stub():
    st = MagicMock(name="streamlit")
    st.session_state = State()
    st.pressed = set()
    st.callback_ran = False
    st.callbacks = {}                       # button key or label -> (on_click, args), from the last render
    st.downloads = []
    st.messages = []

    def button(label, key=None, on_click=None, args=None, disabled=False, **kw):
        if on_click and not disabled:
            st.callbacks[key or label] = (on_click, args or ())
        return bool((key in st.pressed or label in st.pressed) and not disabled)

    def text_area(label, key=None, **kw):
        st.session_state.setdefault(key, "")
        return st.session_state[key]

    def text_input(label, value="", key=None, **kw):
        st.session_state.setdefault(key, value)
        return st.session_state[key]

    def checkbox(label, key=None, value=False, **kw):
        st.session_state.setdefault(key, value)
        return st.session_state[key]

    def selectbox(label, options, key=None, format_func=None, **kw):
        st.session_state.setdefault(key, options[0])
        return st.session_state[key]

    def multiselect(label, options, key=None, **kw):
        st.session_state.setdefault(key, [])
        return st.session_state[key]

    def slider(label, key=None, min_value=0, **kw):
        st.session_state.setdefault(key, min_value)
        return st.session_state[key]

    def file_uploader(label, key=None, **kw):
        return st.session_state.get(key)

    def columns(spec, **kw):
        return [Col(st) for _ in range(spec if isinstance(spec, int) else len(spec))]

    def tabs(names):
        return [Col(st) for _ in names]

    def rerun():
        raise Rerun()

    def stop():
        raise Stop()

    def download_button(label, data=None, file_name=None, **kw):
        st.downloads.append((label, file_name, data))
        return False

    for kind in ("error", "warning", "success", "info"):
        setattr(st, kind, (lambda k: lambda msg, *a, **kw: st.messages.append((k, str(msg))))(kind))
    st.button, st.text_area, st.text_input, st.checkbox, st.selectbox = button, text_area, text_input, checkbox, selectbox
    st.multiselect, st.slider, st.number_input, st.file_uploader = multiselect, slider, slider, file_uploader
    st.secrets = {}
    st.container = lambda *a, **kw: Col(st)
    st.spinner = lambda *a, **kw: Col(st)
    st.columns, st.tabs, st.rerun, st.stop, st.download_button = columns, tabs, rerun, stop, download_button
    st.expander = lambda *a, **kw: Col(st)
    st.progress = lambda *a, **kw: MagicMock()
    return st


def settle(app, stub):
    """One user action as Streamlit sees it: the click is seen by the first run only; an st.rerun() or a button
    callback makes the page run again."""
    for name in list(stub.pressed):          # Streamlit runs a clicked button's callback BEFORE re-running the page
        if name in stub.callbacks:
            fn, args = stub.callbacks[name]
            fn(*args)
    for _ in range(6):
        try:
            app.main()
            rerun = False
        except Rerun:
            rerun = True
        stub.pressed = set()
        if not rerun:
            return
    raise AssertionError("page kept rerunning")


class FakeReq:
    def __init__(self, fn): self.fn = fn
    def execute(self): return self.fn()


class FakeService:
    def __init__(self, forms): self.forms_data = forms
    def forms(self): return self
    def get(self, formId): return FakeReq(lambda: self.forms_data[formId]["form"])
    def responses(self): return self
    def list(self, formId, pageToken=None): return FakeReq(lambda: {"responses": self.forms_data[formId]["responses"]})


def _form(title):
    return {"info": {"title": title}, "items": [
        {"title": t, "questionItem": {"question": {"questionId": q}}}
        for t, q in [("Full Name", "q1"), ("WhatsApp Contact", "q2"), ("City", "q3"), ("School Name", "q4")]]}


def _resp(rid, name, phone, city, school, email):
    ans = lambda v: {"textAnswers": {"answers": [{"value": v}]}}
    return {"responseId": rid, "lastSubmittedTime": "2026-02-01T10:00:00Z", "respondentEmail": email,
            "answers": {"q1": ans(name), "q2": ans(phone), "q3": ans(city), "q4": ans(school)}}


def test_full_page_flow_from_paste_to_download():
    stub = make_stub()
    saved_module = sys.modules.get("streamlit")
    sys.modules["streamlit"] = stub
    try:
        spec = importlib.util.spec_from_file_location("portal_app_under_test", ROOT / "app.py")
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)

        service = FakeService({
            A: {"form": _form("Grade 9 Quiz"), "responses": [
                _resp("r1", "aLI raza", "0314 1837972", "islamabad", "city school", "ali@gmail.com"),
                _resp("r2", "dc", "cd", "cd", "cd", "junk@gmail.com")]},
            B: {"form": _form("Grade 10 Quiz"), "responses": [
                _resp("r9", "Sara Khan", "03331234567", "Lahore", "city school", "sara@gmial.com")]},
        })
        app.get_forms_service = lambda: service

        def rerun_page():
            settle(app, stub)

        with tempfile.TemporaryDirectory() as d:
            app.DATA_DIR = Path(d)
            ss = stub.session_state

            rerun_page()                                       # fresh page
            assert ss.sources == [] and ss.prepared is None

            ss.paste_box = f"{EDIT.format(A)}\n{EDIT.format(B)}\nhttps://forms.gle/short"
            stub.pressed = {"Add to list"}
            rerun_page()
            assert len(ss.sources) == 3 and ss.paste_box == ""

            stub.pressed = {"Check links"}
            rerun_page()
            assert ss.checks[EDIT.format(A)].title == "Grade 9 Quiz"

            ss["label_1"] = "Grade 9 Chemistry"          # what typing in the label box does
            stub.pressed = {"Fetch and review"}
            rerun_page()
            assert ss.prepared is None and set(ss.proposals) == {A, B} and not ss.confirmed   # waiting for confirmation
            assert not (Path(d) / "mappings.json").exists()

            stub.pressed = {"Confirm all that look right"}
            rerun_page()
            assert set(ss.confirmed) == {A, B} and (Path(d) / "mappings.json").exists()
            prepared = ss.prepared
            assert prepared is not None and prepared.blockers          # the forms.gle link is a problem
            assert [o.label for o in prepared.included] == ["Grade 9 Chemistry", "Grade 10 Quiz"]

            stub.pressed = {"Confirm and save the master CSV"}         # blocked: "Save anyway" not ticked
            rerun_page()
            assert ss.saved is None and not (Path(d) / "master.csv").exists()

            ss.allow_partial = True
            stub.pressed = {"Confirm and save the master CSV"}
            rerun_page()
            assert ss.saved is not None and ss.saved.master.rows == 2
            assert (Path(d) / "master.csv").exists()

            stub.pressed = set()
            stub.downloads.clear()
            rerun_page()
            names = [n for _, n, _ in stub.downloads if n != "settings.json"]   # (the Settings tab offers its own download)
            assert names == ["master.csv", "cleaned_dataset.csv", "rejected_rows.csv"]
            master_csv = stub.downloads[0][2].decode("utf-8-sig")
            assert master_csv.splitlines()[0].startswith("Full Name,WhatsApp Contact,City,School Name,Email,Source")
            assert "+923141837972" in master_csv and "Grade 9 Chemistry" in master_csv and "dc" not in master_csv
            assert any(kind == "success" for kind, _ in stub.messages)

            ss.sources = [s for s in ss.sources if "forms.gle" not in s["url"]]   # remove the bad link
            stub.pressed = {"Fetch and review"}
            rerun_page()
            # both forms were confirmed earlier and remembered, so the page goes straight to the review
            assert set(ss.confirmed) == {A, B} and ss.prepared.can_commit and not ss.prepared.blockers
    finally:
        if saved_module is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = saved_module


def test_sign_in_problem_shows_plain_message_not_a_crash():
    stub = make_stub()
    saved_module = sys.modules.get("streamlit")
    sys.modules["streamlit"] = stub
    try:
        spec = importlib.util.spec_from_file_location("portal_app_under_test2", ROOT / "app.py")
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)

        def broken():
            raise app.AuthTokenError("Google no longer accepts the saved sign-in")
        app.get_forms_service = broken
        ss = stub.session_state
        settle(app, stub)
        ss.paste_box = EDIT.format(A)
        stub.pressed = {"Add to list"}
        settle(app, stub)
        stub.pressed = {"Fetch and review"}
        settle(app, stub)
        errors = [m for k, m in stub.messages if k == "error"]
        assert errors and "let the administrator know" in errors[0] and ss.prepared is None
    finally:
        if saved_module is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = saved_module


ODD = "1" + "c" * 43


def _odd_form():
    """A form whose questions the portal cannot guess, so the operator has to pick."""
    return {"info": {"title": "Odd Form"}, "items": [
        {"title": t, "questionItem": {"question": {"questionId": q}}}
        for t, q in [("Student", "q1"), ("Mobile", "q2"), ("Town", "q3"), ("Institute", "q4")]]}


def test_operator_corrects_and_confirms_fields_then_can_change_them_later():
    stub = make_stub()
    saved_module = sys.modules.get("streamlit")
    sys.modules["streamlit"] = stub
    try:
        spec = importlib.util.spec_from_file_location("portal_app_under_test3", ROOT / "app.py")
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        service = FakeService({ODD: {"form": _odd_form(), "responses": [
            _resp("r1", "aLI raza", "0314 1837972", "islamabad", "city school", "ali@gmail.com")]}})
        app.get_forms_service = lambda: service

        def act(pressed):
            stub.pressed = set(pressed)
            settle(app, stub)

        with tempfile.TemporaryDirectory() as d:
            app.DATA_DIR = Path(d)
            ss = stub.session_state
            act([])
            ss.paste_box = EDIT.format(ODD)
            act(["Add to list"])
            act(["Fetch and review"])

            proposal = ss.proposals[ODD]
            assert proposal.status.value == "Needs review" and ss.prepared is None
            key = lambda f: app._choice_key(ODD, f)

            # the operator picks the right questions, but wrongly uses one question twice at first
            ss[key("full_name")] = "Student"
            ss[key("whatsapp_contact")] = "Student"
            act([f"confirm_{ODD}"])
            assert ODD not in ss.confirmed                                   # blocked: same question for two fields

            ss[key("whatsapp_contact")] = "Mobile"
            ss[key("city")] = "Town"
            ss[key("school_name")] = "Institute"
            act([f"confirm_{ODD}"])
            assert ODD in ss.confirmed and ss.confirmed[ODD].columns()["full_name"] == "Student"
            assert ss.prepared is not None and ss.prepared.master.iloc[0].full_name == "Ali Raza"
            assert (Path(d) / "mappings.json").exists()

            act([f"change_{ODD}"])                                            # back to the confirmation screen
            assert ODD not in ss.confirmed and ss.prepared is None
            assert ss[key("full_name")] == "Student"                          # drop-downs remember the earlier choice
            ss[key("full_name")] = "Town"
            ss[key("city")] = "Student"
            act([f"confirm_{ODD}"])
            assert ss.confirmed[ODD].columns()["city"] == "Student"
            assert ss.prepared.master.iloc[0].city == "Ali Raza"             # the correction really changes the output
    finally:
        if saved_module is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = saved_module


# ------------------------------------------------------------------ the Settings tab (Milestone 8)

def _load_app(stub, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "app.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    return app


def _with_stub(test):
    def wrapper():
        stub = make_stub()
        saved_module = sys.modules.get("streamlit")
        sys.modules["streamlit"] = stub
        try:
            test(stub)
        finally:
            if saved_module is None:
                sys.modules.pop("streamlit", None)
            else:
                sys.modules["streamlit"] = saved_module
    wrapper.__name__ = test.__name__
    return wrapper


def _fetched_page(stub, app, d, rows):
    """A page that has fetched one form (confirmed fields) so cleaning rules can be compared run to run."""
    service = FakeService({A: {"form": _form("Grade 9 Quiz"), "responses": rows}})
    app.get_forms_service = lambda: service
    app.DATA_DIR = Path(d)
    ss = stub.session_state

    def act(pressed=()):
        stub.pressed = set(pressed)
        settle(app, stub)

    act()
    ss.paste_box = EDIT.format(A)
    act(["Add to list"])
    act(["Fetch and review"])
    act(["Confirm all that look right"] if len(ss.proposals) > 1 else [f"confirm_{A}"])
    return act


@_with_stub
def test_changing_a_rule_in_settings_changes_the_next_run_without_touching_code(stub):
    app = _load_app(stub, "portal_app_settings1")
    rows = [_resp("r1", "aLI raza", "03141837972", "khi", "city school", "ali@gmail.com")]
    with tempfile.TemporaryDirectory() as d:
        act = _fetched_page(stub, app, d, rows)
        ss = stub.session_state
        assert ss.prepared.master.iloc[0].city == "Karachi" and ss.prepared.master.iloc[0].whatsapp_contact == "+923141837972"

        # the administrator edits the rules on the Settings tab, then saves
        ss["s_city_aliases"] = "khi = Kharadar"
        ss["s_flag_landlines"] = True
        ss["s_f_full_name_steps"] = ["trim", "collapse_spaces"]             # capitalisation fixing switched off
        act(["Save settings"])
        assert ss.settings_error is None and ss.settings_msg[0].startswith("Saved.")
        assert any("cleaning.flag_landlines: false → true" in line for line in ss.settings_msg)
        assert ss.prepared is not None                                        # re-cleaned straight away with the new rules
        row = ss.prepared.master.iloc[0]
        assert row.city == "Kharadar" and row.full_name == "aLI raza"
        assert (Path(d) / "settings.json").exists()

        # the saved settings are what the next fetch uses
        act(["Fetch and review"])
        assert ss.prepared.master.iloc[0].city == "Kharadar"


@_with_stub
def test_bad_settings_are_refused_with_a_plain_message_and_nothing_is_saved(stub):
    app = _load_app(stub, "portal_app_settings2")
    with tempfile.TemporaryDirectory() as d:
        app.DATA_DIR = Path(d)
        ss = stub.session_state
        settle(app, stub)
        ss["s_country"] = "Pakistan"
        stub.pressed = {"Save settings"}
        settle(app, stub)
        assert "two-letter" in ss.settings_error and not (Path(d) / "settings.json").exists()
        ss["s_city_aliases"] = "khi = Karachi\nnonsense line"
        ss["s_country"] = "PK"
        stub.pressed = {"Save settings"}
        settle(app, stub)
        assert "Line 2" in ss.settings_error


@_with_stub
def test_previous_settings_can_be_restored_and_unsaved_edits_discarded(stub):
    app = _load_app(stub, "portal_app_settings3")
    with tempfile.TemporaryDirectory() as d:
        app.DATA_DIR = Path(d)
        ss = stub.session_state
        settle(app, stub)
        ss["s_country"] = "AE"
        stub.pressed = {"Save settings"}
        settle(app, stub)
        ss["s_country"] = "GB"
        stub.pressed = {"Discard unsaved changes"}
        settle(app, stub)
        assert ss["s_country"] == "AE"                                        # edit thrown away, saved value back

        ss["s_country"] = "IN"
        stub.pressed = {"Save settings"}
        settle(app, stub)
        history_names = [e.path.name for e in app._settings_store().history()]
        assert len(history_names) == 2
        ss["s_restore_choice"] = history_names[-1]                            # the oldest: the built-in defaults
        stub.pressed = {"Restore this version"}
        settle(app, stub)
        assert ss["s_country"] == "PK" and app._settings_store().load()["cleaning"]["default_country_code"] == "PK"
        assert ss.settings_msg[0].startswith("Restored version 1")


@_with_stub
def test_adding_and_removing_a_field_from_the_settings_screen(stub):
    app = _load_app(stub, "portal_app_settings4")
    with tempfile.TemporaryDirectory() as d:
        app.DATA_DIR = Path(d)
        ss = stub.session_state
        settle(app, stub)
        ss["s_new_label"] = "Grade"
        stub.pressed = {"Add field"}
        settle(app, stub)
        assert ss.s_field_order[-1] == "grade" and ss["s_f_grade_label"] == "Grade" and ss["s_new_label"] == ""
        stub.pressed = {"Save settings"}
        settle(app, stub)
        assert [f["key"] for f in app._settings_store().load()["fields"]][-1] == "grade"

        stub.pressed = {"s_rm_grade"}
        settle(app, stub)
        stub.pressed = {"Save settings"}
        settle(app, stub)
        assert "grade" not in [f["key"] for f in app._settings_store().load()["fields"]]


@_with_stub
def test_changing_the_field_list_clears_the_fetched_data_but_other_changes_do_not(stub):
    app = _load_app(stub, "portal_app_settings5")
    rows = [_resp("r1", "Ali Raza", "03141837972", "Karachi", "city school", "ali@gmail.com")]
    with tempfile.TemporaryDirectory() as d:
        act = _fetched_page(stub, app, d, rows)
        ss = stub.session_state
        ss["s_country"] = "AE"
        act(["Save settings"])
        assert ss.results and ss.prepared is not None                          # a rule change keeps the fetched data
        ss["s_new_label"] = "Grade"
        act(["Add field"])
        act(["Save settings"])
        assert ss.results == [] and ss.prepared is None                        # a new field means fetching again


@_with_stub
def test_settings_are_locked_behind_the_admin_password_when_one_is_set(stub):
    app = _load_app(stub, "portal_app_settings6")
    stub.secrets = {"admin": {"password": "s3cret"}}
    with tempfile.TemporaryDirectory() as d:
        app.DATA_DIR = Path(d)
        ss = stub.session_state
        settle(app, stub)
        ss["s_country"] = "AE"
        stub.pressed = {"Save settings"}
        settle(app, stub)
        assert not (Path(d) / "settings.json").exists()                        # the editor was never even shown

        ss["admin_try"] = "wrong"
        stub.pressed = {"Unlock"}
        settle(app, stub)
        assert not ss.admin_ok and any("not right" in m for k, m in stub.messages if k == "error")

        ss["admin_try"] = "s3cret"
        stub.pressed = {"Unlock"}
        settle(app, stub)
        assert ss.admin_ok
        ss["s_country"] = "AE"
        stub.pressed = {"Save settings"}
        settle(app, stub)
        assert app._settings_store().load()["cleaning"]["default_country_code"] == "AE"


@_with_stub
def test_workflow_switches_skip_the_preview_and_the_confirmation(stub):
    app = _load_app(stub, "portal_app_settings7")
    rows = [_resp("r1", "Ali Raza", "03141837972", "Karachi", "city school", "ali@gmail.com")]
    with tempfile.TemporaryDirectory() as d:
        act = _fetched_page(stub, app, d, rows)
        ss = stub.session_state
        assert ss.saved is None                                                # default: waits for the Confirm button
        ss["s_require_confirm"] = False
        ss["s_preview"] = False
        act(["Save settings"])
        assert ss.saved is not None and ss.saved.master.rows == 1             # saved by itself once everything worked
        assert (Path(d) / "master.csv").exists()
        assert not any(label == "Confirm and save the master CSV" for label, *_ in stub.downloads)
