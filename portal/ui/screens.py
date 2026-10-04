"""The Streamlit screens: Add forms -> Review -> Saved and download.

Everything that can be tested without a browser lives in the other `portal.ui` modules and in `portal.pipeline`;
this file only draws the pages and wires buttons to them.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from portal.auth import AuthSetupError, AuthTokenError, get_forms_service
from portal.config import load_settings
from portal.mapper import MappingStatus, MappingStore
from portal.pipeline import CommitBlocked, commit_run, prepare_run
from portal.storage import CsvStorage, StorageBusyError

from .exports import cleaned_all, csv_bytes, link_summary, mapping_rows, rejected_all
from .help import EDIT_LINK_GUIDE, STORAGE_NOTE
from .runner import fetch_sources
from .sources import add_sources, problem_count, remove_source

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STEPS = {"sources": "Step 1 of 3 · Add your forms", "review": "Step 2 of 3 · Review", "done": "Step 3 of 3 · Saved"}


# ------------------------------------------------------------------ state

def init_state() -> None:
    defaults = {"sources": [], "next_id": 1, "stage": "sources", "notices": [], "snapshot": [], "results": [],
                "prepared": None, "prepared_flag": None, "commit": None}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _go(stage: str) -> None:
    st.session_state["stage"] = stage


def _reset_run() -> None:
    for key in ("snapshot", "results", "prepared", "prepared_flag", "commit"):
        st.session_state[key] = [] if key in ("snapshot", "results") else None
    st.session_state["stage"] = "sources"


def _on_add() -> None:
    sources, next_id, notices = add_sources(st.session_state["sources"], st.session_state.get("paste_box", ""), st.session_state["next_id"])
    st.session_state.update(sources=sources, next_id=next_id, notices=notices, paste_box="")


def _on_remove(source_id: int) -> None:
    st.session_state["sources"] = remove_source(st.session_state["sources"], source_id)


def _on_clear() -> None:
    st.session_state["sources"] = []


# ------------------------------------------------------------------ pieces

def _show_notices() -> None:
    for note in st.session_state.get("notices", []):
        st.info(note)
    st.session_state["notices"] = []


def _metrics(items: list[tuple[str, object]]) -> None:
    for col, (label, value) in zip(st.columns(len(items)), items):
        with col:
            st.metric(label, value)


# ------------------------------------------------------------------ step 1

def sources_stage(settings: dict, data_dir: Path) -> None:
    st.header("Add your forms")
    with st.expander("Where do I find a form's edit link?"):
        st.markdown(EDIT_LINK_GUIDE)

    st.text_area("Paste one or more Google Form edit links", key="paste_box", height=110,
                 placeholder="https://docs.google.com/forms/d/…/edit")
    st.button("Add to list", key="add_btn", type="primary", on_click=_on_add)
    _show_notices()

    sources = st.session_state["sources"]
    if not sources:
        st.caption("Your list is empty. Paste a link above and press **Add to list**.")
        return

    st.subheader(f"Your forms ({len(sources)})")
    for src in sources:
        c_label, c_link, c_status, c_remove = st.columns([3, 4, 3, 1])
        with c_label:
            src.label = st.text_input("Label", value=src.label, key=f"label_{src.id}", placeholder="Label (optional)",
                                      label_visibility="collapsed").strip()
        with c_link:
            st.caption(src.short_url)
        with c_status:
            st.markdown(src.badge)
        with c_remove:
            st.button("✕", key=f"rm_{src.id}", on_click=_on_remove, args=(src.id,))
        if src.message and src.status is not None:
            st.caption(src.message)

    bad = problem_count(sources)
    if bad:
        st.warning(f"{bad} link(s) can't be used as pasted (see the badges above). Remove them, or paste the form's edit link instead.")

    go, clear = st.columns([2, 1])
    with go:
        pressed = st.button("Check links and review", key="fetch_btn", type="primary")
    with clear:
        st.button("Clear list", key="clear_btn", on_click=_on_clear)
    if pressed:
        _run_fetch(settings, data_dir)


def _run_fetch(settings: dict, data_dir: Path) -> None:
    sources = st.session_state["sources"]
    try:
        service = get_forms_service()
    except (AuthSetupError, AuthTokenError) as exc:
        st.error(f"The portal can't sign in to Google right now. {exc}")
        return
    except Exception as exc:
        st.error(f"Something went wrong while connecting to Google: {exc}")
        return

    bar = st.progress(0.0, text="Starting…")

    def tick(i, total, src, res):
        bar.progress(i / total, text=f"Checked {i} of {total}: {src.label or res.title or src.short_url}")

    try:
        results = fetch_sources(service, sources, progress=tick)
    except Exception as exc:
        bar.empty()
        st.error(f"Something went wrong while reading your forms: {exc}")
        return
    bar.empty()

    st.session_state["snapshot"] = [type(s)(**vars(s)) for s in sources]
    st.session_state["results"] = results
    st.session_state["prepared"] = None
    st.session_state["prepared_flag"] = None
    st.session_state["stage"] = "review"
    st.rerun()


# ------------------------------------------------------------------ step 2

def _prepare(settings: dict, data_dir: Path, include_unreviewed: bool):
    snapshot = st.session_state["snapshot"]
    labels = {s.form_id: s.label for s in snapshot if s.form_id and s.label}
    return prepare_run(st.session_state["results"], settings, mapping_store=MappingStore(data_dir / "mappings.json"),
                       labels=labels, include_unreviewed=include_unreviewed)


def review_stage(settings: dict, data_dir: Path) -> None:
    st.header("Review before saving")
    st.button("← Back to the list", key="back_btn", on_click=_go, args=("sources",))

    flag = bool(st.session_state.get("include_unreviewed_cb", False))
    if st.session_state["prepared"] is None or st.session_state["prepared_flag"] != flag:
        with st.spinner("Cleaning the data…"):
            st.session_state["prepared"] = _prepare(settings, data_dir, flag)
        st.session_state["prepared_flag"] = flag
    prepared = st.session_state["prepared"]

    if not settings["workflow"].get("preview_enabled", True) and prepared.can_commit:
        _save(settings, data_dir, prepared, allow_partial=False)
        return

    included = prepared.included
    cleaned = [o.cleaning.report for o in included if o.cleaning]
    _metrics([
        ("Forms included", len(included)),
        ("Responses fetched", sum(o.fetched_rows for o in included)),
        ("Accepted", sum(r.valid + r.fixed for r in cleaned)),
        ("Rejected", sum(r.rejected for r in cleaned)),
        ("Rows in master", len(prepared.master)),
    ])

    for blocker in prepared.blockers:
        st.error(blocker)
    for warning in prepared.warnings:
        st.warning(warning)

    skipped = [o for o in prepared.outcomes if o.status == "Skipped"]
    if skipped:
        st.checkbox("Include the forms whose fields are uncertain anyway (check the 'How columns were matched' tables first)",
                    key="include_unreviewed_cb")

    st.subheader("What happened to each link")
    st.dataframe(link_summary(st.session_state["snapshot"], st.session_state["results"], prepared), hide_index=True)

    for o in included:
        if o.cleaning is None:
            continue
        r = o.cleaning.report
        with st.expander(f"{o.label}: {r.valid + r.fixed} accepted, {r.rejected} rejected"):
            st.markdown("**How columns were matched**")
            st.dataframe(mapping_rows(o, settings), hide_index=True)
            st.markdown("**Cleaning summary**")
            st.text("\n".join(r.summary_lines(settings)))
            if len(o.cleaning.rejected):
                st.markdown("**Rejected rows** (not in the master)")
                st.dataframe(o.cleaning.rejected, hide_index=True)
            flagged = o.cleaning.accepted[o.cleaning.accepted["flags"] != ""]
            if len(flagged):
                st.markdown("**Kept, but worth a look**")
                st.dataframe(flagged, hide_index=True)

    st.subheader("Preview of the master file")
    st.dataframe(prepared.master.head(100), hide_index=True)
    if len(prepared.master) > 100:
        st.caption(f"Showing the first 100 of {len(prepared.master)} rows.")

    allow_partial = False
    if prepared.blockers and included:
        allow_partial = st.checkbox("Save anyway. Data from the links with problems will be missing from the master.",
                                    key="allow_partial_cb")
    can_save = bool(included) and (not prepared.blockers or allow_partial)
    if st.button("Save to master", key="save_btn", type="primary", disabled=not can_save):
        _save(settings, data_dir, prepared, allow_partial=allow_partial)
    if not included:
        st.info("No form can be included yet. Go back, fix the links shown above, and try again.")


def _save(settings: dict, data_dir: Path, prepared, allow_partial: bool) -> None:
    storage = CsvStorage.from_settings(data_dir, settings)
    try:
        info = commit_run(prepared, storage, settings, allow_partial=allow_partial)
    except (CommitBlocked, StorageBusyError) as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"The master could not be saved: {exc}")
        return

    store = MappingStore(data_dir / "mappings.json")
    for o in prepared.outcomes:  # remember mappings that were detected with confidence, so the next run needs no checking
        if o.status == "Processed" and o.mapping and not o.mapping.needs_attention and o.mapping.status != MappingStatus.SAVED:
            store.save(o.form_id, o.mapping)

    st.session_state["commit"] = {
        "rows": info.master.rows, "forms": info.saved_forms, "path": str(info.master.path),
        "master_bytes": Path(info.master.path).read_bytes(), "master_name": Path(info.master.path).name,
        "cleaned": csv_bytes(cleaned_all(prepared, settings)), "rejected": csv_bytes(rejected_all(prepared, settings)),
        "summary": link_summary(st.session_state["snapshot"], st.session_state["results"], prepared),
        "duplicates": len(prepared.duplicates_removed), "warnings": list(prepared.warnings),
    }
    st.session_state["stage"] = "done"
    st.rerun()


# ------------------------------------------------------------------ step 3

def done_stage(settings: dict, data_dir: Path) -> None:
    done = st.session_state["commit"]
    st.header("Saved")
    if done is None:
        st.info("Nothing has been saved in this session yet.")
        st.button("Start over", key="over_btn", on_click=_reset_run)
        return

    st.success(f"The master file now has {done['rows']} rows from {done['forms']} form(s).")
    if done["duplicates"]:
        st.info(f"{done['duplicates']} repeat submission(s) were removed.")
    for warning in done["warnings"]:
        st.warning(warning)

    st.subheader("Download")
    st.download_button("Download the master CSV", data=done["master_bytes"], file_name=done["master_name"],
                       mime="text/csv", key="dl_master", type="primary")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("Download cleaned data (all forms)", data=done["cleaned"], file_name="cleaned_data.csv",
                           mime="text/csv", key="dl_cleaned")
    with c2:
        st.download_button("Download rejected rows", data=done["rejected"], file_name="rejected_rows.csv",
                           mime="text/csv", key="dl_rejected")
    st.warning(STORAGE_NOTE)

    st.subheader("Run summary")
    st.dataframe(done["summary"], hide_index=True)

    left, right = st.columns(2)
    with left:
        st.button("Run again with the same list", key="again_btn", on_click=_reset_run)
    with right:
        st.button("Start a new list", key="new_btn", on_click=_start_new)


def _start_new() -> None:
    _reset_run()
    st.session_state["sources"] = []


# ------------------------------------------------------------------ entry

def main(settings: dict | None = None, data_dir: Path | None = None) -> None:
    settings = settings or load_settings()
    data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    init_state()

    with st.sidebar:
        st.markdown("### Forms Data Portal")
        st.caption("Pulls Google Form responses into one cleaned master file.")
        st.caption(STORAGE_NOTE)

    stage = st.session_state["stage"]
    st.caption(STEPS.get(stage, ""))
    if stage == "review" and not st.session_state["results"]:
        stage = "sources"
    if stage == "sources":
        sources_stage(settings, data_dir)
    elif stage == "review":
        review_stage(settings, data_dir)
    else:
        done_stage(settings, data_dir)
