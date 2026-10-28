"""Parsing industry template field values (shared by the portal API and CSV imports)."""

import datetime as dt

import pytest

from apps.industry.fields import (
    FieldError,
    clean_custom_fields,
    parse_boolean,
    parse_date,
    parse_number,
    parse_value,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-28", dt.date(2026, 10, 28)),
        ("28-10-2026", dt.date(2026, 10, 28)),
        ("28/10/2026", dt.date(2026, 10, 28)),
        ("28.10.2026", dt.date(2026, 10, 28)),
        ("05/03/2025", dt.date(2025, 3, 5)),
    ],
)
def test_dates_are_day_first(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["2026/10/28", "Oct 28", "31-02-2026", ""])
def test_bad_dates(raw):
    with pytest.raises(FieldError):
        parse_date(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Yes", True),
        ("y", True),
        ("1", True),
        ("TRUE", True),
        ("no", False),
        ("N", False),
        ("0", False),
        (False, False),
    ],
)
def test_booleans(raw, expected):
    assert parse_boolean(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("4", 4), ("1,200", 1200), (" 2.5 ", 2.5), (3, 3), (2.0, 2)]
)
def test_numbers(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw", ["four", "NaN", "inf", True])
def test_bad_numbers(raw):
    with pytest.raises(FieldError):
        parse_number(raw)


def test_choice_matches_case_insensitively_and_returns_the_option():
    field = {"field_type": "choice", "options": ["14.2 kg domestic", "5 kg"]}
    assert parse_value(field, "14.2 KG Domestic") == "14.2 kg domestic"
    with pytest.raises(FieldError, match="Choose one of"):
        parse_value(field, "19 kg")


def test_clean_custom_fields_rejects_non_objects():
    with pytest.raises(Exception, match="Must be an object"):
        clean_custom_fields([], ["not", "a", "dict"])
