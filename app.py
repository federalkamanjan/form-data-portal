"""Forms Data Portal: paste Google Form links, confirm the fields, review the cleaned data, save one master CSV.

Run with:  python -m streamlit run app.py
Everything happens on this one page, top to bottom.
"""
from __future__ import annotations

import hmac
from pathlib import Path

import streamlit as st

from portal import __version__
from portal.auth import AuthSetupError, AuthTokenError, get_forms_service
from portal.cleaner.steps import FLAG_DESCRIPTIONS, STEP_DESCRIPTIONS, STEP_ORDER
from portal.config import SettingsError, SettingsStore, describe_changes
from portal.connector import LinkStatus, check_all, fetch_all
from portal.mapper import MappingStatus, MappingStore, confirm_choices, propose_mapping
from portal.pipeline import CommitBlocked, commit_run, prepare_run
from portal.storage import CsvStorage, StorageBusyError, to_output_headers
from portal.ui import settings_form, view

DATA_DIR = Path(__file__).parent / "data"

HELP_TEXT = """
1. Open **Google Forms** and sign in with the account that owns the form.
2. Click the form so you are on its **editing page** (where you add and change questions).
3. Copy the address from your browser's address bar. It looks like
   `https://docs.google.com/forms/d/…/edit`.
4. Paste it into the box below. You can paste many at once, one per line.

**These will not work:** the link you send to students (`forms.gle/…` or `…/viewform`), or the link to the
responses spreadsheet. The portal will tell you if you paste one of these.
"""


# ------------------------------------------------------------------ state and callbacks

def init_state() -> None:
    ss = st.session_state
    ss.setdefault("sources", [])
    ss.setdefault("next_id", 1)
    ss.setdefault("notes", [])
    ss.setdefault("checks", {})       # url -> LinkResult from "Check links"
    ss.setdefault("results", [])      # LinkResult list from the last fetch
    ss.setdefault("fetch_id", 0)      # changes on every fetch, so the drop-downs start fresh
    ss.setdefault("proposals", {})    # form_id -> MappingResult guessed by the portal
    ss.setdefault("confirmed", {})    # form_id -> MappingResult the operator confirmed (or saved earlier)
    ss.setdefault("prepared", None)   # PreparedRun waiting for the final confirmation
    ss.setdefault("saved", None)      # CommitResult after saving
    ss.setdefault("admin_ok", False)  # unlocked the Settings tab (only matters if an admin password is set)
    ss.setdefault("settings_msg", None)
    ss.setdefault("settings_error", None)


def _store() -> MappingStore:
    return MappingStore(DATA_DIR / "mappings.json")


def _settings_store() -> SettingsStore:
    return SettingsStore(DATA_DIR)


def _reset_review() -> None:
    ss = st.session_state
    ss.results, ss.proposals, ss.confirmed = [], {}, {}
    ss.prepared = None
    ss.saved = None


def _add_links() -> None:
    ss = st.session_state
    ss.sources, ss.next_id, ss.notes = view.add_sources(ss.sources, ss.get("paste_box", ""), ss.next_id)
    ss.paste_box = ""
    _reset_review()


def _remove(source_id: int) -> None:
    ss = st.session_state
    ss.sources = view.remove_source(ss.sources, source_id)
    _reset_review()


def _choice_key(form_id: str, field_key: str) -> str:
    return f"map_{st.session_state.fetch_id}_{form_id}_{field_key}"


def _current_choices(form_id: str, settings: dict) -> dict[str, str | None]:
    """What the drop-downs currently say for one form (falling back to the portal's guess)."""
    ss = st.session_state
    proposal = ss.proposals[form_id]
    choices = {}
    for f in settings["fields"]:
        picked = ss.get(_choice_key(form_id, f["key"]))
        if picked is None:
            picked = proposal.matches[f["key"]].column or view.NONE_CHOICE
        choices[f["key"]] = None if picked == view.NONE_CHOICE else picked
    return choices


def _confirm(form_id: str) -> None:
    ss = st.session_state
    settings = _settings_store().load()
    choices = _current_choices(form_id, settings)
    if view.validate_choices(choices, settings):
        return
    result = confirm_choices(ss.proposals[form_id], choices, settings, form_id=form_id)
    _store().save(form_id, result)       # remembered, so this form is never asked about again
    ss.confirmed[form_id] = result
    ss.prepared = None
    ss.saved = None


def _confirm_all_that_look_right() -> None:
    ss = st.session_state
    for form_id, proposal in list(ss.proposals.items()):
        if form_id not in ss.confirmed and proposal.status == MappingStatus.AUTO:
            _confirm(form_id)


def _change_fields(form_id: str) -> None:
    """Take a confirmed form back to the confirmation screen, with the drop-downs set to what was confirmed."""
    ss = st.session_state
    settings = _settings_store().load()
    old = ss.confirmed.pop(form_id, None)
    _store().forget(form_id)
    if old is not None:
        for f in settings["fields"]:
            ss[_choice_key(form_id, f["key"])] = old.matches[f["key"]].column or view.NONE_CHOICE
    ss.prepared = None
    ss.saved = None


def _service():
    """A signed-in Google client, or None after showing a plain message."""
    try:
        return get_forms_service()
    except (AuthSetupError, AuthTokenError) as exc:
        st.error("The portal can't connect to Google right now. Please let the administrator know.")
        st.caption(f"Details for the administrator: {exc}")
        return None


# ------------------------------------------------------------------ 1. sources

def section_sources(settings: dict) -> None:
    ss = st.session_state
    st.header("1. Add your forms")
    st.caption("Paste the edit link of each Google Form you want to include.")
    with st.expander("Where do I find my form's edit link?"):
        st.markdown(HELP_TEXT)

    st.text_area("Paste form edit links here", key="paste_box", height=110,
                 placeholder="https://docs.google.com/forms/d/…/edit")
    st.button("Add to list", on_click=_add_links, type="primary")
    for note in ss.notes:
        st.caption(note)

    if not ss.sources:
        st.info("No forms added yet.")
        return

    results_by_url = {r.url: r for r in ss.results}
    for src in list(ss.sources):
        c_label, c_status, c_remove = st.columns([3, 4, 1])
        with c_label:
            src["label"] = st.text_input("Label", value=src["label"], key=f"label_{src['id']}",
                                         placeholder=view.label_hint(src, ss.checks), label_visibility="collapsed")
            st.caption(view.short_url(src["url"]))
        with c_status:
            badge_text, note = view.source_status(src, ss.checks, results_by_url)
            st.markdown(f"**{badge_text}**")
            if note:
                st.caption(note)
        with c_remove:
            st.button("Remove", key=f"rm_{src['id']}", on_click=_remove, args=(src["id"],))

    left, right = st.columns(2)
    check = left.button("Check links", help="Quickly checks that the portal can open each form. Nothing is downloaded.")
    fetch = right.button("Fetch and review", type="primary", help="Downloads the responses and shows you the cleaned result.")
    if check:
        _do_check()
    if fetch:
        _do_fetch(settings)


def _do_check() -> None:
    ss = st.session_state
    service = _service()
    if service is None:
        return
    bar = st.progress(0.0, text="Checking links…")
    results = check_all(service, view.sources_text(ss.sources),
                        progress=lambda i, n, r: bar.progress(i / n, text=f"Checked {i} of {n}"))
    ss.checks = {r.url: r for r in results}
    bar.empty()
    st.rerun()


def _do_fetch(settings: dict) -> None:
    ss = st.session_state
    service = _service()
    if service is None:
        return
    bar = st.progress(0.0, text="Fetching responses…")
    results = fetch_all(service, view.sources_text(ss.sources),
                        progress=lambda i, n, r: bar.progress(i / n, text=f"Fetched {i} of {n} links"))
    store = _store()
    proposals, confirmed = {}, {}
    for r in results:
        if r.has_table and r.status == LinkStatus.READY:
            proposal = propose_mapping(r.table, settings, form_id=r.form_id, store=store)
            proposals[r.form_id] = proposal
            if proposal.status == MappingStatus.SAVED:      # confirmed on an earlier visit: no need to ask again
                confirmed[r.form_id] = proposal
    ss.results, ss.proposals, ss.confirmed = results, proposals, confirmed
    ss.fetch_id += 1
    ss.prepared = None
    ss.saved = None
    bar.empty()
    st.rerun()


# ------------------------------------------------------------------ 2. confirm which question is which

def section_confirm(settings: dict, pending: list) -> None:
    ss = st.session_state
    st.header("2. Confirm which question is which")
    st.caption("The portal looked at each form and guessed which question holds each piece of information. "
               "Please check the guesses, fix anything that's wrong, then confirm. "
               "You only do this once for each form; the portal remembers your answer.")

    easy = [r for r in pending if ss.proposals[r.form_id].status == MappingStatus.AUTO]
    if len(easy) > 1:
        st.button("Confirm all that look right", on_click=_confirm_all_that_look_right, type="primary",
                  help="Confirms every form marked 'Looks right' below.")

    labels = view.labels_map(ss.sources, ss.results)
    for r in pending:
        proposal = ss.proposals[r.form_id]
        colour, words = view.form_badge(proposal)
        with st.container(border=True):
            st.markdown(f"**{labels.get(r.form_id) or r.title}**  {view.colored(colour, words)}")
            for note in proposal.notes:
                st.caption(note)
            options = [view.NONE_CHOICE, *view.column_options(r.table)]
            choices = {}
            for f in settings["fields"]:
                match = proposal.matches[f["key"]]
                key = _choice_key(r.form_id, f["key"])
                if key not in ss:   # start from the portal's guess; after that the drop-down's own state rules
                    ss[key] = match.column if match.column in options else view.NONE_CHOICE
                picked = st.selectbox(f["label"], options, key=key,
                                      format_func=lambda c, table=r.table: view.column_display(table, c))
                chosen = None if picked == view.NONE_CHOICE else picked
                choices[f["key"]] = chosen
                tint, hint = view.choice_confidence(match.column, match.score, chosen, settings)
                st.caption(view.colored(tint, hint))
            problems = view.validate_choices(choices, settings)
            for problem in problems:
                st.error(problem)
            missing = view.missing_required(choices, settings)
            if missing and not problems:
                st.warning("No question chosen for: " + ", ".join(missing) + ". Responses without these will be left out of the master.")
            st.button("Confirm these fields", key=f"confirm_{r.form_id}", on_click=_confirm, args=(r.form_id,),
                      type="primary", disabled=bool(problems))


def maybe_prepare(settings: dict) -> None:
    """Once every form is confirmed, clean and combine everything (once)."""
    ss = st.session_state
    if ss.prepared is not None or not ss.results:
        return
    with st.spinner("Cleaning and combining…"):
        ss.prepared = prepare_run(ss.results, settings, mapping_overrides=ss.confirmed,
                                  labels=view.labels_map(ss.sources, ss.results))


# ------------------------------------------------------------------ 3. review

def _show_table(df, empty_message: str) -> None:
    if df.empty:
        st.info(empty_message)
    else:
        st.dataframe(df, hide_index=True)


def section_review(settings: dict) -> None:
    ss = st.session_state
    prepared = ss.prepared
    st.header("2. Review")
    st.caption("Here is what the portal found. Nothing has been saved yet.")
    m = view.run_metrics(prepared)
    a, b, c = st.columns(3)
    a.metric("Forms included", m["forms"])
    b.metric("Responses fetched", m["fetched"])
    c.metric("Accepted", m["accepted"])
    d, e = st.columns(2)
    d.metric("Rejected", m["rejected"])
    e.metric("Rows in master", m["master_rows"])

    st.subheader("What happened with each link")
    st.dataframe(view.link_table(prepared, ss.results, ss.sources), hide_index=True)
    for problem in prepared.blockers:
        st.error(problem)
    for warning in prepared.warnings:
        st.warning(warning)

    if settings["workflow"].get("preview_enabled", True):
        tab_master, tab_rejected, tab_flagged, tab_log = st.tabs(
            ["Master preview", f"Rejected rows ({m['rejected']})", f"Flagged for a look ({m['flagged']})", "What was cleaned"])
        with tab_master:
            _show_table(to_output_headers(prepared.master, settings), "Nothing to show yet.")
        with tab_rejected:
            st.caption("These rows were left out of the master because something required was missing or unusable.")
            _show_table(view.rejected_dataset(prepared, settings), "No rows were rejected.")
        with tab_flagged:
            st.caption("These rows are in the master, but something looked odd (for example a likely email typo).")
            _show_table(view.flagged_dataset(prepared, settings), "Nothing was flagged.")
        with tab_log:
            for o in prepared.included:
                if o.cleaning is None:
                    continue
                st.markdown(f"**{o.label}**")
                st.text("\n".join(o.cleaning.report.summary_lines(settings)))
    else:
        st.caption("The detailed preview is switched off in Settings.")

    with st.expander("Which question was used for each field?"):
        for o in prepared.included:
            if o.mapping is None:
                continue
            st.markdown(f"**{o.label}**")
            st.dataframe(view.mapping_rows(o.mapping, o.raw, settings), hide_index=True)
            st.button("Change these", key=f"change_{o.form_id}", on_click=_change_fields, args=(o.form_id,))


# ------------------------------------------------------------------ 4. save and download

def section_save(settings: dict) -> None:
    ss = st.session_state
    prepared = ss.prepared
    st.header("3. Save and download")

    auto_save = not settings["workflow"].get("require_confirmation", True) and prepared.can_commit
    allow_partial = False
    if prepared.blockers:
        st.warning("Some links could not be included. If you save now, the master will be missing their data. "
                   "The previous master is backed up first.")
        allow_partial = st.checkbox("Save anyway", key="allow_partial")
    can_save = bool(prepared.included) and (not prepared.blockers or allow_partial)

    wants_save = False
    if auto_save:
        wants_save = ss.saved is None
        st.caption("Saving happens automatically (you can change this in Settings).")
    else:
        wants_save = st.button("Confirm and save the master CSV", type="primary", disabled=not can_save)
    if wants_save:
        try:
            storage = CsvStorage.from_settings(DATA_DIR, settings)
            ss.saved = commit_run(prepared, storage, settings, allow_partial=allow_partial)
        except (CommitBlocked, StorageBusyError) as exc:
            st.error(str(exc))

    if ss.saved:
        st.success(f"Saved {ss.saved.master.rows} rows from {ss.saved.saved_forms} forms.")
        st.info("Files on this server are temporary, so please download what you need now.")
        name = settings["output"].get("master_file_name", "master.csv")
        st.download_button("Download master CSV", data=view.csv_bytes(to_output_headers(prepared.master, settings)),
                           file_name=name, mime="text/csv")
        st.download_button("Download cleaned dataset", data=view.csv_bytes(view.cleaned_dataset(prepared, settings)),
                           file_name="cleaned_dataset.csv", mime="text/csv")
        st.download_button("Download rejected rows", data=view.csv_bytes(view.rejected_dataset(prepared, settings)),
                           file_name="rejected_rows.csv", mime="text/csv")


# ------------------------------------------------------------------ settings

def _admin_password() -> str | None:
    try:
        password = st.secrets.get("admin", {}).get("password")
    except Exception:
        return None
    return str(password) if password else None


def _is_admin() -> bool:
    return not _admin_password() or bool(st.session_state.get("admin_ok"))


def _unlock() -> None:
    ss = st.session_state
    password = _admin_password()
    ok = bool(password) and hmac.compare_digest(str(ss.get("admin_try", "")), password)
    ss.admin_ok = ok
    ss.admin_wrong = not ok
    ss.admin_try = ""


def init_settings_widgets(current: dict) -> None:
    """Fill the Settings widgets from the saved settings (at the start, and again after every save or restore)."""
    ss = st.session_state
    if ss.get("s_loaded_version") == current["settings_version"] and ss.get("s_field_order"):
        return
    for key, value in settings_form.widget_values(current).items():
        ss[key] = value
    ss.s_field_order = settings_form.field_order(current)
    ss.s_loaded_version = current["settings_version"]


def _after_settings_change(old: dict, new: dict) -> None:
    ss = st.session_state
    ss.s_loaded_version = None          # widgets reload from what was saved
    ss.prepared = None                  # the next review uses the new rules
    ss.saved = None
    if [f["key"] for f in old["fields"]] != [f["key"] for f in new["fields"]]:
        _reset_review()                 # fields were added or removed: confirmations no longer apply, fetch again


def _record(old: dict, new: dict, headline: str) -> None:
    ss = st.session_state
    changes = describe_changes(old, new)
    ss.settings_error = None
    ss.settings_msg = [f"{headline} These settings are now version {new['settings_version']}."] + (changes or ["Nothing was different."])
    _after_settings_change(old, new)


def _save_settings() -> None:
    ss = st.session_state
    if not _is_admin():
        ss.settings_error = "Please unlock the Settings with the administrator password first."
        return
    store = _settings_store()
    current = store.load()
    try:
        saved = store.save(settings_form.settings_from_widgets(ss, ss.s_field_order, current))
    except SettingsError as exc:
        ss.settings_msg, ss.settings_error = None, str(exc)
        return
    _record(current, saved, "Saved.")


def _discard_changes() -> None:
    st.session_state.s_loaded_version = None
    st.session_state.settings_error = None
    st.session_state.settings_msg = ["Unsaved changes were discarded."]


def _add_field() -> None:
    ss = st.session_state
    try:
        field = settings_form.new_field(ss.get("s_new_label", ""), ss.get("s_new_type", "text"), set(ss.s_field_order))
    except SettingsError as exc:
        ss.settings_error = str(exc)
        return
    for key, value in settings_form.field_widget_values(field).items():
        ss[key] = value
    ss.s_field_order = [*ss.s_field_order, field["key"]]
    ss.s_new_label = ""
    ss.settings_error = None


def _remove_field(key: str) -> None:
    ss = st.session_state
    ss.s_field_order = [k for k in ss.s_field_order if k != key]
    for widget in ("s_dedupe_primary", "s_dedupe_secondary"):
        if ss.get(widget) == key:
            ss[widget] = settings_form.NONE


def _restore(file_name: str) -> None:
    store = _settings_store()
    entry = next((e for e in store.history() if e.path.name == file_name), None)
    if entry is None:
        st.session_state.settings_error = "That earlier version is no longer available."
        return
    current = store.load()
    try:
        _record(current, store.restore(entry), f"Restored version {entry.version}.")
    except SettingsError as exc:
        st.session_state.settings_error = str(exc)


def _reset_to_defaults() -> None:
    store = _settings_store()
    current = store.load()
    _record(current, store.reset_to_defaults(), "Back to the built-in defaults.")


def _import_uploaded() -> None:
    ss = st.session_state
    upload = ss.get("s_upload")
    if upload is None:
        ss.settings_error = "Choose a settings file first."
        return
    store = _settings_store()
    current = store.load()
    try:
        _record(current, store.import_json(upload.getvalue()), "Uploaded settings are in use.")
    except SettingsError as exc:
        ss.settings_error = str(exc)


def section_settings(current: dict) -> None:
    ss = st.session_state
    store = _settings_store()
    st.header("Settings")

    password = _admin_password()
    if password and not ss.admin_ok:
        st.info("The settings are protected. Enter the administrator password to change them.")
        st.text_input("Administrator password", type="password", key="admin_try")
        st.button("Unlock", on_click=_unlock)
        if ss.get("admin_wrong"):
            st.error("That password is not right.")
        return
    if not password:
        st.warning("No administrator password is set, so anyone with this page's link can change these settings. "
                   "To protect them, add an [admin] section with a password to the app's Secrets.")

    st.caption(f"Settings version {current['settings_version']}. Changes apply to the next run. "
               "Nothing is changed until you press **Save settings**.")
    for line in ss.settings_msg or []:
        st.caption(line)
    if ss.settings_error:
        st.error(ss.settings_error)

    field_keys = list(ss.s_field_order)

    with st.expander("Fields: what the portal collects"):
        st.caption("Each field becomes a column in the master. The 'words' help the portal recognise the right question.")
        for key in field_keys:
            with st.container(border=True):
                st.text_input("Name", key=f"s_f_{key}_label")
                st.selectbox("Kind of information", settings_form.FIELD_TYPES, key=f"s_f_{key}_type",
                             format_func=lambda t: settings_form.FIELD_TYPE_TEXT[t])
                st.checkbox("Required (rows without it are left out of the master)", key=f"s_f_{key}_required")
                st.text_area("Words that identify the question (one per line)", key=f"s_f_{key}_keywords", height=90)
                st.multiselect("Cleaning steps", STEP_ORDER, key=f"s_f_{key}_steps", format_func=lambda c: STEP_DESCRIPTIONS[c])
                st.button("Remove this field", key=f"s_rm_{key}", on_click=_remove_field, args=(key,), disabled=len(field_keys) < 2)
        st.markdown("**Add a field**")
        st.text_input("Name of the new field", key="s_new_label", placeholder="e.g. Grade")
        st.selectbox("Kind of information", settings_form.FIELD_TYPES, key="s_new_type",
                     format_func=lambda t: settings_form.FIELD_TYPE_TEXT[t])
        st.button("Add field", on_click=_add_field)

    with st.expander("Cleaning rules"):
        st.text_area("Words that mean 'no answer' (one per line)", key="s_placeholders", height=90)
        st.text_input("Default country for phone numbers (two letters, e.g. PK, IN, AE, GB)", key="s_country")
        st.checkbox("Flag landline numbers", key="s_flag_landlines")
        st.text_area("Short city names (short = full)", key="s_city_aliases", height=90)
        st.text_area("School abbreviations (short = full)", key="s_school_abbr", height=110)
        st.text_area("Known email typos (typo = correct)", key="s_email_typos", height=110)
        st.checkbox("Correct those email typos automatically (otherwise just flag them)", key="s_autofix_typos")
        st.slider("How alike two school names must be to be flagged as possible duplicates", min_value=0.5, max_value=1.0,
                  step=0.01, key="s_school_similarity")
        st.multiselect("Leave a row out of the master (instead of just flagging it) when:", list(FLAG_DESCRIPTIONS),
                       key="s_reject_flags", format_func=lambda c: FLAG_DESCRIPTIONS[c])

    with st.expander("Matching questions to fields"):
        st.slider("Be sure (no check needed) when the match is at least", min_value=0.0, max_value=1.0, step=0.01, key="s_auto_threshold")
        st.slider("Suggest a match when it is at least", min_value=0.0, max_value=1.0, step=0.01, key="s_suggest_threshold")

    with st.expander("Duplicates"):
        st.checkbox("Remove repeat submissions from the master", key="s_dedupe_enabled")
        options = [settings_form.NONE, *field_keys]
        names = {k: ss.get(f"s_f_{k}_label", k) for k in field_keys}
        names[settings_form.NONE] = "(nothing)"
        st.selectbox("Match people by", options, key="s_dedupe_primary", format_func=lambda k: names.get(k, k))
        st.selectbox("If that is empty, match by", options, key="s_dedupe_secondary", format_func=lambda k: names.get(k, k))
        st.selectbox("When someone appears twice", list(settings_form.KEEP_CHOICES), key="s_dedupe_keep",
                     format_func=lambda k: settings_form.KEEP_CHOICES[k])

    with st.expander("Output"):
        st.text_input("Master file name", key="s_master_name")
        st.checkbox("Add Source, Fetched At and Record Status columns", key="s_include_system")
        st.checkbox("Use the field names as column headings", key="s_labels_headers")
        st.number_input("Backups of the master to keep", min_value=0, max_value=100, step=1, key="s_backups")

    with st.expander("Workflow"):
        st.checkbox("Show the detailed preview (master, rejected rows, flagged rows)", key="s_preview")
        st.checkbox("Ask for confirmation before saving", key="s_require_confirm",
                    help="If off, the master is saved automatically when every link worked. If a link failed you are still asked.")

    left, right = st.columns(2)
    left.button("Save settings", type="primary", on_click=_save_settings)
    right.button("Discard unsaved changes", on_click=_discard_changes)

    with st.expander("Earlier versions, backup and reset"):
        history = store.history()
        if history:
            by_name = {e.path.name: e for e in history}
            if ss.get("s_restore_choice") not in by_name:
                ss.s_restore_choice = history[0].path.name
            st.selectbox("Earlier version", list(by_name), key="s_restore_choice", format_func=lambda n: by_name[n].label)
            for line in describe_changes(current, by_name[ss.s_restore_choice].settings):
                st.caption(line)
            st.button("Restore this version", on_click=_restore, args=(ss.s_restore_choice,))
        else:
            st.caption("No earlier versions yet. Each time you save, the version being replaced is kept here.")
        st.markdown("---")
        st.download_button("Download the current settings", data=store.export_json(current), file_name="settings.json", mime="application/json")
        st.file_uploader("Use a settings file", type="json", key="s_upload")
        st.button("Use the uploaded file", on_click=_import_uploaded)
        st.caption("Settings are saved on the server. On Streamlit Community Cloud they can reset when the app restarts, "
                   "so download a copy of settings you want to keep.")
        st.markdown("---")
        sure = st.checkbox("I'm sure I want the built-in defaults back", key="s_reset_sure")
        st.button("Reset to the built-in defaults", on_click=_reset_to_defaults, disabled=not sure)


# ------------------------------------------------------------------ page

def main() -> None:
    st.set_page_config(page_title="Forms Data Portal", page_icon="📋", layout="wide", initial_sidebar_state="collapsed")
    store = _settings_store()
    try:
        settings = store.load()
    except SettingsError as exc:
        st.error(f"The portal's settings have a problem: {exc}")
        st.stop()
        return
    init_state()
    init_settings_widgets(settings)

    st.title("Forms Data Portal")
    st.caption("Paste your Google Form links, check the cleaned results, then save one combined file.")
    if store.last_error:
        st.warning(store.last_error)

    tab_collect, tab_settings = st.tabs(["Collect data", "Settings"])
    with tab_collect:
        section_sources(settings)
        ss = st.session_state
        if ss.results:
            pending = view.needs_confirmation(ss.results, ss.confirmed)
            if pending:
                section_confirm(settings, pending)
            else:
                maybe_prepare(settings)
                if ss.prepared is not None:
                    section_review(settings)
                if ss.prepared is not None:   # a "Change these" click inside the review can clear it
                    section_save(settings)
    with tab_settings:
        section_settings(settings)
    st.caption(f"Version {__version__}")


if __name__ == "__main__":
    main()
