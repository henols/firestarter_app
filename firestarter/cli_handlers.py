"""Click-based CLI handlers for firestarter.

This module is the production CLI surface; main.py re-exports `cli` as `main`
for the console-script entry point.
"""

import datetime
import functools
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Literal  # noqa: UP035

import click
import click.shell_completion
from rich.console import Console

from firestarter import __version__ as version
from firestarter import (
    flash4_erase_gate,
    jp5_gate,
    log_capture,
    page_size_gate,
    sdp_honesty,  # unreadable_state_caveat(), called not re-authored
    transport_counters,
    write_blank_guard,
)
from firestarter.address_parser import parse_address, parse_size
from firestarter.channel import (
    BETA_ONLY_DEV_COMMANDS,
    available_boards,
    dev_command_gate_message,
    is_dev_tools_enabled,
)
from firestarter.chip_resolver import resolve_chip
from firestarter.chip_test import (
    OP_ID,
    SDP_HOLD_NOT_RUN,
    STATUS_ERROR,
    VERDICT_BAD,
    VERDICT_MARGINAL,
    VERDICT_NA,
    VERDICT_OK,
    VERDICT_SKIPPED,
    StepResult,
    count_applicable,
    derive_plan,
    is_uv_eprom,
    run_plan,
    run_status,
    sdp_hold_state,
    sdp_oracle_applicable,
)
from firestarter.config import ConfigManager, get_config_dir
from firestarter.constants import (
    FIRESTARTER_RELEASES_URL,
    FLAG_CHIP_ENABLE,
    FLAG_OUTPUT_ENABLE,
)
from firestarter.database import EpromDatabase
from firestarter.diagnostic_report import (
    AutoCapture,
    DiagnosticReport,
    TransportHealth,
    build_db_diff,
)
from firestarter.eprom_info import EpromConsolePresenter, print_eprom_list_table
from firestarter.eprom_operations import EpromOperator, build_flags
from firestarter.exceptions import (
    ChipNotFoundError,
    ChipNotImplementedError,
    EpromOperationError,
    FirmwareOperationError,
    FirmwareOutdatedError,
    HardwareOperationError,
    NegativeStartAddressError,
    PageAlignmentError,
    PageSizeUnavailableError,
    Pin1HazardRefusedError,
    ProtocolNotImplementedError,
    SerialError,
    SerialTimeoutError,
)
from firestarter.firmware import FIRMWARE_VERSION_RE, FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.lock_status import (
    classify_protection_response,
    exit_code_for_class,
    render_lock_status,
)
from firestarter.logging_utils import SingleLineStatusHandler
from firestarter.protection_readability import (
    GATE_TOKEN_READ_PERMITTED,
    protection_gate_for_entry,
)
from firestarter.sdp_capability import SDP_PROTOCOL_ID, sdp_capability

logger = logging.getLogger("Firestarter")


def _setup_logging(verbose: bool) -> None:
    """Set up logging in the same shape main.py uses today.

    Mirrors the verbose/non-verbose split + SingleLineStatusHandler replacement
    pattern from main.py:594-612. Kept here (rather than imported from main.py)
    so cli_handlers.py is import-clean from a fresh process and does not pull
    argparse into the Click execution path.
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    handler = SingleLineStatusHandler()
    if verbose:
        formatter = logging.Formatter(
            "%(levelname)-7s:%(name)-13s:%(lineno)4d: %(message)s"
        )
    else:
        formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    root_logger.handlers = [handler]


@dataclass
class AppContext:
    """Typed DI container threaded through every Click handler via ctx.obj.

    Constructed once at group entry; pulled by handlers via @click.pass_obj.
    CliRunner tests construct a fresh AppContext per test (mock managers OK).
    """

    db: EpromDatabase
    config_manager: ConfigManager
    eprom_operator: EpromOperator
    hardware_manager: HardwareManager
    firmware_manager: FirmwareManager
    eprom_presenter: EpromConsolePresenter


def _complete_eprom(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> List[click.shell_completion.CompletionItem]:  # noqa: UP006
    """Click shell_complete callback — chip names matching `incomplete` (case-insensitive prefix).

    Runs out-of-process during shell completion; instantiates its own
    EpromDatabase rather than going through ctx.obj (ctx is not constructed
    by Click during completion). Mirrors the argcomplete EpromCompleter
    semantics at main.py:39-45.
    """  # noqa: E501
    db = EpromDatabase()
    return [
        click.shell_completion.CompletionItem(e["name"])
        for e in db.get_eproms(False)
        if e["name"].lower().startswith(incomplete.lower())
    ]


# Release-channel gate — see channel.py. Evaluated at import time on purpose: a
# wheel's __version__ is fixed when it is built, so the choice list a stable
# install renders in `fw --help` is decided once and is decided correctly. Tests
# exercise channel.available_boards() / is_board_available() directly rather than
# reloading this module.
_ALL_BOARDS: tuple[str, ...] = ("uno", "uno328pb", "leonardo", "py32f071")
_BOARD_CHOICES: list[str] = available_boards(_ALL_BOARDS)
_PY32_ENABLED: bool = "py32f071" in _BOARD_CHOICES


def _reject_py32_only_option(name: str, given: bool) -> None:
    """Refuse a py32-only CLI option outside its owning channel.

    `hidden=not _PY32_ENABLED` is a --help cosmetic ONLY: it hides the option
    from the rendered help, it does not reject it when a user types it anyway.

    This is the single sanctioned refusal mechanism for every py32-only option,
    called unconditionally for each one before either is consumed, so the
    refusals cannot drift apart and a third option added later inherits the
    behaviour by calling it here. A test asserts this message occurs exactly
    once in this file.

    Reads `_PY32_ENABLED` at CALL time -- a module global, not a captured
    default -- which is what makes it monkeypatchable while the Click surface
    stays frozen at import time.
    """
    if given and not _PY32_ENABLED:
        raise click.UsageError(f"no such option: {name}")


def map_typed_errors(f: Callable[..., Any]) -> Callable[..., Any]:
    """Map service-layer typed exceptions to ClickException + stable exit codes."""

    @functools.wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return f(*args, **kwargs)
        except ChipNotFoundError as e:
            raise click.ClickException(str(e)) from e
        except FirmwareOutdatedError as e:
            raise click.ClickException(f"Firmware outdated: {e}") from e
        except (SerialError, SerialTimeoutError) as e:
            raise click.ClickException(f"Communication error: {e}") from e
        except ProtocolNotImplementedError as e:
            raise click.ClickException(
                f"Unsupported protocol: {e} — this protocol is recognized but not yet implemented in the firmware."
            ) from e
        except ChipNotImplementedError as e:
            # Render the reason string verbatim.
            # The unsupported_reason strings begin with the
            # required wording, so str(e) = "<name>: <reason>" is already
            # the authoritative status-specific message. Drop the generic
            # "Chip not usable:" prefix so the DB string is the single source
            # of truth for both info display and chip-op refusal.
            raise click.ClickException(str(e)) from e
        except FirmwareOperationError as e:
            # Raised by the USB DFU install path. The message is already
            # operator-actionable (how to enter the bootloader, or how to install
            # pyusb), so it is rendered verbatim rather than prefixed.
            raise click.ClickException(str(e)) from e
        except PageSizeUnavailableError as e:
            raise click.ClickException(str(e)) from e
        except PageAlignmentError as e:
            raise click.ClickException(str(e)) from e
        except NegativeStartAddressError as e:
            # Rendered verbatim, above the generic EpromOperationError arm
            # below -- that arm prefixes "Programmer error: ", which would
            # make this host-voiced refusal (write_blank_guard.py) read as a
            # hardware fault instead of an input-validation refusal.
            raise click.ClickException(str(e)) from e
        except EpromOperationError as e:
            raise click.ClickException(f"Programmer error: {e}") from e
        except Pin1HazardRefusedError as e:
            raise click.ClickException(str(e)) from e
        except HardwareOperationError as e:
            raise click.ClickException(f"Hardware error: {e}") from e

    return wrapper


def build_arg_flags(args: object) -> int:
    """Argparse-Namespace/PlainArgs-bag adapter over ``_build_op_flags``.

    Relocated verbatim (W1's getattr fix preserved byte-identical) from
    main.py:504-518. This is the bag-introspection form
    used by tests/test_bug_characterization.py to pin the BUG-1 contract
    (PlainArgs object with no ``__contains__`` must not raise TypeError).

    Click handlers use ``_build_op_flags(**kwargs)`` directly; this wrapper
    exists for the BUG-1 characterization test that pins the post-Phase-41
    truthiness semantics on the historical helper name.
    """
    blank_check = getattr(args, "blank_check", True)
    force = getattr(args, "force", False)
    verbose = getattr(args, "verbose", False)
    vpe_as_vpp = getattr(args, "vpe_as_vpp", False)
    # Skip-erase is its own explicit flag, NOT implied by
    # `not blank_check`. See _build_op_flags for the rationale (write -b must
    # still erase an erase-capable chip).
    flags = build_flags(
        blank_check,
        force,
        vpe_as_vpp,
        verbose,
        skip_erase=getattr(args, "skip_erase", False),
    )

    if hasattr(args, "input_enable"):
        flags |= 0 if args.input_enable else FLAG_OUTPUT_ENABLE
    if hasattr(args, "chip_disable"):
        flags |= 0 if args.chip_disable else FLAG_CHIP_ENABLE

    return flags


def _maybe_auto_route_to_pre(args: object) -> None:
    """When the installed app is a pre-release, any `fw` invocation that pins
    no channel auto-routes to `--pre`.

    `install` is deliberately NOT part of the condition -- see the guard below
    for the two defects gating on it caused.

    A stable-installed app is unaffected, and an explicit --firmware-version or
    --stable opts out.
    """
    helper_logger = logging.getLogger(__name__)
    # The condition is "the operator pinned no channel", NOT "the operator
    # typed --install". Requiring --install made every OTHER fw invocation on
    # a pre-release app resolve the STABLE channel:
    #   * bare `fw` compared the installed firmware against the newest STABLE
    #     firmware (2.0.6), so a beta app on beta firmware printed "already up
    #     to date" and the newer beta firmware was invisible;
    #   * `fw --force` — the documented reinstall escape hatch — resolved the
    #     stable asset and would have DOWNGRADED the board to firmware this
    #     host cannot speak to, bricking the pairing it was invoked to repair.
    # A stable-installed app is unaffected, and --stable or --firmware-version
    # opts out: those are the other two clauses below and the is_prerelease
    # test.
    if (
        getattr(args, "pre", False)
        or getattr(args, "firmware_version", None)
        or getattr(args, "stable", False)
    ):
        return
    try:
        from packaging.version import InvalidVersion, Version

        import firestarter as _pkg

        try:
            if Version(_pkg.__version__).is_prerelease:
                args.pre = True  # type: ignore[attr-defined]
                helper_logger.info(
                    "Beta app detected — defaulting to --pre. "
                    "Use --firmware-version X.Y.Z to pin a stable version."
                )
        except InvalidVersion:
            pass
    except ImportError:
        pass


def _build_op_flags(
    *,
    blank_check: bool = True,
    force: bool = False,
    verbose: bool = False,
    vpe_as_vpp: bool = False,
    skip_erase: bool = False,
    # SDP auto-unlock tripwire, edit point 1 of 2: this default is one of
    # the two places a developer would touch to disable the host's
    # auto-unlock. Before changing it, read the tripwire comment at the
    # SDP auto-set condition inside write() below -- flipping this default
    # invalidates the removal-safety argument the `dev sdp` removal rests on.
    skip_sdp_unlock: bool = False,
    input_enable: bool | None = None,
    chip_disable: bool | None = None,
) -> int:
    """Map Click kwargs to wire flags.

        - blank_check / force / vpe_as_vpp / verbose -> build_flags(...)
        - skip_erase -> FLAG_SKIP_ERASE (explicit; see below)
        - input_enable / chip_disable presence (any value, even False) -> apply
          the OE / CE mask rule

    OE/CE use None to mean "this command does not take this flag", so only
    `dev reg` and `dev addr` opt in.

    `-b`/`--no-blank-check` does NOT imply skip-erase. Skipping the blank check
    must not skip the erase that electrically-erasable parts require: `write -b`
    on a non-blank such chip used to silently skip the erase, leaving
    un-erasable 0->1 bits while the firmware's DQ7-only poll reported success.
    `skip_erase` is an explicit opt-in.
    """
    # FLAG_SKIP_SDP_UNLOCK is passed INTO build_flags as a keyword, not OR-ed in
    # afterwards the way OE/CE are below: every wire-flag bit stays mapped in the
    # one function that maps wire flags.
    flags = build_flags(
        blank_check,
        force,
        vpe_as_vpp,
        verbose,
        skip_erase=skip_erase,
        skip_sdp_unlock=skip_sdp_unlock,
    )
    if input_enable is not None:
        flags |= 0 if input_enable else FLAG_OUTPUT_ENABLE
    if chip_disable is not None:
        flags |= 0 if chip_disable else FLAG_CHIP_ENABLE
    return flags


class _FirmwareVersionType(click.ParamType):
    """Validates a firmware version against FIRMWARE_VERSION_RE before any
    network call; accepts stable (X.Y.Z) and pre-release (X.Y.ZbN, X.Y.ZrcN).

    `self.fail(...)` raises click.BadParameter, which Click converts to
    SystemExit(2).

    Distinct from `SerialCommunicator._validate_firmware_version`, which is the
    TRANSPORT-layer guard; this is the CLI-layer input validator.
    """

    name = "firmware_version"

    def convert(
        self,
        value: str | None,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str | None:
        if value is None:
            return None
        if not FIRMWARE_VERSION_RE.match(value):
            self.fail(
                f"Invalid firmware version {value!r}. "
                "Expected X.Y.Z, X.Y.ZbN, or X.Y.ZrcN "
                "(e.g. 3.1.0, 3.1.0b2, 3.1.0rc1).",
                param,
                ctx,
            )
        return value


_CLI_START_MONOTONIC_META_KEY = "_firestarter_cli_start_monotonic"


def _cli_start_time() -> float | None:
    """Return the monotonic timestamp the CLI group's first statement
    recorded into the current Click context's `meta` mapping, or `None`
    when there is no current Click context (a direct handler call, e.g.
    from a unit test with no CLI entry point in play) or no stamp was ever
    recorded there. `meta` is shared down the whole context chain, so a
    value the group wrote is readable from any subcommand's own context --
    this is per-invocation state, never module-level, so one `CliRunner`
    invocation cannot hand a later one a stale base.
    """
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return None
    return ctx.meta.get(_CLI_START_MONOTONIC_META_KEY)


@click.group(
    help="EPROM programmer for Arduino and Relatively-Universal-ROM-Programmer shield."
)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose mode")
@click.option(
    "-p",
    "--port",
    default=None,
    help="Serial port to use (e.g. /dev/ttyACM1). Overrides the saved port in config.json for this invocation.",  # noqa: E501
)
@click.version_option(version=version, prog_name="Firestarter")
@click.pass_context
@map_typed_errors
def cli(ctx: click.Context, verbose: bool, port: str | None) -> None:
    ctx.meta[_CLI_START_MONOTONIC_META_KEY] = time.monotonic()
    # CliRunner tests pass a pre-built AppContext via `runner.invoke(cli, ..., obj=app)`;
    # honor that and skip manager construction in test mode. In production
    # ctx.obj starts as None (Click default) so the manager-construction path
    # runs verbatim. This is the standard "AppContext-on-ctx.obj" pattern from
    # Click's docs (https://click.palletsprojects.com/en/stable/complex/).
    # _setup_logging must run AFTER the test-mode short-circuit so it
    # does not destructively replace pytest's caplog handler on the root logger.
    if ctx.obj is not None and isinstance(ctx.obj, AppContext):
        return

    _setup_logging(verbose)

    config_manager = ConfigManager()
    if port:
        config_manager.set_value("port", port, persist=False)

    db = EpromDatabase()
    ctx.obj = AppContext(
        db=db,
        config_manager=config_manager,
        eprom_operator=EpromOperator(config_manager),
        hardware_manager=HardwareManager(config_manager),
        firmware_manager=FirmwareManager(config_manager),
        eprom_presenter=EpromConsolePresenter(db),
    )


@cli.command(name="list")
@click.option("-v", "--verified", is_flag=True, help="Only shows verified EPROMs")
@click.pass_obj
@map_typed_errors
def _list_cmd(app: AppContext, verified: bool) -> None:
    """List all EPROMs in the database."""
    eprom_data_list = app.db.get_eproms(verified=verified)
    if eprom_data_list:
        print_eprom_list_table(eprom_data_list, app.eprom_presenter.spec_builder)
        sys.exit(0)
    sys.exit(1)


@cli.command(name="info")
@click.argument("eprom", shell_complete=_complete_eprom)
@click.option("-c", "--config", is_flag=True, help="Show EPROM config.")
@click.option("-a", "--adapter", is_flag=True, help="Show adapter pin wiring table.")
@click.pass_obj
@map_typed_errors
def info(app: AppContext, eprom: str, config: bool, adapter: bool) -> None:
    """EPROM info."""
    eprom_details = app.db.get_eprom(eprom)
    if not eprom_details:
        logger.error(f"EPROM '{eprom}' not found in database.")
        sys.exit(1)

    eprom_data_for_programmer = app.db.convert_to_programmer(eprom_details)
    raw_config_data, manufacturer = app.db.get_eprom_config(eprom)

    structured_details = app.eprom_presenter.prepare_detailed_eprom_data(
        eprom,
        eprom_details,
        eprom_data_for_programmer,
        raw_config_data,
        manufacturer,
        include_export_config=config,
        include_adapter=adapter,
    )
    if structured_details:
        app.eprom_presenter.present_eprom_details(
            structured_details,
            show_export_config=config,
            show_adapter=adapter,
        )
        sys.exit(0)
    sys.exit(1)


@cli.command(name="search")
@click.argument("text")
@click.pass_obj
@map_typed_errors
def search(app: AppContext, text: str) -> None:
    """Search for EPROMs in the database."""
    search_results = app.db.search_eprom(text, include_unverified=True)
    if search_results:
        print_eprom_list_table(search_results, app.eprom_presenter.spec_builder)
        sys.exit(0)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Chip-op commands
# Each: resolve chip via resolve_chip(eprom, db=app.db) → call
# app.eprom_operator.<op> → sys.exit(0 if ok else 1). The @map_typed_errors
# decorator catches ChipNotFoundError at the Click boundary and re-raises as
# click.ClickException → exit 1. Per-option help text byte-identical to argparse.
# ---------------------------------------------------------------------------


@cli.command(name="read")
@click.argument("eprom", shell_complete=_complete_eprom)
@click.argument("output_file", required=False)
@click.option(
    "-f", "--force", is_flag=True, help="Force, even if the chip id doesn't match."
)
@click.option("-a", "--address", default=None, help="Read start address in dec/hex")
@click.option("-s", "--size", default=None, help="Size of the data to read in dec/hex")
@click.pass_obj
@map_typed_errors
def read(
    app: AppContext,
    eprom: str,
    output_file: str | None,
    force: bool,
    address: str | None,
    size: str | None,
) -> None:
    """Reads the content from an EPROM."""
    eprom_data = resolve_chip(eprom, db=app.db)
    ok = app.eprom_operator.read_eprom(
        eprom,
        eprom_data,
        output_file,
        operation_flags=_build_op_flags(force=force),
        address_str=address,
        size_str=size,
    )
    sys.exit(0 if ok else 1)


# Phase 203 (D-13/D-14, `exit_code_contract_resolved` in 203-03-PLAN.md):
# the five terminal lines `write --verify` can print -- see the five
# constants below. Module-level, format-constant style (mirrors
# `write_blank_guard._REFUSAL_FORMAT`), specifically so a test can assert
# whole sentences AND assert the forbidden word's absence over the
# constants themselves, not over one rendered run.
#
# The rule that makes the branch below readable: the LINE is chosen by what
# happened to the chip, the EXIT CODE by why the invocation ended. That is
# why the three no-write arms (a guard-read transport failure, a
# transport/connection failure during the write itself, and a host- or
# firmware-decided write failure) all share one line and differ only in
# exit code -- and why the could-not-verify line is reserved for the single
# arm where a write genuinely landed and only the read-back failed. No
# separate constant distinguishes the exit-2-vs-exit-1 split among the
# no-write arms: the underlying cause stays visible in the log
# (`_run_state_machine` already emits its own communication-error or
# programmer-error line), so nothing is asked of the operator that only the
# exit code could answer.
#
# The one constant beyond those four exists for a different reason: it is a
# LANDED-write arm, not a no-write arm -- `write_eprom` records
# `last_write_attempt_verdict = 0` before the `--skip-sdp-unlock` ack block
# flips `is_ok`, so the data reached the chip and only the host's check of
# the opt-out acknowledgement failed (203-REVIEW WR-01, 207.1 D-06).
_WRITE_VERIFY_VERDICT_OK = "Write to {eprom}: verified -- the read-back matches."
_WRITE_VERIFY_VERDICT_MISMATCH = (
    "Write to {eprom}: landed, but the read-back did not verify."
)
_WRITE_VERIFY_VERDICT_UNREADABLE = (
    "Write to {eprom}: landed, but could not be verified -- the read-back failed."
)
_WRITE_VERIFY_VERDICT_NO_WRITE = (
    "Write to {eprom}: did not complete -- nothing was verified."
)
_WRITE_VERIFY_VERDICT_LANDED_UNACKNOWLEDGED = (
    "Write to {eprom}: landed, but the firmware did not acknowledge "
    "--skip-sdp-unlock -- nothing was verified."
)


@cli.command(name="write")
@click.argument("eprom", shell_complete=_complete_eprom)
@click.argument("input_file")
@click.option(
    "-b",
    "--no-blank-check",
    "blank_check",
    is_flag=True,
    flag_value=False,
    default=True,
    help="Skip the blank check before write (erase still runs if the chip supports it).",
)
@click.option(
    "--skip-erase",
    "skip_erase",
    is_flag=True,
    default=False,
    help="Also skip the pre-write erase (for already-blank or non-erasable/pre-erased parts). "
    "WARNING: skipping erase on a non-blank electrically-erasable chip leaves un-erased bits "
    "that cannot be reprogrammed.",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force, even if the VPP or chip id doesn't match.",
)
@click.option("-a", "--address", default=None, help="Write start address in dec/hex")
@click.option("--vpe-as-vpp", "vpe_as_vpp", is_flag=True, help="Use VPE as VPP voltage")
@click.option(
    "--pulse-us",
    "pulse_us",
    type=click.IntRange(1, 65535),
    default=None,  # NOT 0 -- click.IntRange type-casts the default through
    # type_cast_value and short-circuits ONLY on None. default=0 is out of
    # range for IntRange(1, 65535), so Click would raise a UsageError BEFORE
    # write()'s body ever runs -- making EVERY `firestarter write` invocation
    # (even with no --pulse-us at all) exit 2 (RESEARCH Pitfall 3, measured
    # on click 8.4.2 in .venv/ci-replica). The nearest in-tree precedent,
    # --read-settling / --read-strobe (below, inside `dev consistency-check`,
    # a dev-gated command), uses `type=int, default=0` with NO range --
    # copying that shape here is the natural move and it is fatal.
    # click.IntRange has ZERO other usages anywhere in this repository; this
    # is a new form, not a followed one.
    help="Override the database program-pulse width for this run (microseconds, "
    "1-65535). This bound is minipro parity (-o pulse=N is a uint16), NOT a "
    "wire-type or hardware limit -- see write()'s docstring.",
)
# SDP auto-unlock tripwire, edit point 2 of 2: this option's `default=False`
# is the second place a developer would touch to disable the host's
# auto-unlock. Before changing it, read the tripwire comment at the SDP
# auto-set condition inside write() below -- flipping this default
# invalidates the removal-safety argument the `dev sdp` removal rests on.
@click.option(
    "--skip-sdp-unlock",
    "skip_sdp_unlock",
    is_flag=True,
    default=False,
    help="Decline the automatic SDP unlock firmware performs at the start of every "
    "protocol-0x0D write. WARNING: on a chip whose software data protection is "
    "actually enabled, the write will then fail. Has NO EFFECT on any other "
    "protocol — the host warns and proceeds.",
)
# Phase 203 (D-13, the phase's one-way door -- confirmed at 203-03-PLAN.md's
# Task 1 checkpoint, see 203-03-SUMMARY.md's "Checkpoint Decision" section):
# --verify changes THIS INVOCATION's exit-code contract; plain `write`
# (without this flag) is completely unchanged.
@click.option(
    "--verify",
    "verify",
    is_flag=True,
    help="After a successful write, read the written region back and compare it "
    "through the same engine `verify` uses. Changes THIS INVOCATION's exit-code "
    "contract: 0 the write landed and the read-back matched, 1 the invocation "
    "ended for a reason the host or the firmware decided (a blank-guard "
    "refusal, a firmware error during the write, a malformed address, or a "
    "read-back that completed and disagreed), 2 the transport or the hardware "
    "failed in ANY phase of the run -- the guard read, the write itself, or "
    "the read-back. Without --verify, write exits 0 on success and 1 on any "
    "failure, exactly as before.",
)
@click.option(
    "--full",
    "full",
    is_flag=True,
    help="With --verify, report every coalesced mismatching range in the "
    "read-back comparison, not just the first. Has no meaning and is refused "
    "without --verify.",
)
@click.pass_obj
@map_typed_errors
def write(
    app: AppContext,
    eprom: str,
    input_file: str,
    blank_check: bool,
    skip_erase: bool,
    force: bool,
    address: str | None,
    vpe_as_vpp: bool,
    pulse_us: int | None,
    skip_sdp_unlock: bool,
    verify: bool,
    full: bool,
) -> None:
    """Writes a binary file to an EPROM.

    \b
    Blank check and erase are separate steps:
      -b, --no-blank-check  skip the blank check only -- a pre-write erase
                            still runs on electrically-erasable chips
      --skip-erase          skip the pre-write erase as well

    Before the write reaches the port, the host -- not the firmware --
    checks that the target region is blank. Four protocol families never
    receive that check: 0x0D (28C parallel) and 0x05 (flash4) auto-erase
    per page immediately before each write, and the SRAM and FRAM
    families have no blank state to check at all. -b has nothing to skip
    on those four families; on every protocol outside this list, it is
    still the way to skip the check.

    --skip-sdp-unlock applies to protocol-0x0D chips, where the firmware
    unlocks software data protection during write init. On any other protocol
    it has no effect and the write proceeds. The host may also set it on its
    own when the resolved chip is protocol-0x0D and its protection state
    cannot be read, and always reports when it does.

    --pulse-us overrides the database program-pulse width for this run only,
    in the range 1..65535 microseconds. Out-of-range values are refused before
    the port is opened. A value above 50000 is refused by the firmware on
    protocol 0x0B before any high voltage is enabled. Using the flag always
    reports both the database pulse and the override, so a log captured
    without its command line still records which pulse was used.

    --verify changes THIS INVOCATION's exit-code contract to 0/1/2 (see
    --verify's own --help text for the full three-way split); plain write,
    without the flag, keeps its existing 0/1 contract unchanged.
    """
    if full and not verify:
        raise click.UsageError(
            "--full has no meaning without --verify: there is no read-back "
            "comparison for it to apply to."
        )

    eprom_data = resolve_chip(eprom, db=app.db)

    # A SEPARATE, sibling `if` -- never an
    # `elif` chained onto the SDP auto-set or skip-erase blocks below, so this
    # can co-fire with either on the same chip. click.echo (never logger.info):
    # this must be visible at DEFAULT verbosity, with no -v needed. Reason:
    # a bench artifact or log captured without the command line beside it
    # cannot otherwise tell you the pulse was not the database's -- and
    # this evidence will be read by strangers.
    if pulse_us is not None:
        db_pulse = eprom_data.get("pulse-delay", 0)
        db_shown = (
            f"{db_pulse} us"
            if db_pulse
            else "firmware default (database supplied none)"
        )
        click.echo(
            f"{eprom.upper()}: --pulse-us {pulse_us} overrides the database "
            f"program pulse for this run ({db_shown} -> {pulse_us} us). "
            "This run's timing is NOT the database's."
        )

    # SDP auto-set: decided here, in the handler, because
    # this is the last place with both the chip NAME and app.db — resolve_chip's
    # programmer dict carries neither `protocol-id` nor `name`.
    # This is a DELIBERATE DIVERGENCE from 3.0.0b11 for the capability-refused
    # 0x0D subset, not a no-op: today's `write` already emits the SDP-disable
    # sequence before the payload on those parts, leaving 0x2AAA<-0x55 /
    # 0x5555<-0x20 stored as data at the bus-truncated magic addresses (an
    # address-ranged or short write does not get overwritten by the payload).
    # The trade-off is dissolved rather than decided on the derived 43/41
    # partition: a part with no SDP has nothing to unlock, so suppressing its
    # auto-unlock costs that part nothing and additionally avoids those three
    # stored bytes. Residual risk is confined to 9 watch-listed entries.
    sdp_entry = app.db.get_eprom(eprom)
    is_protocol_0x0d = (
        bool(sdp_entry) and sdp_entry.get("protocol-id") == SDP_PROTOCOL_ID
    )
    allowed, sdp_reason = sdp_capability(eprom, app.db)
    # SDP auto-unlock tripwire -- THE decision site. This condition IS the
    # removal-safety argument for deleting the standalone
    # `firestarter dev sdp` subcommand): that deletion was safe only BECAUSE
    # this auto-unlock fires by default, unconditionally, for every
    # capability-refused protocol-0x0D part on every `write` -- no user needs
    # a manual unlock surface as long as this stays true. Flipping either
    # default this condition depends on (`skip_sdp_unlock: bool = False` in
    # `_build_op_flags` above, or the `--skip-sdp-unlock` Click option's
    # `default=False` above), narrowing this condition, or making the flag
    # default to SKIPPING the unlock invalidates the removal argument and
    # requires that removal decision to be revisited alongside the change. The
    # companion test that pins this dependency and fails if it breaks is
    # `test_dev_sdp_removal_is_safe_only_because_auto_unlock_is_default_on`
    # in tests/test_write_skip_sdp_unlock.py; the companion note lives at
    # FLAG_SKIP_SDP_UNLOCK's definition in constants.py.
    if is_protocol_0x0d and not allowed and not skip_sdp_unlock:
        skip_sdp_unlock = True
        click.echo(
            f"{eprom.upper()}: auto-setting --skip-sdp-unlock on your behalf "
            f"({sdp_reason}). Firmware's automatic SDP unlock is keyed on "
            "protocol, not on this specific part, so without this the unlock "
            "sequence's command bytes would be stored as data at the "
            "bus-truncated magic addresses on a part with no SDP command "
            "decoder."
        )
    elif skip_sdp_unlock and not is_protocol_0x0d:
        # Warn and proceed: the user asked for something vacuous on this
        # protocol. Do NOT refuse, do NOT abort, do NOT suppress the bit —
        # firmware never reads FLAG_SKIP_SDP_UNLOCK outside protocol 0x0D, so
        # nothing unsafe happens either way, and a blanket-flag script across
        # a mixed batch of chips must still produce identical wire frames.
        observed_protocol = sdp_entry.get("protocol-id") if sdp_entry else None
        click.echo(
            f"{eprom.upper()}: --skip-sdp-unlock has no effect on this chip's "
            f"protocol (observed protocol {observed_protocol!r}) — firmware "
            "only reads this bit on protocol 0x0D writes. Proceeding with a "
            "normal write."
        )

    # Warn and proceed. A deliberate SIBLING `if`, not an `elif`: this checks
    # --skip-erase, a different flag from the SDP block above, so both must be free
    # to fire on the same 0x0D chip.
    #
    # Do NOT refuse, abort, or suppress the bit. Nothing on the 0x0D write path
    # reads an erase-capability bit, so the flag is inert here either way, and the
    # bit is still emitted so a blanket-flag script across a mixed batch produces
    # byte-identical wire frames.
    #
    # The message must NOT claim the 28C family has no erase operation -- it has a
    # standalone `erase`. What is true is narrower: the WRITE path performs no
    # erase, so the flag has nothing to skip there.
    #
    # Deliberately not extended to -b/--no-blank-check: warning that it is vacuous
    # would train users to think the write needs a flag.
    if skip_erase and is_protocol_0x0d:
        click.echo(
            f"{eprom.upper()}: --skip-erase has nothing to skip on this "
            "chip's protocol — the 28C family's write path (protocol 0x0D) "
            "performs no erase step, so there is nothing here for this "
            "flag to skip; each page write applies directly. The family "
            "does have a standalone erase, reachable as `firestarter "
            "erase`, which this flag does not affect. Proceeding with a "
            "normal write."
        )

    if not jp5_gate.confirm_or_refuse(eprom, eprom_data.get("bus-config"), "write"):
        sys.exit(1)
    page_size_gate.require_page_size(eprom, eprom_data, "write")
    # Folded todo `2026-09-16-reject-negative-write-start-address.md`, host
    # half: called here, at the CLI tier, so a negative `-a` on `write`
    # refuses before `app.eprom_operator.write_eprom` is ever invoked, on
    # every write family (guarded or not). `write_eprom` also calls this
    # gate itself (write_blank_guard.require_non_negative_address's own
    # call site), which is what protects `dev test` and `dev write-cycle`
    # -- callers that never go through this CLI handler at all. This call
    # now runs between the page-size gate above and the page-alignment
    # gate below, so a negative start address that is also misaligned, or
    # paired with a misaligned length, gets this clearer refusal instead of
    # the alignment gate's signed-hex wording (203-REVIEW IN-01, 207.1
    # D-08). `require_non_negative_address` returns silently on an
    # unparseable address, so the alignment gate below still owns the
    # could-not-parse error.
    write_blank_guard.require_non_negative_address(eprom, address)
    page_size_gate.require_page_alignment(
        eprom, eprom_data, "write", address, input_file
    )

    ok = app.eprom_operator.write_eprom(
        eprom,
        eprom_data,
        input_file,
        address_str=address,
        operation_flags=_build_op_flags(
            blank_check=blank_check,
            force=force,
            vpe_as_vpp=vpe_as_vpp,
            skip_erase=skip_erase,
            skip_sdp_unlock=skip_sdp_unlock,
        ),
        # Translate Click's `None` ("--pulse-us not supplied") into
        # write_eprom's own integer sentinel (0 means "use the database
        # value" -- see that function's docstring).
        pulse_us=pulse_us or 0,
        pin1_hazard_acknowledged=True,
        # Phase 203 (D-14): suppress write_eprom's own verdict line exactly
        # when --verify is set, so the plain path (verify=False) is
        # untouched byte-for-byte and the --verify path prints exactly one
        # combined line instead of two.
        suppress_verdict_line=verify,
        # FWBLANK-04 (Phase 205): `-b`/`--no-blank-check` no longer travels
        # as a wire bit at all -- it reaches the host-side write guard
        # directly through this keyword.
        blank_check_requested=blank_check,
    )

    if not verify:
        # The plain, unchanged 0/1 contract. Neither verdict attribute is
        # ever read on this path -- D-13's widened contract applies to the
        # --verify invocation only.
        sys.exit(0 if ok else 1)

    if ok:
        # Phase 203 (D-13/D-16): the write landed -- read the same region
        # back through Phase 202's engine. size_str=None resolves the
        # region to the input file's own length, which is exactly the
        # region this write just touched -- D-16's region for free, without
        # computing it a second time here.
        #
        # 203-CR-01: pin the read-back's own connect to the exact port the
        # write itself just used. `getattr(..., None)` rather than a plain
        # attribute read -- an `EpromOperator` double (test or otherwise)
        # that predates this attribute degrades to "no pin" instead of
        # raising `AttributeError`. `write_eprom` resets
        # `last_write_port` to `None` at its own entry and only ever sets it
        # once its own COMMAND_WRITE connect actually succeeds, so a value
        # read here can only be `None` (no pin: `verify_eprom` falls back to
        # its own normal discovery) or the genuine port this write reached.
        verdict = app.eprom_operator.verify_eprom(
            eprom,
            eprom_data,
            input_file,
            address_str=address,
            size_str=None,
            operation_flags=_build_op_flags(force=force),
            full=full,
            suppress_verdict_line=True,
            preferred_port=getattr(app.eprom_operator, "last_write_port", None),
        )
        verdict_line = {
            0: _WRITE_VERIFY_VERDICT_OK,
            1: _WRITE_VERIFY_VERDICT_MISMATCH,
            2: _WRITE_VERIFY_VERDICT_UNREADABLE,
        }[verdict]
        click.echo(verdict_line.format(eprom=eprom.upper()))
        sys.exit(verdict)

    # ok is False: the write itself never completed. Read both cause
    # channels -- every phase of a --verify invocation has one, so no arm
    # of this branch has to guess (exit_code_contract_resolved's seven-arm
    # table, arms 1-4). The LINE is chosen by what happened to the chip;
    # the EXIT CODE by why the invocation ended.
    guard_verdict = app.eprom_operator.last_write_guard_verdict
    attempt_verdict = app.eprom_operator.last_write_attempt_verdict
    if guard_verdict == 1:
        # Arm 1: the guard refused. It already printed its own one line
        # (D-11) -- print nothing more, because D-11's one-line property
        # outranks D-14's verdict line for a refusal that never became a
        # write.
        sys.exit(1)
    if guard_verdict == 2:
        # Arm 2: the guard read itself failed for a transport or hardware
        # reason. The write never ran -- the no-write line, not
        # could-not-verify: nothing landed, and a line that says otherwise
        # is the class of claim WRITE-05 exists to prevent.
        click.echo(_WRITE_VERIFY_VERDICT_NO_WRITE.format(eprom=eprom.upper()))
        sys.exit(2)
    if attempt_verdict == 2:
        # Arm 3: the write was attempted and the transport or the hardware
        # failed under it -- the arm a plan review found missing, and the
        # likeliest transport failure of the three (the write is the
        # longest, most hardware-stressed leg of the run).
        click.echo(_WRITE_VERIFY_VERDICT_NO_WRITE.format(eprom=eprom.upper()))
        sys.exit(2)
    if attempt_verdict == 0:
        # Arm 4b: reachable only through the `--skip-sdp-unlock` ack
        # failure, because every other `False` return leaves the attempt
        # verdict at `None`, 1, or 2. The write landed on the wire --
        # `write_eprom` records verdict 0 before the ack block flips
        # `is_ok` -- so the no-write line above would be false here
        # (203-REVIEW WR-01, 207.1 D-06).
        click.echo(
            _WRITE_VERIFY_VERDICT_LANDED_UNACKNOWLEDGED.format(eprom=eprom.upper())
        )
        sys.exit(1)
    # Arm 4: the write failed for a reason the host or the firmware
    # decided (attempt_verdict == 1, or -- defensively -- any other value).
    click.echo(_WRITE_VERIFY_VERDICT_NO_WRITE.format(eprom=eprom.upper()))
    sys.exit(1)


def _region_refusal_exit_code(
    *,
    eprom: str,
    eprom_data: dict,
    address: str | None,
    size: str | None,
    input_file: str | None = None,
) -> int | None:
    """D-17 region resolution and its two pre-wire refusals, shared by
    `verify` and `blank`.

    An explicit `--size` wins when given. Without one, the declared region
    length is `input_file`'s own length (`verify`) or the rest of the chip
    from `--address` onward (`input_file=None`, `blank`'s default) -- the
    whole chip only when `--address` is also absent. `blank -a <addr>` with
    no `--size` still declares a real, boundable region ("from `addr` to
    the chip's end"), not "nothing to bound" -- CR-01 (202-05 code review):
    an earlier version of this function left `length` as `None` whenever
    both `--size` and `input_file` were absent, which skipped the
    past-chip-end check entirely for exactly this case and let a malformed
    `--address` reach the wire.

    Both refusals are decided HERE, in the CLI tier, before either command
    ever calls into `EpromOperator` -- `_operation_context` is what opens
    the serial port, and D-17 requires both refusals to fire before that
    happens. Returns an exit code (always 2) to refuse with, or `None` when
    the region is acceptable and the caller should proceed to the real
    operation.

    Boundary, stated explicitly (CR-01): a start address exactly equal to
    `memory-size` is past the last addressable byte (valid addresses are
    `[0, memory-size)`) and is refused, with no region left to declare from
    it; `start == memory-size - 1` with no `--size` is the last valid byte
    and declares a 1-byte region, which is accepted.

    A malformed `--address`/`--size` string is deliberately NOT this
    function's job: `_setup_operation`'s own `parse_address`/`parse_size`
    `ValueError` handling already refuses that -- also before the wire,
    also exit 2, via the existing setup-failure path. Returning `None` here
    lets that path run rather than parsing the string a second time with
    different error handling.
    """
    try:
        start = parse_address(address) if address is not None else 0
    except ValueError:
        return None
    start = start or 0

    explicit_size: int | None = None
    if size is not None:
        try:
            explicit_size = parse_size(size)
        except ValueError:
            return None

    if explicit_size is not None and input_file is not None:
        try:
            file_length: int | None = os.path.getsize(input_file)
        except OSError:
            file_length = None
        if file_length is not None and file_length < explicit_size:
            click.echo(
                f"{eprom.upper()}: refused -- the input file is {file_length} "
                f"bytes, shorter than the requested --size of {explicit_size} "
                "bytes. Comparing only the overlap would silently under-check "
                "the file."
            )
            return 2

    mem_size = eprom_data.get("memory-size")

    # CR-01: a start address at or past the chip's declared size is refused
    # unconditionally, before any length is even resolved -- there is no
    # addressable byte left to declare a region from, regardless of whether
    # --size was given. Checked first so the length-based check below never
    # has to reason about a negative or zero "rest of chip" length.
    if mem_size is not None and start >= mem_size:
        click.echo(
            f"{eprom.upper()}: refused -- the start address 0x{start:X} is "
            f"at or past this chip's declared size (0x{mem_size:X})."
        )
        return 2

    if explicit_size is not None:
        length: int | None = explicit_size
    elif input_file is not None:
        try:
            length = os.path.getsize(input_file)
        except OSError:
            length = None
    elif mem_size is not None:
        # blank's default (no --size): the declared region is the rest of
        # the chip from `start` onward, not "nothing to bound" -- the
        # `start >= mem_size` guard above already ruled out a non-positive
        # length here, so this is always a real, positive length.
        length = mem_size - start
    else:
        length = None

    if length is not None and mem_size is not None and start + length > mem_size:
        click.echo(
            f"{eprom.upper()}: refused -- the region 0x{start:X}-"
            f"0x{start + length:X} runs past this chip's declared size "
            f"(0x{mem_size:X})."
        )
        return 2

    return None


_ERASE_SECTOR_BLANK_REFUSAL = (
    "{eprom}: refused -- ``-s``/``--sector-address`` cannot be combined "
    "with ``-b``/``--blank-check``: the post-erase check is whole-device "
    "and cannot follow a sector erase, and the host has no sector-size "
    "knowledge to scope it with."
)


def _erase_sector_blank_refusal_exit_code(
    *, eprom: str, sector_address: str | None, blank_check: bool
) -> int | None:
    """OQ-1's pre-wire refusal, sibling to `_region_refusal_exit_code` above
    -- both decide before `EpromOperator` is reached and both return an
    exit code rather than raising, because `_operation_context` is what
    opens the serial port.

    Measured hazard (205-RESEARCH.md § "Hazard: erase -s <sector> -b on
    protocol 0x06"): on protocol ``0x06``, a non-zero ``handle->address``
    selects a SECTOR erase in ``flash_nor_unlock_erase_execute``
    (``flash_nor_unlock.cpp:118-126``), not the whole-device erase D-01's
    post-erase check assumes. D-01's `erase -b` check is whole-device, and
    the host is not told the sector size -- ``-s`` takes only an address,
    with no sector-size field threaded to the CLI -- so an unconditional
    whole-device check after a sector erase would report the untouched
    remainder as non-blank and exit 1: a reliable false negative where
    today the combination is a silent no-op (``0x06``'s erase-end
    assignment was commented-out dead code for its whole life, then
    deleted outright by ``205-03``'s blank-check sweep).

    Two rejected alternatives, for the record: scoping the check to the
    erased sector is blocked in practice, because the sector size is not
    threaded to the CLI; silently skipping the check when ``-s`` is given
    was rejected because it reproduces exactly the silently-passing defect
    class Phase 201 exists to fix.

    Decided before `resolve_chip` reaches any operator call -- it is a
    pure argument-combination refusal over two CLI options, reading no
    wire dict at all, so it is the cheapest correct place to stop and it
    guarantees the erase does not run. Returns 2 -- the same class
    `_region_refusal_exit_code` returns for both of its refusals, a usage
    refusal decided before the wire; 1 stays reserved for "erased but not
    blank" so D-02's three outcomes remain distinct -- or `None` when the
    combination is acceptable and the caller should proceed to the erase.
    """
    if sector_address is not None and blank_check:
        click.echo(_ERASE_SECTOR_BLANK_REFUSAL.format(eprom=eprom.upper()))
        return 2
    return None


@cli.command(name="verify")
@click.argument("eprom", shell_complete=_complete_eprom)
@click.argument("input_file")
@click.option("-a", "--address", default=None, help="Verify start address in dec/hex")
@click.option(
    "-s", "--size", default=None, help="Size of the data to verify in dec/hex"
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force, even if the VPP or chip id doesn't match.",
)
@click.option(
    "--full",
    is_flag=True,
    help="Report every mismatching range, not just the first.",
)
@click.pass_obj
@map_typed_errors
def verify(
    app: AppContext,
    eprom: str,
    input_file: str,
    address: str | None,
    size: str | None,
    force: bool,
    full: bool,
) -> None:
    """Verifies the content of an EPROM.

    Exits 0 on a match, 1 on a mismatch, 2 on a transport, hardware, setup,
    or region failure. The three are distinct: a transport failure is not
    reported as a mismatch, and a region refusal -- an explicit --size
    longer than the input file, or a region running past the chip's end --
    is reported before the serial port ever opens.

    Without --size, the compared region is the input file's own length;
    with it, --size wins.
    """
    eprom_data = resolve_chip(eprom, db=app.db)
    refusal = _region_refusal_exit_code(
        eprom=eprom,
        eprom_data=eprom_data,
        address=address,
        size=size,
        input_file=input_file,
    )
    if refusal is not None:
        sys.exit(refusal)
    verdict = app.eprom_operator.verify_eprom(
        eprom,
        eprom_data,
        input_file,
        address_str=address,
        size_str=size,
        operation_flags=_build_op_flags(force=force),
        full=full,
    )
    sys.exit(verdict)


@cli.command(name="blank")
@click.argument("eprom", shell_complete=_complete_eprom)
@click.option(
    "-a", "--address", default=None, help="Blank check start address in dec/hex"
)
@click.option(
    "-s", "--size", default=None, help="Size of the data to blank check in dec/hex"
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force, even if the VPP or chip id doesn't match.",
)
@click.option(
    "--full",
    is_flag=True,
    help="Report every non-blank range, not just the first.",
)
@click.pass_obj
@map_typed_errors
def blank(
    app: AppContext,
    eprom: str,
    address: str | None,
    size: str | None,
    force: bool,
    full: bool,
) -> None:
    """Checks if an EPROM is blank.

    Exits 0 when blank, 1 when at least one byte is not blank, 2 on a
    transport, hardware, setup, or region failure. The three are distinct:
    a transport failure is not reported as a not-blank verdict, and a
    region refusal -- a region running past the chip's end -- is reported
    before the serial port ever opens.

    Without --size, the checked region is the whole chip; with it, --size
    wins.
    """
    eprom_data = resolve_chip(eprom, db=app.db)
    refusal = _region_refusal_exit_code(
        eprom=eprom, eprom_data=eprom_data, address=address, size=size
    )
    if refusal is not None:
        sys.exit(refusal)
    verdict = app.eprom_operator.check_eprom_blank(
        eprom,
        eprom_data,
        operation_flags=_build_op_flags(force=force),
        address_str=address,
        size_str=size,
        full=full,
    )
    sys.exit(verdict)


@cli.command(name="erase")
@click.argument("eprom", shell_complete=_complete_eprom)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force, even if the VPP or chip id doesn't match.",
)
@click.option(
    "-b",
    "--blank-check",
    "blank_check",
    is_flag=True,
    default=False,
    help="Do a blank check after erase.",
)
@click.option(
    "-s",
    "--sector-address",
    "sector_address",
    metavar="ADDRESS",
    default=None,
    help="Sector address for sector erase (hex e.g. 0x10000). Omit for chip erase.",
)
@click.option(
    "--ignore-unsupported",
    "ignore_unsupported",
    is_flag=True,
    default=False,
    help="Exit 0 instead of 1 when erase is not supported for this EPROM.",
)
@click.pass_obj
@map_typed_errors
def erase(
    app: AppContext,
    eprom: str,
    force: bool,
    blank_check: bool,
    sector_address: str | None,
    ignore_unsupported: bool,
) -> None:
    """Erase an EPROM, if supported.

    ``-b``/``--blank-check`` requests a blank check performed **after** the
    erase, through the same host-side engine ``blank`` uses. Note the
    inverted sense against ``write``, whose ``-b``/``--no-blank-check`` skips
    a check performed before it -- that inversion is unchanged.

    Without ``-b``, ``erase`` exits 0 when the erase succeeds and 1 when it
    does not. With ``-b``, the exit code widens to three outcomes: 0 erased
    and blank, 1 erased but not blank, 2 when the check itself fails on
    transport, hardware or setup. The three are distinct -- a transport
    failure is never reported as a not-blank chip verdict. It works on
    every part ``erase`` supports. The check reports the first non-blank
    address only; for every coalesced mismatching range with a
    classification bucket, run ``firestarter blank <chip> --full``
    afterward.

    ``-s``/``--sector-address`` applies only to protocols that support a
    sector erase; a whole-device chip erase ignores any sector address
    given for it. ``-s`` and ``-b`` cannot be combined: the post-erase
    check is whole-device and cannot follow a sector erase, and the host
    has no sector-size knowledge to scope it with. That combination is
    refused, exit 2, before the erase runs.

    An unsupported erase exits 1 by default; ``--ignore-unsupported`` makes it
    exit 0 instead, for scripting, while still printing the same line.
    """
    sector_blank_refusal = _erase_sector_blank_refusal_exit_code(
        eprom=eprom, sector_address=sector_address, blank_check=blank_check
    )
    if sector_blank_refusal is not None:
        sys.exit(sector_blank_refusal)

    eprom_data = resolve_chip(eprom, db=app.db)

    if flash4_erase_gate.is_flash4(eprom_data):
        click.echo(flash4_erase_gate.refusal_text(eprom))
        sys.exit(0 if ignore_unsupported else 1)

    if not jp5_gate.confirm_or_refuse(eprom, eprom_data.get("bus-config"), "erase"):
        sys.exit(1)

    ok = app.eprom_operator.erase_eprom(
        eprom,
        eprom_data,
        operation_flags=_build_op_flags(blank_check=blank_check, force=force),
        address_str=sector_address,
        pin1_hazard_acknowledged=True,
    )
    if not ok:
        # Fork D: the check never runs when the erase itself failed -- a
        # not-blank verdict for a part that was never erased would be a
        # fabricated claim about silicon, the same shape D-02 exists to
        # refuse for a transport failure. No second port is opened.
        sys.exit(1)
    if not blank_check:
        sys.exit(0)

    # D-01: the firmware's own post-erase check ran inside the erase's own
    # port session, as `firestarter_operation_end`. FWBLANK-02 deletes that
    # assignment, so this is the host re-implementing the same meaning one
    # tier up, through Phase 202's `check_eprom_blank` -- the same engine
    # `blank` uses. That makes this a SECOND port open, which resets an
    # Uno-class board between the erase and the check; an electrically
    # erased part stays blank across that reset, so the added cost is
    # wall-clock only, not correctness. Collapsing the two opens into one
    # session is Phase 206's SESS-01. `address_str`/`size_str` are passed
    # explicitly as `None` -- the whole-device scope is D-01's decision,
    # not an omission -- and `full=False` is D-03's terse, first-mismatch
    # mode. `check_eprom_blank` already returns the 0/1/2 verdict this
    # docstring promises, so there is no mapping layer: `sys.exit` on it
    # directly, exactly as `blank` does.
    verdict = app.eprom_operator.check_eprom_blank(
        eprom,
        eprom_data,
        operation_flags=_build_op_flags(force=force),
        address_str=None,
        size_str=None,
        full=False,
    )
    sys.exit(verdict)


@cli.command(name="id")
@click.argument("eprom", shell_complete=_complete_eprom)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force, even if the VPP is not correct.",
)
@click.pass_obj
@map_typed_errors
def chip_id(app: AppContext, eprom: str, force: bool) -> None:
    """Checks an EPROM, if supported."""
    eprom_data = resolve_chip(eprom, db=app.db)
    res, detected_id_value = app.eprom_operator.check_eprom_id(
        eprom, eprom_data, operation_flags=_build_op_flags(force=force)
    )

    if not res and detected_id_value:
        logger.info(
            f"Looking up detected Chip ID 0x{detected_id_value:X} in the database..."
        )
        found_eproms_for_detected_id = app.db.search_chip_id(detected_id_value)
        if found_eproms_for_detected_id:
            logger.info(
                f"The detected Chip ID 0x{detected_id_value:X} matches the following EPROMs in the database:"  # noqa: E501
            )
            mapped_found_eproms = [
                app.db.map_chip_record(ic, ic.get("manufacturer", "Unknown"))
                for ic in found_eproms_for_detected_id
            ]
            print_eprom_list_table(
                mapped_found_eproms, app.eprom_presenter.spec_builder
            )
        else:
            logger.warning(
                f"Detected Chip ID 0x{detected_id_value:X} not found in the database."
            )

    sys.exit(0 if res else 1)


# ---------------------------------------------------------------------------
# Voltage commands
# ---------------------------------------------------------------------------


@cli.command(name="vpp")
@click.option("-t", "--timeout", type=int, default=None, hidden=True)
@click.pass_obj
@map_typed_errors
def vpp(app: AppContext, timeout: int | None) -> None:
    """VPP voltage."""
    ok = app.hardware_manager.read_vpp_voltage(
        timeout_seconds=timeout, flags=_build_op_flags()
    )
    sys.exit(0 if ok else 1)


@cli.command(name="vpe")
@click.option("-t", "--timeout", type=int, default=None, hidden=True)
@click.pass_obj
@map_typed_errors
def vpe(app: AppContext, timeout: int | None) -> None:
    """VPE voltage."""
    ok = app.hardware_manager.read_vpe_voltage(
        timeout_seconds=timeout, flags=_build_op_flags()
    )
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# Hardware commands
# ---------------------------------------------------------------------------


@cli.command(name="hw")
@click.pass_obj
@map_typed_errors
def hw(app: AppContext) -> None:
    """Hardware revision."""
    ok = app.hardware_manager.get_hardware_revision(flags=_build_op_flags())
    sys.exit(0 if ok else 1)


@cli.command(name="config")
@click.option(
    "--rev",
    type=float,
    default=None,
    help="WARNING Overrides hardware revision (0-2), only use with HW mods. -1 disables override.",  # noqa: E501
)
@click.option(
    "-r1",
    "--r16",
    "r16",
    type=int,
    default=None,
    help="Set R16 resistance, resistor connected to VPE",
)
@click.option(
    "-r2",
    "--r14r15",
    "r14r15",
    type=int,
    default=None,
    help="Set R14/R15 resistance, resistors connected to GND",
)
@click.pass_obj
@map_typed_errors
def config(
    app: AppContext,
    rev: float | None,
    r16: int | None,
    r14r15: int | None,
) -> None:
    """Handles CONFIGURATION values."""
    # set_hardware_config expects Optional[int]; the Click option accepts float
    # so users can write `--rev 2.0` interchangeably with `--rev 2`. Cast to int
    # at the boundary (rev=-1 sentinel + integer rev values preserved verbatim).
    rev_int = int(rev) if rev is not None else None
    ok = app.hardware_manager.set_hardware_config(
        rev_int, r16, r14r15, flags=_build_op_flags()
    )
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# Firmware command
# The 3-way mutex is enforced post-parse at the top of fw()'s body;
# previously per-option callback _check_install_mutex, now removed)
# _FirmwareVersionType is a custom ParamType. UsageError on --json
# without --list. SimpleNamespace adapter for _maybe_auto_route_to_pre.
# ---------------------------------------------------------------------------


def _maybe_auto_route_to_pre_click(
    install: bool, pre: bool, firmware_version: str | None, stable: bool
) -> bool:
    """Click-side equivalent of the _maybe_auto_route_to_pre helper.

    Builds a namespace from
    the relevant fw kwargs, hand it to the (now-local) helper. Keeps the
    helper's body untouched (zero churn; relocated from main.py in Wave 4 /
    the CLI's own values).

    Returns the (possibly-overridden) pre value so the caller can use it
    for channel resolution.
    """
    ns = SimpleNamespace(
        install=install,
        pre=pre,
        firmware_version=firmware_version,
        stable=stable,
    )
    _maybe_auto_route_to_pre(ns)
    return ns.pre


@cli.command(name="fw")
@click.option(
    "-i", "--install", is_flag=True, help="Try to install the latest firmware."
)
@click.option(
    "--pre",
    is_flag=True,
    help="Fetch latest pre-release firmware (mirrors pip install --pre).",
)
@click.option(
    "--firmware-version",
    "firmware_version",
    type=_FirmwareVersionType(),
    default=None,
    metavar="VERSION",
    help="Pin exact firmware version (e.g. 3.1.0, 3.1.0b2, 3.1.0rc1).",
)
@click.option(
    "--stable",
    is_flag=True,
    help="Explicitly select stable channel. With --list, filters to stable releases only.",  # noqa: E501
)
@click.option(
    "--list",
    "list_releases",
    is_flag=True,
    help="List available firmware releases for the configured board.",
)
@click.option(
    "-b",
    "--board",
    type=click.Choice(_BOARD_CHOICES),
    default="uno",
    help="Microcontroller board (optional), defaults to 'uno'.",
)
@click.option(
    "--usb-id",
    "usb_id",
    type=str,
    default=None,
    metavar="VID:PID",
    hidden=not _PY32_ENABLED,
    help="Restrict USB DFU install to one device, e.g. 1a86:8012 (py32f071 only).",
)
@click.option(
    "--dfu-probe",
    "dfu_probe",
    is_flag=True,
    hidden=not _PY32_ENABLED,
    help="List attached USB DFU devices and exit (py32f071 bootloader discovery).",
)
@click.option(
    "--avrdude-path",
    "avrdude_path",
    type=str,
    default=None,
    help="Full path to avrdude (optional), set if avrdude is not found.",
)
@click.option(
    "-c",
    "--avrdude-config-path",
    "avrdude_config_path",
    type=str,
    default=None,
    help="Full path to avrdude config (optional), set if avrdude version is 6.3 or not found.",  # noqa: E501
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Will install firmware even if the version is the same.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output --list results as JSON array (only with --list).",
)
@click.pass_context
@map_typed_errors
def fw(
    ctx: click.Context,
    install: bool,
    pre: bool,
    firmware_version: str | None,
    stable: bool,
    list_releases: bool,
    board: str,
    usb_id: str | None,
    dfu_probe: bool,
    avrdude_path: str | None,
    avrdude_config_path: str | None,
    force: bool,
    json_output: bool,
) -> None:
    """Firmware version.

    ``--pre``, ``--firmware-version`` and ``--stable`` are mutually exclusive;
    passing more than one is refused before anything is installed.
    """
    app: AppContext = ctx.obj

    # 3-way --pre / --firmware-version / --stable mutex enforced once,
    # post-parse, with a deterministic error message that doesn't depend on
    # the user's option ordering. Raises click.UsageError → exit-2, matching
    # argparse's add_mutually_exclusive_group() contract.
    set_channel_opts = [
        name
        for name, val in (
            ("pre", pre),
            ("firmware-version", firmware_version),
            ("stable", stable),
        )
        if val
    ]
    if len(set_channel_opts) > 1:
        raise click.UsageError(
            f"--{set_channel_opts[0]} is mutually exclusive with "
            f"--{set_channel_opts[1]}."
        )

    # Narrow UsageError upgrade (was a parser-level error).
    if json_output and not list_releases:
        raise click.UsageError("--json requires --list")

    # both py32-only options are refused through one shared
    # helper, called unconditionally for each option with its givenness,
    # before either option is consumed below. `hidden=not _PY32_ENABLED` on
    # both option declarations (above) keeps them out of --help; it does not
    # reject them — that is this refusal's job.
    _reject_py32_only_option("--usb-id", usb_id is not None)
    _reject_py32_only_option("--dfu-probe", dfu_probe)

    # USB DFU discovery: reports what is on the bus and exits. Deliberately
    # placed before every network path — it needs no release metadata, and it is
    # the first thing to run on a board whose bootloader identity is unconfirmed.
    if dfu_probe:
        found = app.firmware_manager.probe_dfu(usb_id=usb_id)
        if not found:
            print("No USB DFU devices found.")
            sys.exit(1)
        print("Attached USB DFU devices:")
        for line in found:
            print(f"  {line}")
        sys.exit(0)

    if list_releases:
        channel_filter: Literal["all", "pre", "stable"]
        if pre:
            channel_filter = "pre"
        elif stable:
            channel_filter = "stable"
        else:
            channel_filter = "all"
        releases = app.firmware_manager.list_releases(
            channel_filter=channel_filter, board=board
        )
        if releases is None:
            click.echo(
                f"Could not list firmware releases for board {board}: the release "
                f"endpoint {FIRESTARTER_RELEASES_URL} could not be read. No release "
                "information was retrieved.",
                err=True,
            )
            sys.exit(1)
        if json_output:
            import json as _json

            print(_json.dumps(releases, indent=2))
        else:
            print(f"{'Version':<12} {'Channel':<14} {'Published':<22} Asset URL")
            for r in releases:
                print(
                    f"{r['version']:<12} {r['channel']:<14} {r['published']:<22} {r['asset_url']}"  # noqa: E501
                )
            if not releases:
                print(f"No releases found for board {board}.")
        sys.exit(0)

    # SimpleNamespace adapter for the magic-default helper (zero churn).
    pre = _maybe_auto_route_to_pre_click(install, pre, firmware_version, stable)

    # Channel resolution for install path.
    channel: Literal["stable", "pre", "pinned"]
    if firmware_version:
        channel = "pinned"
    elif pre:
        channel = "pre"
    else:
        channel = "stable"

    # Pull a fresh port reference from config for the install pathway —
    # mirrors main.py:840 which passes `port_override=args.port`. The Click
    # group already applied --port to the in-memory config (see cli()), so
    # reading it back here is the equivalent operation.
    port_override = app.config_manager.get_value("port", None)

    # Did the operator actually type --board, or is this the "uno" default? A
    # typed --board that disagrees with the attached programmer is a conflict the
    # service layer must refuse rather than silently override.
    board_explicit = (
        ctx.get_parameter_source("board") != click.core.ParameterSource.DEFAULT
    )

    ok = app.firmware_manager.manage_firmware_update(
        install_flag=install,
        avrdude_path_override=avrdude_path,
        avrdude_config_override=avrdude_config_path,
        port_override=port_override,
        board_override=board,
        flags=_build_op_flags(force=force),
        channel=channel,
        pinned_version=firmware_version,
        usb_id=usb_id,
        board_explicit=board_explicit,
    )
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Dev-tools channel gate -- BOTH mechanisms below, not either.
#
# `_DEV_TOOLS_ENABLED` is computed ONCE at import: a wheel's __version__ is
# fixed when it is built, so the choice a stable install renders is decided
# once. (`is_dev_tools_enabled()` is itself call-time, so capturing it into a
# module global here is what freezes the decision.)
#
# `_DevGroup` is the other half: it holds the gated NAMES only, never a
# callback, and supplies the informative refusal. Genuine non-registration
# happens separately at each gated @dev.command block below.
# ---------------------------------------------------------------------------

_DEV_TOOLS_ENABLED: bool = is_dev_tools_enabled()


class _DevGroup(click.Group):
    """The `dev` group's Click command class.

    Holds the gated subcommand NAMES only, never a callback. A gated command
    must not exist as an invokable object in a stable process -- that is
    enforced by conditional registration below, not by this class. This class's
    only job is the informative refusal, so a gated name raises a
    channel-specific UsageError rather than Click's generic,
    typo-indistinguishable "No such command".

    `get_command` is the only method overridden, and that is the settled hook:
    `Group.resolve_command()` calls it and falls through to its own generic
    error only when it returns None, so overriding it intercepts strictly
    before that fallback. `resolve_command` and `list_commands` need no
    override -- an unregistered name is already absent from `self.commands`.
    """

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        real = super().get_command(ctx, cmd_name)
        if real is not None:
            return real
        if cmd_name in BETA_ONLY_DEV_COMMANDS:
            raise click.UsageError(dev_command_gate_message(cmd_name), ctx=ctx)
        return None


# ---------------------------------------------------------------------------
# dev group + 4 sub-commands
# ---------------------------------------------------------------------------


@cli.group(name="dev", cls=_DevGroup)
@map_typed_errors
def dev() -> None:
    """Development and diagnostic commands for the RURP shield.

    On a stable install, only `read` and `test` are available in this
    group -- both are fully supported for end users, despite living inside
    a group named `dev`. The remaining subcommands are development and
    bench tooling, available only on a pre-release install.

    USR button will break command and return.
    """


@dev.command(name="read")
@click.argument("eprom", shell_complete=_complete_eprom)
@click.option("-a", "--address", default=None, help="Read start address in dec/hex")
@click.option("-s", "--size", default=None, help="Size of the data to read in dec/hex")
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force read, even if the chip id doesn't match.",
)
@click.pass_obj
@map_typed_errors
def dev_read(
    app: AppContext,
    eprom: str,
    address: str | None,
    size: str | None,
    force: bool,
) -> None:
    """Reads the content from an EPROM and prints data to console."""
    eprom_data = resolve_chip(eprom, db=app.db)
    ok = app.eprom_operator.dev_read_eprom(
        eprom,
        eprom_data,
        address_str=address,
        size_str=size or "256",
        operation_flags=_build_op_flags(force=force),
    )
    sys.exit(0 if ok else 1)


if _DEV_TOOLS_ENABLED:
    # TRIPWIRE. `dev reg` is gated behind `_DEV_TOOLS_ENABLED`, and it is
    # load-bearing bench tooling: the held-erase-rail DMM proxy an operator uses to
    # hold a register state -- and so a voltage rail -- energised long enough to
    # take a multimeter reading outside a normal read/write cycle.
    #
    # Gating purely on __version__ would silently strand that the moment a stable
    # version is cut, with no error, just an absent command. That is why the
    # FIRESTARTER_DEV_TOOLS=1 bench override exists. Narrowing the accepted value,
    # or removing the OR that composes it with the channel check, strands the bench
    # tooling without warning.
    @dev.command(name="reg")
    @click.argument("msb")
    @click.argument("lsb")
    @click.argument("ctrl")
    @click.option(
        "-i",
        "--input-enable",
        "input_enable",
        is_flag=True,
        help="Input, pulls OE pin high.",
    )
    @click.option(
        "-d",
        "--chip-disable",
        "chip_disable",
        is_flag=True,
        help="Disable, pulls CE pin high.",
    )
    @click.option(
        "-f",
        "--firestarter",
        "firestarter_flag",
        is_flag=True,
        help=(
            "Using Firestarter register definition.\n"
            "By using the firestarter argumet,\n"
            "the control register will be remaped to match\n"
            "the hardware revision of the RURP sheild.\n"
            "See constants.RURP_CONTROL_REGISTER_BITS (mirror of rurp_pinout.h).\n"
            "0x100 - CTRL_VPP_VPE_DROP_ENABLE\n"
            "0x080 - CTRL_VPP_REGULATOR_ENABLE\n"
            "0x040 - CTRL_READ_WRITE\n"
            "0x020 - CTRL_ADDRESS_LINE_18\n"
            "0x010 - CTRL_ADDRESS_LINE_17\n"
            "0x008 - CTRL_VPP_P1_ENABLE\n"
            "0x004 - CTRL_VPE_ENABLE\n"
            "0x002 - CTRL_VPP_A9_ENABLE\n"
            "0x001 - CTRL_ADDRESS_LINE_16"
        ),
    )
    @click.pass_obj
    @map_typed_errors
    def dev_reg(
        app: AppContext,
        msb: str,
        lsb: str,
        ctrl: str,
        input_enable: bool,
        chip_disable: bool,
        firestarter_flag: bool,
    ) -> None:
        """Direct access to registers: MSB, LSB and control register."""
        ok = app.eprom_operator.dev_set_registers(
            msb,
            lsb,
            ctrl,
            firestarter=firestarter_flag,
            flags=_build_op_flags(input_enable=input_enable, chip_disable=chip_disable),
        )
        sys.exit(0 if ok else 1)


if _DEV_TOOLS_ENABLED:

    @dev.command(name="addr")
    @click.argument("eprom", shell_complete=_complete_eprom)
    @click.argument("address")
    @click.option(
        "-i",
        "--input-enable",
        "input_enable",
        is_flag=True,
        help="Input, pulls OE pin high.",
    )
    @click.option(
        "-d",
        "--chip-disable",
        "chip_disable",
        is_flag=True,
        help="Disable, pulls CE pin high.",
    )
    @click.pass_obj
    @map_typed_errors
    def dev_addr(
        app: AppContext,
        eprom: str,
        address: str,
        input_enable: bool,
        chip_disable: bool,
    ) -> None:
        """Direct access to address lines and control register."""
        eprom_data = resolve_chip(eprom, db=app.db)
        ok = app.eprom_operator.dev_set_address_mode(
            eprom,
            eprom_data,
            address,
            flags=_build_op_flags(input_enable=input_enable, chip_disable=chip_disable),
        )
        sys.exit(0 if ok else 1)


if _DEV_TOOLS_ENABLED:

    @dev.command(name="consistency-check")
    @click.argument("eprom", shell_complete=_complete_eprom)
    @click.option(
        "--runs",
        type=int,
        default=3,
        help="Number of consecutive reads (default 3; minimum 2).",
    )
    @click.option(
        "--output-dir",
        "output_dir",
        type=str,
        default=None,
        help="Output dir for per-run binaries (default firestarter-runs/consistency-check-<chip>-<board>-<TS>/).",  # noqa: E501
    )
    @click.option(
        "--keep-files/--no-keep-files",
        "keep_files",
        default=True,
        help="Keep per-run binary files after verdict (default keep).",
    )
    @click.option(
        "--max-diffs",
        "max_diffs",
        type=int,
        default=10,
        help="Max divergent offsets to print on FAIL (default 10).",
    )
    @click.option(
        "-q",
        "--quiet",
        is_flag=True,
        help="Suppress per-run tqdm progress bars.",
    )
    @click.option(
        "-f",
        "--force",
        is_flag=True,
        help="Force read, even if the chip id doesn't match (e.g. Shield-3 missing-chip case).",  # noqa: E501
    )
    @click.option(
        "--read-settling",
        "read_settling_us",
        type=int,
        default=0,
        help="Address-settling delay before /CE assert (µs; 0=firmware default 0µs).",
    )
    @click.option(
        "--read-strobe",
        "read_strobe_us",
        type=int,
        default=0,
        help="/CE read-strobe pulse width (µs; 0=firmware default 3µs).",
    )
    @click.pass_obj
    @map_typed_errors
    def dev_consistency_check(
        app: AppContext,
        eprom: str,
        runs: int,
        output_dir: str | None,
        keep_files: bool,
        max_diffs: int,
        quiet: bool,
        force: bool,
        read_settling_us: int,
        read_strobe_us: int,
    ) -> None:
        """Read EPROM N consecutive times and report SHA-256 divergence.

        Exits 0 on PASS, 1 on FAIL, 2 on hardware error. The three are distinct: a
        hardware error is not reported as a failed comparison.
        """
        eprom_data = resolve_chip(eprom, db=app.db)
        verdict_int = app.eprom_operator.consistency_check_eprom(
            eprom,
            eprom_data,
            runs=runs,
            output_dir=output_dir,
            keep_files=keep_files,
            max_diffs=max_diffs,
            quiet=quiet,
            operation_flags=_build_op_flags(force=force),
            read_settling_us=read_settling_us,
            read_strobe_us=read_strobe_us,
        )
        sys.exit(verdict_int)


if _DEV_TOOLS_ENABLED:

    @dev.command(name="write-cycle")
    @click.argument("eprom", shell_complete=_complete_eprom)
    @click.argument("source_image", type=click.Path(exists=True))
    @click.option(
        "--runs",
        type=int,
        default=5,
        help="Number of write→read-back cycles (default 5).",
    )
    @click.option(
        "--output-dir",
        "output_dir",
        type=str,
        default=None,
        help="Output dir for per-cycle binaries (default firestarter-runs/write-cycle-<chip>-<board>-<TS>/).",  # noqa: E501
    )
    @click.option(
        "-f",
        "--force",
        is_flag=True,
        help="Force write, even if the chip id doesn't match.",
    )
    @click.pass_obj
    @map_typed_errors
    def dev_write_cycle(
        app: AppContext,
        eprom: str,
        source_image: str,
        runs: int,
        output_dir: str | None,
        force: bool,
    ) -> None:
        """Erase, write the source image, read back N times, and compare SHA-256.

        Exits 0 on PASS, 1 on mismatch, 2 on hardware error. The three are distinct: a
        hardware error is not reported as a mismatch.
        """
        eprom_data = resolve_chip(eprom, db=app.db)
        verdict_int = app.eprom_operator.write_cycle_eprom(
            eprom,
            eprom_data,
            source_image_path=source_image,
            runs=runs,
            output_dir=output_dir,
            operation_flags=_build_op_flags(force=force),
        )
        sys.exit(verdict_int)


if _DEV_TOOLS_ENABLED:

    @dev.command(name="fault-inject")
    @click.argument("eprom", shell_complete=_complete_eprom)
    @click.option(
        "--direction",
        type=click.Choice(["outgoing", "incoming"]),
        default="outgoing",
        help="outgoing = corrupt host→fw frame; incoming = mutate fw→host frame.",
    )
    @click.option(
        "--fault-form",
        "fault_form",
        type=click.Choice(["corrupt-crc8", "drop-delimiter"]),
        default="corrupt-crc8",
        help="Fault form: corrupt-crc8 (flip CRC8 byte) or drop-delimiter (drop 0x00).",
    )
    @click.option(
        "--mode",
        type=click.Choice(["cycle", "latency", "connect-cost"]),
        default="cycle",
        help="cycle = read-cycle resync demo (default); latency = per-frame firmware NAK "
        "latency on an established single-port connection (53-04 refinement; no chip needed); "
        "connect-cost = per-connect cost harness, N pinned-port connect/disconnect cycles "
        "(MEAS-01; no chip needed).",
    )
    @click.option(
        "--output-dir",
        "output_dir",
        type=str,
        default=None,
        help="Output dir for transfer binaries.",
    )
    @click.option(
        "--samples",
        "samples",
        type=int,
        default=10,
        help="connect-cost mode only: number of connect/disconnect cycles to time.",
    )
    @click.pass_obj
    @map_typed_errors
    def dev_fault_inject(
        app: AppContext,
        eprom: str,
        direction: str,
        fault_form: str,
        mode: str,
        output_dir: str | None,
        samples: int,
    ) -> None:
        """Demonstrate COBS resync: inject a corrupted frame and assert recovery on the next.

        cycle mode runs one corrupted transfer, then asserts the same connection
        recovers on a clean follow-on transfer.

        latency mode opens one pinned port and times the firmware's per-frame NAK on a
        corrupt CMD_FW_VERSION frame. Use it with ``-p <port>``; an already-established
        connection avoids the multi-port connect-retry that inflates cycle mode's
        outgoing latency.

        connect-cost mode opens and closes one pinned port ``--samples`` times (default 10),
        timing each connect, and reports min/median/max seconds to three decimals plus the
        board-independent structural floor. Use it with ``-p <port>``; the port is pinned
        explicitly so port discovery never inflates the figure.
        """
        if mode == "latency":
            ok = app.eprom_operator.measure_command_nak_latency(
                fault_form=fault_form,
                output_dir=output_dir,
            )
            sys.exit(0 if ok else 1)

        if mode == "connect-cost":
            ok = app.eprom_operator.measure_connect_cost(
                samples=samples,
                output_dir=output_dir,
            )
            sys.exit(0 if ok else 1)

        eprom_data = resolve_chip(eprom, db=app.db)
        ok = app.eprom_operator.fault_inject_cycle(
            eprom,
            eprom_data,
            direction=direction,
            fault_form=fault_form,
            output_dir=output_dir,
        )
        sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# dev lock-status. This is a real silicon read, exposed only on a
# pre-release install, deliberately overruling the host-only recommendation,
# so this command lives inside the same `_DEV_TOOLS_ENABLED` gate every
# other bench subcommand above does.
# ---------------------------------------------------------------------------

if _DEV_TOOLS_ENABLED:

    @dev.command(name="lock-status")
    @click.argument("eprom", shell_complete=_complete_eprom)
    @click.option(
        "-f",
        "--force",
        is_flag=True,
        help=(
            "Proceed past a table refusal anyway. The result is an "
            "unadjudicated probe, never a state claim -- and this never sets "
            "a wire-visible flag (C-16): the table refusal it bypasses is a "
            "host-side decision only."
        ),
    )
    @click.pass_obj
    @map_typed_errors
    def dev_lock_status(app: AppContext, eprom: str, force: bool) -> None:
        """Diagnostic read of a chip's write-protection state -- not a guarantee."""
        # Resolve through db.get_eprom(), never resolve_chip()'s
        # programmer dict -- that dict carries neither 'protocol-id' nor
        # 'name', the exact shape protection_gate_for_entry hard-fails on.
        # This is the last place in this handler with both the chip NAME
        # and app.db, mirroring write()'s own idiom above.
        entry = app.db.get_eprom(eprom)
        if not entry:
            raise ChipNotFoundError(f"{eprom}: not found in database")

        gate_token, gate_reason = protection_gate_for_entry(entry, eprom)

        if gate_token != GATE_TOKEN_READ_PERMITTED and not force:
            # The table already refused, from the database alone -- this
            # needs no hardware. Rendered from the predicate's OWN
            # gate_token/gate_reason directly (never through
            # classify_protection_response, whose generic boilerplate for a
            # passed-through refusal would discard the specific offending
            # alias(es) protection_gate_for_entry already named). Open no
            # serial port on this path: a refusal that still opened the
            # port would make a refusal indistinguishable from a comms
            # failure.
            click.echo(render_lock_status(gate_token, gate_reason, None))
            sys.exit(exit_code_for_class(gate_token))

        # Either the table permits the read, or --force is bypassing its
        # refusal. Both dicts are needed from here on: get_eprom()
        # fed the predicate above; resolve_chip() is what the firmware
        # operation itself needs.
        eprom_data = resolve_chip(eprom, db=app.db)
        try:
            _accepted, payload = app.eprom_operator.read_protection_status(
                eprom, eprom_data, operation_flags=_build_op_flags()
            )
        except EpromOperationError as exc:
            # Keyed on the message **id**, never on text -- a version
            # probe cannot work here because _probe_port's [\d.x]+ truncates
            # the pre-release suffix, so it cannot distinguish the beta that
            # has this command from the beta that does not, and would have
            # to refuse both. map_unknown_cmd_to_outdated_for_operation
            # returns rather than raises, so this caller owns the chaining.
            outdated = sdp_honesty.map_unknown_cmd_to_outdated_for_operation(
                exc, "lock-status", eprom
            )
            if outdated is None:
                raise
            class_token = "firmware_outdated"
            click.echo(render_lock_status(class_token, str(outdated), None))
            raise SystemExit(exit_code_for_class(class_token)) from exc

        # classify_protection_response's forced-past-refusal guard runs
        # before the payload is ever consulted, so a forced read on a
        # refused part can never become a state claim here either.
        class_token, reason = classify_protection_response(
            gate_token, payload, forced=force
        )
        raw_byte = payload[0] if payload else None
        click.echo(render_lock_status(class_token, reason, raw_byte))
        sys.exit(exit_code_for_class(class_token))


# ---------------------------------------------------------------------------
# dev validate-family (Tier-3 runner)
# ---------------------------------------------------------------------------

# r1 calibration tolerance band: 270000 ± 25%
_R1_TARGET: int = 270_000
_R1_TOLERANCE: float = 0.25
_R1_LO: int = int(_R1_TARGET * (1 - _R1_TOLERANCE))  # 202500
_R1_HI: int = int(_R1_TARGET * (1 + _R1_TOLERANCE))  # 337500

# Boards whose write/program cells are hard N/A due to brownout (backlog 999.2).
_UNO328PB_BOARD: str = "uno328pb"

# Authoritative PASS board: only Leonardo's SHA compare is non-advisory.
_AUTHORITATIVE_PASS_BOARD: str = "leonardo"

_VALIDATION_SPEC_PATH: Path = (
    Path(__file__).parent.parent / "tools" / "validation_matrix_spec.json"
)


def _load_validation_spec() -> dict[str, Any]:
    """Load the authored validation matrix spec JSON."""
    return json.loads(_VALIDATION_SPEC_PATH.read_text(encoding="utf-8"))


def _families_for_selection(
    family_arg: str,
    spec: Dict[str, Any],  # noqa: UP006
) -> List[Dict[str, Any]]:  # noqa: UP006
    """Return the list of family dicts matching the CLI argument."""
    families: List[Dict[str, Any]] = spec["families"]  # noqa: UP006
    if family_arg == "all":
        return families
    return [f for f in families if f["id"] == family_arg]


def _emit_skip_deferred_artifact(
    families: List[Dict[str, Any]],  # noqa: UP006
    output_dir: str | None,
    reason: str = "no board/chip/source provided",
) -> None:
    """Emit validation-matrix.{json,md} with all Tier-3 cells as SKIP-deferred.

    A milestone remains closeable at partial bench coverage.
    Artifact name is validation-matrix.{json,md} (hyphen, NEVER underscore).
    """
    cells: List[Dict[str, Any]] = []  # noqa: UP006
    for fam in families:
        tier3 = fam.get("tier3", {})
        boards: List[str] = tier3.get("boards", [])  # noqa: UP006
        skip_boards: List[str] = tier3.get("skip_boards", [])  # noqa: UP006
        # Emit one cell per board in the tier3 boards list
        for board in boards:
            cells.append(
                {
                    "family": fam["id"],
                    "board": board,
                    "tier": 3,
                    "verdict": "SKIP-deferred",
                    "reason": reason,
                    "evidence_sha": None,
                    "retry_count": 0,
                }
            )
        # Emit N/A cells for skip_boards (brownout guard etc.)
        for board in skip_boards:
            cells.append(
                {
                    "family": fam["id"],
                    "board": board,
                    "tier": 3,
                    "verdict": "N/A",
                    "reason": f"board {board!r} is in skip_boards for family {fam['id']!r}",
                    "evidence_sha": None,
                    "retry_count": 0,
                }
            )

    _write_artifact(cells, output_dir)


def _write_artifact(
    cells: List[Dict[str, Any]],  # noqa: UP006
    output_dir: str | None,
) -> None:
    """Write validation-matrix.json and validation-matrix.md to output_dir.

    Artifact name uses hyphens (distinct from authored validation_matrix_spec.json
    ).
    """
    out_path = Path(output_dir) if output_dir else Path(".")
    out_path.mkdir(parents=True, exist_ok=True)

    artifact: Dict[str, Any] = {  # noqa: UP006
        "generated": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "harness_version": "71",
        "cells": cells,
    }

    json_file = out_path / "validation-matrix.json"
    json_file.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    md_file = out_path / "validation-matrix.md"
    md_file.write_text(_render_markdown(cells), encoding="utf-8")


def _render_markdown(cells: List[Dict[str, Any]]) -> str:  # noqa: UP006
    """Render a Markdown table from the cell list."""
    lines = [
        "# Validation Matrix Results",
        "",
        "| Family | Board | Tier | Verdict | Evidence SHA | Retries |",
        "| ------ | ----- | ---- | ------- | ------------ | ------- |",
    ]
    for cell in cells:
        sha = cell.get("evidence_sha") or "—"
        if sha and len(sha) > 16:
            sha = sha[:16] + "…"
        lines.append(
            f"| {cell.get('family', '')} "
            f"| {cell.get('board', '')} "
            f"| {cell.get('tier', '')} "
            f"| {cell.get('verdict', '')} "
            f"| {sha} "
            f"| {cell.get('retry_count', 0)} |"
        )
    return "\n".join(lines) + "\n"


def _classify_sha_result(
    readback_sha: str,
    source_sha: str,
    board: str,
) -> Dict[str, Any]:  # noqa: UP006
    """Classify a post-write SHA comparison result per oracle rules.

    Leonardo: authoritative PASS/FAIL.
    Other boards: result is advisory (not a hard FAIL for the cell).

    Returns a dict with 'verdict' and 'pass_type' keys.
    """
    match = readback_sha == source_sha
    if board == _AUTHORITATIVE_PASS_BOARD:
        return {
            "verdict": "PASS" if match else "FAIL",
            "pass_type": "authoritative",
        }
    return {
        "verdict": "PASS" if match else "advisory",
        "pass_type": "advisory",
    }


def _check_r1_precondition(r1_value: int) -> bool:
    """Return True if r1_value is within the ±25% tolerance band of 270000."""
    return _R1_LO <= r1_value <= _R1_HI


if _DEV_TOOLS_ENABLED:

    @dev.command(name="validate-family")
    @click.argument(
        "family",
        type=click.Choice(
            ["eprom", "eeprom28c", "flash3", "flash4", "flash_intel", "sram", "all"]
        ),
    )
    @click.option("--board", default=None, help="Board name (e.g. leonardo, uno328pb).")
    @click.option("--chip", default=None, help="Representative chip name override.")
    @click.option(
        "--source",
        default=None,
        type=click.Path(),
        help="Source image path for write+verify oracle.",
    )
    @click.option(
        "--output-dir",
        "output_dir",
        type=str,
        default=None,
        help="Output directory for results artifact (default: current directory).",
    )
    @click.pass_obj
    @map_typed_errors
    def dev_validate_family(
        app: AppContext,
        family: str,
        board: str | None,
        chip: str | None,
        source: str | None,
        output_dir: str | None,
    ) -> None:
        """Run the per-family validation matrix Tier-3 runner.

        Emits a validation-matrix.{json,md} results artifact.

        Exits 0 on PASS, 1 on FAIL, 2 on hardware error.

        When no board, chip or source image is available, every Tier-3 cell is recorded
        as SKIP-deferred and the command exits 0.

        Reading the results: Leonardo is the only authoritative PASS board and others
        are advisory; uno328pb write and program cells are hard N/A; an r1 outside
        270000 ohms +/-25% aborts before any write cycle; retry_count is captured into
        each cell.
        """
        spec = _load_validation_spec()
        families = _families_for_selection(family, spec)

        # SKIP-deferred: no port / board / chip / source → record all cells
        # as SKIP-deferred, emit artifact, exit 0.
        port = app.config_manager.get_value("port", None)
        if not port or not board or not chip or not source:
            _emit_skip_deferred_artifact(families, output_dir=output_dir)
            sys.exit(0)

        # Hardware path — oracle rules apply.

        # uno328pb hard N/A for write/program cells (brownout 999.2 — backlog 999.2).
        if board == _UNO328PB_BOARD:
            cells: List[Dict[str, Any]] = []  # noqa: UP006
            for fam in families:
                cells.append(
                    {
                        "family": fam["id"],
                        "board": board,
                        "tier": 3,
                        "verdict": "N/A",
                        "reason": (
                            "uno328pb write/program cells are N/A — brownout backlog 999.2"
                        ),
                        "evidence_sha": None,
                        "retry_count": 0,
                    }
                )
            _write_artifact(cells, output_dir)
            sys.exit(0)

        # r1 precondition: abort before any cycle if r1 is out of band.
        # The r1 value is read from hardware config via the HardwareManager.
        # The hardware path is exercised only with real hardware; here we gate
        # on the operator config.
        r1_raw: int | None = None
        try:
            hw_config = app.config_manager.get_value("r1", None)
            if hw_config is not None:
                r1_raw = int(hw_config)
        except (ValueError, TypeError):
            r1_raw = None

        if r1_raw is not None and not _check_r1_precondition(r1_raw):
            logger.error(
                "r1 precondition failed: r1=%d is outside [%d, %d] (±25%% of 270000). "
                "Recalibrate before running validate-family.",
                r1_raw,
                _R1_LO,
                _R1_HI,
            )
            sys.exit(2)

        # Compose cycle methods for each family -- reuse, not reimplement.
        hw_cells: List[Dict[str, Any]] = []  # noqa: UP006
        overall_verdict = 0

        for fam in families:
            rep_chip = chip or fam.get("tier3", {}).get(
                "test_chip", fam.get("rep_chip", "")
            )
            if not rep_chip:
                logger.warning("No rep_chip for family %r — skipping.", fam["id"])
                continue

            eprom_data = resolve_chip(rep_chip, db=app.db)

            # Compose write_cycle_eprom -- no re-implementation of write+readback.
            verdict_int = app.eprom_operator.write_cycle_eprom(
                rep_chip,
                eprom_data,
                source_image_path=source,
                runs=1,
                output_dir=output_dir,
                operation_flags=0,
            )

            # Derive evidence SHA from source image for the cell record.
            evidence_sha: str | None
            try:
                evidence_sha = hashlib.sha256(Path(source).read_bytes()).hexdigest()
            except OSError:
                evidence_sha = None

            # Map verdict to oracle classification (Leonardo = authoritative).
            # verdict_int==0: write_cycle_eprom's own source-vs-readback SHA compare
            # returned 0 (PASS). Map directly to board-class verdict — the caller
            # MUST NOT add a source==source self-comparison call here (vacuous).
            # The real readback compare already happened inside write_cycle_eprom.
            # Preserve board-class semantics via pass_type: "authoritative" on
            # Leonardo, "advisory" on all other non-uno328pb boards.
            if verdict_int == 0:
                pass_type = (
                    "authoritative"
                    if board == _AUTHORITATIVE_PASS_BOARD
                    else "advisory"
                )
                cell_verdict = "PASS"
            elif verdict_int == 1:
                cell_verdict = "FAIL"
                pass_type = (
                    "authoritative"
                    if board == _AUTHORITATIVE_PASS_BOARD
                    else "advisory"
                )
            else:
                cell_verdict = "SKIP-deferred"  # hw-error → deferred
                pass_type = (
                    "authoritative"
                    if board == _AUTHORITATIVE_PASS_BOARD
                    else "advisory"
                )

            hw_cells.append(
                {
                    "family": fam["id"],
                    "board": board,
                    "tier": 3,
                    "verdict": cell_verdict,
                    "pass_type": pass_type,
                    "evidence_sha": evidence_sha,
                    "retry_count": 1,
                }
            )

            if verdict_int > overall_verdict:
                overall_verdict = verdict_int

        _write_artifact(hw_cells, output_dir)
        sys.exit(overall_verdict)


# ---------------------------------------------------------------------------
# `dev test` -- community chip-validation sweep
# ---------------------------------------------------------------------------

# Per-verdict exit-code mapping. OK/NA/SKIPPED are exit-clean; `marginal` is
# inconclusive (2); BAD beats marginal by EXPLICIT PRECEDENCE in
# `_overall_exit_code`.
#
# It must not be a numeric max(): `marginal` maps to 2 and BAD to 1, so a max
# picks 2 whenever both are present and a run containing both would exit
# inconclusive rather than failed.
_VERDICT_EXIT_CODES = {
    VERDICT_OK: 0,
    VERDICT_NA: 0,
    VERDICT_SKIPPED: 0,
    VERDICT_MARGINAL: 2,
    VERDICT_BAD: 1,
}


def _verdict_code(verdict: str) -> int:
    """Map a single StepResult verdict to its 0/1/2 exit-code contribution."""
    return _VERDICT_EXIT_CODES.get(verdict, 0)


# Exit codes ordered MOST-SEVERE-FIRST. `_overall_exit_code` walks
# this tuple and returns the first code present among a run's per-step
# codes -- an explicit precedence list, never a numeric `max` (a `max` over
# {1, 2} incorrectly picks 2, which is exactly the bug this replaces).
_EXIT_CODE_PRECEDENCE: tuple[int, ...] = (1, 2, 0)


def _overall_exit_code(results: list[StepResult]) -> int:
    """The run's overall exit code: the most severe code present, per
    `_EXIT_CODE_PRECEDENCE` -- BAD (exit 1) outranks marginal
    (exit 2) outranks a clean run (exit 0).

    `_verdict_code`'s `.get(verdict, 0)` stays the single vocabulary
    source -- an unrecognised verdict still contributes exit 0, so this
    helper introduces no sixth verdict status (ROADMAP's own constraint).
    """
    codes = {_verdict_code(r.verdict) for r in results}
    for code in _EXIT_CODE_PRECEDENCE:
        if code in codes:
            return code
    return 0


def _dev_test_exit_code(
    results: list[StepResult], *, sdp_oracle_not_run: bool, run_status_error: bool
) -> int:
    """Exit floor for an ALLOW-chip run whose SDP oracle did not run, so
    `dev test` cannot return 0 on a run that never exercised the oracle at all.

    Composed as a precedence CANDIDATE, never as a numeric max against the
    observed code. A max of 1 (BAD) and 2 (the floor) returns 2, which would
    launder a BAD run into an inconclusive one -- exactly the inversion the
    precedence map exists to prevent. A run that is both BAD and NOT-RUN still
    exits 1.

    Cost, stated: `dev test`'s exit code is no longer a pure function of step
    verdicts -- it gains two non-verdict terms, not exactly one:
    `sdp_oracle_not_run` (above) and `run_status_error` (D-06), each added
    into the SAME candidate set via `codes.add(2)`, decided by the single
    unchanged `_EXIT_CODE_PRECEDENCE` walk. A run that is both BAD and
    `run_status_error` still exits 1 -- that asymmetry against the title's
    status-axis-first ordering (`submit.overall_verdict`) is deliberate.

    The not-run oracle stays SKIPPED rather than becoming `marginal`, because
    `marginal` counts as *ran* and would hold N == M in the applicable ratio,
    defeating the point.

    ALLOW-only: callers gate this at the call site. A REFUSE chip reads NOT-RUN
    legitimately, and flooring its exit code would misrepresent a correct
    refusal as inconclusive.
    """
    codes = {_verdict_code(r.verdict) for r in results}
    if sdp_oracle_not_run:
        codes.add(2)
    if run_status_error:
        codes.add(2)
    for code in _EXIT_CODE_PRECEDENCE:
        if code in codes:
            return code
    return 0


def _sanitize_chip_token(chip: str) -> str:
    """Filesystem-safe token for the dev-test-<chip>.{json,md} artifact names.

    Replaces path separators and other filesystem-unsafe characters with `_`
    so an arbitrary chip name (e.g. containing `/`, spaces, or parens like
    `DS1220(RW)`) never escapes the output directory or breaks on a
    case-sensitive/insensitive filesystem boundary. Deterministic: the same
    chip name always sanitizes to the same token.
    """
    safe_chars = []
    for ch in chip:
        if ch.isalnum() or ch in ("-", "_", "."):
            safe_chars.append(ch)
        else:
            safe_chars.append("_")
    return "".join(safe_chars)


def _chip_id_fields(
    app: "AppContext", chip: str, results: list
) -> tuple[int | None, int | None, str | None]:
    """Derive (chip_id_expected, chip_id_actual, mismatch_reason) for AutoCapture.

    `chip_id_expected` is read directly off the DB entry (host-side, never
    from firmware). `chip_id_actual` is read STRUCTURALLY off the id step's
    own `StepResult.chip_id_detected` field (RPT-A5) rather than recovered
    from `reason` prose, and populates on a PASSING id check as well as on a
    mismatch (RPT-A1) -- it is `None` only when the id step never ran
    (NA/SKIPPED/absent) or returned no id at all. On a pass, the value
    equals `chip_id_expected`: `check_eprom_id`'s OK reply carries no id
    back from the firmware, so `chip_id_detected` is the host's own
    expected id echoed out of the command dict, not an independent
    read-back -- `chip_id_actual` therefore records the id the check was
    verified AGAINST on a pass, and the id the firmware actually reported
    on a mismatch. `chip_id_mismatch_reason` remains prose, read from
    `reason`, and stays `None` unless there is a disagreement to surface.
    """
    full = app.db.get_eprom(chip) or {}
    prog = app.db.convert_to_programmer(full) if full else {}
    chip_id_expected = prog.get("chip-id") or None

    chip_id_actual: int | None = None
    mismatch_reason: str | None = None
    for r in results:
        if r.op == OP_ID:
            chip_id_actual = r.chip_id_detected
            break
    for r in results:
        if r.op == OP_ID and r.reason and "mismatch" in r.reason.lower():
            mismatch_reason = r.reason
            break
    return chip_id_expected, chip_id_actual, mismatch_reason


def _canonical_part_number(part_number: str | None, raw_token: str) -> str | None:
    """RPT-F1: the alias within the matched database row's `part_number`
    that equals `raw_token` under the same normalization
    `database.get_eprom_config` used to match it; when no alias matches,
    the first alias in the list. The alias is carried verbatim, including
    any parenthetical mode annotation.

    Mirrors `get_eprom_config`'s own exact-then-alias-exact-then-
    paren-stripped ladder (`database.py:446-486`) rung for rung, rather
    than writing a second normalization, so the alias this returns is by
    construction one `get_eprom_config` itself matched on. This function
    reduces a name it is already given -- it does not decide which row
    matched; that decision already happened.

    514 of the database's 953 distinct aliases resolve to a comma-joined
    `part_number`, and the naive "first alias" reading is actively wrong
    on several of them -- `w27c020`'s first alias is `W27C02`, a
    genuinely different part number. 43 rows carry a parenthetical mode
    annotation and 24 paren-stripped names collide across more than one
    row (every DALLAS NVRAM ships an `(RW)` row and a `(TEST)` row), so
    stripping the parens here would file two distinct rows under one
    title.

    Returns `None` when `part_number` is `None` or blank (D-24) -- the
    caller falls back to the raw token rather than render the word `None`.
    """
    if not part_number:
        return None

    import re

    def _strip_paren(s: str) -> str:
        return re.sub(r"\([^)]*\)", "", s).strip().lower()

    query = raw_token.lower()
    if query == part_number.lower():
        return part_number

    if "," in part_number or "(" in part_number:
        aliases = [a.strip() for a in part_number.split(",")]
        for alias in aliases:
            if alias.lower() == query:
                return alias
        query_stripped = _strip_paren(raw_token)
        if query_stripped:
            for alias in aliases:
                if _strip_paren(alias) == query_stripped:
                    return alias

    return part_number.split(",")[0].strip()


def _make_sampler(app: "AppContext", report: DiagnosticReport) -> Any:
    """Build the before/after sampler thunk closing over `hardware_manager`.

    Constructed on EVERY run (`dev test` always writes, so
    there is no non-destructive mode left to distinguish this from -- the
    `--destructive`-only construction this docstring used to describe was
    superseded when that flag was deleted). Reuses the existing
    `sample_vpp_mv`/`sample_vpe_mv` monitor path (COMMAND_READ_VPP/VPE,
    energize+measure only) -- no VPP-set call is made here or
    anywhere in this module. `chip_test.run_plan` calls this as an opaque
    `sampler(phase)` callable and never imports `hardware.py` itself (
    decoupling, chip_test.py:542-553).
    """

    def _sampler(phase: str) -> None:
        vpp = app.hardware_manager.sample_vpp_mv()
        vpe = app.hardware_manager.sample_vpe_mv()
        if phase == "before":
            report.vpp_before_mv = vpp
            report.vpe_before_mv = vpe
        elif phase == "after":
            report.vpp_after_mv = vpp
            report.vpe_after_mv = vpe

    return _sampler


def _is_uv_eprom(app: "AppContext", chip: str) -> bool:
    """Read this chip's UV-erasable axis directly off the DB entry.

    Delegates to `chip_test.is_uv_eprom` on the FULL DB dict -- never the
    programmer dict, which carries no `electrical-type`.

    Returns False for a chip absent from the DB; every caller reaches this only
    after the absent-chip hard-fail has run, so that case is unreached.
    """
    full = app.db.get_eprom(chip)
    if not full:
        return False
    return is_uv_eprom(full)


_DEFAULT_RUNS = 3
_ALWAYS_WRITES_PASS_COUNT = 7


@dev.command(name="test")
@click.argument("chip", shell_complete=_complete_eprom)
@click.option(
    "--fast",
    is_flag=True,
    default=False,
    help=(
        "Run one write/verify cycle instead of three. WEAKER TEST: with "
        "nothing to compare, an intermittent write cannot be reported "
        "marginal and read nondeterminism goes unmeasured; such reports "
        "never count toward community agreement. Omit it for the accurate "
        "test."
    ),
)
@click.option(
    "--submit",
    is_flag=True,
    default=False,
    help=(
        "File the report to the project tracker with no prompt, through "
        "the authenticated gh CLI. This publishes a public issue with no "
        "human in the loop. Prints a URL and files nothing when gh is "
        "unavailable."
    ),
)
@click.pass_obj
@map_typed_errors
def dev_test(app: "AppContext", chip: str, fast: bool, submit: bool) -> None:
    """Run the community chip-validation sweep for CHIP.

    Writes to the chip every run (no read-only mode); saves a diagnostic
    report under the config dir's reports directory and offers to file it
    as a GitHub issue. Exit code: 0 clear, 2 marginal, 1 bad (including a
    chip-ID mismatch). The write/verify block runs as a CYCLE, three times,
    and the read step runs three times too -- the cycles are compared, a
    rig-health check for rail droop or bad contact.
    """
    # hard-fail BEFORE any hardware is energized when the chip name
    # is absent from the DB entirely (case A). Keyed strictly off
    # `get_eprom` emptiness -- NEVER a `resolve_chip` support-status refusal
    # -- so an in-DB-but-unsupported chip (case B, e.g. adapter-required)
    # still runs the full community-validation sweep below.
    if not app.db.get_eprom(chip):
        raise ChipNotFoundError(f"{chip}: not found in database")

    # The chip must be known to be in the DB (see above) before its
    # electrical type can be read, so the UV-scope resolution happens here,
    # after the hard-fail.
    plan = derive_plan(
        chip,
        app.db,
        write_scope="partial" if _is_uv_eprom(app, chip) else "full",
    )

    # EpromOperator.comm is a transient per-operation connection torn down
    # after every operator call -- there is no live
    # comm to read programmer_info off of after run_plan returns without
    # opening a new, extraneous connection, which would violate the
    # orchestrator-only contract. Both identity values instead
    # come off the hardware-revision read's OWN connection: its
    # find_and_connect triggers the identity setup ack, which sets
    # comm.firmware_identity before the HARDWARE_REVISION dispatch even
    # runs, so one orchestrator-safe energize/query read (Part A,
    # hardware.py) yields both fields with zero extra connections.
    transport_counters.reset()
    log_capture.install()
    identity = app.hardware_manager.read_programmer_identity()
    auto_capture = AutoCapture(
        host_version=version,
        fw_board_identity=identity.fw_board_identity,
        hw_revision=identity.hw_revision,
        chip=chip,
        protocol=None,
    )
    transport = TransportHealth()
    report = DiagnosticReport(
        auto_capture=auto_capture,
        transport=transport,
        plan=plan,
    )

    # Always built: every run writes now, so there is no
    # non-destructive mode left that would have no write step to bracket.
    sampler = _make_sampler(app, report)
    # `--fast` is the ONLY caller that opts out of
    # the N>=2 repeat policy, and it must say so twice: `runs=1` asks for the
    # single-run plan and `allow_single_run=True` unlocks `run_plan`'s
    # fail-closed guard. Both are required deliberately -- a caller that
    # passes `runs=1` alone still fails the whole plan, so the weaker policy
    # can only ever be reached on purpose. The default path passes neither
    # and is byte-for-byte the pre-existing call.
    #
    # `dev_test` is the ONLY caller anywhere in this tree that acquires a
    # lease (206-03, SESS-01): every other command's connect behaviour is
    # byte-for-byte unchanged. The pre-plan `read_programmer_identity` call
    # above and the sampler's own connects both go through `HardwareManager`,
    # a separate class this lease does not reach (D-05) -- they remain
    # outside it and therefore cap the measurable saving. The whole feature
    # is one commit; its sha is recorded in the phase record as the SESS-02
    # revert target, because SESS-02's bench measurement may require
    # reverting it whole.
    with app.eprom_operator.lease():
        results = run_plan(
            plan,
            app.eprom_operator,
            app.db,
            runs=1 if fast else _DEFAULT_RUNS,
            allow_single_run=fast,
            sampler=sampler,
        )
    report.results = results
    report.banner = count_applicable(plan, results)
    transport_snapshot: dict[str, int] = transport_counters.snapshot()
    report.transport.decode_failures = transport_snapshot["decode_failures"]
    report.transport.timeouts = transport_snapshot["timeouts"]
    report.transport.probe_timeouts = transport_snapshot["probe_timeouts"]
    report.transport.resync_length_missing = transport_snapshot["resync_length_missing"]
    report.transport.resync_body_truncated = transport_snapshot["resync_body_truncated"]
    # the derive-in-engine / assign-in-handler seam. `sdp_hold_state`
    # is computed in chip_test.py (the engine); this line only ASSIGNS it,
    # matching every other derived field above and below (never computed
    # inline here).
    report.sdp_hold_state = sdp_hold_state(plan, results)
    report.run_status = run_status(results)

    full = app.db.get_eprom(chip)
    if full:
        prog = app.db.convert_to_programmer(full)
        auto_capture.protocol = str(prog.get("algorithm"))
        auto_capture.canonical_part_number = _canonical_part_number(
            full.get("name"), chip
        )
    (
        auto_capture.chip_id_expected,
        auto_capture.chip_id_actual,
        auto_capture.chip_id_mismatch_reason,
    ) = _chip_id_fields(app, chip, results)

    report.db_diff = build_db_diff(chip, app.db, results)

    console = Console()
    cli_start = _cli_start_time()
    report.elapsed = (
        None if cli_start is None else round(time.monotonic() - cli_start, 3)
    )
    report.log_capture = log_capture.snapshot()
    log_capture.uninstall()
    report.render(console)

    # The report is ALWAYS persisted, unconditionally, to the reports
    # directory under <config dir> (honors FIRESTARTER_CONFIG_DIR; default
    # ~/.firestarter/reports) -- the removed --output-dir flag was
    # redundant with this env-var seam, never a lost capability.
    out_path = Path(get_config_dir()) / "reports"
    out_path.mkdir(parents=True, exist_ok=True)
    safe_chip = _sanitize_chip_token(chip)

    report_dict = report.to_dict()
    json_file = out_path / f"dev-test-{safe_chip}.json"
    json_file.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")

    # Local import, matching this handler's existing `submit as submit_mod`
    # style further down -- `submit` imports `diagnostic_report`, so a
    # module-level import here would tighten an already-layered graph for
    # one formatter.
    from firestarter.submit import _duration_text as submit_duration_text
    from firestarter.submit import _error_cells as submit_error_cells
    from firestarter.submit import _log_capture_lines as submit_log_capture_lines
    from firestarter.submit import _reason_text as submit_reason_text
    from firestarter.submit import _runs_text as submit_runs_text

    canonical_heading_name = (
        report_dict["auto_capture"]["canonical_part_number"] or chip
    )
    error_cells = submit_error_cells(
        (r.verdict, r.error_code, getattr(r, "error_name", None)) for r in results
    )
    if error_cells is None:
        md_lines = [
            f"# dev test -- {canonical_heading_name}",
            "",
            "| Step | Verdict | Runs | Took | Reason |",
            "| ---- | ------- | ---- | ---- | ------ |",
        ]
    else:
        md_lines = [
            f"# dev test -- {canonical_heading_name}",
            "",
            "| Step | Verdict | Runs | Took | Error | Reason |",
            "| ---- | ------- | ---- | ---- | ----- | ------ |",
        ]
    for idx, r in enumerate(results):
        # `Took` mirrors submit.build_body's own column (schema 1.5) so the
        # saved artifact and the filed issue body carry the same timings.
        # `Runs` does the same for `run_count` (schema 1.7, quick task
        # 260822-aq6). `Reason` suppresses NA-verdict rows to `-` (quick
        # task 260822-gxx) -- all three formatters are imported from
        # `submit` rather than re-implemented, so the two tables can never
        # disagree on how an absent value renders or an NA row suppresses.
        # The console needs no equivalent: `DiagnosticReport.render()`
        # already drops every non-`_RAN_VERDICTS` row (including NA)
        # entirely before it ever reaches a Reason cell.
        took = submit_duration_text(r.duration_s)
        runs = submit_runs_text(r.run_count)
        reason = submit_reason_text(r.verdict, r.reason)
        if error_cells is None:
            md_lines.append(f"| {r.op} | {r.verdict} | {runs} | {took} | {reason} |")
        else:
            md_lines.append(
                f"| {r.op} | {r.verdict} | {runs} | {took} | "
                f"{error_cells[idx]} | {reason} |"
            )
    md_lines.extend(submit_log_capture_lines(report_dict))
    md_lines.append("")
    md_lines.append(report.to_json_block())
    md_file = out_path / f"dev-test-{safe_chip}.md"
    md_file.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    console.print(f"[dim]Report written to {json_file}[/dim]")

    from firestarter import submit as submit_mod

    submit_mod.submit_report(
        report, chip, json_file, console=console, auto_submit=submit
    )

    if not results:
        sys.exit(0)
    # The exit floor is ALLOW-only -- `sdp_oracle_applicable(plan)`
    # gates it, so a REFUSE chip's legitimate `NOT-RUN` (the oracle was
    # never applicable) is never floored.
    code = _dev_test_exit_code(
        results,
        sdp_oracle_not_run=sdp_oracle_applicable(plan)
        and report.sdp_hold_state.startswith(SDP_HOLD_NOT_RUN),
        run_status_error=(report.run_status == STATUS_ERROR),
    )
    sys.exit(code)
