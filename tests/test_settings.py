import copy
import json

from portal.config import load_settings, validate_settings, field_keys, master_columns, SettingsError


def _expect_error(settings):
    try:
        validate_settings(settings)
    except SettingsError:
        return True
    return False


def test_default_settings_load():
    s = load_settings()
    assert field_keys(s) == ["full_name", "whatsapp_contact", "city", "school_name", "email"]


def test_all_five_fields_are_required():
    s = load_settings()
    assert all(f["required"] for f in s["fields"])


def test_master_columns_with_and_without_system_columns():
    s = load_settings()
    assert master_columns(s)[-3:] == ["source_label", "fetched_at", "record_status"]
    s["output"]["include_system_columns"] = False
    assert master_columns(s) == field_keys(s)


def test_unknown_field_type_rejected():
    s = load_settings()
    s["fields"][0]["type"] = "banana"
    assert _expect_error(s)


def test_duplicate_field_key_rejected():
    s = load_settings()
    s["fields"][1] = copy.deepcopy(s["fields"][0])
    assert _expect_error(s)


def test_bad_dedupe_keep_rejected():
    s = load_settings()
    s["dedupe"]["keep"] = "random"
    assert _expect_error(s)


def test_dedupe_key_must_be_a_defined_field():
    s = load_settings()
    s["dedupe"]["key"] = ["nonexistent"]
    assert _expect_error(s)


def test_missing_section_rejected():
    s = load_settings()
    del s["cleaning"]
    assert _expect_error(s)


def test_missing_file_gives_plain_error(tmp_path=None):
    try:
        load_settings("/no/such/file.json")
    except SettingsError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected SettingsError")


def test_custom_settings_file_is_used(tmp_path=None):
    import tempfile, os
    s = load_settings()
    s["output"]["master_file_name"] = "custom.csv"
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        with open(p, "w") as fh:
            json.dump(s, fh)
        assert load_settings(p)["output"]["master_file_name"] == "custom.csv"
