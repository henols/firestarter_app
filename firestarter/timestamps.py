"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Timestamp Formatting Module

Every timestamp the host writes is ISO 8601 in Central European Time: CET
(UTC+01:00) in winter and CEST (UTC+02:00) during EU summer time. Summer time
starts on the last Sunday of March and ends on the last Sunday of October, at
01:00 UTC both times.

The EU rule is computed here instead of through `zoneinfo`, because Windows
and slim Linux images have no tz database and the app does not depend on the
`tzdata` package.
"""

from __future__ import annotations

import datetime

_CET = datetime.timezone(datetime.timedelta(hours=1), "CET")
_CEST = datetime.timezone(datetime.timedelta(hours=2), "CEST")


def _last_sunday_0100_utc(year: int, month: int) -> datetime.datetime:
    """01:00 UTC on the last Sunday of `month` (March or October, both 31 days)."""
    last_day = datetime.datetime(year, month, 31, 1, tzinfo=datetime.UTC)
    return last_day - datetime.timedelta(days=(last_day.weekday() + 1) % 7)


def to_cet(moment: datetime.datetime) -> datetime.datetime:
    """Return `moment` converted to CET or CEST.

    Raises ValueError for a naive `moment`, because its zone is unknown.
    """
    if moment.tzinfo is None:
        raise ValueError("to_cet() needs a timezone-aware datetime")
    year = moment.astimezone(datetime.UTC).year
    summer_start = _last_sunday_0100_utc(year, 3)
    summer_end = _last_sunday_0100_utc(year, 10)
    return moment.astimezone(_CEST if summer_start <= moment < summer_end else _CET)


def now_cet() -> datetime.datetime:
    """The current time in CET or CEST."""
    return to_cet(datetime.datetime.now(datetime.UTC))


def iso_cet(moment: datetime.datetime | None = None) -> str:
    """ISO 8601 extended format, e.g. `2026-10-09T14:30:56+02:00`.

    Uses the current time when `moment` is None.
    """
    cet = now_cet() if moment is None else to_cet(moment)
    return cet.isoformat(timespec="seconds")


def iso_cet_basic(moment: datetime.datetime | None = None) -> str:
    """ISO 8601 basic format for file and directory names, e.g.
    `20261009T143056+0200`.

    The basic format has no colons. Windows does not permit a colon in a path.
    Uses the current time when `moment` is None.
    """
    cet = now_cet() if moment is None else to_cet(moment)
    return cet.strftime("%Y%m%dT%H%M%S%z")


def iso_text_to_cet(text: str) -> str:
    """Return the ISO 8601 timestamp `text` (e.g. GitHub's
    `2026-01-01T00:00:00Z`) in CET, in extended format.

    Returns `text` unchanged when it does not parse or has no UTC offset.
    """
    try:
        moment = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return text
    if moment.tzinfo is None:
        return text
    return iso_cet(moment)
