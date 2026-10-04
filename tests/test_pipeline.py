import copy
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from portal.config import load_settings
from portal.connector.models import LinkResult, LinkStatus
from portal.mapper import MappingStore
from portal.pipeline import CommitBlocked, commit_run, prepare_run
from portal.storage import CsvStorage

S = load_settings()
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)


def raw_table(rows, extra_cols=None, headers=("Full Name", "WhatsApp Contact", "City", "School Name")):
    cols = ["response_id", "submitted_at", "respondent_email", *headers]
    return pd.DataFrame(rows, columns=cols, dtype=str)


def result(form_id, title, rows, **kw):
    table = raw_table(rows, **kw)
    status = LinkStatus.READY if rows else LinkStatus.EMPTY
    return LinkResult(url=f"https://docs.google.com/forms/d/{form_id}/edit", status=status, form_id=form_id,
                      title=title, row_count=len(table), table=table)


FORM_A = [
    ("a1", "2026-01-01T10:00:00Z", "ali@gmail.com", "aLI raza", "0314 1837972", "islamabad", "imcb pakistan town"),
    ("a2", "2026-01-02T10:00:00Z", "sara@gmial.com", "SARA KHAN", "+92 333 6150855", "KHI", "govt girls high sch"),
    ("a3", "2026-01-03T10:00:00Z", "junk@gmail.com", "dc", "cd", "cd", "cd"),                      # rejected
]
FORM_B = [
    ("b1", "2026-02-01T10:00:00Z", "hina@gmail.com", "Hina Noor", "0300 1234567", "Quetta", "Boys College"),
    ("b2", "2026-02-09T10:00:00Z", "ali@gmail.com", "Ali Raza", "03141837972", "Islamabad", "Imcb Pakistan Town"),  # same person as a1
]


def run(results, settings=S, **kw):
    return prepare_run(results, settings, now=NOW, **kw)


def failed(status, msg="nope"):
    return LinkResult(url="https://docs.google.com/forms/d/xxxxxxxxxxxxxxxxxxxxx/edit", status=status, message=msg, form_id="x" * 21)


# ------------------------------------------------------------------ building the master

def test_two_forms_combine_into_one_master_without_rejected_rows():
    prep = run([result("A" * 20, "Grade 9", FORM_A), result("B" * 20, "Grade 10", FORM_B)])
    m = prep.master
    assert prep.can_commit and len(m) == 4
    assert list(m.columns) == ["full_name", "whatsapp_contact", "city", "school_name", "email", "source_label", "fetched_at", "record_status"]
    assert list(m["source_label"]) == ["Grade 9", "Grade 9", "Grade 10", "Grade 10"]
    assert set(m["fetched_at"]) == {"2026-10-02T09:00:00Z"}
    assert "dc" not in set(m["full_name"])
    assert m.iloc[0].email == "ali@gmail.com" and m.iloc[0].whatsapp_contact == "+923141837972"


def test_summary_lines_describe_each_form():
    prep = run([result("A" * 20, "Grade 9", FORM_A)])
    lines = prep.summary_lines()
    assert lines[0] == "Grade 9: 3 fetched, 2 accepted, 1 rejected"
    assert lines[-1].startswith("Master would have 2 rows")


def test_dedupe_off_by_default_keeps_repeat_submissions():
    prep = run([result("A" * 20, "Grade 9", FORM_A), result("B" * 20, "Grade 10", FORM_B)])
    assert len(prep.duplicates_removed) == 0 and list(prep.master["email"]).count("ali@gmail.com") == 2


def test_dedupe_when_switched_on_removes_across_forms_and_keeps_newest():
    s2 = copy.deepcopy(S)
    s2["dedupe"]["enabled"] = True
    prep = run([result("A" * 20, "Grade 9", FORM_A), result("B" * 20, "Grade 10", FORM_B)], s2)
    assert len(prep.master) == 3 and len(prep.duplicates_removed) == 1
    ali = prep.master[prep.master["email"] == "ali@gmail.com"].iloc[0]
    assert ali.source_label == "Grade 10"
    assert "1 duplicates" in prep.summary_lines()[-1]


def test_system_columns_can_be_switched_off():
    s2 = copy.deepcopy(S)
    s2["output"]["include_system_columns"] = False
    prep = run([result("A" * 20, "Grade 9", FORM_A)], s2)
    assert list(prep.master.columns) == ["full_name", "whatsapp_contact", "city", "school_name", "email"]


def test_custom_labels_name_the_source():
    prep = run([result("A" * 20, "Long Google title", FORM_A)], labels={"A" * 20: "Grade 9 - Chemistry"})
    assert set(prep.master["source_label"]) == {"Grade 9 - Chemistry"}


# ------------------------------------------------------------------ problems that must not silently drop data

def test_failed_link_blocks_saving_unless_allowed():
    prep = run([result("A" * 20, "Grade 9", FORM_A), failed(LinkStatus.NO_ACCESS, "can't open")])
    assert not prep.can_commit and any("No access" in b for b in prep.blockers)
    with tempfile.TemporaryDirectory() as d:
        st = CsvStorage(d)
        try:
            commit_run(prep, st, S)
        except CommitBlocked as exc:
            assert "data out of the master" in str(exc)
        else:
            raise AssertionError("expected CommitBlocked")
        assert st.load_master() is None
        commit_run(prep, st, S, allow_partial=True)
        assert len(st.load_master()) == 2


def test_duplicate_link_and_empty_form_are_warnings_not_blockers():
    dup = LinkResult(url="u", status=LinkStatus.DUPLICATE, message="Same form as link #1, so it was skipped.", form_id="A" * 20)
    prep = run([result("A" * 20, "Grade 9", FORM_A), dup, result("E" * 20, "Empty form", [])])
    assert prep.can_commit and not prep.blockers and len(prep.warnings) == 2


def test_form_needing_mapping_review_is_skipped_and_blocks():
    odd = result("C" * 20, "Odd form", [("c1", "2026-01-01T10:00:00Z", "", "Zed", "03001234567", "Lahore", "X")], headers=("Zed Name", "Phone", "City", "School"))
    # remove the name column so the Full Name field cannot be found
    odd.table = odd.table.drop(columns=["Zed Name"])
    prep = run([result("A" * 20, "Grade 9", FORM_A), odd])
    assert any("confirmed" in b for b in prep.blockers) and not prep.can_commit
    assert [o.status for o in prep.outcomes] == ["Processed", "Skipped"]
    assert len(prep.master) == 2
    forced = run([result("A" * 20, "Grade 9", FORM_A), odd], include_unreviewed=True)
    assert forced.outcomes[1].status == "Processed"


def test_nothing_includable_cannot_be_committed():
    prep = run([failed(LinkStatus.ERROR, "Google was busy")])
    assert not prep.can_commit
    with tempfile.TemporaryDirectory() as d:
        try:
            commit_run(prep, CsvStorage(d), S, allow_partial=True)
        except CommitBlocked as exc:
            assert "nothing to save" in str(exc)
        else:
            raise AssertionError("expected CommitBlocked")


def test_saved_mapping_makes_a_review_form_includable():
    odd_rows = [("c1", "2026-01-01T10:00:00Z", "z@x.com", "Zed", "03001234567", "Lahore", "City School")]
    odd = result("C" * 20, "Odd form", odd_rows, headers=("Student", "Mobile", "Town", "Institute"))
    first = run([odd])
    assert first.outcomes[0].status == "Skipped"
    from portal.mapper import result_from_columns
    chosen = result_from_columns(S, {"full_name": "Student", "whatsapp_contact": "Mobile", "city": "Town", "school_name": "Institute",
                                     "email": "respondent_email"}, form_id="C" * 20)
    again = run([odd], mapping_overrides={"C" * 20: chosen})
    assert again.can_commit and again.master.iloc[0].full_name == "Zed"


# ------------------------------------------------------------------ saving

def test_commit_writes_all_layers_and_master_with_friendly_headers():
    prep = run([result("A" * 20, "Grade 9", FORM_A), result("B" * 20, "Grade 10", FORM_B)])
    with tempfile.TemporaryDirectory() as d:
        st = CsvStorage.from_settings(d, S)
        res = commit_run(prep, st, S)
        assert res.saved_forms == 2 and res.master.rows == 4
        for layer in ("raw", "mapped", "cleaned", "rejected"):
            assert st.list_tables(layer) == ["A" * 20, "B" * 20], layer
        assert len(st.load_table("rejected", "A" * 20)) == 1 and "reject_reason" in st.load_table("rejected", "A" * 20).columns
        master = st.load_master()
        assert list(master.columns)[:5] == ["Full Name", "WhatsApp Contact", "City", "School Name", "Email"]
        assert (Path(d) / "master.csv").exists()


def test_running_twice_rebuilds_instead_of_doubling_and_keeps_a_backup():
    results = [result("A" * 20, "Grade 9", FORM_A), result("B" * 20, "Grade 10", FORM_B)]
    with tempfile.TemporaryDirectory() as d:
        st = CsvStorage.from_settings(d, S)
        commit_run(run(results), st, S)
        first = st.load_master()
        later = prepare_run(results, S, now=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc))
        info = commit_run(later, st, S).master
        second = st.load_master()
        assert len(second) == len(first) == 4
        assert first.drop(columns=["Fetched At"]).equals(second.drop(columns=["Fetched At"]))
        assert info.backup is not None and info.backup.exists()


def test_duplicates_layer_saved_only_when_there_are_any():
    s2 = copy.deepcopy(S)
    s2["dedupe"]["enabled"] = True
    with tempfile.TemporaryDirectory() as d:
        st = CsvStorage.from_settings(d, s2)
        commit_run(run([result("A" * 20, "Grade 9", FORM_A), result("B" * 20, "Grade 10", FORM_B)], s2), st, s2)
        assert st.list_tables("duplicates") == ["removed_duplicates"]


def test_saved_mappings_are_used_by_the_pipeline():
    with tempfile.TemporaryDirectory() as d:
        store = MappingStore(Path(d) / "m.json")
        res = result("A" * 20, "Grade 9", FORM_A)
        prep = run([res], mapping_store=store)
        store.save("A" * 20, prep.outcomes[0].mapping)
        again = run([res], mapping_store=store)
        assert again.outcomes[0].mapping.status.value == "Saved"
