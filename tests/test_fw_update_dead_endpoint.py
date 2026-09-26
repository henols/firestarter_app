"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Covers the update-path fall-through branch of `FirmwareManager.manage_firmware_update`:
when a release cannot be resolved (`fetch_release_info` stubbed to `(None, None)`) and a
board HAS been identified, the method must return `False` with exactly one `ERROR` record
naming the board and the endpoint actually addressed, rather than the silent `True` that
branch returned before a guard existed there.

Reaching this branch requires stubbing `check_current_firmware` to an identified triple,
not only the fetch: with no current version and no install intent, an earlier guard
already returns `False`, so a test that stubs the fetch alone would pass against that
earlier guard and prove nothing about the fall-through this module targets.

The `--install` and `--force` flag combinations reach a DIFFERENT, pre-existing guard
inside the `if should_install_now:` block (the install-time "Cannot install" message), not
the new one below it — both flags set `should_install_now = True`, and every branch inside
that block returns, so the new guard is unreachable from either. That structural fact is
what this module counts rather than assumes: every case below asserts the number of
`ERROR`-level records is exactly one, not merely that one was emitted, because a second,
accidental emission on the same path would not be visible to a test that only checks for
"any" error.

Reachability was checked directly. With the new guard's four lines removed from
`manage_firmware_update` (restoring the bare `return True` this module targets),
`pytest tests/test_fw_update_dead_endpoint.py -o addopts= -q` failed 3 of the 6 tests below
(the bare-invocation ERROR-count test, the endpoint-content test, and the pinned-channel
test — every case whose assertion depends on the removed guard actually firing); the three
tests reaching a different, pre-existing guard (`--install`, `--force`) and the up-to-date
test were unaffected, as expected. Restoring the guard returned the module to 6 passed.
"""

import logging

from firestarter.constants import FLAG_FORCE
from firestarter.firmware import FirmwareManager

_UP_TO_DATE_WORDING = "already up to date"


def _identified_manager(monkeypatch, *, latest_version=None, download_url=None):
    """A FirmwareManager whose identity seam reports a board, fetch seam stubbed."""
    fm = FirmwareManager(config_manager=None)
    monkeypatch.setattr(
        fm,
        "check_current_firmware",
        lambda **kw: ("/dev/ttyACM0", "3.1.0", "uno"),
    )
    monkeypatch.setattr(
        fm,
        "fetch_release_info",
        lambda channel="stable", version=None, board="uno": (
            latest_version,
            download_url,
        ),
    )
    return fm


def _error_records(caplog):
    return [r for r in caplog.records if r.levelno == logging.ERROR]


def test_bare_check_with_unresolvable_release_returns_false_and_names_endpoint(
    monkeypatch, caplog
):
    fm = _identified_manager(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="Firmware"):
        result = fm.manage_firmware_update()
    assert result is False
    errors = _error_records(caplog)
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "uno" in message
    assert "henols/firestarter_fw" in message
    assert _UP_TO_DATE_WORDING not in message


def test_install_flag_with_unresolvable_release_emits_exactly_one_error(
    monkeypatch, caplog
):
    fm = _identified_manager(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="Firmware"):
        result = fm.manage_firmware_update(install_flag=True)
    assert result is False
    assert len(_error_records(caplog)) == 1


def test_force_flag_with_unresolvable_release_emits_exactly_one_error(
    monkeypatch, caplog
):
    fm = _identified_manager(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="Firmware"):
        result = fm.manage_firmware_update(flags=FLAG_FORCE)
    assert result is False
    assert len(_error_records(caplog)) == 1


def test_already_up_to_date_path_still_returns_true_with_no_error(monkeypatch, caplog):
    fm = _identified_manager(
        monkeypatch,
        latest_version="3.1.0",
        download_url="https://example.invalid/firestarter_uno.hex",
    )
    with caplog.at_level(logging.ERROR, logger="Firmware"):
        result = fm.manage_firmware_update()
    assert result is True
    assert _error_records(caplog) == []


def test_pinned_channel_names_the_by_tag_endpoint_with_the_requested_tag(
    monkeypatch, caplog
):
    fm = _identified_manager(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="Firmware"):
        result = fm.manage_firmware_update(channel="pinned", pinned_version="3.0.0b29")
    assert result is False
    errors = _error_records(caplog)
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "releases/tags/3.0.0b29" in message
    assert "henols/firestarter_fw" in message


def test_pinned_channel_message_differs_from_the_stable_endpoint(monkeypatch, caplog):
    fm = _identified_manager(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="Firmware"):
        fm.manage_firmware_update(channel="pinned", pinned_version="3.0.0b29")
    message = _error_records(caplog)[0].getMessage()
    assert "releases/latest" not in message
