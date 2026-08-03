"""Tests for `firestarter/sdp_honesty.py` -- the four honesty assertions
protected by RETIRE-03 (Phase 132), now exercising the shared production
helper directly rather than driving `dev sdp` through Click's test harness.

History: originally authored as Click-test-harness tests for the `dev sdp`
subcommand (Phase 120 Plan 08), under the name `test_dev_sdp_cmd.py`. Phase
132 retires that subcommand -- the only production carrier of the D-10
honesty caveat and the D-14 unknown-command-to-outdated-firmware mapping --
and plan 132-02 relocated both pieces of wording into
`firestarter/sdp_honesty.py` before the subcommand's removal made them
unreachable through any CLI surface. This module was moved here by
`git mv tests/test_dev_sdp_cmd.py` in the same commit as
`tools/check_no_exists_proxy.py`'s target-list edit (RETIRE-02, D-03), and
this rewrite retargets the four surviving honesty assertions onto that
helper. Every other case this module used to carry (surface shape, gate
ordering, the consent matrix, the binary exit-code contract) either died
with the subcommand or is covered elsewhere -- see
`.planning/phases/132-retire-dev-sdp-discharge-the-mypy-debt/132-PRUNE-LEDGER.md`
for the full, counted account (D-04).

FALSE-GREEN TRAP, restated for the new SUT (the v1.22 HOST-01 lesson this
module has carried since Phase 120): when the SUT was a CLI surface with
four identically-exiting refusals, an exit-code-only assertion was the
known false-green -- an absent chip, a capability refusal and a
support-status refusal all exited non-zero identically, so only the reason
text distinguished gate order from gate presence. With a pure wording
helper the equivalent false-green is asserting that `emission_summary` or
`map_unknown_cmd_to_outdated` merely *exist*, or return *something* --
rather than asserting on the actual returned string, which is what every
test below still does.

What these four tests prove, and do not prove: they pin the **wording** of
the honesty caveat and the outdated-firmware mapping, exercised directly
against `firestarter/sdp_honesty.py`. Between this phase and Phase 134 (the
leg-report layer, the wording's next intended caller) that wording has **no
user-reachable carrier at all** -- D-05's residual, stated here rather than
left implicit. The one-time proof that this wording reaches a real console
through `click.echo` was taken in plan 132-02, while `dev_sdp` was still
wired through the helper and reachable via Click's test harness; it cannot
be re-taken from this module, which no longer drives any CLI surface at all.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import Mock, patch

from firestarter.cli_handlers import AppContext
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import EpromOperationError
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.messages import MSG_ERR_TIMEOUT, MSG_ERR_UNKNOWN_CMD
from firestarter.sdp_honesty import emission_summary, map_unknown_cmd_to_outdated

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_sdp_capability.py's `_FA_DIR` analog.
_FA_DIR = Path(__file__).parent.parent

# --- Concrete chip names, drawn from 120-SDP-PARTITION.md section 3 -- kept
# for now; plan 132-03 task 3 prunes every entry the rewritten module below
# no longer references and accounts for the removal in 132-PRUNE-LEDGER.md.
# ---

# Absent from the DB entirely -- Gate 1 (SAFE-04).
_ABSENT_CHIP = "NO_SUCH_CHIP_XYZ"
# FRAM -- capability-refused, support_status == "supported" (not adapter-required).
_FRAM_CHIP = "FM28V020"
# Pre-SDP DIP24_2816 generation -- capability-refused, support_status == "supported".
_PRESDP_DIP2816_CHIP = "2816"
# Non-0x0D chip -- wrong-protocol refusal.
_NON_0X0D_CHIP = "w27c512"
# Allowed 0x0D chip -- reaches the confirm/serial gates; the survivors below
# use this one to exercise the helper.
_ALLOWED_CHIP = "AT28C256"

# All nine `adapter-required` 0x0D parts (first alias token of each; see
# 120-SDP-PARTITION.md section 3's REFUSE table). D-08's capability-before-
# support-status ordering is load-bearing on every one of these.
_ADAPTER_REQUIRED_CHIPS = [
    "28C04A",
    "28C04AF",
    "28C16A",
    "28C16AF",
    "AT28C04",
    "AT28C04E",
    "AT28C16",
    "AT28C16E",
    "UPD28C04",
]


def make_app_context(**overrides: object) -> AppContext:
    """Construct a minimal, hardware-free AppContext.

    Mirrors test_dev_test_cmd.py's make_app_context: EpromDatabase uses
    skip_local_override=True and every manager is Mock(spec=...) unless the
    caller overrides it. No real serial port or bench access is ever opened.

    Dead as of this rewrite -- no test below calls this any more, since the
    four survivors exercise `firestarter/sdp_honesty.py` directly and no
    longer need an AppContext at all. Left in place for plan 132-03 task 3
    to remove and account for.
    """
    db = overrides.pop("db", None)
    if db is None:
        db = EpromDatabase(skip_local_override=True)
    config_manager = overrides.pop("config_manager", None)
    if config_manager is None:
        config_manager = ConfigManager()
    return AppContext(
        db=db,
        config_manager=config_manager,
        eprom_operator=overrides.pop("eprom_operator", Mock(spec=EpromOperator)),
        hardware_manager=overrides.pop("hardware_manager", Mock(spec=HardwareManager)),
        firmware_manager=overrides.pop("firmware_manager", Mock(spec=FirmwareManager)),
        eprom_presenter=overrides.pop(
            "eprom_presenter", Mock(spec=EpromConsolePresenter)
        ),
    )


def _off_tty():
    """Context manager forcing the off-TTY branch (D-06). Dead as of this
    rewrite -- see `make_app_context`'s docstring."""
    return patch("firestarter.cli_handlers._is_interactive", return_value=False)


def _on_tty():
    """Context manager forcing the on-TTY branch. Dead as of this rewrite --
    see `make_app_context`'s docstring."""
    return patch("firestarter.cli_handlers._is_interactive", return_value=True)


# ---------------------------------------------------------------------------
# Report honesty (D-10) + firmware-too-old (D-14) -- the four survivors,
# retargeted onto firestarter/sdp_honesty.py directly.
# ---------------------------------------------------------------------------


def test_summary_line_carries_the_unreadable_state_caveat_on_both_directions() -> None:
    """v1.22 HOST-05: symmetry matters because firmware's `0x5F`
    (`MSG_INFO_SDP_UNLOCK_DONE_US`) frame carries no honesty caveat where
    `0x61` (`MSG_INFO_SDP_LOCK_DONE_US`) does (F-120-03) -- so the host
    summary line is the ONLY carrier of the caveat on the unlock direction.
    The catalog fix itself is deferred to Phase 121/122; this test pins the
    host-side symmetry that stands in for it until then.

    Retargeted in Phase 132 (RETIRE-03): the SUT is now
    `sdp_honesty.emission_summary` directly. The symmetry this test pins is
    now unconditional **by construction** -- one function composes both
    directions' summary line, so there is no longer a second code path that
    could drift from the first."""
    enable_line = emission_summary("enable", _ALLOWED_CHIP)
    disable_line = emission_summary("disable", _ALLOWED_CHIP)
    assert "cannot be read back" in enable_line
    assert "cannot be read back" in disable_line


def test_summary_line_carries_no_duration_figure() -> None:
    """v1.22 HOST-05/D-10: the host summary line itself contains no
    microsecond unit and no digit-plus-unit duration token.

    Retargeted in Phase 132 (RETIRE-03): the SUT is now
    `sdp_honesty.emission_summary` directly, so the previous `next(...)`
    line-selection over a captured console run is no longer needed -- the
    helper returns exactly the host's own line, so the scoping this test
    relies on is now structural rather than selected.

    This remains mechanically enforced, not merely a discipline:
    `get_response()` filters the entire INFO band (`NON_RESPONSE_PREFIXES =
    ["INFO", "DEBUG"]`) out at `serial_comm.py:424`, so the operation layer
    literally cannot see the firmware's duration frame to plumb a figure
    through even if someone tried."""
    summary_line = emission_summary("enable", _ALLOWED_CHIP)
    assert not re.search(r"\d+\s*(us|µs|ms|s)\b", summary_line, re.IGNORECASE), (
        summary_line
    )


def test_no_fabricated_lock_state_boolean_in_the_report() -> None:
    """v1.22 HOST-05: the outcome sentence is framed as "the sequence was
    emitted" plus the caveat -- a positive framing assertion, not a brittle
    forbidden-substring word-list, so this leg does not rot as wording
    evolves. This is HOST-05's honesty floor: the host-side application of
    Phase 117 D-05 / Phase 118 D-02 / Phase 119 D-12 -- honesty in the
    message text, never in a status a caller could misread as a state
    claim.

    Retargeted in Phase 132 (RETIRE-03): the SUT is now
    `sdp_honesty.emission_summary` directly."""
    summary_line = emission_summary("enable", _ALLOWED_CHIP)
    assert "was emitted" in summary_line
    assert "cannot be read back" in summary_line
    assert "not a claim about the chip's actual state" in summary_line


def test_firmware_too_old_is_reported_when_unknown_cmd_comes_back() -> None:
    """v1.22 HOST-05/D-14: D-14 keys on the message **id**, not the text.
    This is the command half of HOST-06's asymmetry -- an unknown COMMAND
    produces an error and is detectable, whereas an unknown flag BIT
    produces silence, which is why the flag half needs plan 120-09's ack
    requirement instead.

    Retargeted in Phase 132 (RETIRE-03): the SUT is now
    `sdp_honesty.map_unknown_cmd_to_outdated` directly -- constructed (not
    raised) `EpromOperationError` in, constructed-but-not-raised
    `FirmwareOutdatedError | None` out, the same mock-free equivalent of the
    mock shape this test used before. A second, negative leg proves the
    mapper actually discriminates by `error_code` rather than always
    mapping -- a different code (`MSG_ERR_TIMEOUT`) must map to `None`."""
    exc = EpromOperationError("Unknown command: 9", error_code=MSG_ERR_UNKNOWN_CMD)
    outdated = map_unknown_cmd_to_outdated(exc, "enable", _ALLOWED_CHIP)
    assert outdated is not None
    assert "firestarter fw --install" in str(outdated), str(outdated)
    assert (
        "outdated" in str(outdated).lower()
        or "does not implement" in str(outdated).lower()
    )

    other_exc = EpromOperationError("Timed out", error_code=MSG_ERR_TIMEOUT)
    assert map_unknown_cmd_to_outdated(other_exc, "enable", _ALLOWED_CHIP) is None


# ---------------------------------------------------------------------------
# Import purity (D-01/D-02's forward contract): sdp_honesty.py must stay
# importless of click, so Phase 134's report layer (which has no CLI
# context of its own) can import it without pulling in a CLI dependency.
# ---------------------------------------------------------------------------


def test_sdp_honesty_module_imports_only_leaf_firestarter_modules() -> None:
    """Enforces the import-set invariant `sdp_honesty.py`'s own module
    docstring declares: its top-level import set is a subset of
    `{"__future__", "firestarter.exceptions", "firestarter.messages"}`. Both
    named modules are leaves (`exceptions.py` has zero top-level imports;
    `messages.py` imports only `dataclasses`), and in particular `click` is
    forbidden -- the caller performs the `click.echo`, so a `click`
    dependency here would make this module unusable from Phase 134's report
    layer.

    Scoped to top-level statements only (`tree.body`, never descending into
    a function or an `if TYPE_CHECKING:` block), so a future
    type-checking-only import is not mistaken for a runtime one -- mirrors
    `tests/test_sdp_capability.py`'s
    `test_sdp_capability_module_imports_nothing_but_stdlib_typing` leg
    (D-03's precedent for this exact shape)."""
    module_path = _FA_DIR / "firestarter" / "sdp_honesty.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    imported_modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)

    assert imported_modules <= {
        "__future__",
        "firestarter.exceptions",
        "firestarter.messages",
    }, (
        "D-01/D-02 forward contract: sdp_honesty.py's top-level imports "
        f"must stay a leaf-only subset; found {imported_modules}."
    )
