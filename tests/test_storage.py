import os
import tempfile
import time
from pathlib import Path

import pandas as pd

from portal.config import load_settings
from portal.storage import CsvStorage, FileLock, StorageBusyError, atomic_write_csv, select_master_columns, to_output_headers

S = load_settings()


def df(rows):
    return pd.DataFrame(rows, columns=["a", "b"], dtype=str)


def test_atomic_write_leaves_no_temp_file_and_roundtrips_urdu():
    with tempfile.TemporaryDirectory() as d:
        st = CsvStorage(d)
        st.save_table("cleaned", "form1", df([("علی خان", "x,y"), ("O'Neil", 'say "hi"')]))
        back = st.load_table("cleaned", "form1")
        assert back.equals(df([("علی خان", "x,y"), ("O'Neil", 'say "hi"')]))
        assert [p.name for p in (Path(d) / "cleaned").iterdir()] == ["form1.csv"]


def test_list_and_missing_tables():
    with tempfile.TemporaryDirectory() as d:
        st = CsvStorage(d)
        assert st.list_tables("raw") == [] and st.load_table("raw", "nope") is None and st.load_master() is None
        st.save_table("raw", "b", df([("1", "2")]))
        st.save_table("raw", "a", df([("1", "2")]))
        assert st.list_tables("raw") == ["a", "b"]


def test_unknown_layer_and_unsafe_name_handled():
    with tempfile.TemporaryDirectory() as d:
        st = CsvStorage(d)
        try:
            st.save_table("secrets", "x", df([]))
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")
        path = st.save_table("raw", "../../evil", df([("1", "2")]))
        assert Path(d) in path.parents


def test_commit_master_backs_up_previous_and_prunes():
    with tempfile.TemporaryDirectory() as d:
        st = CsvStorage(d, backups_to_keep=2)
        with st.write_lock():
            first = st.commit_master(df([("1", "1")]))
            assert first.backup is None
            for i in range(2, 6):
                info = st.commit_master(df([(str(i), str(i))]))
                assert info.backup is not None
        backups = sorted((Path(d) / "backups").glob("master_*.csv"))
        assert len(backups) == 2
        assert st.load_master().equals(df([("5", "5")]))
        assert pd.read_csv(backups[-1], dtype=str, encoding="utf-8-sig").equals(df([("4", "4")]))


def test_lock_blocks_second_writer_then_releases():
    with tempfile.TemporaryDirectory() as d:
        st = CsvStorage(d)
        with st.write_lock():
            try:
                with CsvStorage(d).write_lock():
                    pass
            except StorageBusyError as exc:
                assert "try again" in str(exc)
            else:
                raise AssertionError("expected StorageBusyError")
        with st.write_lock():
            pass  # free again


def test_lock_released_after_error():
    with tempfile.TemporaryDirectory() as d:
        st = CsvStorage(d)
        try:
            with st.write_lock():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        with st.write_lock():
            pass


def test_stale_lock_from_a_crash_is_broken():
    with tempfile.TemporaryDirectory() as d:
        lock_path = Path(d) / ".write.lock"
        lock_path.write_text("999 long ago")
        old = time.time() - 3600
        os.utime(lock_path, (old, old))
        with CsvStorage(d, lock_stale_after=600).write_lock():
            pass


def test_master_columns_and_headers():
    combined = pd.DataFrame([{"response_id": "r1", "submitted_at": "t", "full_name": "Ali", "whatsapp_contact": "+92", "city": "X",
                              "school_name": "S", "email": "a@x.com", "record_status": "Fixed", "source_label": "Form A", "fetched_at": "now"}])
    master = select_master_columns(combined, S)
    assert list(master.columns) == ["full_name", "whatsapp_contact", "city", "school_name", "email", "source_label", "fetched_at", "record_status"]
    out = to_output_headers(master, S)
    assert list(out.columns) == ["Full Name", "WhatsApp Contact", "City", "School Name", "Email", "Source", "Fetched At", "Record Status"]
    import copy
    s2 = copy.deepcopy(S)
    s2["output"]["include_system_columns"] = False
    s2["output"]["use_labels_as_headers"] = False
    plain = to_output_headers(select_master_columns(combined, s2), s2)
    assert list(plain.columns) == ["full_name", "whatsapp_contact", "city", "school_name", "email"]
