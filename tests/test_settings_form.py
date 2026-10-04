import copy

from portal.cleaner.steps import STEP_ORDER, ordered_steps
from portal.config import SettingsError, load_settings, validate_settings
from portal.ui.settings_form import (
    NONE, SettingsInputError, field_order, field_widget_values, format_aliases, new_field, parse_aliases, parse_lines,
    settings_from_widgets, slugify, widget_values,
)

S = load_settings()


def rebuild(values=None, order=None, base=S):
    v = widget_values(base)
    v.update(values or {})
    return settings_from_widgets(v, field_order(base) if order is None else order, base)


def test_untouched_screen_gives_back_identical_settings():
    assert rebuild() == S


def test_lists_and_aliases_parse_forgivingly():
    assert parse_lines("n/a\n none ,NA\n\n-\nN/A") == ["n/a", "none", "NA", "-"]
    assert parse_aliases("khi = Karachi\nlhr -> Lahore\nisb: Islamabad\n\n", "city") == {"khi": "Karachi", "lhr": "Lahore", "isb": "Islamabad"}
    assert parse_aliases(format_aliases({"a": "b"}), "x") == {"a": "b"}


def test_a_bad_alias_line_is_explained():
    try:
        parse_aliases("khi = Karachi\njust some words", "city names")
    except SettingsInputError as exc:
        assert "Line 2" in str(exc) and "city names" in str(exc)
    else:
        raise AssertionError("expected SettingsInputError")


def test_editing_widgets_changes_the_settings():
    out = rebuild({
        "s_country": " ae ", "s_city_aliases": "khi = Karachi\nrwp = Rawalpindi", "s_autofix_typos": True,
        "s_reject_flags": ["name_random", "email_domain_typo"], "s_dedupe_enabled": True, "s_dedupe_keep": "most_complete",
        "s_master_name": "students.csv", "s_include_system": False, "s_preview": False, "s_require_confirm": False, "s_backups": 9,
        "s_auto_threshold": 0.9, "s_suggest_threshold": 0.5,
    })
    assert out["cleaning"]["default_country_code"] == "AE"
    assert out["cleaning"]["city_aliases"]["rwp"] == "Rawalpindi"
    assert out["cleaning"]["reject_flags"] == ["name_random", "email_domain_typo"]
    assert out["dedupe"] == {"enabled": True, "key": ["email", "whatsapp_contact"], "keep": "most_complete"}
    assert out["output"]["master_file_name"] == "students.csv" and out["output"]["include_system_columns"] is False
    assert out["workflow"] == {"preview_enabled": False, "require_confirmation": False}
    assert out["mapping"] == {"auto_threshold": 0.9, "suggest_threshold": 0.5}
    validate_settings(out)


def test_cleaning_steps_always_come_out_in_a_safe_order():
    scrambled = ["validate_email", "lowercase", "trim", "tidy_email", "flag_domain_typos"]
    out = rebuild({"s_f_email_steps": scrambled})
    email = next(f for f in out["fields"] if f["key"] == "email")
    assert email["cleaning"] == ["trim", "tidy_email", "lowercase", "validate_email", "flag_domain_typos"]
    assert ordered_steps(["flag_domain_typos", "trim"]) == [s for s in STEP_ORDER if s in ("trim", "flag_domain_typos")]


def test_field_edits_keywords_and_required():
    out = rebuild({"s_f_city_label": " Home Town ", "s_f_city_required": False, "s_f_city_keywords": "city\ntown\nhome town"})
    city = next(f for f in out["fields"] if f["key"] == "city")
    assert city["label"] == "Home Town" and city["required"] is False and city["keywords"] == ["city", "town", "home town"]


def test_add_and_remove_fields():
    taken = set(field_order(S))
    grade = new_field("Grade / Class", "text", taken)
    assert grade["key"] == "grade_class" and grade["required"] is False and "title_case" in grade["cleaning"]
    base_values = widget_values(S)
    base_values.update(field_widget_values(grade))
    out = settings_from_widgets(base_values, [*field_order(S), grade["key"]], S)
    assert [f["key"] for f in out["fields"]][-1] == "grade_class"
    validate_settings(out)

    removed = settings_from_widgets(widget_values(S), [k for k in field_order(S) if k != "city"], S)
    assert "city" not in [f["key"] for f in removed["fields"]]


def test_removing_a_field_used_for_matching_duplicates_is_caught_on_save():
    out = settings_from_widgets(widget_values(S), [k for k in field_order(S) if k != "email"], S)   # email is still the dedupe key
    try:
        validate_settings(out)
    except SettingsError as exc:
        assert "email" in str(exc)
    else:
        raise AssertionError("expected SettingsError")
    fixed = settings_from_widgets({**widget_values(S), "s_dedupe_primary": "whatsapp_contact", "s_dedupe_secondary": NONE},
                                  [k for k in field_order(S) if k != "email"], S)
    assert fixed["dedupe"]["key"] == ["whatsapp_contact"]
    validate_settings(fixed)


def test_slugify_is_unique_and_safe():
    assert slugify("Parent's Phone #", set()) == "parent_s_phone"
    assert slugify("City", {"city"}) == "city_2"
    assert slugify("2nd Choice", set()) == "f_2nd_choice"
    assert slugify("!!!", set()) == "field"


def test_new_field_needs_a_name():
    try:
        new_field("  ", "text", set())
    except SettingsInputError:
        pass
    else:
        raise AssertionError("expected SettingsInputError")


def test_blank_field_name_and_zero_fields_refused():
    for values, order in [({"s_f_city_label": "  "}, None), ({}, [])]:
        try:
            rebuild(values, order)
        except SettingsInputError:
            continue
        raise AssertionError("expected SettingsInputError")


def test_extra_settings_not_on_the_screen_are_kept():
    base = copy.deepcopy(S)
    base["run"]["max_parallel_links"] = 7
    base["system_columns"] = ["source_label", "fetched_at", "record_status"]
    assert rebuild(base=base)["run"]["max_parallel_links"] == 7
