import copy
import time

import pandas as pd

from portal.cleaner import FIXED, REJECTED, VALID, clean_table, clean_value, normalise_phone
from portal.cleaner.email import domain_typo, email_problem, tidy_email_text
from portal.cleaner.schools import expand_abbreviations, find_near_duplicates, normalise_school
from portal.cleaner.steps import looks_random
from portal.cleaner.text import collapse_spaces, is_placeholder, strip_digits_symbols, strip_invisible, title_case
from portal.config import SettingsError, load_settings, validate_settings

S = load_settings()
F = {f["key"]: f for f in S["fields"]}


def one(key, raw):
    return clean_value(raw, F[key], S)


# ------------------------------------------------------------------ text helpers

def test_title_case_variants():
    assert title_case("aLI raza") == "Ali Raza"
    assert title_case("SARA KHAN") == "Sara Khan"
    assert title_case("o'neil al-hassan") == "O'Neil Al-Hassan"
    assert title_case("McDonald") == "McDonald"
    assert title_case("علی خان") == "علی خان"


def test_invisible_characters_removed_but_urdu_joiners_kept():
    assert strip_invisible("Sa\u200bra\ufeff") == "Sara"
    assert strip_invisible("می\u200cخواهم") == "می\u200cخواهم"
    assert strip_invisible("０３１４") == "0314"  # full-width digits normalised


def test_collapse_spaces_handles_nbsp_tabs_newlines():
    assert collapse_spaces("  Ali\u00a0\u00a0 Raza\t\nKhan ") == "Ali Raza Khan"


def test_placeholders():
    ph = S["cleaning"]["placeholders"]
    for v in ["N/A", "none", "-", "---", "  .  ", "NULL", "?"]:
        assert is_placeholder(v, ph), v
    assert not is_placeholder("Nasir", ph) and not is_placeholder("", ph)


def test_strip_digits_symbols():
    assert strip_digits_symbols("ali123 raza_khan") == "ali raza khan"
    assert strip_digits_symbols("@@Sara!!") == "Sara"
    assert strip_digits_symbols("12345") == ""
    assert strip_digits_symbols("Al-Hassan o'neil") == "Al-Hassan o'neil"


def test_random_name_heuristic():
    for bad in ["Ysjgd", "Gsbgdjs", "Gebgd", "Gsibyd", "aaaaaa"]:
        assert looks_random(bad), bad
    for good in ["Ibrahim", "Soban", "Ahmed", "Hafsa", "Shahzad", "Mustafa", "Sky", "علی"]:
        assert not looks_random(good), good


# ------------------------------------------------------------------ phone

def test_pakistani_formats_all_become_e164():
    for raw in ["0314 1837972", "03141837972", "+92 314 1837972", "923141837972", "3141837972", "0092 314 1837972",
                "0314-1837972", "(0314) 183-7972", "۰۳۱۴۱۸۳۷۹۷۲", "0314 1837972 (whatsapp)", "03141837972 ext"]:
        res = normalise_phone(raw, "PK")
        assert res.e164 == "+923141837972", (raw, res)


def test_foreign_number_kept_in_international_form():
    assert normalise_phone("+1 415 555 2671", "PK").e164 == "+14155552671"


def test_garbage_phones_are_errors_with_reason():
    for raw in ["cd", "Gebgd", "123", "abcdefghij", "0314", "N-A"]:
        res = normalise_phone(raw, "PK")
        assert res.e164 is None and res.error, raw


def test_multiple_numbers_use_first_and_flag():
    res = normalise_phone("0300 1234567 / 0333 6150855", "PK")
    assert res.e164 == "+923001234567" and any(c == "phone_multiple" for c, _ in res.flags)


def test_slash_inside_one_number_is_not_split():
    assert normalise_phone("0300/1234567", "PK").e164 == "+923001234567"


# ------------------------------------------------------------------ email

def test_email_validation_rules():
    for good in ["a@b.com", "a.b+c@sub.example.co.uk", "o'neil@x.pk", "first_last@my-school.edu.pk"]:
        assert email_problem(good) is None, good
    for bad in ["", "bilal", "a@b", "a@@b.com", "a b@c.com", ".a@b.com", "a.@b.com", "a..b@c.com", "a@b..com",
                "a@-b.com", "a@b.c", "a@b.c0m", "@b.com", "a@.com"]:
        assert email_problem(bad), bad


def test_tidy_email():
    assert tidy_email_text(" Ali @ gmail.com ") == "Ali@gmail.com"
    assert tidy_email_text("mailto:ali@x.com") == "ali@x.com"
    assert tidy_email_text("<ali@x.com>.") == "ali@x.com"


def test_domain_typo_detection():
    typos = S["cleaning"]["email_domain_typos"]
    assert domain_typo("sara@gmial.com", typos) == ("gmial.com", "gmail.com")
    assert domain_typo("sara@gmail.com", typos) is None
    assert domain_typo("sara@hotmail.co.uk", typos) is None


# ------------------------------------------------------------------ schools / cities

def test_school_normalisation_and_abbreviations():
    assert normalise_school("imcb pakistan town") == "Imcb Pakistan Town"
    assert normalise_school("APS Garrison") == "APS Garrison"
    assert normalise_school("GOVT BOYS HIGH SCHOOL") == "Govt Boys High School"
    assert expand_abbreviations("Govt. Girls High Sch", S["cleaning"]["school_abbreviations"]) == "Government Girls High School"
    assert expand_abbreviations("Sch Road", {"sch": "School"}) == "School Road"


def test_near_duplicate_schools_found_not_merged():
    names = ["Imcb Pakistan Town"] * 3 + ["Imcb Pakistan Towm", "Boys College", "Girls College"]
    found = find_near_duplicates(names, 0.9)
    assert found == {"Imcb Pakistan Towm": "Imcb Pakistan Town"}


def test_city_aliases_and_case():
    assert one("city", "islamabad").value == "Islamabad"
    assert one("city", "KHI").value == "Karachi"
    assert one("city", "LAHORE ").value == "Lahore"


# ------------------------------------------------------------------ single-field results

def test_required_field_rules():
    assert one("full_name", "N/A").errors and one("full_name", "12345").errors and one("full_name", "").errors
    assert one("city", "-").errors
    msg = one("full_name", "").errors[0][1]
    assert msg.startswith("Full Name") and "empty" in msg


def test_cell_reports_what_changed():
    cell = one("whatsapp_contact", " 0314 1837972 ")
    assert cell.value == "+923141837972" and cell.changed and "normalise_phone_e164" in cell.steps_changed
    clean = one("whatsapp_contact", "+923141837972")
    assert not clean.changed and not clean.errors


# ------------------------------------------------------------------ the deliberately messy table

MESSY = [
    # 0 fixed: spacing, case, local phone, alias-free city, email case
    ("  aLI   raza ", "0314 1837972", "islamabad", "imcb pakistan town", " Ali.Raza@GMAIL.com "),
    # 1 fixed: city alias, abbreviation expansion, domain typo flagged
    ("SARA KHAN", "+92 333 6150855", "KHI", "govt girls high sch", "sara@gmial.com"),
    # 2 rejected: junk phone; name looks random
    ("Ysjgd", "Gebgd", "Gsbgdjs", "Gsibyd", "m44@gmail.com"),
    # 3 rejected: junk phone; very short name
    ("dc", "cd", "cd", "cd", "obaid@gmail.com"),
    # 4 rejected: placeholder name
    ("N/A", "03001234567", "Lahore", "City School", "a@b.com"),
    # 5 fixed: digits and underscore removed, 00 prefix, upper-case city
    ("ali123 raza_khan", "0092 300 1234567", "LAHORE", "APS Garrison", "ali@x.pk"),
    # 6 rejected: invalid email
    ("Bilal Ahmed", "03211234567", "Multan", "City School", "bilal"),
    # 7 valid: nothing to change
    ("Ali Raza", "+923141837972", "Islamabad", "Imcb Pakistan Town", "ali@gmail.com"),
    # 8 fixed: zero-width character and non-breaking space
    ("Sa\u200bra\u00a0Malik", "03331234567", "Quetta", "City School", "sara@x.com"),
    # 9 valid: Urdu name
    ("علی خان", "+923001234567", "Karachi", "City School", "ali.khan@x.com"),
    # 10 fixed + flagged: two numbers typed
    ("Hina Noor", "0300 1234567 / 0333 6150855", "Peshawar", "City School", "hina@x.com"),
    # 11 fixed: eastern Arabic digits, trailing word
    ("Zain Ali", "۰۳۱۴۱۸۳۷۹۷۲ whatsapp", "Sialkot", "City School", "zain@x.com"),
    # 12 fixed: school typo, flagged as resembling the common spelling
    ("Omar Farooq", "03451234567", "Islamabad", "Imcb Pakistan Towm", "omar@x.com"),
]
COLS = ["full_name", "whatsapp_contact", "city", "school_name", "email"]


def messy(rows=MESSY):
    df = pd.DataFrame(rows, columns=COLS, dtype=str)
    df.insert(0, "response_id", [f"r{i}" for i in range(len(df))])
    df.insert(1, "submitted_at", [f"2026-09-{(i % 28) + 1:02d}T10:00:00Z" for i in range(len(df))])
    return df


def test_row_statuses_on_messy_table():
    res = clean_table(messy(), S)
    statuses = list(res.cleaned["record_status"])
    assert statuses == [FIXED, FIXED, REJECTED, REJECTED, REJECTED, FIXED, REJECTED, VALID, FIXED, VALID, FIXED, FIXED, FIXED]


def test_cleaned_values():
    c = clean_table(messy(), S).cleaned
    r0, r1, r5, r8 = c.iloc[0], c.iloc[1], c.iloc[5], c.iloc[8]
    assert (r0.full_name, r0.whatsapp_contact, r0.city, r0.school_name, r0.email) == \
        ("Ali Raza", "+923141837972", "Islamabad", "Imcb Pakistan Town", "ali.raza@gmail.com")
    assert (r1.city, r1.school_name, r1.email) == ("Karachi", "Government Girls High School", "sara@gmial.com")
    assert (r5.full_name, r5.whatsapp_contact, r5.city) == ("Ali Raza Khan", "+923001234567", "Lahore")
    assert r8.full_name == "Sara Malik"
    assert c.iloc[11].whatsapp_contact == "+923141837972"


def test_every_rejected_row_has_a_stated_reason():
    res = clean_table(messy(), S)
    assert len(res.rejected) == 4
    assert all(res.rejected["reject_reason"].str.len() > 0)
    reasons = " | ".join(res.rejected["reject_reason"])
    assert "WhatsApp Contact: is not a valid phone number" in reasons
    assert "Full Name: had no usable value" in reasons
    assert "Email: has a problem after the @ sign" in reasons or "Email: does not look like an email address" in reasons


def test_rejected_table_shows_original_values():
    rej = clean_table(messy(), S).rejected
    assert list(rej["full_name"]) == ["Ysjgd", "dc", "N/A", "Bilal Ahmed"]
    assert rej.iloc[0]["whatsapp_contact"] == "Gebgd"


def test_flags_kept_on_accepted_rows():
    acc = clean_table(messy(), S).accepted
    flags = dict(zip(acc["response_id"], acc["flags"]))
    assert "gmial.com" in flags["r1"] and "gmail.com" in flags["r1"]
    assert "several numbers" in flags["r10"]
    assert "resembles 'Imcb Pakistan Town'" in flags["r12"]
    assert flags["r7"] == ""


def test_accepted_has_no_rejected_rows_and_carries_ids():
    res = clean_table(messy(), S)
    assert len(res.accepted) == 9 and "reject_reason" not in res.accepted.columns
    assert set(res.accepted["response_id"]).isdisjoint(set(res.rejected["response_id"]))
    assert list(res.accepted.columns[:2]) == ["response_id", "submitted_at"]


def test_report_counts_add_up_and_summary_reads_well():
    res = clean_table(messy(), S)
    rep = res.report
    assert (rep.valid, rep.rejected) == (2, 4)
    assert rep.valid + rep.fixed + rep.rejected == rep.total == 13
    lines = res.report.summary_lines(S)
    text = "\n".join(lines)
    assert lines[0].startswith("13 rows")
    assert "WhatsApp Contact:" in text and "standardised to international format" in text
    assert "Rejected because:" in text and "Flagged for a look" in text


def test_domain_typo_autofix_setting():
    s2 = copy.deepcopy(S)
    s2["cleaning"]["autofix_email_domain_typos"] = True
    res = clean_table(messy(), s2)
    assert res.cleaned.iloc[1].email == "sara@gmail.com"
    assert "gmial" not in res.cleaned.iloc[1]["flags"]


def test_reject_flags_setting_turns_flags_into_rejections():
    s2 = copy.deepcopy(S)
    s2["cleaning"]["reject_flags"] = ["email_domain_typo"]
    res = clean_table(messy(), s2)
    assert res.cleaned.iloc[1].record_status == REJECTED
    assert "may be a typo" in res.cleaned.iloc[1].reject_reason


def test_removing_a_step_from_settings_turns_it_off():
    s2 = copy.deepcopy(S)
    next(f for f in s2["fields"] if f["key"] == "city")["cleaning"].remove("city_aliases")
    assert clean_table(messy(), s2).cleaned.iloc[1].city == "Khi"


def test_optional_field_may_be_blank():
    s2 = copy.deepcopy(S)
    next(f for f in s2["fields"] if f["key"] == "city")["required"] = False
    rows = [("Ali Raza", "03141837972", "", "City School", "ali@x.com")]
    assert clean_table(messy(rows), s2).cleaned.iloc[0].record_status == FIXED


def test_unknown_step_in_settings_rejected():
    s2 = copy.deepcopy(S)
    s2["fields"][0]["cleaning"].append("make_it_shiny")
    try:
        validate_settings(s2)
    except SettingsError as exc:
        assert "make_it_shiny" in str(exc)
    else:
        raise AssertionError("expected SettingsError")


def test_empty_table_gives_empty_results():
    res = clean_table(messy([]), S)
    assert len(res.cleaned) == len(res.accepted) == len(res.rejected) == 0 and res.report.total == 0


def test_cleaning_does_not_change_input():
    df = messy()
    before = df.copy()
    clean_table(df, S)
    assert df.equals(before)


def test_big_table_is_fast_enough():
    base = MESSY[:2] + MESSY[7:10]
    rows = []
    for i in range(20000):
        n, p, c, s, e = base[i % len(base)]
        rows.append((n, p, c, s, f"user{i % 5000}@x.com"))
    t0 = time.time()
    res = clean_table(messy(rows), S)
    assert res.report.total == 20000 and time.time() - t0 < 60
