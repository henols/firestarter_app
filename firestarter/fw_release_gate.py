"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Pre-flash gate on the firmware release this host is about to install.

3.1.0 retired command ordinals 4 (blank check) and 6 (verify) and the
skip-blank-check flag 0x08, moved verification from the firmware to the host,
added a region-end field to the wire, and added the CAP-02 identity tail to the
operation-setup ack. A CLI that is older than the firmware composes frames the
firmware no longer implements.

The rule is the FEATURE version, (major, minor). The patch number is ignored,
and so is the pre-release suffix: 3.1.0b2 and 3.1.0b5 are one feature version.
A release is refused when its feature version is ABOVE this CLI's.

A stable CLI also refuses every pre-release firmware. A stable CLI is a final
PEP 440 release; a pre-release firmware is a b/rc/dev tag. This rule ignores the
feature version, and nothing waives it: a stable user gets stable firmware only.
A pre-release CLI (a b/rc tag, or a dev checkout) installs either.

A stable CLI also refuses a release BELOW its feature version. A stable CLI
3.2.x runs stable firmware 3.2.y only (`matches_stable_cli`). Nothing waives
this refusal either. `firmware.fetch_release_info` picks the highest matching
stable release for a stable CLI, so `fw --install` installs a matching release.

The reverse pairing -- a CLI newer than the firmware -- is NOT gated here.
`_maybe_auto_route_to_pre` in cli_handlers.py steers channel selection away from
it. The two checks cover opposite directions and neither replaces the other.

Polarity: fail closed. A false pass flashes a board this host cannot drive, and
the board must then be recovered through the bootloader. A false refusal only
makes the install unavailable, and `firestarter fw --list` still enumerates
the releases. A version that cannot be read on either side is refused exactly
like a version that is too new.

One deliberate exception: a release version of None is NOT a refusal. With no
release there is nothing to flash, and `manage_firmware_update` already reports
the failed fetch. Turning a GitHub outage into a version-mismatch refusal would
be a lie. This is the one place the gate looks fail-open and is not.

Escape for the feature-version refusal: `--allow-newer-firmware`. Never an
environment variable. The pre-release and older-line refusals have no escape.

Unlike the pre-serial gates, the authoritative call runs AFTER the release
metadata fetch, because the release tag is only known then. It still runs before
the download and before any byte reaches avrdude or the DFU backend.
`fw --firmware-version X.Y.Z` is also checked in cli_handlers.fw, where the
target is known with no port open and no HTTP request.

No I/O, no environment reads, no serial access.
"""

from packaging.version import InvalidVersion, Version

from firestarter.exceptions import FirmwareReleaseRefusedError

_REFUSAL_FORMAT = (
    "Firmware {release} needs a newer CLI. This CLI is {app}. "
    "Refusing to install firmware {release}.\n"
    "Firmware feature version {release_feature} and CLI feature version "
    "{app_feature} do not speak the same protocol. The programmer can do an "
    "incorrect operation, or no operation.\n"
    "Upgrade the CLI first. Run 'pip install --upgrade firestarter'.\n"
    "For a pre-release CLI, run 'pip install --upgrade --pre firestarter'.\n"
    "Then run this command again.\n"
    "To install a firmware release this CLI supports, run "
    "'firestarter fw --list', then "
    "'firestarter fw --firmware-version X.Y.Z'.\n"
    "To install firmware {release} on CLI {app}, add --allow-newer-firmware. "
    "This pairing is not tested."
)

_UNREADABLE_FORMAT = (
    "Cannot compare firmware {release} with CLI {app}. One version is not "
    "readable. Refusing to install firmware {release}.\n"
    "A CLI and a firmware with different feature versions do not speak the "
    "same protocol. The programmer can do an incorrect operation, and it gives "
    "no error.\n"
    "Upgrade the CLI first. Run 'pip install --upgrade firestarter'.\n"
    "Run 'firestarter fw --list' to see the releases with a readable version.\n"
    "Then run 'firestarter fw --firmware-version X.Y.Z'.\n"
    "To install firmware {release} with no comparison, add "
    "--allow-newer-firmware. This pairing is not tested."
)

_PRERELEASE_ON_STABLE_FORMAT = (
    "Firmware {release} is a pre-release. This CLI is {app}, a stable release. "
    "A stable CLI installs stable firmware only. "
    "Refusing to install firmware {release}.\n"
    "Run 'firestarter fw --install' to install the latest stable firmware.\n"
    "To use pre-release firmware, install a pre-release CLI. "
    "Run 'pip install --upgrade --pre firestarter'."
)

_OLDER_LINE_ON_STABLE_FORMAT = (
    "Firmware {release} is feature version {release_feature}. This CLI is {app}, "
    "a stable release. A stable CLI installs stable firmware {app_feature}.x only. "
    "Refusing to install firmware {release}.\n"
    "Run 'firestarter fw --install' to install the latest stable firmware "
    "{app_feature}.x."
)

_UNREADABLE_TEXT = "an unreadable version"


def feature_version(raw: str | None) -> tuple[int, int] | None:
    """The (major, minor) of a PEP 440 version, or None when it cannot be read.

    Accepts a leading 'v' and a pre-release suffix, because the firmware repo
    publishes both ('v1.23', '3.1.0b2'). The patch number and the pre-release
    suffix are dropped: neither changes the wire protocol.
    """
    if not raw:
        return None
    try:
        parsed = Version(raw)
    except InvalidVersion:
        return None
    return (parsed.major, parsed.minor)


def is_prerelease_version(raw: str | None) -> bool | None:
    """True for a PEP 440 pre-release (b, rc or dev), False for a final release.

    None when the version cannot be read. The caller decides what that means;
    the feature-version check already refuses an unreadable version.
    """
    if not raw:
        return None
    try:
        return Version(raw).is_prerelease
    except InvalidVersion:
        return None


def matches_stable_cli(app_version: str | None, fw_version: str | None) -> bool:
    """True when fw_version is a stable release on app_version's feature version.

    The firmware a stable CLI runs. False when either version cannot be read.
    """
    if is_prerelease_version(fw_version) is not False:
        return False
    app = feature_version(app_version)
    return app is not None and feature_version(fw_version) == app


def is_refused(app_version: str | None, release_version: str | None) -> bool:
    """True when this CLI must not flash this release.

    False when there is no release to judge: with nothing to flash there is no
    hazard, and the caller already reports the failed fetch.
    """
    if not release_version:
        return False
    app = feature_version(app_version)
    release = feature_version(release_version)
    if app is None or release is None:
        return True
    return release > app


def require_installable_release(
    app_version: str | None,
    release_version: str | None,
    *,
    allow_newer: bool = False,
) -> None:
    """Raise FirmwareReleaseRefusedError when this CLI must not flash this release.

    Returns None on pass. `allow_newer` waives the feature-version refusal; it
    defaults to False so a library caller that does not ask for the waiver does
    not get it. Nothing waives the pre-release or older-line refusal on a
    stable CLI.

    An unreadable version on either side is not judged by the stable-CLI rules.
    The feature-version check refuses it.
    """
    if is_prerelease_version(app_version) is False:
        if is_prerelease_version(release_version) is True:
            raise FirmwareReleaseRefusedError(
                _PRERELEASE_ON_STABLE_FORMAT.format(
                    app=app_version, release=release_version
                )
            )
        app = feature_version(app_version)
        release = feature_version(release_version)
        if app and release and release < app:
            raise FirmwareReleaseRefusedError(
                _OLDER_LINE_ON_STABLE_FORMAT.format(
                    app=app_version,
                    release=release_version,
                    app_feature=f"{app[0]}.{app[1]}",
                    release_feature=f"{release[0]}.{release[1]}",
                )
            )
    if allow_newer or not is_refused(app_version, release_version):
        return
    app = feature_version(app_version)
    release = feature_version(release_version)
    template = _REFUSAL_FORMAT if (app and release) else _UNREADABLE_FORMAT
    raise FirmwareReleaseRefusedError(
        template.format(
            app=app_version or _UNREADABLE_TEXT,
            release=release_version,
            app_feature=f"{app[0]}.{app[1]}" if app else _UNREADABLE_TEXT,
            release_feature=(
                f"{release[0]}.{release[1]}" if release else _UNREADABLE_TEXT
            ),
        )
    )
