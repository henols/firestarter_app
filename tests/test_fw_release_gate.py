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

from unittest.mock import MagicMock, Mock

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
    _OLDER_LINE_ON_STABLE_FORMAT,
    _PRERELEASE_ON_STABLE_FORMAT,
    _REFUSAL_FORMAT,
    _UNREADABLE_FORMAT,
    feature_version,
    is_prerelease_version,
    is_refused,
    matches_stable_cli,
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
        ("3.4.0", "3.5.0", True),  # violating input: release is ahead
        ("3.4.0", "3.4.5", False),  # patch ignored
        ("3.4.0b2", "3.4.0b5", False),  # same feature version
        ("3.5.0", "3.4.0", False),  # reverse mismatch: out of scope
        ("3.4.0", "3.4.0", False),  # boundary: equality passes
        ("3.9.0", "4.0.0", True),  # major step
        ("4.0.0", "3.9.0", False),  # major reverse
    ],
)
def test_is_refused_rule(app_version, release_version, refused) -> None:
    assert is_refused(app_version, release_version) is refused


# ---------------------------------------------------------------------------
# B2. The floor -- firmware older than the binary command frame
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("release_version", "refused"),
    [
        ("3.3.0", False),  # boundary: the floor itself passes
        ("3.3.0b1", False),
        ("3.2.0", True),  # skipped: 3.2.0b1 on PyPI is a JSON-era CLI
        ("3.1.1b2", True),  # JSON-era firmware
        ("2.0.6", True),
    ],
)
def test_release_below_the_floor_is_refused(release_version, refused) -> None:
    assert is_refused("3.4.0", release_version) is refused


def test_allow_newer_does_not_waive_the_floor() -> None:
    with pytest.raises(FirmwareReleaseRefusedError):
        require_installable_release("3.4.0", "3.1.1", allow_newer=True)


def test_too_old_refusal_names_the_release_the_floor_and_the_remedy() -> None:
    with pytest.raises(FirmwareReleaseRefusedError) as exc:
        require_installable_release("3.4.0", "3.1.1")
    text = str(exc.value)
    assert "3.1.1" in text
    assert "3.3" in text
    assert "fw --firmware-version" in text
    # The floor has no escape: offering one would install JSON-era firmware.
    assert "--allow-newer-firmware" not in text


# ---------------------------------------------------------------------------
# C. Fail closed -- absent evidence on either side
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("app_version", "release_version"),
    [
        ("garbage", "3.4.0"),
        ("3.4.0", "garbage"),
        (None, "3.4.0"),
        ("", "3.4.0"),
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
    assert is_refused("3.4.0", release_version) is False
    require_installable_release("3.4.0", release_version)


# ---------------------------------------------------------------------------
# D. The override
# ---------------------------------------------------------------------------


def test_allow_newer_waives_the_refusal() -> None:
    require_installable_release("3.4.0", "3.5.0", allow_newer=True)


def test_allow_newer_waives_the_unreadable_case() -> None:
    require_installable_release("garbage", "3.4.0", allow_newer=True)


def test_override_defaults_to_false_for_library_callers() -> None:
    """Omitting the kwarg must not silently grant the waiver."""
    with pytest.raises(FirmwareReleaseRefusedError):
        require_installable_release("3.4.0", "3.5.0")


# ---------------------------------------------------------------------------
# E. Refusal text -- import the constants, never copy them
# ---------------------------------------------------------------------------


def test_too_new_message_is_the_format_constant() -> None:
    with pytest.raises(FirmwareReleaseRefusedError) as exc:
        require_installable_release("3.4.0", "3.5.0")
    assert str(exc.value) == _REFUSAL_FORMAT.format(
        app="3.4.0",
        release="3.5.0",
        app_feature="3.4",
        release_feature="3.5",
    )


def test_unreadable_message_is_the_other_format_constant() -> None:
    with pytest.raises(FirmwareReleaseRefusedError) as exc:
        require_installable_release("garbage", "3.4.0")
    assert str(exc.value) == _UNREADABLE_FORMAT.format(
        app="garbage",
        release="3.4.0",
        app_feature="an unreadable version",
        release_feature="3.4",
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
    monkeypatch.setattr(_pkg, "__version__", "3.4.0")
    result = runner.invoke(
        cli,
        ["fw", "--firmware-version", "3.5.0"],
        obj=make_app_context(firmware_manager=fw_manager),
    )
    assert result.exit_code == 1
    assert "pip install --upgrade firestarter" in result.output
    fw_manager.check_current_firmware.assert_not_called()
    fw_manager.manage_firmware_update.assert_not_called()


def test_pinned_too_old_refuses_before_any_port_or_request(
    runner, fw_manager, monkeypatch
) -> None:
    monkeypatch.setattr(_pkg, "__version__", "3.4.0")
    result = runner.invoke(
        cli,
        ["fw", "--allow-newer-firmware", "--firmware-version", "3.1.1"],
        obj=make_app_context(firmware_manager=fw_manager),
    )
    assert result.exit_code == 1
    assert "older than the binary command frame" in result.output
    fw_manager.check_current_firmware.assert_not_called()
    fw_manager.manage_firmware_update.assert_not_called()


def test_pinned_same_feature_version_reaches_the_manager(
    runner, fw_manager, monkeypatch
) -> None:
    monkeypatch.setattr(_pkg, "__version__", "3.4.0")
    fw_manager.manage_firmware_update.return_value = True
    result = runner.invoke(
        cli,
        ["fw", "--firmware-version", "3.4.5"],
        obj=make_app_context(firmware_manager=fw_manager),
    )
    assert result.exit_code == 0
    fw_manager.manage_firmware_update.assert_called_once()


def test_override_flag_reaches_the_manager(runner, fw_manager, monkeypatch) -> None:
    monkeypatch.setattr(_pkg, "__version__", "3.4.0")
    fw_manager.manage_firmware_update.return_value = True
    result = runner.invoke(
        cli,
        ["fw", "--allow-newer-firmware", "--firmware-version", "3.5.0"],
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
    monkeypatch.setattr(_pkg, "__version__", "3.4.0")
    fw_manager.list_releases.return_value = [
        {
            "version": "3.5.0",
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
    assert "3.5.0" in result.output


# ---------------------------------------------------------------------------
# G. A stable CLI refuses every pre-release firmware
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3.4.6", False),
        ("v3.4.6", False),
        ("3.4.7b1", True),
        ("3.4.0rc1", True),
        ("3.4.7.dev3+gabc1234", True),
        ("not-a-version", None),
        ("", None),
        (None, None),
    ],
)
def test_is_prerelease_version(raw, expected) -> None:
    assert is_prerelease_version(raw) is expected


@pytest.mark.parametrize("release_version", ["3.4.7b1", "3.4.0rc1", "3.5.0.dev1"])
@pytest.mark.parametrize("allow_newer", [False, True])
def test_stable_cli_refuses_a_prerelease(release_version, allow_newer) -> None:
    """No waiver: --allow-newer-firmware does not open this refusal."""
    with pytest.raises(FirmwareReleaseRefusedError) as exc:
        require_installable_release("3.4.6", release_version, allow_newer=allow_newer)
    assert str(exc.value) == _PRERELEASE_ON_STABLE_FORMAT.format(
        app="3.4.6", release=release_version
    )


def test_stable_cli_refuses_an_older_prerelease() -> None:
    """The rule ignores the feature version: an older pre-release is refused too."""
    with pytest.raises(FirmwareReleaseRefusedError):
        require_installable_release("3.4.6", "3.3.0b2")


@pytest.mark.parametrize(
    ("app_version", "release_version"),
    [
        ("3.4.6", "3.4.6"),  # stable on stable
        ("3.4.6", None),  # nothing to flash
        ("3.4.7b1", "3.4.7b2"),  # pre-release CLI
        ("3.4.7.dev3+gabc1234", "3.4.7b1"),  # dev checkout
        ("3.4.7b1", "3.4.6"),  # pre-release CLI, stable firmware
    ],
)
def test_prerelease_rule_passes(app_version, release_version) -> None:
    require_installable_release(app_version, release_version)


def test_stable_cli_unreadable_release_keeps_the_unreadable_refusal() -> None:
    with pytest.raises(FirmwareReleaseRefusedError) as exc:
        require_installable_release("3.4.6", "garbage")
    assert "is a pre-release" not in str(exc.value)
    assert "not readable" in str(exc.value)


def test_pinned_prerelease_on_stable_cli_refuses_before_any_port_or_request(
    runner, fw_manager, monkeypatch
) -> None:
    monkeypatch.setattr(_pkg, "__version__", "3.4.6")
    result = runner.invoke(
        cli,
        ["fw", "--install", "--firmware-version", "3.4.7b1"],
        obj=make_app_context(firmware_manager=fw_manager),
    )
    assert result.exit_code == 1
    assert "is a pre-release" in result.output
    fw_manager.check_current_firmware.assert_not_called()
    fw_manager.manage_firmware_update.assert_not_called()


def test_pre_channel_on_stable_library_call_refuses_before_download(
    monkeypatch,
) -> None:
    """A library caller that asks a stable CLI for channel='pre' is refused."""
    fm = FirmwareManager(config_manager=MagicMock())
    monkeypatch.setattr(
        fm,
        "check_current_firmware",
        lambda **_kw: ("/dev/ttyACM0", "3.4.6", "uno"),
    )
    monkeypatch.setattr(
        fm,
        "fetch_release_info",
        lambda channel="stable", version=None, board="uno", **_kw: (
            "3.4.7b1",
            "https://example.invalid/uno.hex",
        ),
    )
    monkeypatch.setattr(
        fm, "_download_firmware_file", lambda url: pytest.fail("must not download")
    )
    monkeypatch.setattr(
        fm, "_install_firmware", lambda **kw: pytest.fail("must not flash")
    )
    with pytest.raises(FirmwareReleaseRefusedError):
        fm.manage_firmware_update(
            install_flag=True,
            channel="pre",
            app_version="3.4.6",
            allow_newer_firmware=True,
        )


# ---------------------------------------------------------------------------
# H. A stable CLI runs a stable release on its own feature version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("app_version", "fw_version", "expected"),
    [
        ("3.4.6", "3.4.2", True),
        ("3.4.6", "3.4.9", True),  # patch ignored
        ("3.4.6", "3.4.3b1", False),  # pre-release
        ("3.4.6", "3.3.1", False),  # older line
        ("3.4.6", "3.5.0", False),  # newer line
        ("3.4.6", "garbage", False),
        ("3.4.6", None, False),
        (None, "3.4.2", False),
    ],
)
def test_matches_stable_cli(app_version, fw_version, expected) -> None:
    assert matches_stable_cli(app_version, fw_version) is expected


@pytest.mark.parametrize("allow_newer", [False, True])
def test_stable_cli_refuses_an_older_line(allow_newer) -> None:
    """No waiver: --allow-newer-firmware does not open this refusal."""
    with pytest.raises(FirmwareReleaseRefusedError) as exc:
        require_installable_release("3.4.6", "3.3.1", allow_newer=allow_newer)
    assert str(exc.value) == _OLDER_LINE_ON_STABLE_FORMAT.format(
        app="3.4.6", release="3.3.1", app_feature="3.4", release_feature="3.3"
    )


def test_prerelease_cli_installs_an_older_line() -> None:
    require_installable_release("3.4.7b1", "3.3.1")


def _release(tag: str, *, draft: bool = False) -> dict:
    return {
        "tag_name": tag,
        "draft": draft,
        "assets": [
            {
                "name": "firestarter_uno.hex",
                "browser_download_url": f"https://example.invalid/{tag}/uno.hex",
            }
        ],
    }


_RELEASES = [
    _release("3.5.0"),
    _release("3.4.9", draft=True),
    _release("3.4.3b1"),
    _release("3.4.2"),
    _release("3.4.10rc1"),
    _release("3.4.1"),
    _release("3.3.1"),
]


def test_stable_channel_on_stable_cli_picks_the_highest_matching_stable() -> None:
    fm = FirmwareManager(config_manager=MagicMock())
    fm._fetch_all_releases = lambda **_kw: _RELEASES
    fm.fetch_latest_release_info = lambda **_kw: pytest.fail("must not use /latest")
    version, url = fm.fetch_release_info(
        channel="stable", board="uno", app_version="3.4.6"
    )
    assert version == "3.4.2"
    assert url == "https://example.invalid/3.4.2/uno.hex"


def test_stable_channel_with_no_matching_stable_resolves_nothing() -> None:
    fm = FirmwareManager(config_manager=MagicMock())
    fm._fetch_all_releases = lambda **_kw: _RELEASES
    assert fm.fetch_release_info(
        channel="stable", board="uno", app_version="3.6.0"
    ) == (None, None)


def test_stable_channel_on_prerelease_cli_keeps_latest() -> None:
    fm = FirmwareManager(config_manager=MagicMock())
    fm._fetch_all_releases = lambda **_kw: pytest.fail("must use /latest")
    fm.fetch_latest_release_info = lambda board="uno": ("3.5.0", "u")
    assert fm.fetch_release_info(
        channel="stable", board="uno", app_version="3.4.7b2"
    ) == ("3.5.0", "u")


def _manager_on_board(monkeypatch, current_version: str, latest_version: str):
    fm = FirmwareManager(config_manager=MagicMock())
    monkeypatch.setattr(
        fm,
        "check_current_firmware",
        lambda **_kw: ("/dev/ttyACM0", current_version, "uno"),
    )
    monkeypatch.setattr(
        fm,
        "fetch_release_info",
        lambda **_kw: (latest_version, "https://example.invalid/uno.hex"),
    )
    installed = []
    monkeypatch.setattr(fm, "_download_firmware_file", lambda url: "/nonexistent.hex")
    monkeypatch.setattr(
        fm, "_install_firmware", lambda **kw: installed.append(kw) or True
    )
    return fm, installed


@pytest.mark.parametrize("current_version", ["3.4.3b1", "3.5.0", "3.3.1"])
def test_stable_cli_installs_matching_stable_over_a_mismatched_board(
    monkeypatch, current_version
) -> None:
    """A higher version on the board is still not up to date on a stable CLI."""
    fm, installed = _manager_on_board(monkeypatch, current_version, "3.4.2")
    assert fm.manage_firmware_update(install_flag=True, app_version="3.4.6")
    assert len(installed) == 1


def test_stable_cli_bare_fw_offers_the_matching_stable(monkeypatch) -> None:
    fm, installed = _manager_on_board(monkeypatch, "3.4.3b1", "3.4.2")
    prompts = []
    monkeypatch.setattr(
        "firestarter.firmware.Confirm.ask",
        lambda prompt, **_kw: prompts.append(prompt) or False,
    )
    assert fm.manage_firmware_update(app_version="3.4.6")
    assert prompts == ["Install firmware 3.4.2 for uno (current: 3.4.3b1)?"]
    assert installed == []


def test_prerelease_cli_keeps_a_newer_prerelease_board(monkeypatch) -> None:
    fm, installed = _manager_on_board(monkeypatch, "3.4.3b1", "3.4.2")
    assert fm.manage_firmware_update(install_flag=True, app_version="3.4.7b2")
    assert installed == []
