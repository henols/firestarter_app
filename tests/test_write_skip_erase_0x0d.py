"""Hardware-free contract tests for `write --skip-erase` on a protocol-0x0D
chip (Phase 121 Plan 10 / D-13, v1.22 GATE-02 contributes-only).

Mirrors `test_write_skip_sdp_unlock.py`'s in-process `CliRunner` shape, but
uses `Mock(spec=EpromOperator)` directly (matching `test_cli_handlers.py`'s
`test_write_no_blank_check_polarity` / `test_write_b_decouples_skip_erase_phase92`
pattern) rather than a real `EpromOperator` wired to a fake serial port --
this plan only needs to prove (a) the echoed warning text and (b) the
`operation_flags` argument `write_eprom` receives, neither of which requires
a real wire round-trip.

Coverage (each leg names the decision id it pins):
  1. test_skip_erase_on_0x0d_warns_and_proceeds -- D-13: warning line present,
     `write_eprom` still called, exit code 0.
  2. test_skip_erase_on_non_0x0d_does_not_warn -- D-13 scope: no warning on a
     non-0x0D chip, `write_eprom` still called.
  3. test_no_skip_erase_on_0x0d_does_not_warn -- D-13 scope: no warning when
     the flag itself is absent, `write_eprom` still called.
  4. test_blank_check_flag_on_0x0d_does_not_produce_an_erase_warning --
     RESEARCH C-8: the blank-check flag (`-b`/`--no-blank-check`) is
     deliberately NOT extended by this arm, on a 0x0D chip or otherwise.
  5. test_skip_erase_warning_does_not_change_the_emitted_flags -- the
     `operation_flags` value handed to `write_eprom` is identical whether or
     not the D-13 warning arm's condition is met (bit emission is unaffected).
  6. test_both_vacuous_flag_warnings_can_appear_together -- HOST-02 D-18 /
     D-04 / D-13: on a capability-refused 0x0D chip, the pre-existing D-04
     auto-set line (about `--skip-sdp-unlock`) and this plan's new D-13 line
     (about `--skip-erase`) both fire on the same invocation, proving the new
     `if` is a sibling of the existing if/elif chain rather than accidentally
     chained onto it (which would make the two arms mutually exclusive).
     Note: HOST-02 D-18's own vacuous-flag warning is scoped to non-0x0D
     chips, while D-13's warning is scoped to 0x0D chips, so those two
     specific warnings can never co-occur on a single chip -- the D-04
     auto-set line is the "other applicable line" this leg pairs with D-13's.
  7. test_no_blank_check_vacuity_warning_is_printed_on_0x0d -- Phase 153 /
     D-153-05 / RESEARCH Pitfall 5: the absence of a second, symmetric
     warning about `-b`/`--no-blank-check` being vacuous on 0x0D is a
     DELIBERATE decision, not an omission, and this leg fails loudly if that
     decision is ever reversed by accident.

Phase 153 narrative correction (D-153-04, D-153-05, RESEARCH Pitfall 5): every
assertion below was true when authored and remains true, but the REASON the
`--skip-erase` warning (leg 1) exists has changed. It used to fire because the
28C family (protocol 0x0D) had no erase capability at all. Since ERASE-03
restored `FLAG_CAN_ERASE` for algorithm 13, the family has gained a standalone
erase, reachable as `firestarter erase` -- so the warning now fires because
the WRITE PATH specifically performs no erase (D-153-05 deliberately keeps
erase out of `eeprom28c_write_init`), not because the family lacks one. Leg 4
inverts for a related reason: it was written when `-b`/`--no-blank-check` was
"genuinely useful on a non-blank 0x0D part precisely because there is no
erase to make it blank" -- ERASE-01 has since removed the pre-write blank
check on this protocol entirely, so `-b` is itself now a no-op on 0x0D, for an
unrelated reason (there is no blank check left for it to skip). Leg 4's
assertion (no erase-warning text on `-b`) is unaffected by either fact and
needed no change. What DOES need recording is why no new leg asserts a
`-b`-is-vacuous warning: per Pitfall 5, warning about it would train users to
think the write needs a flag, exactly what 152-CONTEXT.md D-08 exists to keep
out of the public release notes. Leg 7 is that decision's guard.
"""

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import AppContext, cli
from firestarter.constants import FLAG_SKIP_ERASE

from .conftest import make_app_context

# Concrete chip names, verified live against the packaged DB in this session:
#   AT28C256 -- protocol 0x0D (13), capability-ALLOWED (SDP-capable).
#   FM28V020 -- protocol 0x0D (13), capability-REFUSED (FRAM, no SDP decoder).
#   W27C512  -- protocol 0x07, not protocol-0x0D at all.
_ALLOWED_0X0D_CHIP = "AT28C256"
_REFUSED_0X0D_CHIP = "FM28V020"
_NON_0X0D_CHIP = "W27C512"

_SKIP_ERASE_WARNING = "has nothing to skip on this chip's protocol"
_AUTO_SET_LINE = "auto-setting --skip-sdp-unlock on your behalf"

# Phase 153 / D-153-05 / RESEARCH Pitfall 5 guard literal (leg 7 below): a
# short, distinctive fragment of the hypothetical warning this project
# deliberately chose NOT to add -- a "the blank-check flag is vacuous on
# 0x0D" line. Asserted against as a module-level literal (not an inline
# string) so a future addition of such a warning, however it is worded
# around this fragment, fails leg 7 loudly rather than passing silently.
_BLANK_CHECK_VACUITY_FRAGMENT = "no-blank-check has nothing to skip"


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _drive_write(
    runner: CliRunner,
    chip: str,
    tmp_path,
    extra_args: list[str] | None = None,
    app: AppContext | None = None,
):
    input_file = tmp_path / f"{chip}.bin"
    input_file.write_bytes(b"\x01\x02\x03\x04")
    if app is None:
        app = make_app_context()
    args = ["write", chip, str(input_file), *(extra_args or [])]
    result = runner.invoke(cli, args, obj=app)
    return result, app


# ---------------------------------------------------------------------------
# Leg 1: --skip-erase on 0x0D warns and proceeds (D-13)
# ---------------------------------------------------------------------------


def test_skip_erase_on_0x0d_warns_and_proceeds(runner: CliRunner, tmp_path) -> None:
    """D-13: `write <0x0D chip> --skip-erase` prints the "nothing to skip"
    line, still calls write_eprom, and exits 0 -- the arm never aborts."""
    result, app = _drive_write(
        runner, _ALLOWED_0X0D_CHIP, tmp_path, extra_args=["--skip-erase"]
    )
    assert result.exit_code == 0, result.output
    assert _SKIP_ERASE_WARNING in result.output
    assert _ALLOWED_0X0D_CHIP.upper() in result.output
    app.eprom_operator.write_eprom.assert_called_once()


# ---------------------------------------------------------------------------
# Leg 2: --skip-erase on a non-0x0D chip does not warn (D-13 scope)
# ---------------------------------------------------------------------------


def test_skip_erase_on_non_0x0d_does_not_warn(runner: CliRunner, tmp_path) -> None:
    """D-13 scope: --skip-erase on a non-0x0D chip is a real (non-vacuous)
    flag on this codebase's other erasable-chip paths, so this arm must NOT
    fire there -- no warning line, write_eprom still called."""
    result, app = _drive_write(
        runner, _NON_0X0D_CHIP, tmp_path, extra_args=["--skip-erase"]
    )
    assert result.exit_code == 0, result.output
    assert _SKIP_ERASE_WARNING not in result.output
    app.eprom_operator.write_eprom.assert_called_once()


# ---------------------------------------------------------------------------
# Leg 3: no --skip-erase on 0x0D does not warn
# ---------------------------------------------------------------------------


def test_no_skip_erase_on_0x0d_does_not_warn(runner: CliRunner, tmp_path) -> None:
    """D-13: without --skip-erase, a 0x0D chip gets no warning at all --
    the arm is keyed on the flag being passed, not on the protocol alone."""
    result, app = _drive_write(runner, _ALLOWED_0X0D_CHIP, tmp_path)
    assert result.exit_code == 0, result.output
    assert _SKIP_ERASE_WARNING not in result.output
    app.eprom_operator.write_eprom.assert_called_once()


# ---------------------------------------------------------------------------
# Leg 4: the blank-check flag is NOT extended by this arm (RESEARCH C-8)
# ---------------------------------------------------------------------------


def test_blank_check_flag_on_0x0d_does_not_produce_an_erase_warning(
    runner: CliRunner, tmp_path
) -> None:
    """RESEARCH C-8: `-b`/`--no-blank-check` on a 0x0D chip must NEVER
    produce an erase-related "nothing to skip" line.

    Phase 153 note (D-153-05 / RESEARCH Pitfall 5): the reason this
    assertion holds has inverted since it was written. It used to hold
    because `-b` skips only the blank check (Phase 92), and was genuinely
    useful on a non-blank AT28C precisely because there was no erase to
    make it blank. ERASE-01 has since removed the pre-write blank check
    on protocol 0x0D entirely, so `-b` is now itself a no-op on 0x0D --
    for the unrelated reason that there is no blank check left for it to
    skip. Either way, an erase-related "nothing to skip" line on `-b`
    would be a false statement about a flag whose vacuity has a different
    cause, so this leg's assertion is unchanged and still required. See
    `test_no_blank_check_vacuity_warning_is_printed_on_0x0d` for the
    companion guard against a *blank-check*-vacuity warning ever being
    added instead."""
    result, app = _drive_write(
        runner, _ALLOWED_0X0D_CHIP, tmp_path, extra_args=["--no-blank-check"]
    )
    assert result.exit_code == 0, result.output
    assert _SKIP_ERASE_WARNING not in result.output
    assert "erase" not in result.output.lower()
    app.eprom_operator.write_eprom.assert_called_once()


# ---------------------------------------------------------------------------
# Leg 5: the emitted flags are byte-identical with and without the warning
# ---------------------------------------------------------------------------


def test_skip_erase_warning_does_not_change_the_emitted_flags(
    runner: CliRunner, tmp_path
) -> None:
    """The D-13 warning is purely cosmetic: the FLAG_SKIP_ERASE bit reaching
    write_eprom's operation_flags must be identical whether the arm's
    "0x0D" condition is met (warning prints) or not (non-0x0D, no warning) --
    captured via write_eprom.call_args, not a hardcoded numeric flags value,
    so this test does not pin an unrelated flag's bit position."""
    result_0x0d, app_0x0d = _drive_write(
        runner, _ALLOWED_0X0D_CHIP, tmp_path, extra_args=["--skip-erase"]
    )
    assert result_0x0d.exit_code == 0, result_0x0d.output
    assert _SKIP_ERASE_WARNING in result_0x0d.output
    flags_0x0d = app_0x0d.eprom_operator.write_eprom.call_args.kwargs["operation_flags"]

    result_non_0x0d, app_non_0x0d = _drive_write(
        runner, _NON_0X0D_CHIP, tmp_path, extra_args=["--skip-erase"]
    )
    assert result_non_0x0d.exit_code == 0, result_non_0x0d.output
    assert _SKIP_ERASE_WARNING not in result_non_0x0d.output
    flags_non_0x0d = app_non_0x0d.eprom_operator.write_eprom.call_args.kwargs[
        "operation_flags"
    ]

    # Both runs passed --skip-erase, so FLAG_SKIP_ERASE must be set in both,
    # regardless of whether the warning line printed.
    assert flags_0x0d & FLAG_SKIP_ERASE
    assert flags_non_0x0d & FLAG_SKIP_ERASE


# ---------------------------------------------------------------------------
# Leg 6: this arm and the pre-existing D-04/D-18 block are not mutually
# exclusive -- both applicable lines fire together on one invocation
# ---------------------------------------------------------------------------


def test_both_vacuous_flag_warnings_can_appear_together(
    runner: CliRunner, tmp_path
) -> None:
    """A capability-refused 0x0D chip with --skip-erase (and no explicit
    --skip-sdp-unlock) drives BOTH the pre-existing D-04 auto-set line
    (about --skip-sdp-unlock) and this plan's new D-13 line (about
    --skip-erase) on a single `write` invocation. This proves the new `if`
    was added as a sibling of the existing if/elif chain, not chained onto
    it as another elif -- an elif placement would have made the two blocks
    mutually exclusive, so this test would go RED if that regression were
    introduced (verified via the deliberate-break proof in this plan's
    execution notes)."""
    result, app = _drive_write(
        runner, _REFUSED_0X0D_CHIP, tmp_path, extra_args=["--skip-erase"]
    )
    assert result.exit_code == 0, result.output
    assert _AUTO_SET_LINE in result.output
    assert _SKIP_ERASE_WARNING in result.output
    app.eprom_operator.write_eprom.assert_called_once()


# ---------------------------------------------------------------------------
# Leg 7: no blank-check-vacuity warning is printed on 0x0D (Phase 153 /
# D-153-05 / RESEARCH Pitfall 5) -- guards a deliberate non-addition
# ---------------------------------------------------------------------------


def test_no_blank_check_vacuity_warning_is_printed_on_0x0d(
    runner: CliRunner, tmp_path
) -> None:
    """Phase 153 / D-153-05 / RESEARCH Pitfall 5: since ERASE-01 removed the
    pre-write blank check on protocol 0x0D, `-b`/`--no-blank-check` is now
    itself a no-op on this protocol -- symmetric with the pre-existing
    `--skip-erase` vacuity this module already covers. This project
    DELIBERATELY did not add a second, symmetric warning about it: doing so
    would train users to think the write needs a flag, which is exactly the
    recommendation 152-CONTEXT.md D-08 exists to keep out of the public
    release notes. This leg asserts that absence against a module-level
    literal (`_BLANK_CHECK_VACUITY_FRAGMENT`), so a future addition of such a
    warning fails this leg loudly rather than passing silently. The leg was
    observed failing against a temporary echo carrying that literal before
    this commit -- see this plan's SUMMARY.md for the transcribed failure."""
    result, app = _drive_write(
        runner, _ALLOWED_0X0D_CHIP, tmp_path, extra_args=["--no-blank-check"]
    )
    assert result.exit_code == 0, result.output
    app.eprom_operator.write_eprom.assert_called_once()
    assert _BLANK_CHECK_VACUITY_FRAGMENT not in result.output
