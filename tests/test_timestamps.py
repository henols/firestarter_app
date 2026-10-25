"""Every timestamp the host writes is ISO 8601 in CET, or in CEST during EU
summer time (``firestarter.timestamps``).
"""

import datetime
import re

import pytest

from firestarter.timestamps import (
    iso_cet,
    iso_cet_basic,
    iso_text_to_cet,
    now_cet,
    to_cet,
)


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.UTC)


@pytest.mark.parametrize(
    "moment, expected",
    [
        # Winter is CET, UTC+01:00.
        (_utc(2026, 1, 15, 12, 0, 0), "2026-01-15T13:00:00+01:00"),
        # Summer time starts at 01:00 UTC on the last Sunday of March.
        (_utc(2026, 3, 29, 0, 59, 59), "2026-03-29T01:59:59+01:00"),
        (_utc(2026, 3, 29, 1, 0, 0), "2026-03-29T03:00:00+02:00"),
        # Summer is CEST, UTC+02:00.
        (_utc(2026, 7, 1, 12, 0, 0), "2026-07-01T14:00:00+02:00"),
        # Summer time ends at 01:00 UTC on the last Sunday of October.
        (_utc(2026, 10, 25, 0, 59, 59), "2026-10-25T02:59:59+02:00"),
        (_utc(2026, 10, 25, 1, 0, 0), "2026-10-25T02:00:00+01:00"),
        # In 2024, March 31 is itself the last Sunday.
        (_utc(2024, 3, 31, 1, 0, 0), "2024-03-31T03:00:00+02:00"),
        (_utc(2024, 10, 27, 1, 0, 0), "2024-10-27T02:00:00+01:00"),
        (_utc(2025, 3, 30, 0, 59, 59), "2025-03-30T01:59:59+01:00"),
        # The CET date can be one day after the UTC date.
        (_utc(2026, 12, 31, 23, 30, 0), "2027-01-01T00:30:00+01:00"),
    ],
)
def test_iso_cet_applies_the_eu_summer_time_rule(moment, expected):
    assert iso_cet(moment) == expected


def test_to_cet_accepts_any_aware_offset():
    tokyo = datetime.timezone(datetime.timedelta(hours=9))
    moment = datetime.datetime(2026, 7, 1, 21, 0, 0, tzinfo=tokyo)
    assert iso_cet(moment) == "2026-07-01T14:00:00+02:00"


def test_to_cet_refuses_a_naive_datetime():
    with pytest.raises(ValueError):
        to_cet(datetime.datetime(2026, 7, 1, 12, 0, 0))


def test_iso_cet_basic_has_no_colons():
    stamp = iso_cet_basic(_utc(2026, 10, 9, 12, 30, 56))
    assert stamp == "20261009T143056+0200"
    assert ":" not in stamp


def test_now_is_cet_or_cest():
    assert now_cet().utcoffset() in (
        datetime.timedelta(hours=1),
        datetime.timedelta(hours=2),
    )
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+0[12]:00", iso_cet())
    assert re.fullmatch(r"\d{8}T\d{6}\+0[12]00", iso_cet_basic())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-01-01T00:00:00Z", "2026-01-01T01:00:00+01:00"),
        ("2026-09-25T00:00:00Z", "2026-09-25T02:00:00+02:00"),
        ("2026-09-25T02:00:00+02:00", "2026-09-25T02:00:00+02:00"),
        # Text that is not an aware ISO 8601 timestamp is returned unchanged.
        ("", ""),
        ("not a date", "not a date"),
        ("2026-09-25T00:00:00", "2026-09-25T00:00:00"),
    ],
)
def test_iso_text_to_cet(text, expected):
    assert iso_text_to_cet(text) == expected
