"""Tests for `firestarter.fw_release_gate` -- the pre-flash release gate.

The gate refuses a firmware release whose FEATURE version (major, minor) is
above this CLI's. Patch numbers and pre-release suffixes are ignored, so
3.1.0b2 and 3.1.0b5 are one feature version.

Coverage is organised as the standard requires: both sides of the threshold,
violating input, and the absent-evidence cases that prove the fail-closed arm
is reachable. The CLI-wiring tests additionally assert that nothing was called
on the firmware manager, which is what proves the refusal happened before any
port opened and before any HTTP request.

No test pins the live `firestarter.__version__`. Every case passes the CLI
version explicitly or monkeypatches the package attribute, so a release bump
cannot redden this file.
"""

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

import firestarter as _pkg
from firestarter.cli_handlers import cli
from firestarter.exceptions import (
    FirmwareOperationError,
    FirmwareReleaseRefusedError,
)
from firestarter.firmware import FirmwareManager
from firestarter.fw_release_gate import (
    _REFUSAL_FORMAT,
    _UNREADABLE_FORMAT,
    feature_version,
    is_refused,
    require_installable_release,
)

from .conftest import make_app_context

# ---------------------------------------------------------------------------
# A. feature_version -- parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3.1.0", (3, 1)),
        ("3.1.0b2", (3, 1)),  # pre-release suffix dropped
        ("v3.2.0", (3, 2)),  # the fw repo publishes v-prefixed tags too
        ("3", (3, 0)),  # one segment; .minor defaults to 0
        ("3.1.0rc1", (3, 1)),
        ("not-a-version", None),
        ("", None),
        (None, None),
    ],
)
def test_feature_version_parsing(raw, expected) -> None:
    assert feature_version(raw) == expected


# ---------------------------------------------------------------------------
# B. The rule, with both sides of the threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("app_version", "release_version", "refused"),
    [
        ("3.1.0", "3.2.0", True),  # violating input: release is ahead
        ("3.1.0", "3.1.5", False),  # patch ignored
        ("3.1.0b2", "3.1.0b5", False),  # same feature version
        ("3.2.0", "3.1.0", False),  # reverse mismatch: out of scope
        ("3.1.0", "3.1.0", False),  # boundary: equality passes
        ("3.9.0", "4.0.0", True),  # major step
        ("4.0.0", "3.9.0", False),  # major reverse
    ],
)
def test_is_refused_rule(app_version, release_version, refused) -> None:
    assert is_refused(app_version, release_version) is refused


# ---------------------------------------------------------------------------
# C. Fail closed -- absent evidence on either side
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("app_version", "release_version"),
    [
        ("garbage", "3.1.0"),
        ("3.1.0", "garbage"),
        (None, "3.1.0"),
        ("", "3.1.0"),
    ],
)
def test_unreadable_version_refuses(app_version, release_version) -> None:
    """An unreadable version on either side refuses exactly like a too-new one."""
    assert is_refused(app_version, release_version) is True
    with pytest.raises(FirmwareReleaseRefusedError):
        require_installable_release(app_version, release_version)


@pytest.mark.parametrize("release_version", [None, ""])
def test_absent_release_does_not_refuse(release_version) -> None:
    """A missing release is NOT a refusal, deliberately.

    With no release there is nothing to flash, and manage_firmware_update
    already reports the failed fetch. Turning a GitHub outage into a
    version-mismatch refusal would be a lie. This is the single documented
    fail-open edge of an otherwise fail-closed gate.
    """
    assert is_refused("3.1.0", release_version) is False
    require_installable_release("3.1.0", release_version)


# ---------------------------------------------------------------------------
# D. The override
# ---------------------------------------------------------------------------


def test_allow_newer_waives_the_refusal() -> None:
    require_installable_release("3.1.0", "3.2.0", allow_newer=True)


def test_allow_newer_waives_the_unreadable_case() -> None:
    require_installable_release("garbage", "3.1.0", allow_newer=True)


def test_override_defaults_to_false_for_library_callers() -> None:
    """Omitting the kwarg must not silently grant the waiver."""
    with pytest.raises(FirmwareReleaseRefusedError):
        require_installable_release("3.1.0", "3.2.0")


# ---------------------------------------------------------------------------
# E. Refusal text -- import the constants, never copy them
# ---------------------------------------------------------------------------


def test_too_new_message_is_the_format_constant() -> None:
    with pytest.raises(FirmwareReleaseRefusedError) as exc:
        require_installable_release("3.1.0", "3.2.0")
    assert str(exc.value) == _REFUSAL_FORMAT.format(
        app="3.1.0",
        release="3.2.0",
        app_feature="3.1",
        release_feature="3.2",
    )


def test_unreadable_message_is_the_other_format_constant() -> None:
    with pytest.raises(FirmwareReleaseRefusedError) as exc:
        require_installable_release("garbage", "3.1.0")
    assert str(exc.value) == _UNREADABLE_FORMAT.format(
        app="garbage",
        release="3.1.0",
        app_feature="an unreadable version",
        release_feature="3.1",
    )


@pytest.mark.parametrize("template", [_REFUSAL_FORMAT, _UNREADABLE_FORMAT])
def test_every_refusal_names_the_remedy_and_the_escape(template) -> None:
    assert "pip install --upgrade firestarter" in template
    assert "--allow-newer-firmware" in template


def test_refusal_renders_without_a_prefix() -> None:
    """Subclassing FirmwareOperationError is what gets verbatim rendering.

    map_typed_errors has no arm of its own for this class; it falls through to
    the FirmwareOperationError arm, which prints the message with no prefix.
    """
    assert issubclass(FirmwareReleaseRefusedError, FirmwareOperationError)


# ---------------------------------------------------------------------------
# F. CLI wiring -- the pinned, pre-serial, pre-network path
# ---------------------------------------------------------------------------


@pytest.fixture
def fw_manager() -> Mock:
    return Mock(spec=FirmwareManager)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_pinned_too_new_refuses_before_any_port_or_request(
    runner, fw_manager, monkeypatch
) -> None:
    """The refusal must land before check_current_firmware and before any fetch.

    The two `assert_not_called` lines are the non-vacuity core of this file: a
    refusal that happened after a port opened would still exit 1 and still
    print the right text, and only these assertions tell the two apart.
    """
    monkeypatch.setattr(_pkg, "__version__", "3.1.0")
    result = runner.invoke(
        cli,
        ["fw", "--firmware-version", "3.2.0"],
        obj=make_app_context(firmware_manager=fw_manager),
    )
    assert result.exit_code == 1
    assert "pip install --upgrade firestarter" in result.output
    fw_manager.check_current_firmware.assert_not_called()
    fw_manager.manage_firmware_update.assert_not_called()


def test_pinned_same_feature_version_reaches_the_manager(
    runner, fw_manager, monkeypatch
) -> None:
    monkeypatch.setattr(_pkg, "__version__", "3.1.0")
    fw_manager.manage_firmware_update.return_value = True
    result = runner.invoke(
        cli,
        ["fw", "--firmware-version", "3.1.5"],
        obj=make_app_context(firmware_manager=fw_manager),
    )
    assert result.exit_code == 0
    fw_manager.manage_firmware_update.assert_called_once()


def test_override_flag_reaches_the_manager(runner, fw_manager, monkeypatch) -> None:
    monkeypatch.setattr(_pkg, "__version__", "3.1.0")
    fw_manager.manage_firmware_update.return_value = True
    result = runner.invoke(
        cli,
        ["fw", "--allow-newer-firmware", "--firmware-version", "3.2.0"],
        obj=make_app_context(firmware_manager=fw_manager),
    )
    assert result.exit_code == 0
    kwargs = fw_manager.manage_firmware_update.call_args.kwargs
    assert kwargs["allow_newer_firmware"] is True


def test_list_still_enumerates_a_too_new_release(
    runner, fw_manager, monkeypatch
) -> None:
    """--list is the operator's discovery path and must survive a refusal.

    Without it there is no supported way to find out which release to pin.
    """
    monkeypatch.setattr(_pkg, "__version__", "3.1.0")
    fw_manager.list_releases.return_value = [
        {
            "version": "3.2.0",
            "channel": "stable",
            "published": "2026-09-25T00:00:00Z",
            "asset_url": "https://example.invalid/firestarter_uno.hex",
        }
    ]
    result = runner.invoke(
        cli,
        ["fw", "--list"],
        obj=make_app_context(firmware_manager=fw_manager),
    )
    assert result.exit_code == 0
    assert "3.2.0" in result.output
