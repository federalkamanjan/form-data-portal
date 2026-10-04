import copy

import pandas as pd

from portal.config import load_settings
from portal.dedupe import deduplicate, duplicate_keys

S = load_settings()


def frame(rows):
    return pd.DataFrame(rows, columns=["response_id", "submitted_at", "full_name", "whatsapp_contact", "city", "school_name", "email"], dtype=str)


ROWS = [
    ("r1", "2026-01-01T10:00:00Z", "Ali Raza", "+923141837972", "Islamabad", "City School", "ali@x.com"),
    ("r2", "2026-03-01T10:00:00Z", "Ali Raza", "+923141837972", "", "City School", "ALI@x.com "),   # same email, newer, less complete
    ("r3", "2026-02-01T10:00:00Z", "Sara Khan", "+923331234567", "Lahore", "City School", "sara@x.com"),
    ("r4", "2026-02-02T10:00:00Z", "Hina", "+923001234567", "Quetta", "Boys College", ""),             # no email: key falls back to phone
    ("r5", "2026-02-03T10:00:00Z", "Hina Noor", "+923001234567", "Quetta", "Boys College", ""),
    ("r6", "2026-02-04T10:00:00Z", "No Keys", "", "Multan", "City School", ""),                         # nothing to match on
    ("r7", "2026-02-05T10:00:00Z", "No Keys", "", "Multan", "City School", ""),
]


def test_key_uses_email_then_phone_and_ignores_case_and_spaces():
    keys = list(duplicate_keys(frame(ROWS), ["email", "whatsapp_contact"]))
    assert keys[0] == keys[1] == "email:ali@x.com"
    assert keys[3] == keys[4] == "whatsapp_contact:+923001234567"
    assert keys[5] == keys[6] == ""


def test_keep_newest():
    kept, removed = deduplicate(frame(ROWS), S)
    assert list(kept["response_id"]) == ["r2", "r3", "r5", "r6", "r7"]
    assert list(removed["response_id"]) == ["r1", "r4"]
    assert "duplicate_key" in removed.columns


def test_keep_first():
    s2 = copy.deepcopy(S)
    s2["dedupe"]["keep"] = "first"
    kept, _ = deduplicate(frame(ROWS), s2)
    assert list(kept["response_id"]) == ["r1", "r3", "r4", "r6", "r7"]


def test_keep_most_complete_prefers_filled_fields_over_newer():
    s2 = copy.deepcopy(S)
    s2["dedupe"]["keep"] = "most_complete"
    kept, _ = deduplicate(frame(ROWS), s2)
    assert "r1" in set(kept["response_id"]) and "r2" not in set(kept["response_id"])


def test_rows_without_any_key_are_never_removed():
    kept, _ = deduplicate(frame(ROWS), S)
    assert {"r6", "r7"} <= set(kept["response_id"])


def test_original_order_preserved_and_unparseable_dates_tolerated():
    rows = [("a", "not a date", "A", "+923001111111", "x", "s", "a@x.com"), ("b", "", "A", "+923001111111", "x", "s", "a@x.com")]
    kept, removed = deduplicate(frame(rows), S)
    assert len(kept) == 1 and len(removed) == 1


def test_empty_input():
    kept, removed = deduplicate(frame([]), S)
    assert kept.empty and removed.empty
