import copy
import json
import os
import tempfile
from pathlib import Path

from portal.config import SettingsError, SettingsStore, describe_changes, load_settings, validate_settings


def _edit(settings, **cleaning):
    s = copy.deepcopy(settings)
    s["cleaning"].update(cleaning)
    return s


def test_no_file_means_shipped_defaults():
    with tempfile.TemporaryDirectory() as d:
        st = SettingsStore(d)
        assert st.load() == load_settings() and st.last_error is None and st.history() == []


def test_save_bumps_version_and_keeps_the_old_one():
    with tempfile.TemporaryDirectory() as d:
        st = SettingsStore(d)
        base = st.load()
        saved = st.save(_edit(base, default_country_code="AE"))
        assert saved["settings_version"] == base["settings_version"] + 1
        assert st.load()["cleaning"]["default_country_code"] == "AE"
        history = st.history()
        assert len(history) == 1 and history[0].version == base["settings_version"]
        assert history[0].settings["cleaning"]["default_country_code"] == "PK"


def test_restore_brings_back_an_old_version_and_can_itself_be_undone():
    with tempfile.TemporaryDirectory() as d:
        st = SettingsStore(d)
        st.save(_edit(st.load(), default_country_code="AE"))
        st.save(_edit(st.load(), default_country_code="IN"))
        oldest = st.history()[-1]
        assert oldest.settings["cleaning"]["default_country_code"] == "PK"
        restored = st.restore(oldest)
        assert restored["cleaning"]["default_country_code"] == "PK" and restored["settings_version"] == 4
        assert any(h.settings["cleaning"]["default_country_code"] == "IN" for h in st.history())   # undo is possible


def test_invalid_settings_are_refused_and_nothing_changes():
    with tempfile.TemporaryDirectory() as d:
        st = SettingsStore(d)
        bad = _edit(st.load(), default_country_code="Pakistan")
        try:
            st.save(bad)
        except SettingsError as exc:
            assert "two-letter" in str(exc)
        else:
            raise AssertionError("expected SettingsError")
        assert not (Path(d) / "settings.json").exists() and st.history() == []


def test_value_checks_have_plain_messages():
    s = load_settings()
    for mutate, words in [
        (lambda x: x["output"].update(master_file_name="a/b.csv"), "master file name"),
        (lambda x: x["output"].update(master_file_name="master"), "master file name"),
        (lambda x: x["mapping"].update(auto_threshold=0.4, suggest_threshold=0.6), "suggest"),
        (lambda x: x["cleaning"].update(school_similarity_threshold=0.2), "similarity"),
        (lambda x: x["output"].update(backups_to_keep=-1), "backups"),
    ]:
        bad = copy.deepcopy(s)
        mutate(bad)
        try:
            validate_settings(bad)
        except SettingsError as exc:
            assert words in str(exc).lower(), (words, str(exc))
        else:
            raise AssertionError(words)


def test_damaged_saved_file_falls_back_to_defaults_with_a_message():
    with tempfile.TemporaryDirectory() as d:
        st = SettingsStore(d)
        st.save(_edit(st.load(), default_country_code="AE"))
        Path(d, "settings.json").write_text("{not json")
        assert st.load() == load_settings() and "could not be used" in st.last_error


def test_damaged_history_file_is_skipped():
    with tempfile.TemporaryDirectory() as d:
        st = SettingsStore(d)
        st.save(_edit(st.load(), default_country_code="AE"))
        Path(d, "settings_history", "settings_20200101_000000_000000_v9.json").write_text("garbage")
        assert len(st.history()) == 1


def test_history_is_pruned():
    with tempfile.TemporaryDirectory() as d:
        st = SettingsStore(d, keep_history=3)
        for code in ["AE", "IN", "GB", "US", "CA", "DE"]:
            st.save(_edit(st.load(), default_country_code=code))
        assert len(st.history()) == 3


def test_reset_to_defaults_is_a_new_undoable_version():
    with tempfile.TemporaryDirectory() as d:
        st = SettingsStore(d)
        st.save(_edit(st.load(), default_country_code="AE"))
        after = st.reset_to_defaults()
        assert after["cleaning"]["default_country_code"] == "PK" and after["settings_version"] == 3
        assert st.history()[0].settings["cleaning"]["default_country_code"] == "AE"


def test_export_and_import_roundtrip_and_bad_files():
    with tempfile.TemporaryDirectory() as d:
        st = SettingsStore(d)
        exported = json.loads(st.export_json())
        exported["cleaning"]["default_country_code"] = "GB"
        assert st.import_json(json.dumps(exported))["cleaning"]["default_country_code"] == "GB"
        for junk in ["not json at all", "[1, 2]", '{"fields": []}']:
            try:
                st.import_json(junk)
            except SettingsError:
                continue
            raise AssertionError(junk)


def test_describe_changes_is_readable():
    a = load_settings()
    b = copy.deepcopy(a)
    b["cleaning"]["default_country_code"] = "AE"
    b["fields"][2]["required"] = False
    b["fields"][0]["keywords"].append("candidate")
    lines = describe_changes(a, b)
    assert "cleaning.default_country_code: PK → AE" in lines
    assert any(l.startswith("fields[city].required: true → false") for l in lines)
    assert describe_changes(a, a) == []


def test_unicode_survives_saving():
    with tempfile.TemporaryDirectory() as d:
        st = SettingsStore(d)
        s = st.load()
        s["cleaning"]["city_aliases"]["کراچی"] = "Karachi"
        st.save(s)
        assert st.load()["cleaning"]["city_aliases"]["کراچی"] == "Karachi"
