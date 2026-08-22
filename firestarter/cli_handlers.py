"""Click-based CLI handlers for firestarter (Phase 41 / v1.8).

This module is the production CLI surface; main.py re-exports ``cli`` as
``main`` for the ``firestarter`` console-script entry point (D-08, D-16).
The argparse machinery in main.py was deleted in Plan 41-04 (Wave 4).

Commands surfaced from here:
  - 3 read-only: list / info / search
  - 6 chip-ops: read / write / verify / blank / erase / id
  - 2 voltage: vpp / vpe
  - 2 hardware: hw / config
  - 1 firmware: fw (3-way --pre/--firmware-version/--stable mutex + version
    validator)
  - 1 group: dev (6 sub-commands: read / reg / addr / consistency-check /
                              write-cycle / validate-family)
"""

import datetime
import functools
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Literal, Optional  # noqa: UP035

import click
import click.shell_completion
from rich.console import Console
from rich.prompt import Confirm

from firestarter import __version__ as version
from firestarter import sdp_honesty  # unreadable_state_caveat(), called not re-authored
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
    sdp_hold_state,
    sdp_oracle_applicable,
)
from firestarter.config import ConfigManager, get_config_dir
from firestarter.constants import FLAG_CHIP_ENABLE, FLAG_OUTPUT_ENABLE
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
    """Typed DI container threaded through every Click handler via ctx.obj (D-05, D-07).

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
    """Refuse a py32-only CLI option outside its owning channel (HOST-02 / D-08).

    ``hidden=not _PY32_ENABLED`` on an option's ``@click.option`` decorator is a
    ``--help`` cosmetic only: it keeps the option out of the rendered help text,
    it does not reject the option when a user types it anyway. That confusion
    is exactly the bug HOST-02 exists to close: on a stable build, ``--usb-id``
    was accepted (exit 0) while ``--dfu-probe`` was refused (exit 2), even
    though both are py32-only surface.

    This helper is the single sanctioned refusal mechanism for every py32-only
    option. It is called unconditionally for each option, passing that
    option's givenness, before either option is consumed. One shared code
    path means the two refusals cannot drift apart from each other, and a
    third py32-only option added later inherits the same behaviour for free
    just by calling this helper here — see the one-code-path guard in
    ``tests/test_py32_channel_gating.py``, which asserts this refusal's
    message occurs exactly once in this file.

    Reads ``_PY32_ENABLED`` at call time — a module global, not a captured
    default argument — which is what makes this helper directly unit-testable
    in-process via monkeypatch while the Click command surface built from the
    decorators above stays frozen at import time.

    The message text and the resulting exit code are preserved exactly from
    the pre-existing ``--dfu-probe`` refusal this helper replaces, per
    HOST-02's requirement that ``--usb-id`` be rejected exactly as
    ``--dfu-probe`` already is.
    """
    if given and not _PY32_ENABLED:
        raise click.UsageError(f"no such option: {name}")


def map_typed_errors(f: Callable[..., Any]) -> Callable[..., Any]:
    """Map service-layer typed exceptions to ClickException + stable exit codes (D-03)."""

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
            # DB-04 SC#2 Approach A: render the reason string verbatim.
            # Plan 01 reworded unsupported_reason strings to begin with the
            # SC-required wording, so str(e) = "<name>: <reason>" is already
            # the authoritative status-specific message. Drop the generic
            # "Chip not usable:" prefix so the DB string is the single source
            # of truth for both info display and chip-op refusal.
            raise click.ClickException(str(e)) from e
        except FirmwareOperationError as e:
            # Raised by the USB DFU install path. The message is already
            # operator-actionable (how to enter the bootloader, or how to install
            # pyusb), so it is rendered verbatim rather than prefixed.
            raise click.ClickException(str(e)) from e
        except EpromOperationError as e:
            raise click.ClickException(f"Programmer error: {e}") from e
        except HardwareOperationError as e:
            raise click.ClickException(f"Hardware error: {e}") from e

    return wrapper


def build_arg_flags(args: object) -> int:
    """Argparse-Namespace/PlainArgs-bag adapter over ``_build_op_flags``.

    Relocated verbatim (W1's getattr fix preserved byte-identical) from
    main.py:504-518 per Phase 41 D-16. This is the bag-introspection form
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
    # Phase 92 decouple: skip-erase is its own explicit flag, NOT implied by
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
    """D-22 / D-25 beta-app magic default: when installed app is a pre-release,
    any 'fw' invocation that pins no channel (no --pre, no --firmware-version,
    no --stable) auto-routes to the --pre channel. ``args.install`` is NOT part
    of the condition — see the comment on the guard below for the two defects
    that gating on it caused.

    Relocated verbatim from main.py:211-249 per Phase 41 D-16. Signature is
    ``(args) -> None`` — NO logger parameter (Phase 18 revision warning #6).
    Uses ``logging.getLogger(__name__)`` internally so pytest's caplog captures
    records automatically by logger name.

    Callers in cli_handlers.py reach this helper via ``_maybe_auto_route_to_pre_click``
    which builds a ``SimpleNamespace`` from the explicit Click kwargs (D-15
    adapter pattern — zero churn to this helper's body).

    D-23: stable-installed apps (Version.is_prerelease=False) are unaffected.
    D-24: explicit --firmware-version OR --stable opts out of this magic.
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
    # D-23 (a stable-installed app is unaffected) and D-24 (--stable or
    # --firmware-version opts out) are both unchanged: they are the other two
    # clauses below and the is_prerelease test.
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
    # D-14 / RETIRE-07 tripwire, edit point 1 of 2: this default is one of
    # the two places a developer would touch to disable the host's
    # auto-unlock. Before changing it, read the tripwire comment at the
    # D-04 auto-set condition inside write() below -- flipping this default
    # invalidates the removal-safety argument RETIRE-01 rests on.
    skip_sdp_unlock: bool = False,
    input_enable: Optional[bool] = None,
    chip_disable: Optional[bool] = None,
) -> int:
    """Click-side equivalent of main.py's build_arg_flags helper.

    Click handlers receive their options as explicit kwargs, so there is no
    args-bag introspection needed. This helper applies the same flag-mapping
    rules build_arg_flags uses (post-41-01 truthiness semantics):

        - blank_check / force / vpe_as_vpp / verbose -> build_flags(...)
        - skip_erase -> FLAG_SKIP_ERASE (explicit; see decouple note below)
        - input_enable presence (any value, even False) -> apply OE mask rule
        - chip_disable presence (any value, even False) -> apply CE mask rule

    The OE/CE flags use None to mean "this command does not take this flag"
    so they behave like the main.py `hasattr(args, "input_enable")` gate:
    only `dev reg` and `dev addr` opt into them.

    Phase 92 decouple (was `skip_erase=not blank_check`): `-b`/`--no-blank-check`
    no longer implies skip-erase. Skipping the blank check must NOT skip the
    erase that electrically-erasable parts (FLAG_CAN_ERASE: flash3/flash4/EEPROM
    /EPROM-EEPROM) require — `write -b` on a non-blank such chip used to silently
    skip the erase, leaving un-erasable 0->1 bits while the firmware's DQ7-only
    poll reported "successful" (the Phase-90/91 "12V-VPP regression" false alarm).
    `skip_erase` is now an explicit opt-in (write `--skip-erase`), default False.
    """
    # D-19: FLAG_SKIP_SDP_UNLOCK is passed into build_flags as a keyword
    # argument, NOT OR-ed into `flags` after the call the way
    # FLAG_OUTPUT_ENABLE / FLAG_CHIP_ENABLE are below — every wire-flag bit
    # stays mapped in the one function that maps wire flags (build_flags),
    # deliberately not following the OE/CE precedent in this same helper.
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
    """Click ParamType that mirrors main.py:194-208 `_validate_firmware_version`.

    Validates against `FIRMWARE_VERSION_RE` before any network call (D-07);
    accepts stable (X.Y.Z) and pre-release (X.Y.ZbN, X.Y.ZrcN) forms (D-08).
    On mismatch, `self.fail(...)` raises `click.BadParameter` which Click
    converts to `SystemExit(2)` — preserves the argparse `ArgumentTypeError`
    → exit-2 contract.

    Custom ParamType subclass picked over a plain option callback per D-13.5
    (Claude's Discretion): more Click-canonical + reusable across
    `--firmware-version` instances if ever added elsewhere.

    NOTE: Distinct from `SerialCommunicator._validate_firmware_version`
    @staticmethod introduced in Phase 40 D-01..D-05 — that's the
    TRANSPORT-layer guard. This class is the CLI-layer input validator.
    """

    name = "firmware_version"

    def convert(
        self,
        value: Optional[str],
        param: Optional[click.Parameter],
        ctx: Optional[click.Context],
    ) -> Optional[str]:
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


@click.group()
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
def cli(ctx: click.Context, verbose: bool, port: Optional[str]) -> None:
    """EPROM programmer for Arduino and Relatively-Universal-ROM-Programmer shield."""
    # CliRunner tests pass a pre-built AppContext via `runner.invoke(cli, ..., obj=app)`;
    # honor that and skip manager construction in test mode. In production
    # ctx.obj starts as None (Click default) so the manager-construction path
    # runs verbatim. This is the standard "AppContext-on-ctx.obj" pattern from
    # Click's docs (https://click.palletsprojects.com/en/stable/complex/).
    # WR-02: _setup_logging must run AFTER the test-mode short-circuit so it
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
# Chip-op commands (Wave 3 / Plan 41-03 / D-12 step 1; Plan 42-02 / D-03/D-05)
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
    output_file: Optional[str],
    force: bool,
    address: Optional[str],
    size: Optional[str],
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
# D-14 / RETIRE-07 tripwire, edit point 2 of 2: this option's `default=False`
# is the second place a developer would touch to disable the host's
# auto-unlock. Before changing it, read the tripwire comment at the D-04
# auto-set condition inside write() below -- flipping this default
# invalidates the removal-safety argument RETIRE-01 rests on.
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
@click.pass_obj
@map_typed_errors
def write(
    app: AppContext,
    eprom: str,
    input_file: str,
    blank_check: bool,
    skip_erase: bool,
    force: bool,
    address: Optional[str],
    vpe_as_vpp: bool,
    pulse_us: Optional[int],
    skip_sdp_unlock: bool,
) -> None:
    """Writes a binary file to an EPROM.

    TRAP #3 / D-13.3: ``--no-blank-check`` uses
    ``is_flag=True, flag_value=False, default=True`` so the presence of ``-b``
    flips ``blank_check`` to False (mirrors argparse ``store_false default=True``).
    The inverse ``--blank-check`` polarity lives on the ``erase`` command —
    both polarities coexist verbatim per the rationale lock.

    Phase 92 decouple: ``-b`` now skips ONLY the blank check. The pre-write erase
    still runs for electrically-erasable chips (FLAG_CAN_ERASE) so ``write -b`` on
    a non-blank flash/EEPROM works. Use ``--skip-erase`` to also skip the erase
    (previously implied by ``-b``) for already-blank or non-erasable parts.

    Phase 153 (ERASE-01/ERASE-02): since this phase, ``-b``/``--no-blank-check``
    is **unread** on protocols ``0x0D`` and ``0x05`` — neither protocol's write
    path performs a pre-write blank check any more, so the flag is a no-op on
    both families and is not needed to write a non-blank part on either one.
    The flag remains live, with its Phase 92 meaning above, on every other
    protocol. This does not change the paragraph above: the erase/blank-check
    decoupling it describes still governs whichever protocols still read the
    flag.

    TRAP #6 / D-17/D-18 (v1.22 HOST-02): ``--skip-sdp-unlock`` is exposed
    on ``write`` ONLY — firmware auto-unlocks in ``eeprom28c_write_init`` and
    nowhere else, so ``read``/``verify``/``blank``/``erase`` have nothing to
    skip and the flag is deliberately absent from all four (D-17). On a
    non-protocol-0x0D chip the flag has no effect: firmware never reads this
    bit outside protocol 0x0D, so the host warns and proceeds rather than
    refusing or silently dropping the bit — the bit is still emitted so a
    blanket-flag script across a mixed batch produces identical wire frames
    (D-18). The host may also set this bit **on its own**, without the user
    passing it, when the resolved chip is protocol-0x0D and capability-refused
    (``firestarter.sdp_capability``) — see the D-04 auto-set block below,
    which always prints a mandatory, default-visible report line when it
    fires.

    TRAP #7 / D-14..D-18 (v1.31 HOST-04/HOST-05): ``--pulse-us`` overrides
    the database program-pulse width for this run only. D-14: it rides the
    existing ``"pulse-delay"`` wire key (``write_eprom``'s own ``pulse_us``
    parameter, plan 143-04's transport half) — no new command, no new wire
    field. D-15: bounds are enforced by the option's own
    ``click.IntRange(1, 65535)``, which refuses out of range at Click PARSE
    TIME, exit 2. The guarantee this gives HOST-05 ("before any serial byte
    is sent") is NOT "before ``AppContext`` builds" — ``cli()``'s group
    callback runs first, before this command's own parameters are even
    type-converted — the guarantee is that NOTHING in ``cli()`` or
    ``AppContext`` construction opens a serial port, so a parse-time refusal
    is still structurally before any serial byte. D-16: a value in
    ``50001..65535`` is host-legal but firmware-refused on protocol ``0x0B``
    only (``configure_eprom``'s ``energy_cap_us``-keyed pre-flight check,
    ``MSG_ERR_PULSE_TOO_WIDE``) before any high voltage is enabled — the host
    deliberately mirrors no table value to pre-empt it (that would require
    duplicating ``energy_cap_us`` host-side). D-17: using the flag ALWAYS
    prints a default-visible report line (see below) naming both the
    database pulse replaced and the override — provenance, because a bench
    artifact or log captured without the command line beside it cannot
    otherwise tell you the pulse was not the database's. D-18: the flag
    exists on ``write`` ONLY, mirroring the reasoning above for
    ``--skip-sdp-unlock`` — ``read``/``verify``/``blank``/``erase`` emit no
    program pulse, so there is nothing to override.
    """
    eprom_data = resolve_chip(eprom, db=app.db)

    # D-17 (v1.31 HOST-04/HOST-05): a SEPARATE, sibling `if` -- never an
    # `elif` chained onto the D-04 or D-13 blocks below, so this can co-fire
    # with either on the same chip (S-6). click.echo (never logger.info):
    # this must be visible at DEFAULT verbosity, with no -v needed. Reason:
    # a bench artifact or log captured without the command line beside it
    # cannot otherwise tell you the pulse was not the database's -- and
    # Phase 145's evidence will be read by strangers.
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

    # D-04 auto-set (v1.22 HOST-04): decided here, in the handler, because
    # this is the last place with both the chip NAME and app.db — resolve_chip's
    # programmer dict carries neither `protocol-id` nor `name` (RESEARCH F-06).
    # This is a DELIBERATE DIVERGENCE from 3.0.0b11 for the capability-refused
    # 0x0D subset, not a no-op: today's `write` already emits the SDP-disable
    # sequence before the payload on those parts, leaving 0x2AAA<-0x55 /
    # 0x5555<-0x20 stored as data at the bus-truncated magic addresses (an
    # address-ranged or short write does not get overwritten by the payload).
    # The trade-off is dissolved rather than decided on the derived 43/41
    # partition: a part with no SDP has nothing to unlock, so suppressing its
    # auto-unlock costs that part nothing and additionally avoids those three
    # stored bytes. Residual risk is confined to 120-WATCHLIST.md's 9 entries.
    sdp_entry = app.db.get_eprom(eprom)
    is_protocol_0x0d = (
        bool(sdp_entry) and sdp_entry.get("protocol-id") == SDP_PROTOCOL_ID
    )
    allowed, sdp_reason = sdp_capability(eprom, app.db)
    # D-14 / RETIRE-07 tripwire -- THE decision site. This condition IS the
    # removal-safety argument for RETIRE-01 (Phase 132 deleted the standalone
    # `firestarter dev sdp` subcommand): that deletion was safe only BECAUSE
    # this auto-unlock fires by default, unconditionally, for every
    # capability-refused protocol-0x0D part on every `write` -- no user needs
    # a manual unlock surface as long as this stays true. Flipping either
    # default this condition depends on (`skip_sdp_unlock: bool = False` in
    # `_build_op_flags` above, or the `--skip-sdp-unlock` Click option's
    # `default=False` above), narrowing this condition, or making the flag
    # default to SKIPPING the unlock invalidates the removal argument and
    # requires RETIRE-01 to be revisited alongside the change. The companion
    # test that pins this dependency and fails if it breaks is
    # `test_dev_sdp_removal_is_safe_only_because_auto_unlock_is_default_on`
    # in tests/test_write_skip_sdp_unlock.py; the companion note lives at
    # FLAG_SKIP_SDP_UNLOCK's definition in constants.py. This is a comment
    # only (D-05) -- no output is added here, and the condition itself is
    # unchanged.
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
        # D-18 warn-and-proceed: the user asked for something vacuous on this
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

    # D-13 warn-and-proceed (v1.22 GATE-02, contributes-only): a DELIBERATE
    # SIBLING `if`, not an `elif` chained onto the block above — this checks
    # `--skip-erase`, an entirely different flag from the D-04/D-18 block's
    # `skip_sdp_unlock`, so both blocks must be free to fire independently on
    # the same 0x0D chip (e.g. a capability-refused 0x0D part gets the D-04
    # auto-set line AND this line together). Do NOT refuse, do NOT abort, do
    # NOT suppress the bit: nothing on the 0x0D WRITE path reads an
    # erase-capability bit, so the flag is inert here regardless of whether
    # this message fires. The bit is still emitted (unconditionally, via
    # `_build_op_flags` below) so a blanket-flag script across a mixed batch
    # of chips still produces byte-identical wire frames whether or not this
    # line printed. Scope unchanged from D-13: this arm still deliberately
    # does NOT extend to `-b`/`--no-blank-check`.
    #
    # Phase 153 correction (RESEARCH C-8, now inverted): this arm's message
    # used to justify itself by claiming the 28C family "has no erase
    # operation at all". That clause is now FALSE — ERASE-03 restored
    # `FLAG_CAN_ERASE` for algorithm 13 and the family gained a standalone
    # `firestarter erase` (D-153-04, D-153-05). What is still true, and is
    # now the message's only justification, is narrower: the WRITE path
    # specifically performs no erase (D-153-05 deliberately keeps erase out
    # of `eeprom28c_write_init`), so this flag still has nothing to skip
    # there. The corrected message states only that, and names the
    # standalone command so it is a useful redirect rather than a merely
    # non-false statement.
    #
    # The original comment's OWN reasoning for not extending this arm to
    # `-b`/`--no-blank-check` has ALSO inverted: it argued `-b` was
    # "genuinely useful on a non-blank 0x0D part precisely because there is
    # no erase to make the part blank" — but ERASE-01 removed the pre-write
    # blank check on this protocol entirely, so `-b` is now itself a no-op
    # on 0x0D, for an unrelated reason (there is no blank check left for it
    # to skip). The CONCLUSION is nevertheless unchanged — still no second
    # warning here — but for a different reason: per RESEARCH §Common
    # Pitfalls Pitfall 5, warning that `-b` is vacuous would train users to
    # think the write needs a flag, which is exactly the recommendation
    # 152-CONTEXT.md D-08 exists to keep out of the public release notes.
    # This paragraph records that the inversion was noticed and the
    # conclusion re-derived, not merely carried over stale.
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
        # D-14: translate Click's `None` ("--pulse-us not supplied") into
        # write_eprom's own integer sentinel (0 means "use the database
        # value" -- see that function's docstring, plan 143-04).
        pulse_us=pulse_us or 0,
    )
    sys.exit(0 if ok else 1)


@cli.command(name="verify")
@click.argument("eprom", shell_complete=_complete_eprom)
@click.argument("input_file")
@click.option("-a", "--address", default=None, help="Verify start address in dec/hex")
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force, even if the VPP or chip id doesn't match.",
)
@click.pass_obj
@map_typed_errors
def verify(
    app: AppContext,
    eprom: str,
    input_file: str,
    address: Optional[str],
    force: bool,
) -> None:
    """Verifies the content of an EPROM."""
    eprom_data = resolve_chip(eprom, db=app.db)
    ok = app.eprom_operator.verify_eprom(
        eprom,
        eprom_data,
        input_file,
        address_str=address,
        operation_flags=_build_op_flags(force=force),
    )
    sys.exit(0 if ok else 1)


@cli.command(name="blank")
@click.argument("eprom", shell_complete=_complete_eprom)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force, even if the VPP or chip id doesn't match.",
)
@click.pass_obj
@map_typed_errors
def blank(app: AppContext, eprom: str, force: bool) -> None:
    """Checks if an EPROM is blank."""
    eprom_data = resolve_chip(eprom, db=app.db)
    ok = app.eprom_operator.check_eprom_blank(
        eprom, eprom_data, operation_flags=_build_op_flags(force=force)
    )
    sys.exit(0 if ok else 1)


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
@click.pass_obj
@map_typed_errors
def erase(
    app: AppContext,
    eprom: str,
    force: bool,
    blank_check: bool,
    sector_address: Optional[str],
) -> None:
    """Erase an EPROM, if supported.

    TRAP #3 / D-13.3: this command keeps the inverse ``--blank-check`` polarity
    (``is_flag=True default=False``) — opposite of ``write``'s
    ``--no-blank-check``. Both polarities coexist verbatim from argparse.

    D-153-04: unlike ``write``'s ``-b``, this command's ``-b``/``--blank-check``
    requests a blank check performed **after** the erase, not skipped before it
    — the inverse polarity above is a naming/default inversion, not just a
    default flip. On protocol ``0x0D`` this post-erase check is not wired (no
    ``operation_end`` arm was added to the software chip-erase handler), so
    ``-b`` is a documented no-op there, not a discovered one.

    D-153-04 (RESEARCH A7): ``-s``/``--sector-address`` exists for the
    ``0x06`` sector-erase protocol. The ``0x0D`` software chip erase is
    device-global by construction (the whole part is erased in one AN 0544B
    sequence) and ignores any sector address given for it.
    """
    eprom_data = resolve_chip(eprom, db=app.db)
    ok = app.eprom_operator.erase_eprom(
        eprom,
        eprom_data,
        operation_flags=_build_op_flags(blank_check=blank_check, force=force),
        address_str=sector_address,
    )
    sys.exit(0 if ok else 1)


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
# Voltage commands (Wave 3 / D-12 step 2)
# ---------------------------------------------------------------------------


@cli.command(name="vpp")
@click.option("-t", "--timeout", type=int, default=None, hidden=True)
@click.pass_obj
@map_typed_errors
def vpp(app: AppContext, timeout: Optional[int]) -> None:
    """VPP voltage."""
    ok = app.hardware_manager.read_vpp_voltage(
        timeout_seconds=timeout, flags=_build_op_flags()
    )
    sys.exit(0 if ok else 1)


@cli.command(name="vpe")
@click.option("-t", "--timeout", type=int, default=None, hidden=True)
@click.pass_obj
@map_typed_errors
def vpe(app: AppContext, timeout: Optional[int]) -> None:
    """VPE voltage."""
    ok = app.hardware_manager.read_vpe_voltage(
        timeout_seconds=timeout, flags=_build_op_flags()
    )
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# Hardware commands (Wave 3 / D-12 step 3)
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
    rev: Optional[float],
    r16: Optional[int],
    r14r15: Optional[int],
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
# Firmware command (Wave 3 / D-12 step 4)
# TRAPs #4 (3-way mutex enforced post-parse at top of fw() body — WR-03;
# previously per-option callback _check_install_mutex, now removed)
# + #5 (_FirmwareVersionType custom ParamType). D-14 (UsageError on --json
# without --list). D-15 (SimpleNamespace adapter for _maybe_auto_route_to_pre).
# ---------------------------------------------------------------------------


def _maybe_auto_route_to_pre_click(
    install: bool, pre: bool, firmware_version: Optional[str], stable: bool
) -> bool:
    """Click-side equivalent of the _maybe_auto_route_to_pre helper.

    D-15 picks the SimpleNamespace adapter approach: build a namespace from
    the relevant fw kwargs, hand it to the (now-local) helper. Keeps the
    helper's body untouched (zero churn; relocated from main.py in Wave 4 /
    Plan 41-04 per D-16).

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
    firmware_version: Optional[str],
    stable: bool,
    list_releases: bool,
    board: str,
    usb_id: Optional[str],
    dfu_probe: bool,
    avrdude_path: Optional[str],
    avrdude_config_path: Optional[str],
    force: bool,
    json_output: bool,
) -> None:
    """Firmware version.

    Implements TRAP #4 (3-way --pre / --firmware-version / --stable mutex via
    a single post-parse check at the top of the command body — WR-03; replaces
    the earlier per-option callback _check_install_mutex which depended on
    Click's left-to-right option-processing order) and TRAP #5 (firmware-version
    validator via custom Click ParamType _FirmwareVersionType). D-14 narrow
    upgrade: the post-parse `--json requires --list` check uses
    `raise click.UsageError(...)` (exit-2 + "Usage:" formatting preserved
    from the argparse `fw_parser.error()` form).
    """
    app: AppContext = ctx.obj

    # WR-03: 3-way --pre / --firmware-version / --stable mutex enforced once,
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

    # D-14 narrow UsageError upgrade (was: fw_parser.error in main.py:798).
    if json_output and not list_releases:
        raise click.UsageError("--json requires --list")

    # HOST-02 / D-08: both py32-only options are refused through one shared
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
        if json_output:
            import json as _json

            print(_json.dumps(releases, indent=2))
        else:
            print(f"{'Version':<12} {'Channel':<14} {'Published':<22} Asset URL")
            for r in releases:
                print(
                    f"{r['version']:<12} {r['channel']:<14} {r['published']:<22} {r['asset_url']}"  # noqa: E501
                )
        sys.exit(0)

    # D-15: SimpleNamespace adapter for the magic-default helper (zero churn).
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
# CHAN-01..07 (Phase 136) — dev-tools channel gate. D-01: the gate is BOTH
# mechanisms below, not either. `_DEV_TOOLS_ENABLED` is computed ONCE, at
# import time, from `channel.is_dev_tools_enabled()` -- mirroring
# `_PY32_ENABLED` above: a wheel's `__version__` is fixed when it is built, so
# the choice a stable install renders is decided once, and decided correctly,
# rather than re-evaluated per invocation (`is_dev_tools_enabled()` is itself
# call-time/unmemoized -- see its own docstring in channel.py -- so capturing
# it into a module global here is what freezes the decision). `_DevGroup` is
# the other half: it holds the six gated NAMES only
# (`channel.BETA_ONLY_DEV_COMMANDS`), never a callback, and supplies the
# informative refusal (CHAN-03) for a name that resolves to nothing real.
# Genuine non-registration (CHAN-02) happens separately, below, at each of the
# six gated `@dev.command` blocks, each guarded at module scope by
# `_DEV_TOOLS_ENABLED`.
# ---------------------------------------------------------------------------

_DEV_TOOLS_ENABLED: bool = is_dev_tools_enabled()


class _DevGroup(click.Group):
    """`dev` group's Click command class (D-01's exact name).

    Holds the six gated `dev` subcommand NAMES only, via
    `channel.BETA_ONLY_DEV_COMMANDS` -- never a callback. A gated command
    must not exist as an invokable object in a stable process; that is
    enforced by conditional registration (the `_DEV_TOOLS_ENABLED` guards
    below), not by this class. This class's only job is the informative
    refusal (CHAN-03): when a gated-but-unregistered name is looked up, raise
    a channel-specific `UsageError` instead of letting Click fall through to
    its generic, typo-indistinguishable `No such command %r.` error.

    `get_command` is the only method overridden, and that choice is not a
    guess -- it is the empirically-settled hook from plan 136-01's spike
    (`tests/test_click_group_gate_hook.py`): `click.Group.resolve_command()`
    calls `self.get_command(ctx, cmd_name)` itself and only falls through to
    its own generic error when `get_command` returns `None`, so overriding
    `get_command` intercepts strictly before that fallback ever runs.
    `resolve_command` therefore needs no override at all, and `list_commands`
    needs none either: once a gated name is genuinely unregistered (Task 2,
    below), it is already absent from `self.commands`, so it is already
    absent from `list_commands`'s output with no extra code.
    """

    def get_command(self, ctx: click.Context, cmd_name: str) -> Optional[click.Command]:
        real = super().get_command(ctx, cmd_name)
        if real is not None:
            return real
        if cmd_name in BETA_ONLY_DEV_COMMANDS:
            raise click.UsageError(dev_command_gate_message(cmd_name), ctx=ctx)
        return None


# ---------------------------------------------------------------------------
# dev group + 4 sub-commands (Wave 3 / D-12 step 5)
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
    address: Optional[str],
    size: Optional[str],
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
    # CHAN-06 tripwire (Phase 136, RETIRE-07-style: names WHY before a future
    # edit touches the gate). `dev reg` is gated behind `_DEV_TOOLS_ENABLED`,
    # which freezes `channel.is_dev_tools_enabled()` at import time --
    # `is_prerelease_build() OR dev_tools_enabled_by_env()`. This command is
    # load-bearing bench tooling: it is the held-erase-rail DMM proxy an
    # operator uses to hold a register state (and therefore a voltage rail)
    # energised long enough for a multimeter reading outside a normal
    # read/write cycle. Gating purely on `__version__` would silently strand
    # that bench dependency the moment a stable version is cut, or between
    # betas, on an editable devcontainer install -- no error, just an absent
    # command. That is exactly why the `FIRESTARTER_DEV_TOOLS=1` bench
    # override exists: `channel.dev_tools_enabled_by_env` (exact-match
    # `"1"` against the `FIRESTARTER_DEV_TOOLS` environment variable, fails
    # closed on everything else) and `channel.is_dev_tools_enabled`'s `OR`
    # composing it with the channel check are the two companions this
    # depends on: narrowing the accepted `FIRESTARTER_DEV_TOOLS` value, or
    # removing that `OR`, strands the bench tooling without warning.
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
        help="Suppress per-run tqdm progress bars (D-11).",
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
        output_dir: Optional[str],
        keep_files: bool,
        max_diffs: int,
        quiet: bool,
        force: bool,
        read_settling_us: int,
        read_strobe_us: int,
    ) -> None:
        """Read EPROM N consecutive times and report SHA-256 divergence.

        D-12 step 5 / 3-way verdict contract:
            verdict_int = consistency_check_eprom(...)  # 0=PASS, 1=FAIL, 2=hw-error
            sys.exit(verdict_int)  # NOT bool-to-int wrap

        The bool-to-int wrap would collapse the 2=hardware-error case to 1=FAIL,
        breaking the v1.6 RCA diagnostic.
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
        output_dir: Optional[str],
        force: bool,
    ) -> None:
        """Erase → write source image → read-back N times; assert SHA-256 == source SHA.

        3-way verdict contract (mirrors dev consistency-check):
            verdict_int = write_cycle_eprom(...)  # 0=PASS, 1=mismatch, 2=hw-error
            sys.exit(verdict_int)  # NOT bool-to-int wrap — preserves 0/1/2

        The bool-to-int wrap would collapse the 2=hardware-error case to 1=mismatch,
        breaking the v1.6 RCA diagnostic. XACT-01 / Phase 53 Plan 02.
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
        type=click.Choice(["cycle", "latency"]),
        default="cycle",
        help="cycle = read-cycle resync demo (default); latency = per-frame firmware NAK "
        "latency on an established single-port connection (53-04 refinement; no chip needed).",
    )
    @click.option(
        "--output-dir",
        "output_dir",
        type=str,
        default=None,
        help="Output dir for transfer binaries.",
    )
    @click.pass_obj
    @map_typed_errors
    def dev_fault_inject(
        app: AppContext,
        eprom: str,
        direction: str,
        fault_form: str,
        mode: str,
        output_dir: Optional[str],
    ) -> None:
        """Demonstrate COBS resync: inject a corrupted frame and assert recovery on the next.

        cycle mode: one corrupted transfer then asserts the same connection recovers on a
        clean follow-on transfer (XACT-02 / Phase 53 Plan 02).

        latency mode: opens ONE pinned port and times the firmware's per-frame NAK on a
        corrupt CMD_FW_VERSION frame (established connection — avoids the multi-port
        connect-retry that inflates cycle-mode's outgoing latency). Use with -p <port>.
        """
        if mode == "latency":
            ok = app.eprom_operator.measure_command_nak_latency(
                fault_form=fault_form,
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
# dev lock-status (Phase 151 / LOCK-02, LOCK-03, LOCK-04 -- D-01, D-04, D-06,
# D-07, D-08, D-10). D-01 chose a real silicon read exposed only on a
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
            "Proceed past a table refusal anyway (D-07). The result is an "
            "unadjudicated probe, never a state claim -- and this never sets "
            "a wire-visible flag (C-16): the table refusal it bypasses is a "
            "host-side decision only."
        ),
    )
    @click.pass_obj
    @map_typed_errors
    def dev_lock_status(app: AppContext, eprom: str, force: bool) -> None:
        """Diagnostic read of a chip's write-protection state -- not a guarantee (D-01)."""
        # D-04: resolve through db.get_eprom(), never resolve_chip()'s
        # programmer dict -- that dict carries neither 'protocol-id' nor
        # 'name', the exact shape protection_gate_for_entry hard-fails on.
        # This is the last place in this handler with both the chip NAME
        # and app.db, mirroring write()'s own D-04 idiom above.
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
        # refusal (D-07). Both dicts are needed from here on: get_eprom()
        # fed the predicate above; resolve_chip() is what the firmware
        # operation itself needs.
        eprom_data = resolve_chip(eprom, db=app.db)
        try:
            _accepted, payload = app.eprom_operator.read_protection_status(
                eprom, eprom_data, operation_flags=_build_op_flags()
            )
        except EpromOperationError as exc:
            # D-04, keyed on the message **id**, never on text -- a version
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
# dev validate-family (71-06 / HARN-01 Tier-3 + HARN-02 + HARN-03)
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


def _load_validation_spec() -> Dict[str, Any]:  # noqa: UP006 (python3.9 compat)
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
    output_dir: Optional[str],
    reason: str = "no board/chip/source provided",
) -> None:
    """Emit validation-matrix.{json,md} with all Tier-3 cells as SKIP-deferred.

    D-06: milestone remains closeable at partial bench coverage.
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
    output_dir: Optional[str],
) -> None:
    """Write validation-matrix.json and validation-matrix.md to output_dir.

    Artifact name uses hyphens (distinct from authored validation_matrix_spec.json
    — Pitfall 4 / D-02).
    """
    out_path = Path(output_dir) if output_dir else Path(".")
    out_path.mkdir(parents=True, exist_ok=True)

    artifact: Dict[str, Any] = {  # noqa: UP006
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
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
    """Classify a post-write SHA comparison result per oracle rules (HARN-03 / D-08).

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
        board: Optional[str],
        chip: Optional[str],
        source: Optional[str],
        output_dir: Optional[str],
    ) -> None:
        """Run the per-family validation matrix Tier-3 runner (HARN-01 / D-05).

        Composes write_cycle_eprom / consistency_check_eprom (no re-implementation).
        Emits validation-matrix.{json,md} results artifact (D-02).

        SKIP-deferred path (D-06): when no board/chip/source is available, records
        all Tier-3 cells as SKIP-deferred and exits 0 — milestone stays closeable
        at partial bench coverage.

        3-way verdict contract (mirrors dev write-cycle + consistency-check):
            0 = PASS  1 = FAIL  2 = hw-error

        Non-vacuous oracle (HARN-03 / D-08):
        - Leonardo is the only authoritative PASS board; other boards are advisory.
        - uno328pb write/program cells are hard N/A (brownout 999.2).
        - r1 ≈ 270000 ±25% precondition aborts before any write cycle.
        - retry_count is captured into each cell.
        """
        spec = _load_validation_spec()
        families = _families_for_selection(family, spec)

        # D-06 SKIP-deferred: no port / board / chip / source → record all cells
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

        # r1 precondition: abort before any cycle if r1 is out of band (D-08).
        # The r1 value is read from hardware config via the HardwareManager.
        # In Phase 71 (software scaffold), the hardware path is exercised only
        # in Phase 73 with real hardware; here we gate on the operator config.
        r1_raw: Optional[int] = None
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

        # Compose cycle methods for each family (D-10 reuse-not-reimpl).
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

            # Compose write_cycle_eprom (D-10: no re-implementation of write+readback).
            verdict_int = app.eprom_operator.write_cycle_eprom(
                rep_chip,
                eprom_data,
                source_image_path=source,
                runs=1,
                output_dir=output_dir,
                operation_flags=0,
            )

            # Derive evidence SHA from source image for the cell record.
            evidence_sha: Optional[str]
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
            # Leonardo, "advisory" on all other non-uno328pb boards (HARN-03 / D-08).
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
# `dev test` -- community chip-validation sweep (Phase 112, D-01..D-05)
# ---------------------------------------------------------------------------

# Per-verdict -> exit-code mapping (D-01): OK/NA/SKIPPED are exit-clean;
# `marginal` is an inconclusive result (exit 2); BAD beats marginal via
# EXPLICIT PRECEDENCE (`_overall_exit_code`, D-14), mirroring
# dev_validate_family's own `if verdict_int > overall_verdict` pattern
# (cli_handlers.py:1622-1623).
#
# v1.30 Phase 134 correction 3 (134-CONTEXT.md, D-14): this contract was
# shipped INVERTED against both its own prose and `dev_test`'s docstring
# below (:2119-2121) -- the mechanism used to be a bare call to Python's
# builtin numeric maximum over `_verdict_code(r.verdict) for r in results`.
# Because `_VERDICT_EXIT_CODES` maps `marginal -> 2` and `BAD -> 1`, that
# numeric maximum picked 2 whenever both were present: marginal numerically
# outranked BAD, so a run containing BOTH verdicts exited 2, not 1 -- the
# exact opposite of what this comment and that docstring already claimed.
# `_overall_exit_code` restores the claimed behaviour; it is a bugfix, not
# a contract change.
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


# Exit codes ordered MOST-SEVERE-FIRST (D-14). `_overall_exit_code` walks
# this tuple and returns the first code present among a run's per-step
# codes -- an explicit precedence list, never a numeric `max` (a `max` over
# {1, 2} incorrectly picks 2, which is exactly the bug this replaces).
_EXIT_CODE_PRECEDENCE: tuple[int, ...] = (1, 2, 0)


def _overall_exit_code(results: list[StepResult]) -> int:
    """The run's overall exit code: the most severe code present, per
    `_EXIT_CODE_PRECEDENCE` (D-14) -- BAD (exit 1) outranks marginal
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


def _dev_test_exit_code(results: list[StepResult], *, sdp_oracle_not_run: bool) -> int:
    """`_overall_exit_code`'s D-15 extension (v1.30 Phase 134 plan 134-07,
    LEG-12/LEG-13): an ALLOW-chip run whose SDP oracle (`write-inhibited`)
    did NOT run gets an exit FLOOR of 2, so `firestarter dev test <chip>`
    can no longer return 0 on a run that never exercised the oracle at all
    (the P-04 shape: a not-run oracle filed as PASS by a community
    reporter).

    Composed the SAME WAY `_overall_exit_code` composes BAD-outranks-
    marginal: the floor CONTRIBUTES a candidate code (`2`) into the set fed
    to `_EXIT_CODE_PRECEDENCE`'s most-severe-first selection -- it is NEVER
    the builtin numeric maximum applied between the observed code and the
    floor value. That builtin, given `1` (BAD) and `2` (the floor), returns
    `2` -- so a naive floor would re-launder a BAD run's exit 1 into exit 2,
    recreating exactly the laundering D-14 removed. Composing it as a
    precedence candidate instead keeps BAD's rank intact: a run that is
    both BAD and NOT-RUN still exits 1, not 2.

    Stated cost (D-15, recorded here because this is the one place a
    future reader would look for it): `dev test`'s exit code stops being a
    PURE function of step verdicts -- it gains exactly ONE non-verdict
    term, this `sdp_oracle_not_run` flag.

    Why the not-run oracle stays `SKIPPED` rather than becoming `marginal`
    (D-15's own reasoning, restated at this call site): `_RAN_VERDICTS =
    frozenset({OK, BAD, MARGINAL})` (`chip_test.py`) counts `marginal` as
    *ran* -- recording a non-running oracle as `marginal` would hold
    `N == M` in `count_applicable`'s ratio and defeat LEG-13 outright, the
    exact laundering this milestone exists to stop.

    The floor is ALLOW-only: callers gate `sdp_oracle_not_run` with
    `sdp_oracle_applicable(plan)` at the call site, never here -- a REFUSE
    chip's SDP steps read `NOT-RUN` legitimately (the oracle was never
    applicable to begin with), and flooring that chip's exit code would
    misrepresent a correct refusal as an inconclusive result.
    """
    codes = {_verdict_code(r.verdict) for r in results}
    if sdp_oracle_not_run:
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
) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """Derive (chip_id_expected, chip_id_actual, mismatch_reason) for AutoCapture.

    `chip_id_expected` is read directly off the DB entry (host-side, never
    from firmware). `chip_id_actual`/`chip_id_mismatch_reason` are recovered
    from the id step's `StepResult.reason` text (the ONLY place
    `chip_test._dispatch_id` records the detected id, RPT-02) when a mismatch
    was reported; on a clean/NA/SKIPPED id step there is no actual-id
    disagreement to surface, so both stay `None`.
    """
    full = app.db.get_eprom(chip) or {}
    prog = app.db.convert_to_programmer(full) if full else {}
    chip_id_expected = prog.get("chip-id") or None

    chip_id_actual: Optional[int] = None
    mismatch_reason: Optional[str] = None
    for r in results:
        if r.op == OP_ID and r.reason and "mismatch" in r.reason.lower():
            mismatch_reason = r.reason
            # reason text: "chip-ID mismatch: expected 0x.., detected 0x.."
            try:
                detected_hex = r.reason.rsplit("0x", 1)[-1]
                chip_id_actual = int(detected_hex, 16)
            except (ValueError, IndexError):
                chip_id_actual = None
            break
    return chip_id_expected, chip_id_actual, mismatch_reason


def _is_interactive() -> bool:
    """TTY check factored into its own function so tests can monkeypatch it
    directly (D-02) -- `click.testing.CliRunner.invoke` replaces `sys.stdin`
    with its own stream for the duration of the call, so a test-time
    `patch("sys.stdin.isatty", ...)` applied before `invoke()` does not
    survive; patching `firestarter.cli_handlers._is_interactive` does.
    """
    return sys.stdin.isatty()


def _make_sampler(app: "AppContext", report: DiagnosticReport) -> Any:
    """Build the before/after sampler thunk closing over `hardware_manager`.

    Constructed on EVERY run (Phase 121 D-04: `dev test` always writes, so
    there is no non-destructive mode left to distinguish this from -- the
    `--destructive`-only construction this docstring used to describe was
    superseded when that flag was deleted). Reuses the existing
    `sample_vpp_mv`/`sample_vpe_mv` monitor path (COMMAND_READ_VPP/VPE,
    energize+measure only -- SAFE-02) -- no VPP-set call is made here or
    anywhere in this module. `chip_test.run_plan` calls this as an opaque
    `sampler(phase)` callable and never imports `hardware.py` itself (D-04
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
    """Read this chip's UV-erasable-EPROM axis directly off the DB entry.

    Delegates to `chip_test.is_uv_eprom` on the **full** DB dict from
    `app.db.get_eprom(chip)` -- never `resolve_chip`'s/`convert_to_
    programmer`'s programmer dict, which carries no `electrical-type` and is
    the wrong seam per `is_uv_eprom`'s own docstring (D-01, DEVTEST-03,
    RESEARCH C-4). Named exactly `_is_uv_eprom` because
    `check_devtest_orchestrator.py`'s `_HANDLER_FUNCTION_NAMES` allow-list
    has carried that name since Phase 112 pointing at nothing -- landing the
    handler-side helper under this name is free gate coverage rather than a
    new allow-list entry.

    Returns `False` for a chip absent from the DB; every caller reaches this
    helper only after SAFE-04's absent-chip hard-fail has already run, so
    that case is unreached in practice.
    """
    full = app.db.get_eprom(chip)
    if not full:
        return False
    return is_uv_eprom(full)


def _default_uv_write_confirm(prompt: str) -> bool:
    """Module confirm helper `_resolve_write_scope` defaults to (D-01)."""
    return bool(Confirm.ask(prompt, default=False))


def _resolve_write_scope(
    app: "AppContext",
    chip: str,
    *,
    interactive: bool,
    confirm_fn: Callable[[str], bool] = _default_uv_write_confirm,
) -> str:
    """Decide this run's `derive_plan(..., write_scope=...)` literal (D-01/D-03).

    Quick task 260821-wna (D-C) changes what the scope literal MEANS on a UV
    part: it used to pick "how wide is the write window" (both answers
    resolved to the same 256-byte window, D-01's original inert-prompt
    finding); it now picks the CONSENT CEILING the execution-time resolver
    is permitted to reach -- `full` permits a full-device write when the
    chip reads blank (D-C), `partial` never does, regardless of chip state.
    On a chip that is NOT blank, `full` and `partial` still both resolve to
    a single masked 256-byte slot (D-A/D-B) -- the ceiling only matters when
    the blank-check actually reports blank.

    1. Not UV (`_is_uv_eprom` False) -> the full-write scope, **no prompt at
       all**. A UV write is irrecoverable without a lamp; EEPROM and Flash
       writes are recoverable via erase and SRAM/FRAM writes are essentially
       free, so every other family -- explicitly including this milestone's
       own AT28C family -- runs the full write/verify/erase round-trip
       unprompted (and, since this task, covers the FULL DEVICE rather than
       a small region, D-D).
    2. UV and not interactive -> the partial scope, no prompt. Per D-03 an
       absent TTY is a DECLINED prompt, not absent consent -- a single
       256-byte slot is written (never the whole device, regardless of
       chip state) so a piped or CI run still yields write evidence.
    3. UV and interactive -> ask, defaulting to decline. A yes returns the
       full scope: the whole device MAY be written, but only if the chip
       reads blank (D-C) -- a used chip still receives just one masked
       256-byte slot even under this answer. A no returns the partial
       scope: one 256-byte slot only, unconditionally. Neither answer is
       ever described as non-destructive or read-only.

    `interactive` is taken as a parameter rather than calling
    `_is_interactive()` internally, and `confirm_fn` is an injected
    keyword-only callable -- both so tests can drive every branch without
    patching module internals.
    """
    if not _is_uv_eprom(app, chip):
        return "full"
    if not interactive:
        return "partial"
    chip_upper = chip.upper()
    prompt = (
        f"{chip_upper} is a UV-erasable EPROM -- its write cannot be undone "
        "without a UV eraser. Yes permits the whole device to be written if "
        "it reads blank, and otherwise writes one 256-byte slot; no writes "
        "one 256-byte slot only -- neither answer is read-only or "
        "non-destructive."
    )
    if confirm_fn(prompt):
        return "full"
    return "partial"


# D-09 (v1.30 Phase 134): the write-pass number backing a real sweep
# invariant -- a full ALLOW-shaped run makes 6 write passes over the write
# region (the shipped write/verify/erase steps write twice, plus this
# phase's SDP leg's baseline/inhibited/restore writes).
# `tests/test_dev_test_cmd.py` derives this same number from a live
# `derive_plan` result and asserts it equals this constant; if that test
# ever measures a different number, change THIS constant, never the test.
# (Quick task 260821-spg removed the console notice this constant used to
# feed; the invariant itself, and this constant, stay.)
_ALWAYS_WRITES_PASS_COUNT = 6


# Design history for `dev_test`, moved here from its docstring by quick
# task 260821-spg: this prose used to BE the docstring, which Click
# renders verbatim as `--help` output -- load-bearing project history that
# had no business being printed to every tester who typed `--help`. Moved
# verbatim (as comments), not deleted; `--help` now carries only
# user-facing usage text. Quick task 260821-spg also deleted the two
# `click.echo(...)` calls this function used to make (the always-writes
# notice and the SDP-recovery line) -- both were prose-only; the
# behaviour they described (six write passes, SDP lock applied/released)
# is unchanged and is still computed and still in the JSON/console table.
#
# Takes ZERO options -- CHIP is the only argument (D-05, Phase 121). The
# four flags this command carried through v1.21 (`--destructive`,
# `--output-dir`, `-y`/`--yes`, `--submit`) are gone; each now errors as
# an unknown option.
#
# ALWAYS WRITES (D-04): every run writes to the chip, unconditionally. A
# UV-erasable EPROM is asked first (D-01), and quick task 260821-wna (D-C)
# changes what the two answers DO: yes permits the whole device to be
# written IF the chip reads blank, and otherwise writes one masked
# 256-byte slot (D-A/D-B); no writes one 256-byte slot only,
# unconditionally -- never read-only or non-destructive either way, and
# the two answers no longer resolve to the same window on a used chip. Off
# a TTY the ask is treated as a DECLINED prompt, not absent consent, so a
# single 256-byte slot is written anyway (D-03). Every OTHER family --
# explicitly including this milestone's own AT28C family, an
# electrically-erasable EEPROM -- is written in full with NO prompt at
# all, because that write is recoverable via erase (unlike an
# irrecoverable UV write); as of this task that full write now covers the
# WHOLE DEVICE (minus flash4's two boot blocks) rather than a small region
# (D-D). A large part's full-device pass is therefore several
# device-length transfers at 250000 baud -- minutes, not seconds. The
# report is unconditionally persisted to `<config dir>/reports` (honors
# `FIRESTARTER_CONFIG_DIR`) and is always handed to `submit_report`
# (DEVTEST-05/06; Plan 121-11 owns that function's internals).
#
# REVERSAL (Phase 121 D-01/D-03/D-04/D-05, operator-specified
# 2026-07-29): this supersedes v1.21's non-destructive-by-default premise
# entirely, SAFE-01's CLI-only `--destructive` flag (removed, not merely
# disabled), and SAFE-03's statement that the destructive confirm was
# "the ONLY interactive input left in this handler" (superseded by the
# UV-only ask above). Phase 112 Plan 04's deliberate removal of every
# interactive prompt about tester-supplied identity is PARTIALLY reversed
# in spirit by that same UV ask -- it is a new interactive prompt, just
# not an identity-collection one; shield revision, chip origin and
# pot-adjustment stay un-asked.
#
# Exit code (D-01): 0 if every step is OK/NA/SKIPPED, 2 if any step is
# marginal (and none BAD), 1 if any step is BAD (including a chip-ID
# mismatch) -- computed as max over per-step exit codes.
@dev.command(name="test")
@click.argument("chip", shell_complete=_complete_eprom)
@click.pass_obj
@map_typed_errors
def dev_test(app: "AppContext", chip: str) -> None:
    """Run the community chip-validation sweep for CHIP.

    Writes to the chip every run (no read-only mode); saves a diagnostic
    report under the config dir's reports directory and offers to file it
    as a GitHub issue. Exit code: 0 clear, 2 marginal, 1 bad (including a
    chip-ID mismatch).
    """
    # SAFE-04: hard-fail BEFORE any hardware is energized when the chip name
    # is absent from the DB entirely (case A). Keyed strictly off
    # `get_eprom` emptiness -- NEVER a `resolve_chip` support-status refusal
    # -- so an in-DB-but-unsupported chip (case B, e.g. adapter-required)
    # still runs the full community-validation sweep below.
    if not app.db.get_eprom(chip):
        raise ChipNotFoundError(f"{chip}: not found in database")

    # The chip must be known to be in the DB (SAFE-04 above) before its
    # electrical type can be read, so the UV-scope resolution happens here,
    # after the hard-fail.
    interactive = _is_interactive()
    write_scope = _resolve_write_scope(app, chip, interactive=interactive)
    plan = derive_plan(chip, app.db, write_scope=write_scope)

    # EpromOperator.comm is a transient per-operation connection torn down
    # after every operator call (see 112-02-SUMMARY.md) -- there is no live
    # comm to read programmer_info off of after run_plan returns without
    # opening a new, extraneous connection, which would violate the
    # orchestrator-only contract (SAFE-02). Both identity values instead
    # come off the hardware-revision read's OWN connection (D-01): its
    # find_and_connect triggers the CAP-02 setup ack, which sets
    # comm.firmware_identity before the HARDWARE_REVISION dispatch even
    # runs, so one orchestrator-safe energize/query read (Part A,
    # hardware.py) yields both fields with zero extra connections.
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

    # Always built (D-04): every run writes now, so there is no
    # non-destructive mode left that would have no write step to bracket.
    sampler = _make_sampler(app, report)
    results = run_plan(plan, app.eprom_operator, app.db, sampler=sampler)
    report.results = results
    report.banner = count_applicable(plan, results)
    # LEG-12: the derive-in-engine / assign-in-handler seam. `sdp_hold_state`
    # is computed in chip_test.py (the engine); this line only ASSIGNS it,
    # matching every other derived field above and below (never computed
    # inline here).
    report.sdp_hold_state = sdp_hold_state(plan, results)

    full = app.db.get_eprom(chip)
    if full:
        prog = app.db.convert_to_programmer(full)
        auto_capture.protocol = str(prog.get("algorithm"))
    (
        auto_capture.chip_id_expected,
        auto_capture.chip_id_actual,
        auto_capture.chip_id_mismatch_reason,
    ) = _chip_id_fields(app, chip, results)

    report.db_diff = build_db_diff(chip, app.db, results)

    console = Console()
    report.render(console)

    # The report is ALWAYS persisted, unconditionally, to the reports
    # directory under <config dir> (honors FIRESTARTER_CONFIG_DIR; default
    # ~/.firestarter/reports) -- the removed --output-dir flag was
    # redundant with this env-var seam, never a lost capability.
    out_path = Path(get_config_dir()) / "reports"
    out_path.mkdir(parents=True, exist_ok=True)
    safe_chip = _sanitize_chip_token(chip)

    json_file = out_path / f"dev-test-{safe_chip}.json"
    json_file.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    # Local import, matching this handler's existing `submit as submit_mod`
    # style further down -- `submit` imports `diagnostic_report`, so a
    # module-level import here would tighten an already-layered graph for
    # one formatter.
    from firestarter.submit import _duration_text as submit_duration_text
    from firestarter.submit import _runs_text as submit_runs_text

    md_lines = [
        f"# dev test -- {chip}",
        "",
        "| Step | Verdict | Runs | Took | Reason |",
        "| ---- | ------- | ---- | ---- | ------ |",
    ]
    for r in results:
        # `Took` mirrors submit.build_body's own column (schema 1.5) so the
        # saved artifact and the filed issue body carry the same timings.
        # `Runs` does the same for `run_count` (schema 1.7, quick task
        # 260822-aq6) -- both formatters are imported from `submit` rather
        # than re-implemented, so the two tables can never disagree on how
        # an absent value renders.
        took = submit_duration_text(r.duration_s)
        runs = submit_runs_text(r.run_count)
        md_lines.append(
            f"| {r.op} | {r.verdict} | {runs} | {took} | {r.reason or '-'} |"
        )
    md_lines.append("")
    md_lines.append(report.to_json_block())
    md_file = out_path / f"dev-test-{safe_chip}.md"
    md_file.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    console.print(f"[dim]Report written to {json_file}[/dim]")

    # Unconditional (DEVTEST-05): every run reaches the filing ask, not only
    # an explicit --submit run -- Plan 121-11 owns submit_report's internal
    # dedup-before-ask / ask-anyway-on-failure / comment-on-duplicate logic.
    from firestarter import submit as submit_mod

    submit_mod.submit_report(report, chip, json_file, console=console)

    if not results:
        sys.exit(0)
    # D-15: the exit floor is ALLOW-only -- `sdp_oracle_applicable(plan)`
    # gates it, so a REFUSE chip's legitimate `NOT-RUN` (the oracle was
    # never applicable) is never floored.
    code = _dev_test_exit_code(
        results,
        sdp_oracle_not_run=sdp_oracle_applicable(plan)
        and report.sdp_hold_state.startswith(SDP_HOLD_NOT_RUN),
    )
    sys.exit(code)
