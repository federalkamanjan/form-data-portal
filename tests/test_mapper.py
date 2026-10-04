import os
import tempfile

import pandas as pd

from portal.config import load_settings
from portal.mapper import (
    MappingError, MappingStatus, MappingStore, apply_mapping, detect_mapping, propose_mapping, result_from_columns,
)
from portal.mapper.matching import content_score, normalize_title, title_score

S = load_settings()
SYS = ["response_id", "submitted_at", "respondent_email"]

NAMES = ["Ali Raza", "Sara Khan", "Bilal Ahmed"]
PHONES = ["03141837972", "+92 333 6150855", "0300-1234567"]
CITIES = ["Islamabad", "Lahore", "Karachi"]
SCHOOLS = ["IMCB Pakistan Town", "Boys College", "City School"]
EMAILS = ["ali@gmail.com", "sara@yahoo.com", "bilal@outlook.com"]


def _table(columns_values, with_respondent_email=None):
    """columns_values: list of (title, values). Adds the system columns the connector always produces."""
    data = {"response_id": ["r1", "r2", "r3"], "submitted_at": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "respondent_email": with_respondent_email or ["", "", ""]}
    for title, values in columns_values:
        data[title] = values
    return pd.DataFrame(data, dtype=str)


def _cols(result):
    return result.columns()


# ------------------------------------------------------------------ matching helpers

def test_normalize_drops_numbering_and_punctuation():
    assert normalize_title("2.What is your Name?") == "what is your name"
    assert normalize_title("(3) E-mail") == "e mail"


def test_exact_label_scores_one():
    assert title_score("WhatsApp Contact", ["WhatsApp Contact"]) == 1.0


def test_longer_titles_dilute_keyword_hits():
    assert title_score("School Name", ["Full Name", "name"]) < title_score("Full Name", ["Full Name", "name"])
    assert title_score("Name of your school", ["name"]) < 0.85
    assert title_score("Which city hosts the Olympic games this year", ["city"]) < 0.6


def test_typos_still_match():
    assert title_score("Whatsap Contact", ["WhatsApp Contact"]) >= 0.85


def test_content_sniffing_only_for_email_and_phone():
    assert content_score(EMAILS, "email") > 0
    assert content_score(PHONES, "phone") > 0
    assert content_score(NAMES, "text") == 0
    assert content_score(["hello", "world"], "email") == 0
    assert content_score(["12345", "67"], "phone") == 0


def test_content_alone_never_reaches_auto_threshold():
    assert content_score(EMAILS, "email") < 0.85


# ------------------------------------------------------------------ real-world form

def test_real_form_layout_maps_all_five_fields():
    quiz = [(f"{i}. Which of the following is a property of lipids?", ["a) x", "b) y", "c) z"]) for i in range(1, 6)]
    table = _table(
        [("Full Name", NAMES), ("WhatsApp Contact", PHONES), ("School Name", SCHOOLS), ("City", CITIES), *quiz],
        with_respondent_email=EMAILS,
    )
    r = detect_mapping(table, S)
    assert _cols(r) == {"full_name": "Full Name", "whatsapp_contact": "WhatsApp Contact", "city": "City",
                        "school_name": "School Name", "email": "respondent_email"}
    assert r.status == MappingStatus.AUTO and not r.needs_attention


# ------------------------------------------------------------------ differently worded forms

VARIANTS = {
    "plain": [("Full Name", NAMES), ("WhatsApp Contact", PHONES), ("City", CITIES), ("School Name", SCHOOLS), ("Email", EMAILS)],
    "reworded": [("Student Name", NAMES), ("Mobile No", PHONES), ("Town", CITIES), ("Institution", SCHOOLS), ("Email Address", EMAILS)],
    "casual": [("Your name", NAMES), ("WhatsApp number", PHONES), ("City/Town", CITIES), ("School", SCHOOLS), ("E-mail", EMAILS)],
    "numbered": [("1. Name", NAMES), ("2. Contact Number", PHONES), ("3. City", CITIES), ("4. School", SCHOOLS), ("5. Email", EMAILS)],
    "typos": [("Ful Name", NAMES), ("Whatsap Contact", PHONES), ("Citty", CITIES), ("Scool Name", SCHOOLS), ("Emial", EMAILS)],
    "shuffled_with_extras": [("Age", ["14", "15", "16"]), ("Email", EMAILS), ("Class", ["9", "10", "9"]), ("School", SCHOOLS),
                             ("Roll Number", ["101", "102", "103"]), ("Phone", PHONES), ("City", CITIES), ("Name", NAMES)],
}
EXPECTED = {
    "plain": ["Full Name", "WhatsApp Contact", "City", "School Name", "Email"],
}


def _expected_columns(variant):
    cols = dict(VARIANTS[variant])
    titles = list(cols)
    # expected order: full_name, whatsapp_contact, city, school_name, email
    pick = {
        "plain": titles,
        "reworded": titles,
        "casual": titles,
        "numbered": titles,
        "typos": titles,
        "shuffled_with_extras": ["Name", "Phone", "City", "School", "Email"],
    }[variant]
    return dict(zip(["full_name", "whatsapp_contact", "city", "school_name", "email"], pick))


def test_all_variants_map_correctly_and_automatically():
    for name, cols in VARIANTS.items():
        r = detect_mapping(_table(cols), S)
        assert _cols(r) == _expected_columns(name), name
        assert r.status == MappingStatus.AUTO, (name, r.notes)


def test_auto_match_rate_meets_target():
    total = right = 0
    for name, cols in VARIANTS.items():
        got = _cols(detect_mapping(_table(cols), S))
        for key, col in _expected_columns(name).items():
            total += 1
            right += got[key] == col
    assert right / total >= 0.9


# ------------------------------------------------------------------ email handling

def test_respondent_email_preferred_and_typed_email_fills_blanks():
    table = _table(
        [("Full Name", NAMES), ("WhatsApp Contact", PHONES), ("City", CITIES), ("School Name", SCHOOLS), ("Email", EMAILS)],
        with_respondent_email=["verified@x.com", "", "b@x.com"],
    )
    r = detect_mapping(table, S)
    assert r.matches["email"].column == "respondent_email" and r.matches["email"].fallback == "Email"
    mapped = apply_mapping(table, r, S)
    assert list(mapped["email"]) == ["verified@x.com", "sara@yahoo.com", "b@x.com"]


def test_blank_respondent_email_column_is_ignored():
    table = _table([("Full Name", NAMES), ("Email", EMAILS)])
    r = detect_mapping(table, S)
    assert r.matches["email"].column == "Email"


# ------------------------------------------------------------------ uncertain and missing fields

def test_missing_field_needs_review_with_message():
    table = _table([("Full Name", NAMES), ("WhatsApp Contact", PHONES), ("School Name", SCHOOLS), ("Email", EMAILS)])
    r = detect_mapping(table, S)
    assert r.matches["city"].column is None
    assert r.status == MappingStatus.REVIEW and r.needs_attention
    assert any("City" in n for n in r.notes)


def test_low_confidence_match_flagged():
    table = _table([("Father's Name", NAMES), ("WhatsApp Contact", PHONES), ("City", CITIES),
                    ("School Name", SCHOOLS), ("Email", EMAILS)])
    r = detect_mapping(table, S)
    assert r.matches["full_name"].column == "Father's Name"
    assert r.status == MappingStatus.REVIEW


def test_column_used_only_once():
    table = _table([("Name", NAMES), ("WhatsApp Contact", PHONES), ("City", CITIES), ("School", SCHOOLS), ("Email", EMAILS)])
    cols = [c for c in _cols(detect_mapping(table, S)).values() if c]
    assert len(cols) == len(set(cols))


def test_candidates_offered_for_confirm_screen():
    table = _table([("Name", NAMES), ("School Name", SCHOOLS)])
    r = detect_mapping(table, S)
    assert r.matches["full_name"].candidates and r.matches["full_name"].candidates[0][0] == "Name"


def test_empty_table_still_maps_by_title():
    table = pd.DataFrame(columns=SYS + ["Full Name", "WhatsApp Contact", "City", "School Name", "Email"], dtype=str)
    r = detect_mapping(table, S)
    assert r.matches["full_name"].column == "Full Name" and r.matches["whatsapp_contact"].column == "WhatsApp Contact"


# ------------------------------------------------------------------ apply

def test_apply_mapping_shape_and_raw_values_kept():
    table = _table([("Full Name", ["  aLI "] * 3), ("WhatsApp Contact", PHONES), ("City", CITIES),
                    ("School Name", SCHOOLS), ("Email", EMAILS), ("Extra", ["x"] * 3)])
    r = detect_mapping(table, S)
    mapped = apply_mapping(table, r, S)
    assert list(mapped.columns) == ["response_id", "submitted_at", "full_name", "whatsapp_contact", "city", "school_name", "email"]
    assert mapped.loc[0, "full_name"] == "  aLI "


def test_unmapped_field_becomes_blank_column():
    r = result_from_columns(S, {"full_name": "Name"})
    mapped = apply_mapping(_table([("Name", NAMES)]), r, S)
    assert list(mapped["city"]) == ["", "", ""]


def test_apply_with_missing_column_raises_plain_error():
    r = result_from_columns(S, {"full_name": "Ghost"})
    try:
        apply_mapping(_table([("Name", NAMES)]), r, S)
    except MappingError as exc:
        assert "Ghost" in str(exc)
    else:
        raise AssertionError("expected MappingError")


# ------------------------------------------------------------------ saved mappings

def _full_table(rename=None):
    cols = [("Full Name", NAMES), ("WhatsApp Contact", PHONES), ("City", CITIES), ("School Name", SCHOOLS), ("Email", EMAILS)]
    if rename:
        cols = [(rename.get(t, t), v) for t, v in cols]
    return _table(cols)


def test_saved_mapping_reused_when_still_valid():
    with tempfile.TemporaryDirectory() as d:
        store = MappingStore(os.path.join(d, "mappings.json"))
        auto = propose_mapping(_full_table(), S, form_id="F1", store=store)
        assert auto.status == MappingStatus.AUTO and auto.needs_confirmation
        store.save("F1", auto)
        again = propose_mapping(_full_table(), S, form_id="F1", store=store)
        assert again.status == MappingStatus.SAVED and not again.needs_confirmation
        assert again.columns() == auto.columns()


def test_new_unrelated_question_does_not_disturb_saved_mapping():
    with tempfile.TemporaryDirectory() as d:
        store = MappingStore(os.path.join(d, "m.json"))
        store.save("F1", propose_mapping(_full_table(), S, form_id="F1"))
        table = _full_table()
        table["Shoe size"] = ["8", "9", "10"]
        assert propose_mapping(table, S, form_id="F1", store=store).status == MappingStatus.SAVED


def test_renamed_question_triggers_reconfirmation():
    with tempfile.TemporaryDirectory() as d:
        store = MappingStore(os.path.join(d, "m.json"))
        store.save("F1", propose_mapping(_full_table(), S, form_id="F1"))
        changed = propose_mapping(_full_table(rename={"City": "Home Town"}), S, form_id="F1", store=store)
        assert changed.status == MappingStatus.CHANGED and changed.needs_attention
        assert "City" in changed.notes[0]
        assert changed.matches["city"].column == "Home Town"  # a fresh proposal is still offered


def test_operator_choice_saved_and_reloaded():
    with tempfile.TemporaryDirectory() as d:
        store = MappingStore(os.path.join(d, "m.json"))
        choice = result_from_columns(S, {"full_name": "Name", "city": None}, form_id="F2", method="manual")
        store.save("F2", choice)
        entry = store.get("F2")
        assert entry["mapping"]["full_name"] == "Name" and entry["mapping"]["city"] is None and "saved_at" in entry


def test_forget_and_corrupt_file_handled():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.json")
        store = MappingStore(p)
        store.save("F1", result_from_columns(S, {"full_name": "Name"}))
        store.forget("F1")
        assert store.get("F1") is None
        with open(p, "w") as fh:
            fh.write("{not json")
        assert store.get("F1") is None
        assert os.path.exists(p + ".bad")
        store.save("F3", result_from_columns(S, {"full_name": "Name"}))
        assert store.get("F3") is not None


# ------------------------------------------------------------------ confirmed choices (Milestone 7)

def test_confirm_choices_saves_as_confirmed_and_keeps_unchanged_fallback():
    from portal.mapper import confirm_choices
    table = _table([("Full Name", NAMES), ("WhatsApp Contact", PHONES), ("City", CITIES), ("School Name", SCHOOLS), ("Email", EMAILS)],
                   with_respondent_email=["a@x.com", "", "c@x.com"])
    proposal = detect_mapping(table, S)
    keep = confirm_choices(proposal, proposal.columns(), S, form_id="F9")
    assert keep.status == MappingStatus.SAVED and not keep.needs_confirmation and keep.form_id == "F9"
    assert keep.matches["email"].fallback == "Email"
    changed = confirm_choices(proposal, {**proposal.columns(), "email": "Email"}, S)
    assert changed.matches["email"].fallback is None
    assert apply_mapping(table, keep, S).loc[1, "email"] == "sara@yahoo.com"


def test_confirm_choices_allows_a_field_with_no_question():
    from portal.mapper import confirm_choices
    proposal = detect_mapping(_table([("Name", NAMES)]), S)
    done = confirm_choices(proposal, {"full_name": "Name", "city": None}, S)
    assert done.columns()["city"] is None and done.matches["city"].method == "none"
