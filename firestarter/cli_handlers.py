"""Click migration target for v1.8 / Phase 41 (CLI-01, CLI-02).

Wave 2 lands the skeleton + 3 read-only commands (list/info/search); Wave 3
(this file's current state) lands the remaining 11 commands: 6 chip-ops
(read/write/verify/blank/erase/id), 2 voltage (vpp/vpe), 2 hardware
(hw/config), 1 firmware (fw with 3-way mutex + version validator), plus the
`dev` group with 4 sub-commands (read/reg/addr/consistency-check).

The entry point in main.py STAYS argparse until Wave 4 (Plan 41-04).
This module is feature-complete reviewable dead code from the user's
perspective until the entry-point swap.
"""

import logging
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import List, Optional  # noqa: UP035

import click
import click.shell_completion

from firestarter import __version__ as version
from firestarter.chip_resolver import resolve_chip
from firestarter.config import ConfigManager
from firestarter.constants import FLAG_CHIP_ENABLE, FLAG_OUTPUT_ENABLE
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter, print_eprom_list_table
from firestarter.eprom_operations import EpromOperator, build_flags
from firestarter.exceptions import ChipNotFoundError
from firestarter.firmware import FIRMWARE_VERSION_RE, FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.logging_utils import SingleLineStatusHandler

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


def _resolve_or_exit(name: str, db: EpromDatabase) -> Optional[dict]:  # noqa: UP006
    """Resolve a chip name, logging the not-found error and returning None on miss.

    Relocated verbatim from main.py:521-533 per Phase 41 D-08. The main.py
    copy still exists (used by the argparse dispatcher) and is deleted in
    Wave 4 / Plan 41-04. Both copies coexist through this wave; the chip-op
    Click handlers use this local copy.

    Phase 42 ERR-01 will replace this with a typed-exception → ClickException
    mapping layer (decorator); the shim is the deliberate seam.
    """
    try:
        return resolve_chip(name, db=db)
    except ChipNotFoundError:
        logger.error(f"EPROM '{name}' not found in database.")
        return None


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
    flags = build_flags(
        blank_check, force, vpe_as_vpp, verbose, skip_erase=not blank_check
    )

    if hasattr(args, "input_enable"):
        flags |= 0 if args.input_enable else FLAG_OUTPUT_ENABLE
    if hasattr(args, "chip_disable"):
        flags |= 0 if args.chip_disable else FLAG_CHIP_ENABLE

    return flags


def _maybe_auto_route_to_pre(args: object) -> None:
    """D-22 / D-25 beta-app magic default: when installed app is a pre-release,
    bare 'fw -i' (no --pre, no --firmware-version) auto-routes to --pre channel.

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
    if not (
        getattr(args, "install", False)
        and not getattr(args, "pre", False)
        and not getattr(args, "firmware_version", None)
        and not getattr(args, "stable", False)
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
    input_enable: Optional[bool] = None,
    chip_disable: Optional[bool] = None,
) -> int:
    """Click-side equivalent of main.py's build_arg_flags helper.

    Click handlers receive their options as explicit kwargs, so there is no
    args-bag introspection needed. This helper applies the same flag-mapping
    rules build_arg_flags uses (post-41-01 truthiness semantics):

        - blank_check / force / vpe_as_vpp / verbose -> build_flags(...)
        - input_enable presence (any value, even False) -> apply OE mask rule
        - chip_disable presence (any value, even False) -> apply CE mask rule

    The OE/CE flags use None to mean "this command does not take this flag"
    so they behave like the main.py `hasattr(args, "input_enable")` gate:
    only `dev reg` and `dev addr` opt into them.
    """
    flags = build_flags(
        blank_check, force, vpe_as_vpp, verbose, skip_erase=not blank_check
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


def _check_install_mutex(
    ctx: click.Context, param: click.Parameter, value: object
) -> object:
    """Per-option callback enforcing the --pre / --firmware-version / --stable mutex.

    Replaces argparse's `add_mutually_exclusive_group()` for the fw command's
    channel_group (D-13.4 TRAP #4). Picked per-option callback over
    `result_callback` (Claude's Discretion): locality — the mutex declaration
    sits next to the options it constrains, matching argparse's per-action
    grouping idiom.

    Raises `click.BadParameter` (exit-2) on violation; this matches argparse's
    `SystemExit(2)` behaviour for mutually-exclusive-group violations. The
    mutex applies in BOTH install AND `--list` contexts (matches argparse's
    `add_mutually_exclusive_group()` scope).
    """
    if not value:
        return value
    siblings = ("pre", "firmware_version", "stable")
    # param.name is non-None for any option (Click sets it from the option spec).
    param_name = param.name or ""
    for other in siblings:
        if other == param_name:
            continue
        other_value = ctx.params.get(other)
        if other_value:
            raise click.BadParameter(
                f"--{param_name.replace('_', '-')} is mutually exclusive with "
                f"--{other.replace('_', '-')}.",
                ctx=ctx,
                param=param,
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
def info(app: AppContext, eprom: str, config: bool, adapter: bool) -> None:
    """EPROM info."""
    eprom_details = app.db.get_eprom(eprom)
    if not eprom_details:
        logger.error(f"EPROM '{eprom}' not found in database.")
        sys.exit(1)

    eprom_data_for_programmer = None
    if eprom_details:
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
def search(app: AppContext, text: str) -> None:
    """Search for EPROMs in the database."""
    search_results = app.db.search_eprom(text, include_unverified=True)
    if search_results:
        print_eprom_list_table(search_results, app.eprom_presenter.spec_builder)
        sys.exit(0)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Chip-op commands (Wave 3 / Plan 41-03 / D-12 step 1)
# Each: resolve chip via _resolve_or_exit → call app.eprom_operator.<op> →
# sys.exit(0 if ok else 1). Per-option help text byte-identical to argparse.
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
def read(
    app: AppContext,
    eprom: str,
    output_file: Optional[str],
    force: bool,
    address: Optional[str],
    size: Optional[str],
) -> None:
    """Reads the content from an EPROM."""
    eprom_data = _resolve_or_exit(eprom, app.db)
    if not eprom_data:
        sys.exit(1)
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
    help="Do not perform blank check before write (and skip erase).",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force, even if the VPP or chip id doesn't match.",
)
@click.option("-a", "--address", default=None, help="Write start address in dec/hex")
@click.option("--vpe-as-vpp", "vpe_as_vpp", is_flag=True, help="Use VPE as VPP voltage")
@click.pass_obj
def write(
    app: AppContext,
    eprom: str,
    input_file: str,
    blank_check: bool,
    force: bool,
    address: Optional[str],
    vpe_as_vpp: bool,
) -> None:
    """Writes a binary file to an EPROM.

    TRAP #3 / D-13.3: ``--no-blank-check`` uses
    ``is_flag=True, flag_value=False, default=True`` so the presence of ``-b``
    flips ``blank_check`` to False (mirrors argparse ``store_false default=True``).
    The inverse ``--blank-check`` polarity lives on the ``erase`` command —
    both polarities coexist verbatim per the rationale lock.
    """
    eprom_data = _resolve_or_exit(eprom, app.db)
    if not eprom_data:
        sys.exit(1)
    ok = app.eprom_operator.write_eprom(
        eprom,
        eprom_data,
        input_file,
        address_str=address,
        operation_flags=_build_op_flags(
            blank_check=blank_check, force=force, vpe_as_vpp=vpe_as_vpp
        ),
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
def verify(
    app: AppContext,
    eprom: str,
    input_file: str,
    address: Optional[str],
    force: bool,
) -> None:
    """Verifies the content of an EPROM."""
    eprom_data = _resolve_or_exit(eprom, app.db)
    if not eprom_data:
        sys.exit(1)
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
def blank(app: AppContext, eprom: str, force: bool) -> None:
    """Checks if an EPROM is blank."""
    eprom_data = _resolve_or_exit(eprom, app.db)
    if not eprom_data:
        sys.exit(1)
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
    """
    eprom_data = _resolve_or_exit(eprom, app.db)
    if not eprom_data:
        sys.exit(1)
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
def id(app: AppContext, eprom: str, force: bool) -> None:
    """Checks an EPROM, if supported."""
    eprom_data = _resolve_or_exit(eprom, app.db)
    if not eprom_data:
        sys.exit(1)
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
                app.db._map_data(ic, ic.get("manufacturer", "Unknown"))
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
def vpp(app: AppContext, timeout: Optional[int]) -> None:
    """VPP voltage."""
    ok = app.hardware_manager.read_vpp_voltage(
        timeout_seconds=timeout, flags=_build_op_flags()
    )
    sys.exit(0 if ok else 1)


@cli.command(name="vpe")
@click.option("-t", "--timeout", type=int, default=None, hidden=True)
@click.pass_obj
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
def config(
    app: AppContext,
    rev: Optional[float],
    r16: Optional[int],
    r14r15: Optional[int],
) -> None:
    """Handles CONFIGURATION values."""
    ok = app.hardware_manager.set_hardware_config(
        rev, r16, r14r15, flags=_build_op_flags()
    )
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# Firmware command (Wave 3 / D-12 step 4)
# TRAPs #4 (3-way mutex via _check_install_mutex) + #5 (_FirmwareVersionType
# custom ParamType). D-14 (UsageError on --json without --list). D-15
# (SimpleNamespace adapter for _maybe_auto_route_to_pre).
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
    callback=_check_install_mutex,
    help="Fetch latest pre-release firmware (mirrors pip install --pre).",
)
@click.option(
    "--firmware-version",
    "firmware_version",
    type=_FirmwareVersionType(),
    default=None,
    metavar="VERSION",
    callback=_check_install_mutex,
    help="Pin exact firmware version (e.g. 3.1.0, 3.1.0b2, 3.1.0rc1).",
)
@click.option(
    "--stable",
    is_flag=True,
    callback=_check_install_mutex,
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
    type=click.Choice(["uno", "uno328pb", "leonardo"]),
    default="uno",
    help="Microcontroller board (optional), defaults to 'uno'.",
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
def fw(
    ctx: click.Context,
    install: bool,
    pre: bool,
    firmware_version: Optional[str],
    stable: bool,
    list_releases: bool,
    board: str,
    avrdude_path: Optional[str],
    avrdude_config_path: Optional[str],
    force: bool,
    json_output: bool,
) -> None:
    """Firmware version.

    Implements TRAP #4 (3-way --pre / --firmware-version / --stable mutex via
    per-option callback _check_install_mutex) and TRAP #5 (firmware-version
    validator via custom Click ParamType _FirmwareVersionType). D-14 narrow
    upgrade: the post-parse `--json requires --list` check uses
    `raise click.UsageError(...)` (exit-2 + "Usage:" formatting preserved
    from the argparse `fw_parser.error()` form).
    """
    app: AppContext = ctx.obj

    # D-14 narrow UsageError upgrade (was: fw_parser.error in main.py:798).
    if json_output and not list_releases:
        raise click.UsageError("--json requires --list")

    if list_releases:
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

    ok = app.firmware_manager.manage_firmware_update(
        install_flag=install,
        avrdude_path_override=avrdude_path,
        avrdude_config_override=avrdude_config_path,
        port_override=port_override,
        board_override=board,
        flags=_build_op_flags(force=force),
        channel=channel,
        pinned_version=firmware_version,
    )
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# dev group + 4 sub-commands (Wave 3 / D-12 step 5)
# ---------------------------------------------------------------------------


@cli.group(name="dev")
def dev() -> None:
    """Debug command for development purposes.

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
def dev_read(
    app: AppContext,
    eprom: str,
    address: Optional[str],
    size: Optional[str],
    force: bool,
) -> None:
    """Reads the content from an EPROM and prints data to console."""
    eprom_data = _resolve_or_exit(eprom, app.db)
    if not eprom_data:
        sys.exit(1)
    ok = app.eprom_operator.dev_read_eprom(
        eprom,
        eprom_data,
        address_str=address,
        size_str=size or "256",
        operation_flags=_build_op_flags(force=force),
    )
    sys.exit(0 if ok else 1)


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
def dev_addr(
    app: AppContext,
    eprom: str,
    address: str,
    input_enable: bool,
    chip_disable: bool,
) -> None:
    """Direct access to address lines and control register."""
    eprom_data = _resolve_or_exit(eprom, app.db)
    if not eprom_data:
        sys.exit(1)
    ok = app.eprom_operator.dev_set_address_mode(
        eprom,
        eprom_data,
        address,
        flags=_build_op_flags(input_enable=input_enable, chip_disable=chip_disable),
    )
    sys.exit(0 if ok else 1)


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
    help="Output dir for per-run binaries (default consistency-check-<chip>-<board>-<TS>/).",  # noqa: E501
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
    "-q", "--quiet", is_flag=True, help="Suppress per-run tqdm progress bars (D-11)."
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force read, even if the chip id doesn't match (e.g. Shield-3 missing-chip case).",  # noqa: E501
)
@click.pass_obj
def dev_consistency_check(
    app: AppContext,
    eprom: str,
    runs: int,
    output_dir: Optional[str],
    keep_files: bool,
    max_diffs: int,
    quiet: bool,
    force: bool,
) -> None:
    """Read EPROM N consecutive times and report SHA-256 divergence.

    D-12 step 5 / 3-way verdict contract:
        verdict_int = consistency_check_eprom(...)  # 0=PASS, 1=FAIL, 2=hw-error
        sys.exit(verdict_int)  # NOT bool-to-int wrap

    The bool-to-int wrap would collapse the 2=hardware-error case to 1=FAIL,
    breaking the v1.6 RCA diagnostic.
    """
    eprom_data = _resolve_or_exit(eprom, app.db)
    if not eprom_data:
        sys.exit(1)
    verdict_int = app.eprom_operator.consistency_check_eprom(
        eprom,
        eprom_data,
        runs=runs,
        output_dir=output_dir,
        keep_files=keep_files,
        max_diffs=max_diffs,
        quiet=quiet,
        operation_flags=_build_op_flags(force=force),
    )
    sys.exit(verdict_int)
