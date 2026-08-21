"""CLI-surface tests for `dev lock-status` (Phase 151, LOCK-02/03/04).

Every leg drives `firestarter dev lock-status <chip> [--force]` end to end
through `click.testing.CliRunner`, with a `Mock(spec=EpromOperator)` standing
in for the hardware boundary -- `EpromOperator.read_protection_status` is
the one method that would open a serial port, so patching/asserting on it
IS asserting on the port-opening seam (`mock_operator.read_protection_status
.assert_not_called()` proves no port was opened; a configured
`return_value`/`side_effect` on it stands in for a fed device response).
This exercises the real Click command body -- the real
`protection_gate_for_entry` predicate, the real `classify_protection_response`
classifier, and the real `render_lock_status` renderer -- and mocks only the
one seam that would otherwise need a real board attached.

D-10's exit-code map is the spine of this file: every leg that checks an
exit code checks the printed class token in the *same* assertion block,
never the code alone -- a bare `$?` of `2` is ambiguous across four
different classes (`not_readable` / `not_implemented` / `undocumented_alias`
/ `no_mechanism`), and this codebase already carries a cautionary precedent
of a `max()`-based exit-code defect (`dev test`'s `max(1, 2) == 2`) that a
code-only assertion would never have caught.

Concrete chip names used here, each independently verified against the
real, committed `chip_database.json` via `protection_gate_for_entry`, so no
leg here has to fabricate a hand-built fake entry:

  - `27C256`   -- protocol 0x07, UV-EPROM -> `no_mechanism`.
  - `AM28F010` -- protocol 0x10 -> `not_implemented` (D-02: documented
    readable, but this release implements no read for this protocol).
  - `AT28C256` -- protocol 0x0D -> `not_readable`.
  - `W29C020`  -- protocol 0x05, the `W29C020,W29C020C,W29C022` DB entry ->
    `undocumented_alias`, because `W29C022` is nowhere in
    `lockable-proms.md` and D-06's unanimity rule refuses the whole entry
    regardless of how the bare-`W29C020` C-17 tiebreak resolves. This is
    the measured consequence D-06/D-07 record: no `0x05` row answers by
    default, not even the operator's own `W29C020`.
  - `W29C040`  -- protocol 0x05, the `W29C040,W29C042` DB entry -> also
    `undocumented_alias`, but the two aliases carry two DIFFERENT
    readability annotations (`W29C040` is documented-not-readable,
    `W29C042` is undocumented) -- the set-handling leg below.
  - `AM29F010` -- protocol 0x06, documented-readable -> `read_permitted`
    at the gate, so this is the only chip used here for a real (mocked)
    silicon read: `protected` / `unprotected` / `firmware_outdated`.

No leg here asserts that the operator's own `W29C020` reports a state
class -- that is unreachable by design under D-06 -- and no leg asserts
that any sequence (`0x05` or `0x06`) is itself correct or silicon-validated
(151-DESIGN.md §8): every assertion is about the CLI's *routing* of a class
token to an exit code, never about whether a decode is right.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import AppContext, cli
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import EpromOperationError
from firestarter.lock_status import PROTECTION_CLASSES, SILICON_ONLY_TOKENS
from firestarter.messages import MSG_ERR_UNKNOWN_CMD

from .conftest import make_app_context as _make_app_context

# A read-permitted chip (protocol 0x06, documented-readable per
# `lockable-proms.md`) -- the only chip in this file ever handed to a
# (mocked) `read_protection_status` call without needing `--force`.
_READ_PERMITTED_CHIP = "AM29F010"

# The worked C-17/D-06 example: refuses regardless of the bare-`W29C020`
# tiebreak, because `W29C022` is undocumented either way.
_UNDOCUMENTED_ALIAS_CHIP = "W29C020"

# A second curation-surface refusal whose two aliases carry two DIFFERENT
# readability annotations -- proves the refusal names a *set*, not one verdict.
_TWO_STATE_ALIAS_CHIP = "W29C040"

_NO_MECHANISM_CHIP = "27C256"
_NOT_IMPLEMENTED_CHIP = "AM28F010"
_NOT_READABLE_CHIP = "AT28C256"

# An `error_code` distinct from `MSG_ERR_UNKNOWN_CMD` (0xAB) -- the negative
# control for leg 7's D-04 keying-on-id-not-text requirement.
_OTHER_ERROR_CODE = 0x99
assert _OTHER_ERROR_CODE != MSG_ERR_UNKNOWN_CMD


def make_app_context(*, eprom_operator=None, **overrides) -> AppContext:
    """Local delegate onto `conftest.make_app_context`, defaulting
    `eprom_operator` to a `MagicMock(spec=EpromOperator)` -- this file never
    needs a real transport, only a controllable stand-in for the one method
    (`read_protection_status`) that would open a port.
    """
    if eprom_operator is None:
        eprom_operator = MagicMock(spec=EpromOperator)
    return _make_app_context(eprom_operator=eprom_operator, **overrides)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _first_token(output: str) -> str:
    """The class token is always the first whitespace-delimited field of
    the first printed line (`render_lock_status`'s own contract)."""
    return output.splitlines()[0].split()[0]


def _invoke(runner: CliRunner, app: AppContext, chip: str, *, force: bool = False):
    args = ["dev", "lock-status", chip]
    if force:
        args.append("--force")
    return runner.invoke(cli, args, obj=app)


# ---------------------------------------------------------------------------
# Leg 1 + 2: the class-token x exit-code matrix, and exit 0's real-read floor
# ---------------------------------------------------------------------------

# One case per one of D-09's eight class tokens. `mock` is `None` for a
# table refusal (the operator must never be called), `("value", is_ok,
# payload)` to configure a `return_value`, or `("error", error_code)` to
# configure a `side_effect`.
_MATRIX_CASES = {
    "no_mechanism": (_NO_MECHANISM_CHIP, False, None, 2),
    "not_implemented": (_NOT_IMPLEMENTED_CHIP, False, None, 2),
    "not_readable": (_NOT_READABLE_CHIP, False, None, 2),
    "undocumented_alias": (_UNDOCUMENTED_ALIAS_CHIP, False, None, 2),
    "protected": (
        _READ_PERMITTED_CHIP,
        False,
        ("value", True, bytes([0xCD, 0x01])),
        0,
    ),
    "unprotected": (
        _READ_PERMITTED_CHIP,
        False,
        ("value", True, bytes([0xAB, 0x00])),
        0,
    ),
    "firmware_outdated": (
        _READ_PERMITTED_CHIP,
        False,
        ("error", MSG_ERR_UNKNOWN_CMD),
        3,
    ),
    "unadjudicated_probe": (
        _UNDOCUMENTED_ALIAS_CHIP,
        True,
        ("value", True, bytes([0x11, 0x00])),
        4,
    ),
}

assert set(_MATRIX_CASES) == set(PROTECTION_CLASSES), (
    "every one of D-09's eight class tokens must have exactly one matrix case"
)


def _configure_operator(mock_operator: MagicMock, mock_config) -> None:
    if mock_config is None:
        return
    kind = mock_config[0]
    if kind == "value":
        _, is_ok, payload = mock_config
        mock_operator.read_protection_status.return_value = (is_ok, payload)
    elif kind == "error":
        _, error_code = mock_config
        mock_operator.read_protection_status.side_effect = EpromOperationError(
            "simulated firmware error", error_code=error_code
        )
    else:  # pragma: no cover -- defensive, never reached by this file's cases
        raise ValueError(f"unknown mock kind {kind!r}")


@pytest.mark.parametrize(
    "class_token", list(PROTECTION_CLASSES), ids=list(PROTECTION_CLASSES)
)
def test_matrix_class_token_and_exit_code(runner: CliRunner, class_token: str) -> None:
    """D-10's whole contract, one leg per class: the printed token AND the
    exit code are asserted together, in this one block, never separately.
    """
    chip, force, mock_config, expected_exit = _MATRIX_CASES[class_token]
    mock_operator = MagicMock(spec=EpromOperator)
    _configure_operator(mock_operator, mock_config)
    app = make_app_context(eprom_operator=mock_operator)

    result = _invoke(runner, app, chip, force=force)

    assert _first_token(result.output) == class_token, result.output
    assert result.exit_code == expected_exit, (class_token, result.output)

    if mock_config is None:
        # A table refusal needs no hardware -- the operator must never be
        # reached, so no serial port is ever opened on this path.
        mock_operator.read_protection_status.assert_not_called()
    else:
        mock_operator.read_protection_status.assert_called_once()


def test_exit_zero_reachable_only_from_silicon_only_tokens() -> None:
    """CLI-level restatement of D-10's `$? == 0` contract: exactly the two
    silicon-only tokens exit 0, and both cases feed a real device payload.
    """
    zero_exit_tokens = {
        token
        for token, (_c, _f, _m, exit_code) in _MATRIX_CASES.items()
        if exit_code == 0
    }
    assert zero_exit_tokens == SILICON_ONLY_TOKENS, zero_exit_tokens
    for token in zero_exit_tokens:
        _chip, _force, mock_config, _exit = _MATRIX_CASES[token]
        assert mock_config is not None and mock_config[0] == "value", (
            f"{token} must be backed by a fed device payload"
        )


# ---------------------------------------------------------------------------
# Leg 3: the W29C020 refusal, named -- D-06/D-07's measured consequence
# ---------------------------------------------------------------------------


def test_w29c020_refuses_by_default_naming_w29c022(runner: CliRunner) -> None:
    """No `0x05` row answers by default -- not even the operator's own
    `W29C020` -- because `W29C022` is undocumented in `lockable-proms.md`
    and D-06's unanimity rule refuses the whole entry regardless of how the
    bare-`W29C020` C-17 tiebreak resolves. Opens no serial port: the gate
    refuses before `read_protection_status` (the port-opening seam) is ever
    called.
    """
    mock_operator = MagicMock(spec=EpromOperator)
    app = make_app_context(eprom_operator=mock_operator)

    result = _invoke(runner, app, _UNDOCUMENTED_ALIAS_CHIP, force=False)

    assert _first_token(result.output) == "undocumented_alias", result.output
    assert "W29C022" in result.output, result.output
    assert result.exit_code == 2, result.output
    mock_operator.read_protection_status.assert_not_called()


# ---------------------------------------------------------------------------
# Leg 4: the W29C040 refusal handles a SET, with differing annotations
# ---------------------------------------------------------------------------


def test_w29c040_refusal_names_both_aliases_with_differing_states(
    runner: CliRunner,
) -> None:
    """The `W29C040,W29C042` DB entry refuses naming BOTH aliases, and the
    two carry two different readability annotations
    (`documented-not-readable` for `W29C040`, `undocumented` for `W29C042`)
    -- D-06's unanimity rule refuses on the worst of a set, not a single
    verdict.
    """
    mock_operator = MagicMock(spec=EpromOperator)
    app = make_app_context(eprom_operator=mock_operator)

    result = _invoke(runner, app, _TWO_STATE_ALIAS_CHIP, force=False)

    assert _first_token(result.output) == "undocumented_alias", result.output
    assert "W29C040 (documented-not-readable)" in result.output, result.output
    assert "W29C042 (undocumented)" in result.output, result.output
    assert result.exit_code == 2, result.output
    mock_operator.read_protection_status.assert_not_called()


# ---------------------------------------------------------------------------
# Leg 5: the --force probe never becomes a state claim, for any fed decode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "decode_byte",
    [0x00, 0x01, 0xFF],
    ids=["unprotected-code", "protected-code", "indeterminate"],
)
def test_forced_probe_is_never_a_state_claim(
    runner: CliRunner, decode_byte: int
) -> None:
    """D-07's opt-in cannot be read as a guarantee: forcing past the
    `W29C020` table refusal always prints `unadjudicated_probe` and exits 4
    -- never a `SILICON_ONLY_TOKENS` member -- regardless of what the fed
    decode byte says, including the code that would otherwise decode as a
    definite unprotected state. `classify_protection_response`'s
    forced-past-refusal guard runs before the payload is ever consulted.
    """
    mock_operator = MagicMock(spec=EpromOperator)
    mock_operator.read_protection_status.return_value = (
        True,
        bytes([0x5A, decode_byte]),
    )
    app = make_app_context(eprom_operator=mock_operator)

    result = _invoke(runner, app, _UNDOCUMENTED_ALIAS_CHIP, force=True)

    token = _first_token(result.output)
    assert token == "unadjudicated_probe", result.output
    assert token not in SILICON_ONLY_TOKENS
    assert result.exit_code == 4, result.output


# ---------------------------------------------------------------------------
# Leg 6: --force never reaches the wire -- the flags word is unchanged
# ---------------------------------------------------------------------------


def test_force_does_not_change_the_wire_flags_word(runner: CliRunner) -> None:
    """C-16 / 151-DESIGN.md §6: firmware's `FLAG_FORCE` means one specific
    thing -- downgrade a chip-ID mismatch to a warning -- and `lock-status`
    performs no chip-ID check, so the bit is never set here. The
    `operation_flags` word `read_protection_status` receives must be
    byte-identical with and without `--force`; `--force` only ever changes
    HOST-side behaviour (whether the table refusal is bypassed at all).
    """
    payload = (True, bytes([0x00, 0x00]))

    mock_operator_no_force = MagicMock(spec=EpromOperator)
    mock_operator_no_force.read_protection_status.return_value = payload
    app_no_force = make_app_context(eprom_operator=mock_operator_no_force)
    _invoke(runner, app_no_force, _READ_PERMITTED_CHIP, force=False)
    flags_without_force = (
        mock_operator_no_force.read_protection_status.call_args.kwargs[
            "operation_flags"
        ]
    )

    mock_operator_forced = MagicMock(spec=EpromOperator)
    mock_operator_forced.read_protection_status.return_value = payload
    app_forced = make_app_context(eprom_operator=mock_operator_forced)
    _invoke(runner, app_forced, _READ_PERMITTED_CHIP, force=True)
    flags_with_force = mock_operator_forced.read_protection_status.call_args.kwargs[
        "operation_flags"
    ]

    assert flags_with_force == flags_without_force


# ---------------------------------------------------------------------------
# Leg 7: firmware_outdated, keyed on the message id, never on text
# ---------------------------------------------------------------------------


def test_unknown_command_error_maps_to_firmware_outdated(runner: CliRunner) -> None:
    """D-04: an `EpromOperationError` whose `error_code` is
    `MSG_ERR_UNKNOWN_CMD` means the attached firmware predates this
    command and does not recognise it at all -- surfaced as `firmware_outdated`,
    exit 3, never a state and never a bare "Programmer error".
    """
    mock_operator = MagicMock(spec=EpromOperator)
    mock_operator.read_protection_status.side_effect = EpromOperationError(
        "unknown command", error_code=MSG_ERR_UNKNOWN_CMD
    )
    app = make_app_context(eprom_operator=mock_operator)

    result = _invoke(runner, app, _READ_PERMITTED_CHIP, force=False)

    assert _first_token(result.output) == "firmware_outdated", result.output
    assert result.exit_code == 3, result.output


def test_unrelated_error_code_does_not_map_to_firmware_outdated(
    runner: CliRunner,
) -> None:
    """Negative control: an `EpromOperationError` with a DIFFERENT
    `error_code` must NOT be classified `firmware_outdated` -- the mapping
    is keyed on the message id, never on text or on "any error at all".
    """
    mock_operator = MagicMock(spec=EpromOperator)
    mock_operator.read_protection_status.side_effect = EpromOperationError(
        "some other programmer error", error_code=_OTHER_ERROR_CODE
    )
    app = make_app_context(eprom_operator=mock_operator)

    result = _invoke(runner, app, _READ_PERMITTED_CHIP, force=False)

    assert "firmware_outdated" not in result.output, result.output
    assert result.exit_code != 3, result.output


# ---------------------------------------------------------------------------
# Leg 8: the raw byte is visible on the probe path, even when unresolved
# ---------------------------------------------------------------------------


def test_raw_byte_visible_in_hex_on_forced_probe(runner: CliRunner) -> None:
    """D-03: the probe's raw result must survive even when the class is not
    a state claim -- a raw byte with bits set in BOTH nibbles must appear
    rendered in hex in the forced-probe output.
    """
    mock_operator = MagicMock(spec=EpromOperator)
    mock_operator.read_protection_status.return_value = (True, bytes([0xA5, 0xFF]))
    app = make_app_context(eprom_operator=mock_operator)

    result = _invoke(runner, app, _UNDOCUMENTED_ALIAS_CHIP, force=True)

    assert _first_token(result.output) == "unadjudicated_probe", result.output
    assert "0xA5" in result.output, result.output
