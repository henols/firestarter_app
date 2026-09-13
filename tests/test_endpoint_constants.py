"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.

Standalone invariant pin: every FIRESTARTER_*_URL endpoint constant in
`firestarter/constants.py` must address `henols/firestarter_fw/`, the
firmware repository's renamed slug, independently of any other test in
this suite.

This module exists because the derived fixtures at
`tests/test_firmware_install.py:383,494` cannot, on their own, catch a
constant that regresses back to the pre-rename slug. Those two sites are
mock pagination `next_url` values fed back into `mock_releases_factory`
and never asserted by anything downstream; an f-string built from the
constant merely tracks whatever the constant currently says, so it stays
green on exactly the drift this pin exists to catch. This module is the
only mechanism in the repository that fails when one of the three
constants is edited alone.

No live network check can substitute for this pin either. The bare slug
`henols/firestarter` still answers with a transparent HTTP redirect to
`henols/firestarter_fw` and returns byte-identical release data, so a
request against a stale constant succeeds today and will keep succeeding
until the day the redirect is retired. A test that exercises the wire
would stay green on a regression this pin catches immediately.

Each assertion below is positive-only: it checks that the constant
contains the renamed slug with a trailing slash, so a value that merely
ends at the bare repository name cannot satisfy it. None of them checks
that the bare slug `henols/firestarter` is absent. That slug is a proper
prefix of `henols/firestarter_fw`, so a negative assertion of that shape
either passes on every value handed to it (rendering the check inert) or
rejects the correct, already-renamed value (rendering the check wrong).
The only sound instrument for separating the two slugs is a
boundary-aware pattern, and that pattern belongs to the repository-wide
sweep built in a later phase, not to this per-constant pin.

Each assertion also checks the slug rather than the full URL. Any of the
three constants may legitimately grow a new path segment — the
`{tag}` template on `FIRESTARTER_RELEASE_BY_TAG_URL` is exactly that
kind of legitimate variation — and a pin written against the full URL
would need editing for a change that has nothing to do with which
repository is addressed.

The three constants are imported individually by name, at module scope,
rather than discovered by scanning `firestarter.constants` for anything
whose name matches a pattern. A future branch may carry only some of
these names — the very reason this module exists as a standalone unit
rather than as an addition to an existing suite — and a scan-based
import would let a removed or renamed constant vanish from this pin
silently, passing over an empty collection instead of failing to
collect.

Each of the three assertions below is written as its own test function
carrying the constant's name, so that a failure names exactly which of
the three has regressed rather than reporting only that one of three
is wrong.
"""

from firestarter.constants import (
    FIRESTARTER_RELEASE_BY_TAG_URL,
    FIRESTARTER_RELEASE_URL,
    FIRESTARTER_RELEASES_URL,
)

_RENAMED_SLUG = "henols/firestarter_fw/"


def test_firestarter_release_url_addresses_renamed_slug():
    """FIRESTARTER_RELEASE_URL must address henols/firestarter_fw/."""
    assert _RENAMED_SLUG in FIRESTARTER_RELEASE_URL, (
        f"FIRESTARTER_RELEASE_URL does not address {_RENAMED_SLUG!r}: "
        f"{FIRESTARTER_RELEASE_URL!r}"
    )


def test_firestarter_releases_url_addresses_renamed_slug():
    """FIRESTARTER_RELEASES_URL must address henols/firestarter_fw/."""
    assert _RENAMED_SLUG in FIRESTARTER_RELEASES_URL, (
        f"FIRESTARTER_RELEASES_URL does not address {_RENAMED_SLUG!r}: "
        f"{FIRESTARTER_RELEASES_URL!r}"
    )


def test_firestarter_release_by_tag_url_addresses_renamed_slug():
    """FIRESTARTER_RELEASE_BY_TAG_URL must address henols/firestarter_fw/."""
    assert _RENAMED_SLUG in FIRESTARTER_RELEASE_BY_TAG_URL, (
        f"FIRESTARTER_RELEASE_BY_TAG_URL does not address {_RENAMED_SLUG!r}: "
        f"{FIRESTARTER_RELEASE_BY_TAG_URL!r}"
    )
