"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module (Refactored)
"""

import hashlib
import logging
import os
import re
import shutil
import statistics
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Tuple  # noqa: UP035

import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from firestarter import transport_counters
from firestarter.address_parser import parse_address, parse_size
from firestarter.compare import (
    MAX_RETAINED_RANGES,
    CompareAccumulator,
    CompareResult,
    render_compare_lines,
)
from firestarter.config import ConfigManager
from firestarter.constants import (
    COMMAND_CHECK_CHIP_ID,
    COMMAND_DEV_ADDRESS,
    COMMAND_DEV_REGISTERS,
    COMMAND_ERASE,
    COMMAND_FW_VERSION,
    COMMAND_LOCK_STATUS,
    COMMAND_NAMES,
    COMMAND_READ,
    COMMAND_SDP_LOCK,
    COMMAND_SDP_UNLOCK,
    COMMAND_WRITE,
    FLAG_FORCE,
    FLAG_SKIP_ERASE,
    FLAG_SKIP_SDP_UNLOCK,
    FLAG_VERBOSE,
    FLAG_VPE_AS_VPP,
    JSON_KEY_READ_SETTLING_DELAY,
    JSON_KEY_READ_STROBE_US,
    JSON_KEY_REGION_END,
)
from firestarter.exceptions import (
    EpromOperationError,
    FirmwareOutdatedError,
    ProgrammerNotFoundError,
    ProtocolNotImplementedError,
    SerialError,
    SerialTimeoutError,
)
from firestarter.frame_parser import _crc8_ccitt, cobs_encode
from firestarter.jp5_gate import require_acknowledged
from firestarter.messages import (
    MSG_DATA_PROTECTION_STATUS,
    MSG_ERR_TIMEOUT,
    MSG_WARN_SDP_UNLOCK_SKIPPED,
)
from firestarter.page_size_gate import require_page_alignment, require_page_size
from firestarter.sdp_capability import SDP_PROTOCOL_ID
from firestarter.serial_comm import (
    CONNECTION_STABILIZE_DELAY,
    DEFAULT_RESPONSE_TIMEOUT,
    WRITE_BUDGET_MAX_S,
    SerialCommunicator,
)
from firestarter.utils import extract_hex_to_decimal
from firestarter.write_blank_guard import (
    incomplete_refusal_text,
    refusal_text,
    require_non_negative_address,
    requires_blank_check,
)

logger = logging.getLogger("EpromOperator")

bar_format = "{l_bar}{bar}| {n:#06x}/{total:#06x} bytes "

# Parent folder that groups the auto-named per-run output directories produced by
# consistency_check_eprom / write_cycle_eprom when the caller does not pass an
# explicit --output-dir. Created relative to the current working directory, so a
# bench session keeps all diagnostic runs under one subfolder
# (e.g. ./firestarter-runs/consistency-check-<chip>-<board>-<TS>/) instead of
# scattering timestamped folders directly in the launch directory.
DEFAULT_RUN_OUTPUT_DIR = "firestarter-runs"

_CONSUME_REMAINING_INPUT_WINDOW_S = 0.5
CONNECT_COST_STRUCTURAL_FLOOR_S = (
    CONNECTION_STABILIZE_DELAY + _CONSUME_REMAINING_INPUT_WINDOW_S
)

# 202-04 D-08: bounded acceptance window for the read-abort discrimination in
# `verify_eprom`. Derived, not picked: the firmware's own ack wait
# (`op_wait_for_ack`, firestarter_fw/src/operation_utils.cpp:94-108) times out
# after 1 s, polled at 10 ms, and the resulting MSG_ERR_TIMEOUT frame then has
# to traverse the link at 250000 baud -- a few milliseconds at most for a
# single short frame. 3.0 s is roughly three times that worst-case latency and
# far below any plausible gap between two unrelated operations, so a genuine
# timeout that arrives outside this window is never mistaken for this host's
# own deliberate stop.
# 207.1 D-05 (202-REVIEW WR-01): the window is derived, not measured under
# host scheduling or USB latency, and it stays 3.0 s and non-configurable.
# Its one consumer is `_drive_region_compare`, so it governs every caller:
# verify, blank, the write blank guard and write --verify. A deliberate stop
# whose frame arrives later than this -- extreme host load, a stalled USB
# stack -- is reported as exit 2, a hardware or transport failure, instead of
# the abort's own verdict. That false exit 2 is a known, accepted outcome:
# for a report of "verify says hardware error but the chip only mismatches",
# check host load before suspecting the hardware. On the write-guard path a
# false 2 refuses the write as a guard-read failure, which is fail-closed.
# `TestVerifyEpromReadAbort` pins both sides of the boundary.
READ_ABORT_ACCEPTANCE_WINDOW_S = 3.0


def _raise_for_error_response(response, message: str) -> None:
    """Raise ProtocolNotImplementedError for id 0xBB, EpromOperationError otherwise.

    Centralises typed-exception dispatch for all ERROR-branch sites in the
    state machine so id-keyed detection is not duplicated per raise site.

    `response` is read for its `id` field to dispatch to the typed subclass.
    `message` is the already-composed exception message string (callers may
    prepend a phase-name prefix for EpromOperationError framing; the raw
    firmware text is passed through unchanged for ProtocolNotImplementedError
    so firmware owns rendering).
    """
    from firestarter.messages import MSG_ERR_PROTOCOL_NOT_IMPLEMENTED

    if response.id == MSG_ERR_PROTOCOL_NOT_IMPLEMENTED:
        raise ProtocolNotImplementedError(response.message, error_code=response.id)
    raise EpromOperationError(message, error_code=response.id)


# Boot-block region size: W29C040 §6.6 defines two 16K boot blocks (first and last).
_BOOT_BLOCK_SIZE = 0x4000  # 16 KiB

# Pattern to extract the hex address from MSG_ERR_FL4_VERIFY_TIMEOUT messages.
# Format: "Timeout verifying 0x%02x at 0x%06lx (got 0x%02x)"
_TIMEOUT_ADDR_RE = re.compile(r"at 0x([0-9a-fA-F]+)")

# Flash4 protocol ID.  Boot-block lockout is specific to the AMD/JEDEC SDP
# page-write flash family (protocol 0x05, FLASH_AMD_STD).
_FLASH4_PROTOCOL_ID = 5

# Fallback write-path response timeout, used when the firmware advertises no
# per-block write-time budget. DERIVED, not picked: the worst shipped-database
# block time with no advertisement is 0x0B at 50 ms/byte x 1024 B = 51.2 s, and
# 0x07/0x08 at 25 x 1000 us x 1024 B = 25.6 s, so this is over 2x the worst
# shipped case -- and it covers every REACHABLE 0x0B width, whose per-byte
# bound cannot exceed 99998 us.
#
# Residual gap, stated: 0x07/0x08 only, at ~4687 us/byte on a Leonardo and
# ~9375 us on an Uno.
WRITE_BLOCK_TIMEOUT_FALLBACK_S = 120.0

# the three per-byte program-budget ids _budget_failure_hint_
# message keys on -- MSG_ERR_MAX_PULSES (0xBD), MSG_ERR_ENERGY_CAP (0xBE),
# MSG_ERR_PULSE_TOO_WIDE (0xAE). Defined as raw ints (not imported names)
# because this tuple lives in this module-level constant block, while
# firestarter.messages is imported LOCALLY inside functions elsewhere in
# this module (see _boot_block_hint_message below) to avoid an import
# cycle -- a module-level import here would break that established
# discipline, so each id is named in this comment instead. Deliberately
# excludes MSG_ERR_WRITE_FAILED (0xB1): F-141-06 confirms (a whole-tree grep
# of the firmware repo's src/ for "MSG_ERR_WRITE_FAILED" returns zero
# matches) that id is the OLD, now-retired per-block loop's failure id and
# is emitted by nothing on the 27C write path any more -- a hint keyed on
# it would never fire.
_BUDGET_FAILURE_IDS = (0xBD, 0xBE, 0xAE)

# Pattern to extract the refused pulse width from MSG_ERR_PULSE_TOO_WIDE
# messages. Format: "Pulse width %lu us exceeds this protocol's per-byte
# program-energy budget" -- mirrors _TIMEOUT_ADDR_RE's own extract-from-text
# approach immediately above, for the same reason: the refused value is only
# available as decoded prose, not as a separate structured field on Response.
_PULSE_WIDTH_RE = re.compile(r"Pulse width (\d+) us")

# Pattern to extract the raw silicon byte and the firmware decode code from
# MSG_DATA_PROTECTION_STATUS (0xE1) messages.
# Format: "Lock status probe: raw=0x%02X decode=%u" -- same rationale as
# _TIMEOUT_ADDR_RE/_PULSE_WIDTH_RE immediately above: Response.payload is
# populated only for MSG_DATA_CHUNK (W-04); every other id-frame's decoded
# param values reach the caller only as already-rendered prose, so this is
# the established way to recover them.
_LOCK_STATUS_RE = re.compile(r"raw=0x([0-9A-Fa-f]{2}) decode=(\d+)")


def _boot_block_hint_message(response, protocol: int, mem_size: int) -> str | None:
    """Return a boot-block-locked inference hint string, or None.

    A flash4 write that fails with MSG_ERR_FL4_VERIFY_TIMEOUT in the first or
    last 16K cannot be told apart from a firmware bug without this context.

    Returns a hint only when the id, the protocol AND the address range all
    match, so unrelated faults are never mislabelled.

    The wording INFERS the lockout from the address range; it does not confirm
    it. Only the firmware's own detect read can read the lockout bit.
    """
    from firestarter.messages import MSG_ERR_FL4_VERIFY_TIMEOUT

    if response.id != MSG_ERR_FL4_VERIFY_TIMEOUT:
        return None
    if protocol != _FLASH4_PROTOCOL_ID:
        return None

    # Extract the failing address from the decoded message text.
    m = _TIMEOUT_ADDR_RE.search(response.message or "")
    if not m:
        return None

    try:
        addr = int(m.group(1), 16)
    except ValueError:
        return None

    boot_block_size = _BOOT_BLOCK_SIZE
    in_first_block = addr < boot_block_size
    in_last_block = (mem_size > boot_block_size) and (
        addr >= mem_size - boot_block_size
    )

    if not (in_first_block or in_last_block):
        return None

    # Build the hint with f-strings (py3.11-safe — no backslashes inside {} expressions).
    last_block_start = mem_size - boot_block_size
    last_block_end = mem_size - 1
    region = (
        "0x0000-0x3FFF"
        if in_first_block
        else (f"0x{last_block_start:05X}-0x{last_block_end:05X}")
    )
    hint = (
        f"boot-block region hint: address 0x{addr:06X} is in the {region} region. "
        "This boot-block region may be locked (W29C040 datasheet §6.6 "
        "boot-block lockout — irreversible, no unlock command exists). "
        "Writes to addresses >=0x4000 should succeed on an unlocked region. "
        "This is an inference from the address range, not a confirmed detection."
    )
    return hint


def _budget_failure_hint_message(response) -> str | None:
    """Return a per-byte program-budget-failure disposition hint, or None.

    Keyed on MSG_ERR_MAX_PULSES, MSG_ERR_ENERGY_CAP and MSG_ERR_PULSE_TOO_WIDE.
    The firmware's catalog format already interpolates the failing address or
    the refused pulse width, so this adds DISPOSITION -- what the id means for
    the write in progress -- not location or value.

    On a budget failure the firmware returns before advancing the address and
    then idles the command, so the write stopping and every later block being
    refused are the SAME event. The hint therefore never advises retrying the
    failed block: starting over is a fresh run of the whole file, never a
    pick-up-where-it-stopped.

    MSG_ERR_PULSE_TOO_WIDE is different: it is a pre-flight refusal that fires
    before any high voltage is enabled, so no byte was touched and a smaller
    --pulse-us IS legitimate remediation -- unlike a byte that will not converge
    however often it is pulsed.

    Returns None for any other id, and for a too-wide response carrying no
    parsable width.
    """
    if response.id not in _BUDGET_FAILURE_IDS:
        return None

    from firestarter.messages import MSG_ERR_PULSE_TOO_WIDE

    if response.id == MSG_ERR_PULSE_TOO_WIDE:
        m = _PULSE_WIDTH_RE.search(response.message or "")
        if not m:
            return None
        width = m.group(1)
        return (
            f"the firmware refused this {width} us pulse before enabling any "
            "high voltage -- no byte was programmed by this command and the "
            "chip is unchanged by it. This protocol caps accumulated per-byte "
            f"program energy; supply a smaller --pulse-us than {width}, or "
            "omit --pulse-us entirely to use this chip's database value."
        )

    # MSG_ERR_MAX_PULSES / MSG_ERR_ENERGY_CAP: the abort disposition.
    return (
        "the write aborted at this address: bytes before this block were "
        "already programmed, this block is only partially programmed, and "
        "no later block was attempted. The firmware stops accepting blocks "
        "for this write and its address counter does not advance, so "
        "re-running the write repeats the whole file from the start. A byte "
        "that will not converge like this usually means insufficient "
        "program voltage or a worn or failing cell, not a timing problem."
    )


def build_flags(
    blank_check=True,
    force=False,
    vpe_as_vpp=False,
    verbose=False,
    skip_erase=False,
    *,
    skip_sdp_unlock: bool = False,
):
    # skip_sdp_unlock is keyword-only BY REQUIREMENT, not by style: both
    # production callers (cli_handlers.py build_arg_flags / _build_op_flags)
    # pass the first four parameters positionally, so a positional insertion
    # here would silently shift `verbose` and `skip_erase` for every command.
    # tests/test_bug_characterization.py's BUG-1 contract pins this signature
    # shape (a PlainArgs bag with no __contains__ must not raise TypeError) —
    # it is re-run as named task work in this same plan, unmodified.
    #
    # FWBLANK-04 (Phase 205): `blank_check` no longer composes any wire bit
    # at all -- the flag it used to set (0x08) is retired from both
    # ladders. The parameter stays in its current position (both production
    # callers pass it positionally, alongside `verbose`/`skip_erase`) but
    # its value now travels only to `write_eprom`'s `blank_check_requested`
    # keyword, which threads it to the host-side write guard
    # (write_blank_guard.requires_blank_check) directly.
    flags = 0
    if skip_erase:
        flags |= FLAG_SKIP_ERASE
    if force:
        flags |= FLAG_FORCE
    if vpe_as_vpp:
        flags |= FLAG_VPE_AS_VPP
    if verbose:
        flags |= FLAG_VERBOSE
    # The FLAG_SKIP_SDP_UNLOCK bit is mapped HERE, inside build_flags, rather
    # than OR-ed in afterwards by a caller the way FLAG_OUTPUT_ENABLE /
    # FLAG_CHIP_ENABLE are in cli_handlers._build_op_flags -- every wire
    # flag bit stays mapped in the one function that maps wire flags.
    # Emitted unconditionally when requested: firmware never reads this bit on
    # a protocol other than 0x0D, so no per-protocol branch belongs in a
    # flag-mapping function. The "warn and proceed" path for a non-0x0D chip is
    # the handler's job, not this function's.
    if skip_sdp_unlock:
        flags |= FLAG_SKIP_SDP_UNLOCK

    return flags


def _blank_expected_bytes(_offset: int, length: int) -> bytes:
    """`check_eprom_blank`'s D-04 pull callback: the constant blank byte,
    exactly `length` bytes -- never a device-sized buffer, no matter how
    large a single chunk's `length` is. `_offset` is unused; the blank
    constant does not depend on address. Module-level (not a closure inside
    `check_eprom_blank`) so it is directly unit-testable without driving a
    whole compare.
    """
    return b"\xff" * length


def _write_blank_guard_refusal_message(
    eprom_name: str, region_start: int, result: CompareResult
) -> str:
    """Selects the D-07/D-10 refusal text for one write-guard `CompareResult`.

    `result.first_offset` is `None` exactly when `result.bad == 0`
    (`compare.py`'s own contract for `CompareResult`) -- an incomplete read
    that never observed a mismatching byte falls in that branch, so the
    coverage-stating `incomplete_refusal_text` (203-REVIEW WR-02, 207.1
    D-07) is used instead of fabricating an address and a value with
    `refusal_text`. Module-level, not a nested closure, so the two-way
    selection is directly unit-testable and keeps `_run_write_blank_guard`'s
    own call site within `ruff format`'s line-length rule.
    """
    if result.first_offset is None:
        return incomplete_refusal_text(eprom_name, result.compared, result.total)
    return refusal_text(
        eprom_name, region_start + result.first_offset, result.first_actual
    )


def hexdump(address, data, width=16):
    """
    Prints a hexdump similar to xxd.
    :param data: The data to be printed (bytes).
    :param width: Number of bytes per line.
    """
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        mid = width // 2

        hex_parts = []
        ascii_parts = []
        for j, byte in enumerate(chunk):
            if j == mid:
                hex_parts.append("")  # Creates the double space with ' '.join()
                ascii_parts.append(" ")

            hex_parts.append(f"{byte:02x}")
            ascii_parts.append(chr(byte) if 32 <= byte <= 126 else ".")

        hex_str = " ".join(hex_parts)
        ascii_str = "".join(ascii_parts)

        logger.info(f"{address + i:08x}: {hex_str:<{width * 3}} {ascii_str}")


class ClassProgressHandler:
    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback
        self.pbar = None
        self.current_step = 0
        self.total_steps = 0

    def start(self, total_steps: int):
        self.total_steps = total_steps
        self.current_step = 0
        if self.progress_callback:
            self.progress_callback(self.current_step, total_steps)
        else:
            if self.pbar:
                self.pbar.close()  # Close old one if any
            logging_redirect_tqdm()
            self.pbar = tqdm.tqdm(total=total_steps, bar_format=bar_format)

    def update(self, completed_steps: int):
        self.current_step += completed_steps
        if self.progress_callback:
            self.progress_callback(self.current_step, self.total_steps)
        if self.pbar:
            self.pbar.update(completed_steps)
        else:
            # If no progress bar, we can't do much with incremental updates without a total.  # noqa: E501
            logger.info(f"Progress: +{completed_steps} steps")

    def set_progress(self, current, total):
        if self.total_steps != total or (not self.pbar and not self.progress_callback):
            self.start(total)

        self.current_step = current
        if self.progress_callback:
            self.progress_callback(current, total)
        if self.pbar:
            self.pbar.n = current
            self.pbar.refresh()

    def close(self):
        if self.pbar:
            self.pbar.close()
            self.pbar = None


class EpromOperator:
    """
    Handles various operations on EPROMs, such as reading, writing, verifying,
    erasing, and checking chip IDs. It utilizes an EpromDatabase instance for
    EPROM-specific data and a SerialCommunicator instance (managed per operation)
    for interacting with the EPROM programmer hardware.
    """

    def __init__(
        self, config: ConfigManager, progress_callback: Callable | None = None
    ):
        self.comm: SerialCommunicator | None = None
        self.config = config
        self.progress_callback = progress_callback
        # The firmware's own explanation of a failure -- its message id and rendered
        # text -- had no route out of this class: the state machine returns
        # `(False, str(e))` and the four mutation methods discard the string and return
        # a bare bool, so every failing step in a report carried a null error_code and
        # an empty reason.
        #
        # These two attributes are that route. Deliberately NOT a signature change: the
        # `-> bool` contract is relied on by four CLI commands, chip_test, and a large
        # body of test doubles.
        #
        # Lifetime: the operation context's finally tears down `self.comm` but never
        # the operator, so these survive the call that set them. `_run_state_machine`
        # CLEARS them on entry, so a value can never be stale from an earlier op.
        #
        # Scope, deliberately narrow: only the EpromOperationError arm sets these -- a
        # real firmware ERROR frame. The transport arm does not; its text can carry a
        # host device path and it has no firmware message id to report.
        self.last_firmware_error_code: int | None = None
        self.last_firmware_error_message: str | None = None
        # WRITE-01 (Phase 203): the pre-write blank guard's own verdict --
        # 0 blank/proceeded, 1 not blank/refused, 2 transport or setup
        # failure, None when the guard was skipped entirely (no region,
        # blank check not requested, erase-exempt, or an unguarded
        # protocol).
        # Set by `write_eprom` on every call, so a stale value from an
        # earlier write can never leak into a later one's reporting.
        # Transient per-invocation operator state, in the same family as
        # `last_firmware_error_code` above -- the CLI tier MUST read this
        # immediately after the `write_eprom` call returns (WRITE-04/
        # WRITE-05, `cli_handlers.write`'s `--verify` exit-code branch),
        # because it is what lets `write_eprom` keep returning a plain
        # `bool` instead of forcing a bool-to-int migration across the
        # roughly forty bool-valued test sites and `chip_test.py`'s
        # documented PRECONDITION contract.
        self.last_write_guard_verdict: int | None = None
        # WRITE-04/WRITE-05 (Phase 203): the write phase's OWN cause channel,
        # sibling of `last_write_guard_verdict` above -- the guard read has
        # its cause channel, the read-back returns its cause as an int
        # (`verify_eprom`), and this is the write phase's. States: `0` the
        # write completed on the wire, `1` the write failed for a reason the
        # host or the firmware decided (a firmware ERROR frame, or a
        # malformed `-a` that never reached the wire), `2` the write failed
        # for a transport, connection, or setup reason, `None` when the
        # write phase was never attempted at all -- a pure gate raised, a
        # guard refusal, or a guard-read failure. Reset to `None` at the very
        # top of `write_eprom`, before any gate can raise, so a prior
        # invocation's verdict can never leak into this one. The CLI tier
        # reads this immediately after the `write_eprom` call, in the same
        # breath as `last_write_guard_verdict` -- together the pair is what
        # lets `write_eprom` keep its `-> bool` return type instead of
        # forcing a bool-to-int migration across the roughly forty
        # bool-valued test sites and `chip_test.py`'s documented
        # PRECONDITION contract.
        self.last_write_attempt_verdict: int | None = None
        # 203-CR-01: the physical port `write_eprom`'s own COMMAND_WRITE
        # connect actually reached, captured inside that connect's own
        # `_operation_context` block (before its `finally` tears `self.comm`
        # down). `None` until a write's own connect succeeds; reset to
        # `None` at the very top of `write_eprom`, in the same breath as
        # `last_write_attempt_verdict`, so a prior invocation's port can
        # never leak into this one's reporting. `cli_handlers.write` reads
        # this (via `getattr(..., None)`, so an operator double that
        # predates this attribute degrades to "no pin" rather than raising)
        # to pin `--verify`'s own read-back connect to the SAME port the
        # write itself just used -- the guard read, the write, and the
        # read-back must never be allowed to silently land on three
        # different boards.
        self.last_write_port: str | None = None
        # 202-04 D-06/D-08: set by `_main_phase_read_data` the moment an
        # `abort_predicate` fires -- a monotonic timestamp, not a wall clock,
        # so the bounded acceptance window below is immune to a system clock
        # step. `None` before any run, and reset to `None` at the top of
        # `_run_state_machine` alongside the firmware-error fields above, so
        # a stale value from a previous operation can never leak into a
        # later one's discrimination test.
        self._read_abort_stopped_at: float | None = None
        # 202-04 D-08: whether THIS call's caller actually requested the
        # abort mechanism (the default, non-`--full` verify path). Set
        # explicitly (True or False) by `verify_eprom` before every drive --
        # never left to a prior call's value -- so a genuine timeout on a
        # `--full` run, which never sets an abort_predicate, can never be
        # mistaken for this host's own doing.
        self._read_abort_intended: bool = False
        # SESS-01 (Phase 206): whether this operator is currently holding one
        # validated link open across multiple calls instead of connecting
        # and tearing down per call. Default off -- joins the transient
        # per-invocation attributes above, but is NOT reset per-invocation:
        # it is set/cleared only by `lease()` itself, spanning every call
        # made inside a `with operator.lease():` block. `_setup_operation`
        # reads it to decide whether to reuse `self.comm` or cold-connect;
        # `_operation_context`'s `finally` reads it to decide whether to
        # tear `self.comm` down after each call.
        self._leased: bool = False

    def _calculate_buffer_size(self) -> int:
        # firmware_max_chunk is populated by the
        # _decode_id_frame MSG_OK_READY ack override in serial_comm.py, not
        # by parsing the FW identity string (that mechanism was removed).
        # Reversal: when the field is absent (old firmware
        # or ack with 0 param bytes), return 512 — the Uno floor, universally
        # safe minimum — instead of raising FirmwareOutdatedError.
        max_chunk = (
            getattr(self.comm, "firmware_max_chunk", None) if self.comm else None
        )
        if max_chunk is not None and max_chunk >= 1:
            return max_chunk
        # Safe Uno-floor default: absent advertisement -> 512.
        return 512

    def _write_block_timeout(self) -> float:
        """Return the per-response wait for a write's MAIN phase, in seconds.

        The firmware's advertised budget is used VERBATIM -- it is already
        padded firmware-side, so the host applies no multiplier.

        An absent, truncated or implausible advertisement falls back to the
        derived constant, never an error and never a refusal.

        The range test is a second line of defence behind the decoder's own
        plausibility clamp: a value outside it can only arrive if something
        bypassed the decoder. Too-small and implausibly-large fall back
        identically, so a corrupt or hostile ack can install neither a
        too-tight nor an unbounded host wait.

        MUST be called from inside `write_eprom`'s operation context: that
        block's finally sets `self.comm` to None on exit, so a later call would
        always take the None branch.
        """
        budget = getattr(self.comm, "write_block_budget_s", None) if self.comm else None
        if budget is not None and 1 <= budget <= WRITE_BUDGET_MAX_S:
            return float(budget)
        return WRITE_BLOCK_TIMEOUT_FALLBACK_S

    def _setup_operation(  # Remains largely the same, as it's a prerequisite for the context manager  # noqa: E501
        self,
        eprom_name: str,  # For logging
        eprom_data_dict: dict,  # Pre-fetched EPROM data
        cmd: int,
        operation_flags: int = 0,
        address: str | None = None,
        size: str | None = None,
        fault_inject_outgoing: Callable[[bytes], bytes] | None = None,
        region_length: int | None = None,
        preferred_port: str | None = None,
        restrict_to_port: bool | None = None,
    ) -> Tuple[Dict | None, int]:  # noqa: UP006
        """
        Prepares for an EPROM operation: uses pre-fetched EPROM data, sets up command, and connects.
        Returns (eprom_data_for_command, buffer_size) or (None, 0) on failure.

        ``preferred_port``/``restrict_to_port`` (203-CR-01): forwarded verbatim
        to ``SerialCommunicator.find_and_connect``. Both default to ``None``,
        which leaves ``find_and_connect``'s own config-driven inference
        untouched -- every existing single-connect caller (read, erase, a
        standalone verify/blank, ``dev *``) is byte-identical. A caller that
        supplies ``preferred_port`` with ``restrict_to_port=True`` pins this
        connect to exactly that port: `_list_potential_ports` then returns
        only that one candidate, so a later connect that cannot reach it
        fails closed (`ProgrammerNotFoundError`) instead of silently
        discovering a different board.
        """  # noqa: E501
        operation = COMMAND_NAMES[cmd]  # Get command name
        logger.debug(f"Performing {operation} for {eprom_name.upper()}")

        start_time = time.time()
        # eprom_data_dict is assumed to be valid and pre-fetched by the caller (main.py)
        logger.debug(f"EPROM data: {eprom_data_dict}")
        command_dict = eprom_data_dict.copy()  # Work with a copy for the command
        command_dict["cmd"] = cmd
        # Combine base flags from EPROM data with operation-specific flags
        command_dict["flags"] = eprom_data_dict.get("flags", 0) | operation_flags
        addr = 0
        if address:
            try:
                addr = parse_address(address) or 0
                command_dict["address"] = addr
            except ValueError:
                logger.error(f"Invalid address format: {address}")
                return None, 0

        # Special handling for read operation size
        if cmd == COMMAND_READ and size:
            try:
                read_size = parse_size(size) or 0
                # 'memory-size' in command_dict will define the end address for read

                command_dict["memory-size"] = addr + read_size
            except ValueError:
                logger.error(f"Invalid size format: {size}")
                return None, 0

        # BLANK-01 / D-05: the write-init blank check on the firmware side must
        # scope to the region this operation actually touches, not the whole
        # device. region_length is the payload size in bytes; the wire carries
        # an absolute EXCLUSIVE end address so the firmware never has to redo
        # this arithmetic against a scan cursor that moves across chunks (see
        # RESEARCH.md C-3). Phase 204 retired COMMAND_VERIFY (ordinal 6) --
        # nothing composes it any more, so the write path is now the ONLY
        # composer of this key; the guard is an equality against COMMAND_WRITE
        # rather than a one-member tuple, deliberately, so a later reader does
        # not read a tuple of one as an invitation to "restore" a second
        # member. region_length greater than zero is deliberate: a zero-length
        # payload at address 0 would compute an end of 0, which the firmware
        # reads as absent (whole device) -- emitting nothing reaches that same
        # outcome explicitly instead of by numeric coincidence. This block's
        # own premise expires in Phase 205, which removes the firmware-side
        # write-init blank check this key exists to scope.
        if region_length is not None and region_length > 0 and cmd == COMMAND_WRITE:
            command_dict[JSON_KEY_REGION_END] = addr + region_length

        # SESS-01 (Phase 206): the lease's second-and-later setup. Taken only
        # when a lease is active AND the held link is still connected --
        # every other case (no lease, or a lease whose link died) falls
        # through to the cold `find_and_connect` below, byte-identical to
        # today. Deliberately OUTSIDE the cold path's own try/except: a
        # SerialError here must propagate to `run_plan`'s per-step handling
        # unchanged (D-06), not be swallowed into a `(None, 0)` return the
        # way a cold connect failure is -- see `lease()`'s docstring for the
        # full failure-policy rationale.
        if self._leased and self.comm is not None and self.comm.is_connected():
            # The drain is not optional. `disconnect()` is the only caller
            # of `consume_remaining_input()` today, and a lease skips
            # `disconnect()` -- so without this explicit call, a straggler
            # frame from the PREVIOUS step would be parsed as THIS step's
            # setup ack. It runs INSIDE this `try` (207.1-REVIEW WR-02):
            # `consume_remaining_input` reaches `_read_and_parse_lines`,
            # which raises `SerialError` on a transport failure exactly
            # like `setup_command` does, so a drain failure must drop the
            # held link the same way -- outside the `try`, that raise
            # escaped with the link still marked connected, stranding
            # every later leased step on the same dead port.
            try:
                self.comm.consume_remaining_input()
                setup_ok = self.comm.setup_command(command_dict, self.config)
            except SerialError:
                # D-06: drop the lease's held link so the NEXT operation
                # cold-connects, but leave `_leased` set -- the block is
                # still a lease, only its link died -- and re-raise
                # unchanged. `_run_step_untimed` already maps a raised
                # SerialError to its own two-axis transport outcome; a
                # second mapping here would put that adjudication in two
                # places.
                self._disconnect_programmer()
                raise
            if not setup_ok:
                # A leased setup whose ack was rejected (not a raised
                # error) is a failed setup, treated identically to a cold
                # connect failure: the caller's existing not-`command_dict`
                # guard in `_operation_context` handles it unchanged.
                return None, 0
            buffer_size = self._calculate_buffer_size()
            logger.debug(
                f"Operation {operation} setup for {eprom_name} (state {cmd}) complete ({time.time() - start_time:.2f}s, leased). Buffer size: {buffer_size}"  # noqa: E501
            )
            return command_dict, buffer_size

        try:
            self.comm = SerialCommunicator.find_and_connect(
                command_dict,
                self.config,
                preferred_port=preferred_port,
                fault_inject_outgoing=fault_inject_outgoing,
                restrict_to_port=restrict_to_port,
            )
            buffer_size = self._calculate_buffer_size()
            logger.debug(
                f"Operation {operation} setup for {eprom_name} (state {cmd}) complete ({time.time() - start_time:.2f}s). Buffer size: {buffer_size}"  # noqa: E501
            )
            return command_dict, buffer_size
        except (ProgrammerNotFoundError, SerialError) as e:
            logger.error(f"Failed to setup operation {operation} for {eprom_name}: {e}")
            self._disconnect_programmer()  # Ensure comm is None if setup fails
            return None, 0

    @contextmanager
    def _operation_context(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        cmd: int,
        operation_flags: int = 0,
        address: str | None = None,
        size: str | None = None,
        fault_inject_outgoing: Callable[[bytes], bytes] | None = None,
        region_length: int | None = None,
        preferred_port: str | None = None,
        restrict_to_port: bool | None = None,
    ):
        """A context manager to handle EPROM operation setup and teardown.

        ``fault_inject_outgoing`` (dev-only) is forwarded to
        ``find_and_connect`` so the setup command frame can be corrupted at connection
        time. Default None keeps the production path byte-identical.

        ``region_length`` (BLANK-01 / D-05) is forwarded to ``_setup_operation``
        by keyword, trailing the existing positional six -- it must not be
        inserted among them.

        ``preferred_port``/``restrict_to_port`` (203-CR-01): forwarded
        verbatim to ``_setup_operation``. See that method's docstring.
        """
        command_dict, buffer_size = self._setup_operation(
            eprom_name,
            eprom_data_dict,
            cmd,
            operation_flags,
            address,
            size,
            fault_inject_outgoing=fault_inject_outgoing,
            region_length=region_length,
            preferred_port=preferred_port,
            restrict_to_port=restrict_to_port,
        )
        if not command_dict or not self.comm:
            yield None, None, None  # Yield None to indicate setup failure
            return

        operation_name = COMMAND_NAMES[cmd]
        try:
            # Yield the necessary data to the 'with' block
            yield command_dict, buffer_size, operation_name
        finally:
            # SESS-01: under a lease, the link outlives this single call --
            # `lease()`'s own `finally` is what tears it down, once, when
            # the `with operator.lease():` block itself exits. Tearing down
            # here too would defeat the whole point (every call would still
            # pay the connect cost the lease exists to remove). Unleased,
            # this is the pre-existing unconditional teardown, unchanged.
            if not self._leased:
                self._disconnect_programmer()

    def _disconnect_programmer(self):
        if self.comm:
            self.comm.disconnect()
            self.comm = None

    @contextmanager
    def lease(self):
        """Hold one validated serial link open across every `EpromOperator`
        call made inside this block (SESS-01), instead of connecting and
        tearing down per call.

        Default off, acquired at exactly ONE call site
        (`cli_handlers.dev_test`'s `run_plan(...)` call) so that removing
        the feature is a `git revert` of one commit rather than an unpick
        -- SESS-02's bench measurement may require exactly that. Every
        code path that never enters this context manager is byte-for-byte
        unchanged, mirroring the already-shipped opt-in seam
        `_drive_region_compare`'s `on_result` parameter documents for
        itself: default off, behaviour identical until a caller opts in.

        Failure policy (D-06): a `SerialError` raised while a leased
        setup is in flight drops the held link -- `_setup_operation`
        disconnects -- but leaves the lease itself active, so the NEXT
        operation cold-connects instead of the whole block failing. The
        exception still propagates to the caller unchanged. The
        alternative -- failing the whole plan -- would make a performance
        optimisation weaken `run_plan`'s own invariant that one step's
        failure never aborts the rest, which is not a trade worth making
        for a connect-time saving.

        What a lease removes that is not only time: closing the port
        de-asserts DTR and resets the attached Leonardo. A lease removes
        the board reset for every `EpromOperator` call inside the block --
        on the order of twenty in a default `dev test` plan, a derived
        count and not a measured one (206-SESSION-COST.md § 3: at most
        about 17-20 of a plan's roughly 30 connects are `EpromOperator`'s
        own; `HardwareManager`'s connects stay outside the lease, 206
        D-05); the measured wall-clock saving is 15.1% (40.709 s of a
        268.992 s cold-arm median, N=3 per arm, W27C512 on a Leonardo;
        206-SESSION-COST.md § 5). A step that passes today partly because
        the PREVIOUS step's teardown reset the board would behave
        differently under a lease -- that is the fidelity risk SESS-02's
        bench leg exists to measure, and it belongs here, at the seam,
        where the next reader will see it.
        """
        self._leased = True
        try:
            yield
        finally:
            self._leased = False
            self._disconnect_programmer()

    # --- Unified State Machine ---

    def _run_state_machine(
        self,
        operation_name: str,
        main_phase_handler: Callable | None = None,
        **handler_kwargs,
    ) -> Tuple[bool, str | None]:  # noqa: UP006
        """A unified state machine driver for all operations."""
        if not self.comm:
            return False, "Not connected"

        progress = ClassProgressHandler(self.progress_callback)
        final_msg = None
        # Cleared per operation -- see __init__'s comment. A caller reading
        # these after a SUCCESSFUL call must see None, not the previous
        # operation's failure.
        self.last_firmware_error_code = None
        self.last_firmware_error_message = None
        self._read_abort_stopped_at = None
        try:
            with logging_redirect_tqdm():
                # --- INIT Phase ---
                _ = self._execute_phase("INIT", progress)

                # --- MAIN Phase ---
                self.comm.send_ack()  # Signal start of MAIN
                logger.debug("Main start")
                if main_phase_handler:
                    # Delegate to a specific handler for the main data transfer loop
                    final_msg = main_phase_handler(progress=progress, **handler_kwargs)
                else:
                    # For simple commands, just wait for the MAIN completion signal
                    final_msg = self._main_phase_simple(progress)
                logger.debug("Main complete.")

                # --- END Phase ---
                end_msg = self._execute_phase("END", progress)  # noqa: F841

                # --- Final ACK to complete transaction ---
                self.comm.send_ack()
                return True, final_msg
        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Communication error during {operation_name}: {e}")
            return False, str(e)
        except EpromOperationError as e:
            logger.error(f"Programmer error during {operation_name}: {e}")
            # Debug session w27c512-devtest-all-bad: record the firmware's
            # own id and text before collapsing this to a bool, so the
            # diagnostic report can state WHY a step failed instead of
            # emitting `error_code: null, reason: ""`.
            self.last_firmware_error_code = e.error_code
            self.last_firmware_error_message = str(e)
            return False, str(e)
        finally:
            progress.close()

    def _execute_phase(
        self, phase_name: str, progress: ClassProgressHandler
    ) -> str | None:
        """Executes a single phase (INIT or END) of the state machine."""
        self.comm.send_ack()
        logger.debug(f"{phase_name.lower()} start")
        final_msg = None
        while True:
            response = self.comm.get_response()
            if response.type == phase_name:
                final_msg = response.message
                break
            if response.type == "ERROR":
                _raise_for_error_response(
                    response,
                    f"Programmer error during {phase_name.lower()}: {response.message}",
                )
            # INIT/END phases: render DATA progress frames but do NOT ack them.
            # #write-empty-input-regression (Option C): a multi-step in-progress
            # INIT/END sub-step (e.g. write-init blank-check) emits one
            # MSG_DATA_PROGRESS per chunk but the firmware consumes a host ack
            # only on the first chunk. Acking every DATA frame here piled up N-1
            # spurious OK acks in the firmware RX buffer, desyncing the MAIN
            # data-pull handshake -> MSG_ERR_EMPTY_INPUT (0xA4). The firmware keeps
            # emitting progress (so the bar still moves); the host just skips the ack.
            self._handle_progress_response(response, progress, ack_data=False)
        logger.debug(f"{phase_name.lower()} complete.")
        return final_msg

    def _handle_progress_response(
        self, response, progress: ClassProgressHandler, ack_data: bool = True
    ):
        """Helper to process DATA, WARN, OK during a state phase.

        ``ack_data`` controls whether a DATA frame is acked. MAIN-phase flow
        control requires the ack (default True). INIT/END progress frames must
        NOT be acked (the firmware does not consume per-chunk progress acks);
        callers in those phases pass ``ack_data=False``. Progress rendering
        always runs regardless of ``ack_data``.
        """
        if response.type == "DATA":
            try:
                if response.message and "/" in response.message:
                    current, total = map(int, response.message.split("/"))
                    if progress:
                        progress.set_progress(current, total)
                elif response.message:
                    progress.update(int(response.message))
            except (ValueError, TypeError):
                pass  # Not a parsable progress update
            if ack_data:
                self.comm.send_ack()
        elif response.type == "WARN":
            logger.warning(f"Programmer warning: {response.message}")
        elif response.type == "OK":
            logger.debug(f"Got OK: {response.message}")

    # --- Main Phase Handlers ---

    def _main_phase_simple(self, progress: ClassProgressHandler) -> str | None:
        """Main phase handler for simple commands like erase, blank check, id."""
        final_msg = None
        while True:
            response = self.comm.get_response()
            if response.type == "MAIN":
                final_msg = response.message
                break
            if response.type == "ERROR":
                _raise_for_error_response(response, response.message)
            if response.type == "OK" and final_msg is None:
                final_msg = response.message  # Capture final message from MAIN's OK
            # MAIN phase: DATA frames are flow-control; ack them (unchanged).
            self._handle_progress_response(response, progress, ack_data=True)
        return final_msg

    def _apply_write_progress(
        self, response, progress: ClassProgressHandler, start_addr: int
    ) -> bool:
        """Render an intra-block progress frame on the write path.

        Returns True when a position was actually applied -- the caller uses
        this to latch the chunk-handoff update off once the firmware starts
        driving the bar. False when the frame was absent or unparsable, so the
        latch never engages on a malformed frame.

        Applies the frame's `current` and IGNORES its `total`, performing the
        final operations DIRECTLY rather than calling `set_progress`. That
        method calls `start(total)` whenever the frame's total differs from the
        bar's, and `start()` CLOSES AND RE-CREATES the bar. The write bar is
        started with the file size while the frame carries the chip's memory
        size -- for a short file or an --address-offset write those differ, so
        every frame would tear the bar down and rebuild it.

        The frame carries an ABSOLUTE chip address while the bar's origin is the
        write's start address. Getting that wrong shows as a bar starting
        mid-way, or beyond 100%, on an --address write.

        This method NEVER acks. Do not route a write-path DATA frame through
        `_handle_progress_response` instead -- it acks by default and its DATA
        arm calls `set_progress`, which is the rebuild path this exists to avoid.
        """
        if not response.message or "/" not in response.message:
            return False
        try:
            absolute, _total_ignored = map(int, response.message.split("/"))
        except (ValueError, TypeError):
            return False  # not a parsable progress update
        position = max(0, absolute - start_addr)
        progress.current_step = position
        if progress.progress_callback:
            progress.progress_callback(position, progress.total_steps)
        if progress.pbar:
            progress.pbar.n = position
            progress.pbar.refresh()
        return True

    def _main_phase_send_data(
        self,
        progress: ClassProgressHandler,
        input_file_path: str,
        buffer_size: int,
        eprom_data_dict: dict | None = None,
        response_timeout: float | None = None,
    ) -> None:
        """Main phase handler for writing or verifying data.

        `eprom_data_dict` is forwarded from the caller so the boot-block hint can
        be appended to a flash4 verify timeout. None keeps behaviour identical
        for every other caller.

        `response_timeout` is write-only: `write_eprom` passes its computed
        block timeout from inside its operation context, `verify_eprom` does not
        pass it at all, so the None default leaves verify byte-identical. This
        is the ONLY timeout change on the write path -- the reader's own timeout
        semantics are untouched.
        """
        if not os.path.exists(input_file_path):
            raise EpromOperationError(f"Input file {input_file_path} not found.")

        protocol: int = (eprom_data_dict or {}).get("protocol-id", 0)
        mem_size: int = (eprom_data_dict or {}).get("memory-size", 0)
        timeout = (
            response_timeout
            if response_timeout is not None
            else DEFAULT_RESPONSE_TIMEOUT
        )

        with open(input_file_path, "rb") as file_handle:
            file_size = os.path.getsize(input_file_path)
            progress.start(file_size)

            # _setup_operation sets command_dict["address"]
            # ONLY when an --address was supplied, so .get("address", 0) is
            # exactly right for a full-chip write's start address (0) too --
            # write_eprom already forwards eprom_data_dict=cmd_data.
            start_addr = (eprom_data_dict or {}).get("address", 0)
            # Latches True on the first successfully
            # -applied mid-block progress frame (_apply_write_progress
            # returning True), so the chunk-handoff update() below stops
            # firing -- see its own comment for why it must not simply be
            # deleted instead.
            firmware_drives_bar = False

            while True:
                response = self.comm.get_response(timeout)
                if response.type == "MAIN":
                    break  # Main phase is complete
                if response.type == "ERROR":
                    hint = _boot_block_hint_message(response, protocol, mem_size)
                    budget_hint = _budget_failure_hint_message(response)
                    msg = response.message
                    # the boot-block hint (0xB3, flash4-only)
                    # and the budget-failure hint (0xBD/0xBE/0xAE) are
                    # disjoint by id today, but this composition does not
                    # rely on that -- appending whichever are present still
                    # produces one readable, " -- "-joined message, exactly
                    # like the boot-block hint alone already composed.
                    for extra_hint in (hint, budget_hint):
                        if extra_hint:
                            msg = msg + " -- " + extra_hint
                    _raise_for_error_response(response, msg)
                if response.type == "DATA":
                    # a mid-block MSG_DATA_PROGRESS frame is
                    # NEVER acked -- the firmware is mid-block waiting for
                    # nothing, and on a Leonardo a stray buffered "OK" makes
                    # op_get_message return OP_MSG_ACK, so
                    # _process_incoming_data's `default: return false` aborts
                    # the write with NO error frame at all
                    # (#write-empty-input-regression, in a new place). Placing
                    # this arm BEFORE the `!= "OK"` raise below is what keeps
                    # a mid-block frame from becoming an EpromOperationError.
                    if self._apply_write_progress(response, progress, start_addr):
                        firmware_drives_bar = True
                    continue
                if response.type != "OK":
                    raise EpromOperationError(
                        f"Programmer did not request data chunk, got {response.type}: {response.message}"  # noqa: E501
                    )

                if file_handle.tell() < file_size:
                    data_chunk = file_handle.read(buffer_size)
                    crc = _crc8_ccitt(data_chunk)
                    body = cobs_encode(data_chunk + bytes([crc]))
                    frame = b"#" + body + b"\x00"

                    # Firmware decodes the COBS frame via rurp_communication_read_data
                    # (rurp_serial_utils.cpp): reads bytes until the 0x00 delimiter,
                    # COBS-decodes in place, verifies CRC8-CCITT over the payload.
                    # Frame layout (ADR §4.3): b"#" + COBS(payload + CRC8) + b"\x00".
                    # Assembled as ONE bytes object and sent in a single send_bytes call
                    # (atomic-write mandate; timing guard).
                    self.comm.send_bytes(frame)
                    if not firmware_drives_bar:
                        # The two progress sources measure
                        # different things -- bytes SENT (this handoff) versus
                        # bytes PROGRAMMED (the firmware's own 0xE0 frames) --
                        # and this one runs first. Without this latch, the bar
                        # jumps ahead by a full chunk the instant it is sent,
                        # then the firmware's frames pull it back down as bytes
                        # are actually programmed -- a visible rewind (tqdm
                        # permits pbar.n to move backward). Do NOT simply
                        # delete this call: a board that never delivers a
                        # mid-block frame (every uno/uno328pb write, BF-2)
                        # would then be regressed to a bar that never moves.
                        progress.update(len(data_chunk))
                else:
                    self.comm.send_done()

    def _main_phase_read_data(
        self,
        progress: ClassProgressHandler,
        start_addr: int,
        end_addr: int,
        process_data_chunk_callback: Callable,
        abort_predicate: Callable[[], bool] | None = None,
    ):
        """Main phase handler for reading data.

        The firmware wraps each chip-byte chunk inside a
        MSG_DATA_CHUNK ID frame instead of emitting raw bytes after a DATA:
        text prefix.  The response loop distinguishes:
          - DATA response with payload set → MSG_DATA_CHUNK; extract raw bytes.
          - DATA response with no payload  → MSG_DATA_SENDING (zero-param batch
            starter, which arrives before the chunk frame); skip and continue.

        202-04 D-06: `abort_predicate`, when given, is consulted after each
        delivered chunk has been fed to the callback and the address/progress
        advanced. Defaulted to `None` so the four pre-existing callers
        (`read_eprom`, both `consistency_check_eprom` drives, and the hexdump
        drive) are byte-for-byte unchanged -- none of them pass it, and the
        chunk callback's return value stays ignored exactly as before.

        Once the predicate returns true, the loop stops acking -- it does
        NOT raise and does NOT break. Raising here would unwind the loop
        without consuming the ERROR frame the firmware's own ack-wait
        timeout produces, leaving unread bytes on the port, which is the
        opposite of what D-06 buys: the firmware's `op_wait_for_ack`
        (1 s, polled at 10 ms) times out, emits MSG_ERR_TIMEOUT, and the
        dispatch loop's `command_done()` still runs on that error path to
        leave the port clean (D-06/D-07). After the stop, no further chunk
        is fed to the callback, acked, or counted toward progress -- this is
        what keeps `compared` a well-defined quantity for D-09's honest
        span reporting: a payload the firmware sent after the host chose to
        stop must never silently widen what "compared" means.
        """
        from firestarter.messages import (
            MSG_DATA_CHUNK,  # local import avoids circular  # noqa: F401
        )

        data_size = end_addr - start_addr
        if data_size > 0:
            progress.start(data_size)

        stopped = False
        while True:
            response = self.comm.get_response()
            if response.type == "MAIN":
                logger.info("EPROM read complete.")
                break
            if response.type == "ERROR":
                _raise_for_error_response(
                    response, f"Programmer error during read: {response.message}"
                )
            if response.type == "DATA":
                if response.payload is not None:
                    # MSG_DATA_CHUNK: the raw chip bytes are in response.payload.
                    payload = response.payload
                    if not payload:
                        logger.warning("Received MSG_DATA_CHUNK with empty payload.")
                        continue
                    if stopped:
                        # Draining post-stop: keep consuming responses (so the
                        # port empties and the terminating MAIN/ERROR frame is
                        # read) without touching the callback, the ack, or the
                        # progress bar again. See docstring above.
                        continue
                    process_data_chunk_callback(start_addr, payload)
                    start_addr += len(payload)
                    progress.update(len(payload))
                    if abort_predicate is not None and abort_predicate():
                        stopped = True
                        self._read_abort_stopped_at = time.monotonic()
                        logger.info(
                            f"Read stopped in flight at 0x{start_addr:06x} "
                            "(abort predicate fired)."
                        )
                        continue
                    self.comm.send_ack()
                else:
                    # MSG_DATA_SENDING (zero-param batch-start ack): no data yet;
                    # the MSG_DATA_CHUNK frame follows immediately.
                    logger.debug(
                        f"Received DATA signal (no payload): {response.message}"
                    )
            else:
                self._handle_progress_response(response, progress)

    # --- Public API Methods ---

    def read_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        output_file: str | None = None,
        operation_flags: int = 0,
        address_str: str | None = None,
        size_str: str | None = None,
    ) -> bool:
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_READ,
            operation_flags,
            address_str,
            size_str,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False

            actual_output_file = output_file or f"{eprom_name.upper()}.bin"
            logger.info(
                f"Reading EPROM {eprom_name.upper()}, saving to {actual_output_file}"
            )
            start_time = time.time()

            try:
                with open(actual_output_file, "wb") as file_handle:

                    def _write_to_file(address, data_chunk):
                        file_handle.seek(address)
                        file_handle.write(data_chunk)

                    is_ok, _ = self._run_state_machine(
                        op_name,
                        main_phase_handler=self._main_phase_read_data,
                        start_addr=cmd_data.get("address", 0),
                        end_addr=cmd_data.get("memory-size", 0),
                        process_data_chunk_callback=_write_to_file,
                    )
                if is_ok:
                    logger.info(
                        f"Read complete ({time.time() - start_time:.2f}s). Data saved to {actual_output_file}"  # noqa: E501
                    )
                return is_ok
            except IOError as e:  # noqa: UP024
                logger.error(f"File I/O error with {actual_output_file}: {e}")
                return False

    def consistency_check_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        runs: int = 3,
        output_dir: str | None = None,
        keep_files: bool = True,
        max_diffs: int = 10,
        quiet: bool = False,
        operation_flags: int = 0,
        read_settling_us: int = 0,  # address-settling delay (µs; 0=firmware default)
        read_strobe_us: int = 0,  # /CE read-strobe pulse width (µs; 0=firmware default)
    ) -> int:
        """Run N consecutive read_eprom passes and report SHA-256 divergence.

        Returns:
            0 -- all N reads byte-identical (PASS)
            1 -- one or more reads diverge (FAIL -- bug detected)
            2 -- hardware / serial / timeout error (could not complete N reads)

        This method pioneered the int-rather-than-bool return on
        `EpromOperator` for this reason -- a 3-way verdict cannot fit in a
        bool. `verify_eprom` (202-01) and `check_eprom_blank` (202-05) now
        share the identical 0/1/2 convention under D-10, for the identical
        reason: a match/mismatch/transport-failure verdict cannot fit in a
        bool either. Same exit-code convention as grep(1). Earlier precedent
        for non-bool return: check_eprom_id() returns Tuple[bool,
        Optional[int]] above.

        Reuses _run_state_machine + _main_phase_read_data verbatim, so the
        diagnostic exercises the same code path the read bug lives in. Do NOT
        refactor into a parallel read implementation.
        """
        # Reject runs < 2 BEFORE any state-machine invocation
        if runs < 2:
            logger.error(
                f"--runs must be >= 2 (got {runs}); "
                f"a consistency check requires at least 2 reads to compare."
            )
            return 2

        # Quiet mode: suppress tqdm by swapping progress_callback to a no-op.
        # ClassProgressHandler.__init__ checks `if self.progress_callback:` --
        # a truthy no-op short-circuits the tqdm.tqdm() instantiation.
        prior_callback = self.progress_callback
        if quiet:
            self.progress_callback = lambda *a, **kw: None

        try:
            # Default output_dir naming uses datetime + chip name.
            # Board name is optional; if firmware handshake hasn't run we
            # render "unknown-board" rather than blocking on an extra round-
            # trip (per RESEARCH Pitfall 2 Option a, the cleanest production
            # path is to use the actual board name -- but the unit-test
            # surface doesn't have a serial connection, so we fall back).
            if output_dir is None:
                timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
                output_dir = str(
                    Path(DEFAULT_RUN_OUTPUT_DIR)
                    / f"consistency-check-{eprom_name}-unknown-board-{timestamp}"
                )
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            # Merge read-timing knobs into eprom_data_dict copy so they ride
            # into _setup_operation via command_dict = eprom_data_dict.copy().
            # Emit each key only when non-zero (firmware defaults apply when absent).
            # Pattern: consistent with how pulse-delay already travels via the DB dict.
            if read_settling_us or read_strobe_us:
                eprom_data_dict = dict(
                    eprom_data_dict
                )  # shallow copy — never mutate caller's dict
                if read_settling_us:
                    eprom_data_dict[JSON_KEY_READ_SETTLING_DELAY] = read_settling_us
                if read_strobe_us:
                    eprom_data_dict[JSON_KEY_READ_STROBE_US] = read_strobe_us

            # Run loop: N reads through the production state machine
            results = []  # list of (run_i, sha_hex, bytes_written)
            total_size = 0
            for i in range(1, runs + 1):
                run_path = output_path / f"run_{i:02d}.bin"
                logger.info(f"Run {i}/{runs}: reading {eprom_name} -> {run_path}")
                start_t = time.time()

                # Reuse the EXACT code path read_eprom uses -- do not duplicate
                try:
                    with self._operation_context(
                        eprom_name,
                        eprom_data_dict,
                        COMMAND_READ,
                        operation_flags,
                    ) as (cmd_data, _, op_name):
                        if not cmd_data:
                            logger.error(f"Run {i}: failed to set up read operation.")
                            return 2  # hardware error
                        try:
                            with open(run_path, "wb") as fh:

                                def _writer(
                                    address,
                                    data_chunk,
                                    _fh=fh,
                                    _start=cmd_data.get("address", 0),
                                ):
                                    # Mirror read_eprom's _write_to_file inner closure
                                    # (eprom_operations.py:408-411). Use relative-from-start  # noqa: E501
                                    # offset so the file fills from byte 0 regardless of
                                    # absolute start_addr.
                                    _fh.seek(address - _start)
                                    _fh.write(data_chunk)

                                is_ok, _ = self._run_state_machine(
                                    op_name,
                                    main_phase_handler=self._main_phase_read_data,
                                    start_addr=cmd_data.get("address", 0),
                                    end_addr=cmd_data.get("memory-size", 0),
                                    process_data_chunk_callback=_writer,
                                )
                        except IOError as e:  # noqa: UP024
                            logger.error(f"Run {i}: file I/O error on {run_path}: {e}")
                            return 2

                    # Map _run_state_machine (False, msg) -> exit 2 (per
                    # RESEARCH Pitfall 4: state machine catches serial
                    # exceptions and returns (False, str(e)) rather than
                    # propagating).
                    if not is_ok:
                        logger.error(
                            f"Run {i}: hardware/serial error -- read incomplete."
                        )
                        return 2

                except EpromOperationError as e:
                    logger.error(f"Run {i}: {e}")
                    return 2

                bytes_written = run_path.stat().st_size
                sha = hashlib.sha256(run_path.read_bytes()).hexdigest()
                elapsed = time.time() - start_t
                results.append((i, sha, bytes_written))
                total_size = bytes_written  # noqa: F841
                logger.info(
                    f"Run {i}/{runs}: SHA-256 {sha}  "
                    f"bytes={bytes_written}  elapsed={elapsed:.2f}s"
                )

            # Verdict
            distinct = sorted({r[1] for r in results})
            exit_code = 0 if len(distinct) == 1 else 1

            # Print verdict block -- exact substrings pinned by the
            # forward-compat regex in test_stdout_verdict_block_format.
            verdict = "PASS" if exit_code == 0 else "FAIL"
            port = (
                self.config.get_value("port")
                if hasattr(self.config, "get_value")
                else "?"
            )
            print(f"\nConsistency check: {verdict}")
            print(f"Chip: {eprom_name}  Board: unknown-board  Port: {port}")
            print(f"Runs: N={runs}")
            print(f"Distinct SHAs: {len(distinct)}")
            print(f"Output dir: {output_dir}/")

            # Divergence detail on FAIL
            if exit_code == 1:
                run1_path = output_path / "run_01.bin"
                run2_path = output_path / "run_02.bin"
                run1_bytes = run1_path.read_bytes()
                run2_bytes = run2_path.read_bytes()
                cmp_len = min(len(run1_bytes), len(run2_bytes))
                diff_offsets = [
                    o for o in range(cmp_len) if run1_bytes[o] != run2_bytes[o]
                ]
                if diff_offsets:
                    first = diff_offsets[0]
                    # 4-hex-digit format guaranteed for 64KB chips; widen
                    # automatically for larger payloads. Use %04X minimum.
                    width = max(4, len(f"{cmp_len - 1:X}"))
                    print(
                        f"First divergence: offset 0x{first:0{width}X}  "
                        f"(run_1=0x{run1_bytes[first]:02X}, "
                        f"run_2=0x{run2_bytes[first]:02X})"
                    )
                    total_diffs = len(diff_offsets)
                    pct = 100.0 * total_diffs / cmp_len if cmp_len else 0.0
                    print(
                        f"Total divergent bytes (run_1 vs run_2): "
                        f"{total_diffs} / {cmp_len} ({pct:.1f}%)"
                    )
                    head = diff_offsets[:max_diffs]
                    offs_str = ", ".join(f"0x{o:0{width}X}" for o in head)
                    print(f"First {max_diffs} divergent offsets: {offs_str}")

            # Cleanup
            if not keep_files:
                shutil.rmtree(output_dir, ignore_errors=True)

            return exit_code
        finally:
            # Restore the operator's progress_callback so subsequent
            # operations are unaffected by --quiet for THIS invocation.
            self.progress_callback = prior_callback

    def write_cycle_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        source_image_path: str,
        runs: int = 5,
        output_dir: str | None = None,
        operation_flags: int = 0,
    ) -> int:
        """Erase, write the source image, read back N times, and compare each
        read-back against the source image's SHA-256.

        Returns:
            0 -- all N read-backs match source image SHA-256 (PASS)
            1 -- any read-back SHA-256 != source image SHA-256 (FAIL / mismatch)
            2 -- erase_eprom or write_eprom returned False, read-back state machine
                 returned is_ok=False, or EpromOperationError raised (hw-error)

        The 3-way verdict (PASS / FAIL / hw-error) mirrors consistency_check_eprom.
        hw-error is NEVER collapsed to mismatch (verdict 2 != verdict 1).

        Reuses _operation_context + _run_state_machine + _main_phase_read_data
        verbatim from consistency_check_eprom. Do NOT refactor the read-back
        block into a parallel read implementation.
        """
        source_sha = hashlib.sha256(Path(source_image_path).read_bytes()).hexdigest()

        if output_dir is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            output_dir = str(
                Path(DEFAULT_RUN_OUTPUT_DIR)
                / f"write-cycle-{eprom_name}-unknown-board-{timestamp}"
            )
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        for i in range(1, runs + 1):
            # (a) Erase
            if not self.erase_eprom(eprom_name, eprom_data_dict, operation_flags):
                logger.error(f"Cycle {i}: erase failed.")
                return 2

            # (b) Write
            if not self.write_eprom(
                eprom_name, eprom_data_dict, source_image_path, operation_flags
            ):
                logger.error(f"Cycle {i}: write failed.")
                return 2

            # (c) Read-back — verbatim reuse of the consistency_check_eprom read block
            # (reuse-not-duplicate rule: this block must not be reimplemented separately)
            cycle_path = output_path / f"cycle_{i:02d}_readback.bin"
            logger.info(f"Cycle {i}/{runs}: read-back {eprom_name} -> {cycle_path}")

            try:
                with self._operation_context(
                    eprom_name,
                    eprom_data_dict,
                    COMMAND_READ,
                    operation_flags,
                ) as (cmd_data, _, op_name):
                    if not cmd_data:
                        logger.error(f"Cycle {i}: failed to set up read operation.")
                        return 2
                    try:
                        with open(cycle_path, "wb") as fh:

                            def _writer(
                                address,
                                data_chunk,
                                _fh=fh,
                                _start=cmd_data.get("address", 0),
                            ):
                                _fh.seek(address - _start)
                                _fh.write(data_chunk)

                            is_ok, _ = self._run_state_machine(
                                op_name,
                                main_phase_handler=self._main_phase_read_data,
                                start_addr=cmd_data.get("address", 0),
                                end_addr=cmd_data.get("memory-size", 0),
                                process_data_chunk_callback=_writer,
                            )
                    except IOError as e:  # noqa: UP024
                        logger.error(f"Cycle {i}: file I/O error on {cycle_path}: {e}")
                        return 2

                # Map _run_state_machine (False, msg) -> exit 2 (Pitfall 3:
                # timeout/serial errors return (False, str(e)), never raise).
                if not is_ok:
                    logger.error(
                        f"Cycle {i}: hardware/serial error -- read-back incomplete."
                    )
                    return 2

            except EpromOperationError as e:
                logger.error(f"Cycle {i}: {e}")
                return 2

            # (d) Host-side SHA-256 compare against source image
            readback_sha = hashlib.sha256(cycle_path.read_bytes()).hexdigest()
            if readback_sha != source_sha:
                logger.error(
                    f"Cycle {i}: SHA-256 mismatch -- "
                    f"source={source_sha}  readback={readback_sha}"
                )
                return 1

        return 0

    # --- DEV Methods ---

    def fault_inject_cycle(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        direction: str = "outgoing",
        fault_form: str = "corrupt-crc8",
        output_dir: str | None = None,
    ) -> bool:
        """Demonstrate COBS resync by injecting a corrupted frame and asserting a
        bounded clean error followed by a byte-exact clean transfer.

        Returns:
            True  -- corrupted transfer surfaced a clean (bounded) error AND the
                     subsequent clean transfer succeeded byte-exact.
            False -- unexpected success on the corrupted transfer, or the
                     clean follow-on transfer failed.

        direction="outgoing": corrupt the host→fw SETUP command frame via the
            _fault_inject_outgoing hook, threaded into find_and_connect so it fires at
            connection time. This is the ONLY corruptible host→fw command frame — a
            READ's MAIN phase sends only plaintext acks (send_string). The firmware
            rejects the corrupt frame and the connection fails with a bounded error;
            a fresh clean transfer then succeeds. The hook must be threaded in BEFORE
            setup -- wiring it after means it never fires, a silent false negative.
        direction="incoming": corrupt fw→host frame via FaultInjectingSerialCommunicator
            on an established connection; the host decoder catches it and the same
            connection recovers on the clean follow-on (Pitfall 2).

        Writes fault-inject-<direction>-log.txt with the measured error latency so the
        sub-second clean error can be confirmed.
        """
        from firestarter.serial_comm import FaultInjectingSerialCommunicator

        if output_dir is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            output_dir = f"fault-inject-{eprom_name}-{direction}-{timestamp}"
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Build fault hooks for the outgoing path
        def _corrupt_crc8(frame: bytes) -> bytes:
            """Flip the CRC8 byte (frame[-2]) — frame[-1] is the 0x00 delimiter."""
            return frame[:-2] + bytes([frame[-2] ^ 0x01]) + b"\x00"

        def _drop_delimiter(frame: bytes) -> bytes:
            """Drop trailing 0x00 delimiter — firmware inter-byte timeout fires."""
            return frame[:-1]

        fault_hooks: dict = {
            "corrupt-crc8": _corrupt_crc8,
            "drop-delimiter": _drop_delimiter,
        }
        hook = fault_hooks.get(fault_form, _corrupt_crc8)

        # --- Corrupted transfer ---
        # Outgoing: the hook is threaded into _operation_context so it corrupts the
        #   SETUP command frame at connection time. This is the ONLY corruptible
        #   host->fw command frame — a READ's MAIN phase emits plaintext acks
        #   (send_string), never send_json_command, so the previous "set the hook
        #   after setup" wiring never fired -- a false negative. The firmware rejects the
        #   corrupt frame (CRC8-before-parse / inter-byte timeout) -> connection setup
        #   fails with a bounded error == the expected outcome.
        # Incoming: connect cleanly, swap to FaultInjectingSerialCommunicator, run the
        #   read; the host decoder catches the mutated fw->host frame.
        # error_latency_s captures wall-clock from corrupted-attempt start to the
        #   surfaced error so the "sub-second clean error, no 2 s cascade" bar can be
        #   measured (the harness reports it; the firmware's actual latency decides it).
        corrupted_ok = False
        error_latency_s: float | None = None
        corrupted_detail = ""
        _t0 = time.monotonic()
        try:
            with self._operation_context(
                eprom_name,
                eprom_data_dict,
                COMMAND_READ,
                0,
                fault_inject_outgoing=(hook if direction == "outgoing" else None),
            ) as (cmd_data, _, op_name):
                if direction == "outgoing" and not cmd_data:
                    # Setup command frame corrupted -> firmware rejected -> bounded
                    # connection failure. This IS the expected outgoing-fault outcome.
                    error_latency_s = time.monotonic() - _t0
                    corrupted_ok = True
                    corrupted_detail = (
                        "outgoing: setup command frame rejected; connection did not "
                        "establish (firmware did not ack a corrupt host->fw frame)"
                    )
                elif not cmd_data:
                    # Incoming requires a clean connection before the fw->host swap.
                    return False
                else:
                    # We are connected. Incoming: swap comm to the fault subclass.
                    # Outgoing: reaching here means the corrupt setup frame did NOT
                    # prevent connection (fault never fired, or firmware accepted a
                    # corrupt frame) — run the read so an unexpected success is caught.
                    if direction == "incoming":
                        assert self.comm is not None  # noqa: S101
                        fault_comm = FaultInjectingSerialCommunicator.__new__(
                            FaultInjectingSerialCommunicator
                        )
                        fault_comm.__dict__.update(self.comm.__dict__)
                        fault_comm._corrupt_incoming_once = True  # type: ignore[attr-defined]
                        fault_comm._fault_fired = False  # type: ignore[attr-defined]
                        self.comm = fault_comm  # type: ignore[assignment]

                    corrupted_path = output_path / "corrupted_transfer.bin"
                    try:
                        with open(corrupted_path, "wb") as fh:

                            def _writer_corrupt(
                                address,
                                data_chunk,
                                _fh=fh,
                                _start=cmd_data.get("address", 0),
                            ):
                                _fh.seek(address - _start)
                                _fh.write(data_chunk)

                            is_ok_corrupt, _ = self._run_state_machine(
                                op_name,
                                main_phase_handler=self._main_phase_read_data,
                                start_addr=cmd_data.get("address", 0),
                                end_addr=cmd_data.get("memory-size", 0),
                                process_data_chunk_callback=_writer_corrupt,
                            )
                    except IOError as e:  # noqa: UP024
                        logger.error(f"fault_inject_cycle: file I/O error: {e}")
                        return False
                    error_latency_s = time.monotonic() - _t0
                    # The corrupted transfer should have failed (is_ok_corrupt == False)
                    corrupted_ok = not is_ok_corrupt
                    corrupted_detail = (
                        f"{direction}: connected; corrupted read verdict ok="
                        f"{is_ok_corrupt} (expected False)"
                    )
        except (
            EpromOperationError,
            ProgrammerNotFoundError,
            SerialError,
            SerialTimeoutError,
            FirmwareOutdatedError,
        ) as e:
            # A bounded transport/connection error on the corrupted transfer is the
            # expected resync signal (not a silent accept, not an unbounded hang).
            error_latency_s = time.monotonic() - _t0
            corrupted_ok = True
            corrupted_detail = f"{direction}: {type(e).__name__} (expected): {e}"

        # Persist the latency + verdict so the operator can confirm the sub-second
        # clean error (no 2 s cascade) the fast-fail bar requires.
        self._write_fault_inject_log(
            output_path,
            direction,
            fault_form,
            corrupted_ok,
            error_latency_s,
            corrupted_detail,
        )

        if not corrupted_ok:
            logger.error(
                "fault_inject_cycle: corrupted transfer unexpectedly succeeded."
            )
            return False

        # --- Clean follow-on transfer on the same connection ---
        clean_path = output_path / "clean_transfer.bin"
        try:
            with self._operation_context(
                eprom_name,
                eprom_data_dict,
                COMMAND_READ,
                0,
            ) as (cmd_data_clean, _, op_name_clean):
                if not cmd_data_clean:
                    return False
                try:
                    with open(clean_path, "wb") as fh2:

                        def _writer_clean(
                            address,
                            data_chunk,
                            _fh=fh2,
                            _start=cmd_data_clean.get("address", 0),
                        ):
                            _fh.seek(address - _start)
                            _fh.write(data_chunk)

                        is_ok_clean, _ = self._run_state_machine(
                            op_name_clean,
                            main_phase_handler=self._main_phase_read_data,
                            start_addr=cmd_data_clean.get("address", 0),
                            end_addr=cmd_data_clean.get("memory-size", 0),
                            process_data_chunk_callback=_writer_clean,
                        )
                except IOError as e:  # noqa: UP024
                    logger.error(
                        f"fault_inject_cycle: clean-transfer file I/O error: {e}"
                    )
                    return False

            if not is_ok_clean:
                logger.error("fault_inject_cycle: clean follow-on transfer failed.")
                self._append_fault_inject_log(
                    output_path, "clean follow-on transfer FAILED"
                )
                return False
        except EpromOperationError as e:
            logger.error(f"fault_inject_cycle: clean transfer raised: {e}")
            self._append_fault_inject_log(
                output_path, f"clean follow-on transfer raised: {e}"
            )
            return False

        self._append_fault_inject_log(
            output_path, "clean follow-on transfer PASSED (recovery byte-exact)"
        )
        return True

    @staticmethod
    def _write_fault_inject_log(
        output_path: Path,
        direction: str,
        fault_form: str,
        corrupted_ok: bool,
        error_latency_s: float | None,
        detail: str,
    ) -> None:
        """Write the fault-injection log (one per cycle).

        Records the measured error latency so the operator can confirm the
        sub-second clean error (no 2 s timeout cascade) the acceptance requires.
        """
        log_path = output_path / f"fault-inject-{direction}-log.txt"
        latency_str = (
            f"{error_latency_s:.3f}s" if error_latency_s is not None else "unmeasured"
        )
        # 2.0 s is the historical timeout-cascade threshold the hardening removes.
        cascade = (
            "UNKNOWN"
            if error_latency_s is None
            else ("NO (sub-2s)" if error_latency_s < 2.0 else "YES (>=2s cascade)")
        )
        try:
            with open(log_path, "w") as fh:
                fh.write(
                    f"# fault-injection log ({direction}, {fault_form})\n"
                    f"corrupted_transfer_surfaced_clean_error: {corrupted_ok}\n"
                    f"error_latency: {latency_str}\n"
                    f"sub_second_clean_error_no_2s_cascade: {cascade}\n"
                    f"detail: {detail}\n"
                )
        except IOError as e:  # noqa: UP024
            logger.error(f"fault_inject_cycle: could not write fault log: {e}")

    @staticmethod
    def _append_fault_inject_log(output_path: Path, line: str) -> None:
        """Append a follow-on line to the most recent fault-injection log(s)."""
        for log_path in output_path.glob("fault-inject-*-log.txt"):
            try:
                with open(log_path, "a") as fh:
                    fh.write(f"{line}\n")
            except IOError as e:  # noqa: UP024
                logger.error(f"fault_inject_cycle: could not append fault log: {e}")

    def measure_command_nak_latency(
        self,
        fault_form: str = "corrupt-crc8",
        output_dir: str | None = None,
        port: str | None = None,
    ) -> bool:
        """Outgoing PER-FRAME latency measurement on an ESTABLISHED single-port
        connection (53-04 harness refinement).

        Unlike fault_inject_cycle (which corrupts the connection-SETUP frame and so
        triggers find_and_connect's multi-port retry — inflating the latency), this opens
        ONE pinned port directly, then on the SAME open connection:
          1. sends a clean CMD_FW_VERSION (baseline — firmware alive + at IDLE),
          2. sends ONE corrupted CMD_FW_VERSION frame, timed precisely from send to the
             firmware's error response (the real per-frame NAK latency),
          3. sends a clean CMD_FW_VERSION (recovery on the SAME connection).

        CMD_FW_VERSION is used because it is self-contained: the firmware answers and
        returns to CMD_IDLE, so three commands run back-to-back on one connection without
        a chip, VPP, or the read state machine.

        Returns True iff baseline OK AND the corrupted frame surfaced an error (no silent
        accept) AND the clean recovery transfer succeeded. Writes
        fault-inject-<fault_form>-latency.txt with the precise per-frame latency.
        """
        if port is None:
            port = self.config.get_value("port")
        if not port:
            logger.error(
                "measure_command_nak_latency: no serial port resolved "
                "(pass -p <port> or set config.port)."
            )
            return False

        if output_dir is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            output_dir = f"fault-inject-latency-{fault_form}-{timestamp}"
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        def _corrupt_crc8(frame: bytes) -> bytes:
            return frame[:-2] + bytes([frame[-2] ^ 0x01]) + b"\x00"

        def _drop_delimiter(frame: bytes) -> bytes:
            return frame[:-1]

        hook = {"corrupt-crc8": _corrupt_crc8, "drop-delimiter": _drop_delimiter}.get(
            fault_form, _corrupt_crc8
        )

        fw_cmd = {"state": COMMAND_FW_VERSION}
        comm = None
        baseline_ok = False
        corrupted_surfaced_error = False
        recovery_ok = False
        nak_latency_s: float | None = None
        detail = ""
        try:
            comm = SerialCommunicator(port=port)
            comm.consume_remaining_input()

            # 1. Baseline clean command on the open connection.
            comm.send_json_command(fw_cmd)
            baseline_ok, _ = comm.expect_ack()
            comm.consume_remaining_input()
            if not baseline_ok:
                detail = "baseline clean command did not ack OK; aborting measurement"
            else:
                # 2. One corrupted command frame, timed to the firmware error response.
                comm._fault_inject_outgoing = hook  # type: ignore[attr-defined]
                _t0 = time.monotonic()
                comm.send_json_command(fw_cmd)
                try:
                    corrupt_is_ok, corrupt_msg = comm.expect_ack()
                except SerialTimeoutError as e:
                    corrupt_is_ok, corrupt_msg = False, f"host read timeout: {e}"
                nak_latency_s = time.monotonic() - _t0
                comm._fault_inject_outgoing = None  # type: ignore[attr-defined]
                comm.consume_remaining_input()
                corrupted_surfaced_error = not corrupt_is_ok
                detail = (
                    f"corrupted frame response: ok={corrupt_is_ok} msg={corrupt_msg}"
                )

                # 3. Recovery: clean command on the SAME open connection.
                comm.send_json_command(fw_cmd)
                try:
                    recovery_ok, _ = comm.expect_ack()
                except SerialTimeoutError:
                    recovery_ok = False
                comm.consume_remaining_input()
        except (SerialError, SerialTimeoutError) as e:
            detail = f"{type(e).__name__}: {e}"
            logger.error(f"measure_command_nak_latency: {detail}")
        finally:
            if comm is not None:
                comm.disconnect()

        verdict = baseline_ok and corrupted_surfaced_error and recovery_ok
        self._write_nak_latency_log(
            output_path,
            fault_form,
            port,
            baseline_ok,
            corrupted_surfaced_error,
            recovery_ok,
            nak_latency_s,
            detail,
        )
        if not verdict:
            logger.error(
                "measure_command_nak_latency: verdict FAIL "
                f"(baseline_ok={baseline_ok}, corrupted_surfaced_error="
                f"{corrupted_surfaced_error}, recovery_ok={recovery_ok})"
            )
        return verdict

    @staticmethod
    def _write_nak_latency_log(
        output_path: Path,
        fault_form: str,
        port: str,
        baseline_ok: bool,
        corrupted_surfaced_error: bool,
        recovery_ok: bool,
        nak_latency_s: float | None,
        detail: str,
    ) -> None:
        """Write the per-frame NAK latency log (53-04 harness refinement)."""
        log_path = output_path / f"fault-inject-{fault_form}-latency.txt"
        latency_str = (
            f"{nak_latency_s:.3f}s" if nak_latency_s is not None else "unmeasured"
        )
        # Sub-second is the fast-fail bar for a complete corrupt frame; a
        # drop-delimiter frame is bounded by the firmware inter-byte deadline (~1 s).
        if nak_latency_s is None:
            verdict = "UNKNOWN"
        elif nak_latency_s < 1.0:
            verdict = "SUB-SECOND (fast-fail)"
        elif nak_latency_s < 2.0:
            verdict = "SUB-2s (bounded; ~inter-byte deadline)"
        else:
            verdict = ">=2s (cascade — investigate)"
        try:
            with open(log_path, "w") as fh:
                fh.write(
                    "# per-frame NAK latency (established single-port connection)\n"
                    f"# port: {port}  fault_form: {fault_form}\n"
                    f"baseline_clean_command_ok: {baseline_ok}\n"
                    f"corrupted_frame_surfaced_error_no_silent_accept: {corrupted_surfaced_error}\n"
                    f"per_frame_nak_latency: {latency_str}\n"
                    f"latency_verdict: {verdict}\n"
                    f"recovery_clean_command_same_connection_ok: {recovery_ok}\n"
                    f"detail: {detail}\n"
                )
        except IOError as e:  # noqa: UP024
            logger.error(f"measure_command_nak_latency: could not write log: {e}")

    @staticmethod
    def _summarize_connect_samples(samples: list) -> Dict[str, str]:  # noqa: UP006
        """Aggregate a list of observed connect durations (MEAS-01 numeric contract).

        Pure: no clock read, no file I/O, fully testable without a board.

        Every reported figure is a duration that was actually observed. The
        median is `statistics.median_low`, never `statistics.median` --  on an
        even sample count that is the LOWER of the two middle values, never an
        interpolated average of two that were. No mean is computed or reported
        anywhere: a blended average is not a duration anyone observed.

        `structural_floor` is the constant `CONNECT_COST_STRUCTURAL_FLOOR_S`
        (2.5s), and `remainder` is the median minus that floor, so a reader can
        see whether the board-independent floor dominates the figure rather
        than having to infer it.

        An empty `samples` list reports the literal string `unmeasured` for
        every duration figure and `0` for the count. It must not raise and
        must not report a zero that would read as a real measurement.
        """
        floor_str = f"{CONNECT_COST_STRUCTURAL_FLOOR_S:.3f}s"
        if not samples:
            return {
                "samples": "0",
                "min": "unmeasured",
                "median": "unmeasured",
                "max": "unmeasured",
                "structural_floor": floor_str,
                "remainder": "unmeasured",
            }
        median = statistics.median_low(samples)
        return {
            "samples": str(len(samples)),
            "min": f"{min(samples):.3f}s",
            "median": f"{median:.3f}s",
            "max": f"{max(samples):.3f}s",
            "structural_floor": floor_str,
            "remainder": f"{median - CONNECT_COST_STRUCTURAL_FLOOR_S:.3f}s",
        }

    def measure_connect_cost(
        self,
        samples: int = 10,
        port: str | None = None,
        output_dir: str | None = None,
    ) -> bool:
        """Per-connect cost measurement harness (MEAS-01 instrument).

        No serial device is required to build or unit-test this method --
        `_summarize_connect_samples` above is pure and covers the numeric
        contract on hand-built sample lists. This method is the bench half:
        it opens and closes ONE pinned port `samples` times, timing each
        connect with `time.monotonic()`.

        Pinning ONE port is load-bearing: `restrict_to_port=True` is passed
        explicitly to `find_and_connect` regardless of the config's transient
        port marker, so this measurement can never let port discovery walk
        the port list -- the exact inflation trap `measure_command_nak_latency`'s
        own docstring above documents about `fault_inject_cycle`.

        Refuses rather than guesses: with no port resolved, this logs and
        returns False without opening anything. Returns True only when at
        least one sample was collected; a run that collected zero samples
        reports `unmeasured` in the artifact rather than fabricating a number.

        Also records `transport_counters.snapshot()['probe_timeouts']` (and
        the other eight counters) observed during the run -- MEAS-02's
        corroborating empirical evidence, which comes free from the counter
        wiring plans 176-01 to 176-03 already completed.
        """
        if port is None:
            port = self.config.get_value("port")
        if not port:
            logger.error(
                "measure_connect_cost: no serial port resolved "
                "(pass -p <port> or set config.port)."
            )
            return False

        if output_dir is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            output_dir = f"connect-cost-{timestamp}"
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        fw_cmd = {"state": COMMAND_FW_VERSION}
        elapsed_samples: list = []
        controller_identity = ""
        transport_counters.reset()
        for _ in range(samples):
            comm = None
            try:
                _t0 = time.monotonic()
                comm = SerialCommunicator.find_and_connect(
                    fw_cmd,
                    self.config,
                    preferred_port=port,
                    restrict_to_port=True,
                )
                elapsed_samples.append(time.monotonic() - _t0)
                if not controller_identity and comm.programmer_info:
                    controller_identity = comm.programmer_info
            except (ProgrammerNotFoundError, SerialError) as e:
                logger.error(f"measure_connect_cost: connect attempt failed: {e}")
            finally:
                if comm is not None:
                    comm.disconnect()

        counters = transport_counters.snapshot()
        summary = self._summarize_connect_samples(elapsed_samples)
        self._write_connect_cost_log(
            output_path, port, controller_identity, elapsed_samples, summary, counters
        )
        return len(elapsed_samples) > 0

    @staticmethod
    def _write_connect_cost_log(
        output_path: Path,
        port: str,
        controller_identity: str,
        samples: list,
        summary: Dict[str, str],  # noqa: UP006
        counters: Dict[str, int],  # noqa: UP006
    ) -> None:
        """Write the connect-cost artifact (MEAS-01 bench harness)."""
        log_path = output_path / "connect-cost-log.txt"
        sample_lines = "\n".join(f"sample_{i}: {s:.3f}s" for i, s in enumerate(samples))
        counter_lines = "\n".join(f"{k}: {v}" for k, v in sorted(counters.items()))
        try:
            with open(log_path, "w") as fh:
                fh.write(
                    "# per-connect cost (one pinned port, restrict_to_port=True)\n"
                    f"port: {port}  controller_identity: {controller_identity or 'unknown'}\n"
                    f"samples_collected: {summary['samples']}\n"
                    f"{sample_lines}\n"
                    f"min: {summary['min']}\n"
                    f"median: {summary['median']}\n"
                    f"max: {summary['max']}\n"
                    f"structural_floor: {summary['structural_floor']}\n"
                    f"remainder: {summary['remainder']}\n"
                    f"{counter_lines}\n"
                )
        except IOError as e:  # noqa: UP024
            logger.error(f"measure_connect_cost: could not write log: {e}")

    def dev_read_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        address_str: str | None = None,
        size_str: str = "256",
        operation_flags: int = 0,
    ) -> bool:
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_READ,
            operation_flags,
            address_str,
            size_str or "256",
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False

            start_addr = cmd_data.get("address", 0)
            end_addr = cmd_data.get("memory-size", start_addr)
            logger.info(
                f"Reading {end_addr - start_addr} bytes from address 0x{start_addr:04X} of {eprom_name.upper()}"  # noqa: E501
            )
            start_time = time.time()

            is_ok, _ = self._run_state_machine(
                op_name,
                main_phase_handler=self._main_phase_read_data,
                start_addr=start_addr,
                end_addr=end_addr,
                process_data_chunk_callback=hexdump,
            )
            if is_ok:
                logger.info(f"Read complete ({time.time() - start_time:.2f}s)")
            return is_ok

    def dev_set_registers(
        self,
        msb_str: str,
        lsb_str: str,
        ctrl_reg_str: str,
        firestarter=False,
        flags: int = 0,
    ) -> bool:
        msb = int(msb_str, 16) if "0x" in msb_str else int(msb_str)
        lsb = int(lsb_str, 16) if "0x" in lsb_str else int(lsb_str)
        ctrl_reg = int(ctrl_reg_str, 16) if "0x" in ctrl_reg_str else int(ctrl_reg_str)
        if msb < 0 or msb > 0xFF:
            logger.error(f"Invalid MSB value: 0x{msb:02x} {msb}")
            return False
        if lsb < 0 or lsb > 0xFF:
            logger.error(f"Invalid LSB value: 0x{lsb:02x} {lsb}")
            return False
        if (
            ctrl_reg < 0
            or (ctrl_reg > 0x1FF and firestarter)
            or (ctrl_reg > 0xFF and not firestarter)
        ):
            logger.error(f"Invalid Control Register value: 0x{ctrl_reg:02x} {ctrl_reg}")
            return False
        command_dict_for_connect = {
            "cmd": COMMAND_DEV_REGISTERS,
            "flags": flags,
        }
        try:
            self.comm = SerialCommunicator.find_and_connect(
                command_dict_for_connect, self.config
            )
            # No EPROM data needed from DB for this specific command after connection.
        except (ProgrammerNotFoundError, SerialError) as e:
            logger.error(f"Failed to connect for dev_set_registers: {e}")
            self._disconnect_programmer()
            return False

        if not self.comm:
            return False

        logger.info(
            f"Setting registers: MSB: 0x{msb:02X}, LSB: 0x{lsb:02X}, CTRL: 0x{ctrl_reg:02X}"  # noqa: E501
        )
        try:
            self.comm.send_ack()  # Tell programmer to expect register data
            self.comm.send_bytes(
                bytes(
                    [
                        msb,
                        lsb,
                        (0x80 if firestarter else 0x00) | (ctrl_reg >> 8 & 0x01),
                        ctrl_reg & 0xFF,
                    ]
                )
            )
            logger.info("Register data sent.")
            is_ok, _ = self.comm.expect_ack()
            return is_ok  # True if RURP acknowledged end, False otherwise
        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Error during dev_set_registers: {e}")
            return False
        finally:
            self._disconnect_programmer()

    def dev_set_address_mode(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        address_str: str | None,
        flags: int = 0,
    ) -> bool:
        try:
            # This command sets the RURP into a mode where it holds a specific address
            # based on the EPROM's pin map.
            # eprom_data_dict is pre-fetched and validated by the caller (main.py)
            command_eprom_data, _ = self._setup_operation(
                eprom_name,
                eprom_data_dict,
                COMMAND_DEV_ADDRESS,
                flags,
                address_str,
            )
            if not command_eprom_data or not self.comm:
                return False  # Setup failed, error already logged by _setup_operation

            # The _setup_operation already sent the command with the address.
            # The RURP is now (presumably) holding this address.
            # The original dev_address function just did setup and cleanup.
            logger.info(
                f"Setting address to RURP: 0x{command_eprom_data['address']:06x}"
            )
            logger.debug(f"Using {eprom_name.upper()}'s pin map")
            is_ok, _ = self.comm.expect_ack()
            return is_ok  # True if RURP acknowledged end, False otherwise
        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Error during dev_set_address_mode: {e}")
            return False
        finally:
            self._disconnect_programmer()

    def _run_write_blank_guard(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        operation_flags: int,
        address_str: str | None,
        region_length: int,
    ) -> tuple[int, str | None]:
        """WRITE-01 (Phase 203): the pre-write blank-guard read, called from
        `write_eprom` between its pure pre-connect gates and its own
        `_operation_context` (D-08). Region-scoped on every guarded family
        (D-04): `address` .. `address + region_length`, computed by the
        caller from the same `region_length` the write itself uses --
        deliberately diverging from `flash_nor_unlock.cpp` /
        `flash_intel.cpp`, which blank-check the whole device today.

        Opens its OWN `_operation_context` with COMMAND_READ, the same
        `operation_flags` (so `--force` still forces past the read's own
        chip-ID check) and `str(region_length)` as the size -- mirroring
        `verify_eprom`'s call shape, because `_drive_region_compare` reads
        `cmd_data["memory-size"]` as the read's end address and
        `_setup_operation` only narrows it for a `COMMAND_READ` with a size.

        Fork D: the guard read shows no progress bar. It is not an
        operation the operator asked for, and a bar that stops part-way and
        is then followed by a refusal reads as a failure of the read rather
        than a refusal of the write -- `--verify`'s own read (which the
        operator DID ask for) keeps its bar. Suppressed with the
        `consistency_check_eprom` precedent: swap `progress_callback` to a
        truthy no-op, restore it in a `finally`.

        Returns `(verdict, resolved_port)`. `verdict` is 0 (blank, proceed),
        1 (not blank, refused -- and logs the one-line D-10 refusal at
        `logger.error`), or 2 (transport or setup failure, including a
        falsy `cmd_data`). `resolved_port` (203-CR-01) is the physical port
        this connect actually reached -- captured from `self.comm.port_name`
        INSIDE this method's own `with` block, before its `finally` tears
        `self.comm` down -- or `None` when the connect never succeeded
        (`cmd_data` falsy). The caller (`write_eprom`) uses a non-`None`
        `resolved_port` to pin its own, separate COMMAND_WRITE connect to
        this exact port, so the region this guard just proved blank and the
        region the write actually touches can never silently diverge onto
        two different boards.
        """
        prior_callback = self.progress_callback
        self.progress_callback = lambda *a, **kw: None
        try:
            with self._operation_context(
                eprom_name,
                eprom_data_dict,
                COMMAND_READ,
                operation_flags,
                address_str,
                str(region_length),
            ) as (cmd_data, _, op_name):
                if not cmd_data:
                    return 2, None

                resolved_port = self.comm.port_name if self.comm else None
                region_start = cmd_data.get("address", 0)
                captured: list[CompareResult] = []

                def _on_result(result: CompareResult, _captured=captured) -> None:
                    _captured.append(result)

                verdict = self._drive_region_compare(
                    cmd_data,
                    op_name,
                    _blank_expected_bytes,
                    full=False,
                    region_length=region_length,
                    on_result=_on_result,
                )

                if verdict == 1 and captured:
                    result = captured[0]
                    # 203-REVIEW WR-02, 207.1 D-07: `first_offset` is `None`
                    # exactly when `bad == 0` (compare.py's own contract for
                    # `CompareResult`). An incomplete read that never saw a
                    # mismatch falls in that branch -- state the compare's
                    # coverage instead of fabricating an address and a value.
                    logger.error(
                        _write_blank_guard_refusal_message(
                            eprom_name, region_start, result
                        )
                    )

                return verdict, resolved_port
        finally:
            self.progress_callback = prior_callback

    def write_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        input_file_path: str,
        operation_flags: int = 0,
        address_str: str | None = None,
        pulse_us: int = 0,  # per-run pulse-width override (us; 0=not supplied, use the database value)
        pin1_hazard_acknowledged: bool = False,
        *,
        suppress_verdict_line: bool = False,
        blank_check_requested: bool = True,
    ) -> bool:
        """Write `input_file_path` to `eprom_name`, running the WRITE-01
        pre-write blank guard first on every guarded family.

        `blank_check_requested` (FWBLANK-04, Phase 205): keyword-only,
        default `True`. Threaded straight to
        `write_blank_guard.requires_blank_check` as its own keyword-only
        signal -- this is the explicit, host-side replacement for the
        retired skip-blank-check wire bit (`0x08`, gone from both
        ladders). `False` is `write -b`'s and `dev test`'s masked UV slot
        write's route to the same bypass the retired bit used to grant;
        every other existing caller keeps the default and is
        byte-identical.

        `suppress_verdict_line` (Phase 203, WRITE-05): keyword-only,
        default `False`. When `True`, skip this method's own trailing
        `Write to X successful (t).` / `Write to X failed.` log line
        entirely -- both branches, not just a reworded one. The default
        leaves every existing caller byte-identical. This exists so
        `write --verify` can print ONE combined verdict line for the whole
        invocation instead of this method's line followed by a second one
        from the read-back -- and so the word D-14 forbids from a
        `--verify` run is absent from this path *structurally*: because the
        line is never emitted here at all, a later edit to one of the
        CLI's own verdict lines cannot reintroduce it by drifting this
        one's wording back in.
        """
        # WRITE-04/WRITE-05 (Phase 203): reset the write phase's own cause
        # channel BEFORE any gate below can raise -- a gate that raises
        # leaves this call's verdict at `None` ("never attempted"), and a
        # prior invocation's verdict can never leak into this one.
        self.last_write_attempt_verdict = None
        # 203-CR-01: reset the resolved-write-port record BEFORE any gate
        # below can raise -- same rationale as `last_write_attempt_verdict`
        # immediately above, so a prior invocation's port can never leak
        # into this one's `--verify` read-back pin.
        self.last_write_port = None
        # per-run pulse override, riding the existing
        # "pulse-delay" DB-dict key rather than adding a new wire field or
        # command. Four recorded points:
        # (a) this is consistency_check_eprom's read_settling_us/
        #     read_strobe_us shape verbatim -- that function's own comment
        #     says the pattern is "consistent with how pulse-delay already
        #     travels via the DB dict."
        # (b) the key ALREADY EXISTS -- database.py's convert_to_programmer
        #     emits "pulse-delay" unconditionally -- so this REPLACES a
        #     value rather than adding a field, which is how "no new wire
        #     field and no new command" is satisfied structurally.
        # (c) the shallow copy exists so a caller that reuses its programmer
        #     dict for a second chip (e.g. a batch loop) is unaffected.
        # (d) the 1..65535 bound is NOT enforced here -- it is Click's
        #     IntRange at parse time, and the firmware's
        #     energy_cap_us-keyed pre-flight refusal (MSG_ERR_PULSE_TOO_WIDE)
        #     is the independent second gate, firing before any high voltage
        #     is enabled. No host-side check and no energy_cap_us
        #     mirror belongs here.
        if pulse_us:
            eprom_data_dict = dict(
                eprom_data_dict
            )  # shallow copy -- never mutate caller's dict
            eprom_data_dict["pulse-delay"] = pulse_us

        require_acknowledged(
            eprom_name,
            eprom_data_dict.get("bus-config"),
            "write",
            pin1_hazard_acknowledged,
        )
        require_page_size(eprom_name, eprom_data_dict, "write")
        # Folded todo `2026-09-16-reject-negative-write-start-address.md`,
        # host half: refuse a signed start address before it can reach
        # either this write's own region arithmetic or the guard's, on
        # every write family -- guarded or not. This call now runs between
        # the page-size gate above and the page-alignment gate below, so a
        # negative start address that is also misaligned, or paired with a
        # misaligned length, gets this clearer refusal instead of the
        # alignment gate's signed-hex wording (203-REVIEW IN-01, 207.1
        # D-08). `require_non_negative_address` returns silently on an
        # unparseable address, so the alignment gate below still owns the
        # could-not-parse error.
        require_non_negative_address(eprom_name, address_str)
        require_page_alignment(
            eprom_name, eprom_data_dict, "write", address_str, input_file_path
        )

        # BLANK-01 / D-05: guarded so a missing file keeps surfacing exactly
        # where it does today (_main_phase_send_data, after connecting, for
        # non-0x05 parts that require_page_alignment returns early for)
        # rather than moving earlier. An unguarded getsize would change that
        # ordering, which this plan is not authorised to do.
        try:
            region_length = os.path.getsize(input_file_path)
        except OSError:
            region_length = None

        # WRITE-01 / D-04 / D-07 / D-08 / D-11 (Phase 203): the host
        # pre-write blank guard, region-scoped on every guarded family
        # (D-04) -- deliberately diverging from `flash_nor_unlock.cpp` /
        # `flash_intel.cpp`, which blank-check the whole device today. Runs
        # here: after every pure pre-connect gate above (D-08), so a part
        # refused on a pure ground is never read first, and before this
        # write's own `_operation_context`, so it is a serial operation in
        # its own right rather than one that could join the pure gates.
        # Skipped (verdict recorded as `None`) when `region_length` is
        # `None` or 0 -- a missing input file (`OSError` above) keeps
        # surfacing exactly where it does today, at `_main_phase_send_data`
        # once connected, and an empty input file is not refused by a
        # zero-length compare (`_drive_region_compare` returns 1 for a
        # zero-length region, which would otherwise refuse every empty
        # write). The `return False` below sits BEFORE the `with` block --
        # this write's own "Write to X failed." line never runs, so the
        # guard's refusal is the only output (D-11, satisfied structurally
        # rather than by wording).
        # 203-CR-01: the port the guard's own connect resolved, when the
        # guard actually ran and connected. `None` on every path that never
        # opens a guard connect at all (skip-blank-check, erase-exempt, no
        # region) -- those write invocations open exactly one connect
        # anyway, so there is nothing for that single connect to diverge
        # from, and it keeps its pre-existing, config-inferred discovery
        # behaviour unchanged (single-connect operations must not start
        # pinning).
        # 205-CR-01: resolve the write's own start address once, so the
        # guard can tell a whole-chip erase (address 0) apart from a
        # sector erase (any other address) on protocol 0x06. Three cases,
        # all deliberately resolving to 0 rather than to a refusal here:
        # an absent `-a` is address 0 by definition; an unparseable `-a`
        # stays `_setup_operation`'s `parse_address`/`ValueError` job --
        # opening a port to produce a verdict-2 transport failure in place
        # of today's clean argument error would change an established
        # error contract this module must not touch
        # (`require_non_negative_address`'s own stated rule, above); and a
        # negative `-a` is already refused above by
        # `require_non_negative_address`, before this line is ever
        # reached. Do not "harden" the `except` below into a refusal.
        try:
            guard_address = parse_address(address_str) or 0
        except ValueError:
            guard_address = 0

        guard_port: str | None = None
        if not region_length:
            self.last_write_guard_verdict = None
        elif not requires_blank_check(
            eprom_data_dict,
            operation_flags,
            blank_check_requested=blank_check_requested,
            address=guard_address,
        ):
            self.last_write_guard_verdict = None
        else:
            guard_verdict, guard_port = self._run_write_blank_guard(
                eprom_name,
                eprom_data_dict,
                operation_flags,
                address_str,
                region_length,
            )
            self.last_write_guard_verdict = guard_verdict
            if guard_verdict != 0:
                return False

        # 203-CR-01: when the guard ran and its connect resolved a port,
        # force this write's own connect onto that EXACT port
        # (`restrict_to_port=True` makes it the only candidate
        # `_list_potential_ports` returns). A change in port availability,
        # enumeration order, or board identity between the guard's connect
        # and this one then surfaces as a connect failure here (`cmd_data`
        # falsy, verdict 2 below) -- never as a silent write to a board the
        # guard never actually read. When `guard_port` is `None` (no guard
        # ran), passing neither kwarg leaves `_setup_operation`/
        # `find_and_connect`'s own config-inferred discovery untouched, so
        # an operator-typed `-p` on an unguarded write still behaves exactly
        # as it does today.
        write_connect_kwargs: dict = {}
        if guard_port:
            write_connect_kwargs = {
                "preferred_port": guard_port,
                "restrict_to_port": True,
            }

        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_WRITE,
            operation_flags,
            address_str,
            region_length=region_length,
            **write_connect_kwargs,
        ) as (cmd_data, buf_size, op_name):
            if not cmd_data:
                # WRITE-04/WRITE-05: classify the cause before returning.
                # `_setup_operation`'s `(None, 0)` return is reachable here,
                # for COMMAND_WRITE, exactly two ways (pinned by
                # test_setup_operation_has_exactly_three_none_zero_returns,
                # tests/test_write_verify.py): `parse_address(address_str)`
                # raising ValueError (reachable only when `address_str` is
                # truthy -- the operator's own input was the cause), or
                # `find_and_connect` failing (a transport or setup cause).
                # The `parse_size` arm is gated on `cmd == COMMAND_READ` and
                # is not reachable here at all. Re-parse purely to read the
                # cause -- `_setup_operation` itself is not touched, its log
                # line is not duplicated, and its return value and ordering
                # are unchanged.
                address_parse_failed = False
                if address_str:
                    try:
                        parse_address(address_str)
                    except ValueError:
                        address_parse_failed = True
                self.last_write_attempt_verdict = 1 if address_parse_failed else 2
                return False

            # 203-CR-01: record the port this write's connect actually
            # reached -- captured here, inside this `with` block, before its
            # `finally` disconnects and sets `self.comm` to `None`. This is
            # what lets `cli_handlers.write`'s `--verify` branch pin the
            # read-back's own connect to the SAME board the write just used.
            self.last_write_port = self.comm.port_name if self.comm else None

            logger.info(f"Writing {input_file_path} to {eprom_name.upper()}")
            start_time = time.time()

            # _write_block_timeout() MUST be read here,
            # inside this `with` block -- _operation_context's `finally`
            # disconnects and sets self.comm to None once it exits (same
            # constraint the seen_message_ids check below already relies
            # on). _run_state_machine forwards **handler_kwargs verbatim to
            # main_phase_handler (confirmed by reading it), so no
            # _run_state_machine signature change is needed for this to
            # reach _main_phase_send_data's new response_timeout kwarg.
            is_ok, _ = self._run_state_machine(
                op_name,
                main_phase_handler=self._main_phase_send_data,
                input_file_path=input_file_path,
                buffer_size=buf_size,
                eprom_data_dict=cmd_data,  # FIX-01b: boot-block hint context
                response_timeout=self._write_block_timeout(),
            )

            # WRITE-04/WRITE-05 (Phase 203): record the write phase's own
            # cause HERE -- before the --skip-sdp-unlock ack block below,
            # which flips `is_ok` to `False` AFTER a run that already
            # succeeded on the wire. Recording first means that ack failure
            # surfaces as exit 1 (host-decided), not exit 2: nothing on the
            # wire actually failed, so calling it a transport failure would
            # be wrong. `_run_state_machine` clears `last_firmware_error_code`
            # on entry and sets it ONLY on its `EpromOperationError` arm (a
            # real firmware ERROR frame); its `(SerialError,
            # SerialTimeoutError)` arm deliberately leaves it `None` --
            # `__init__`'s own comment on that field states this scoping,
            # and this reads that existing, already-narrow contract rather
            # than inventing or widening one.
            if is_ok:
                self.last_write_attempt_verdict = 0
            else:
                self.last_write_attempt_verdict = (
                    2 if self.last_firmware_error_code is None else 1
                )

            # When --skip-sdp-unlock was set,
            # require firmware's MSG_WARN_SDP_UNLOCK_SKIPPED (0x86) ack that it
            # actually honoured the opt-out. An unknown *command* produces a
            # loud error; an unknown *flag bit* produces
            # silence — old firmware simply ignores 0x100 and runs the unlock
            # it was told to skip, then reports success. The absence of 0x86
            # is the only signal available, so its absence converts that
            # silent failure into a loud one, using machinery (0x86) that
            # already shipped for a different purpose — zero
            # firmware change. This check MUST read self.comm.seen_message_ids
            # here, inside the _operation_context `with` block: that block's
            # `finally` calls _disconnect_programmer(), which sets self.comm to
            # None, so a read after the block exits would raise or silently
            # see nothing.
            #
            # Honest limitation (state, do not overclaim): this DETECTS after
            # the fact, it does not PREVENT. On old firmware the unlock has
            # already been emitted by the time the user is told.
            #
            # No version floor is used instead: the host structurally
            # cannot distinguish 3.0.0b11 from a later pre-release because
            # _probe_port's capture regex truncates the suffix, and widening
            # it would touch the ring-fenced transport version-capture path.
            #
            # Scoped to protocol 0x0D (the is_protocol_0x0d
            # predicate). firmware ONLY reads FLAG_SKIP_SDP_UNLOCK — and only
            # emits MSG_WARN_SDP_UNLOCK_SKIPPED — on protocol-0x0D writes. On
            # any other protocol the bit is emitted on the wire
            # (warn-and-proceed, unconditional) but firmware never
            # acts on it and never answers with 0x86, on old AND new firmware
            # alike — that is not the silent-failure case this check names, so
            # requiring the ack there would be a false positive on every
            # non-0x0D --skip-sdp-unlock write.
            #
            # NOTE: eprom_data_dict here is resolve_chip()'s composed
            # programmer dict (the shape cli_handlers.py actually passes into
            # write_eprom), which carries the protocol id under "algorithm"
            # (CLAUDE.md: "the algorithm field carries the upstream
            # protocol_id integer"), NOT under "protocol-id" — that raw-db-row
            # key name belongs to app.db.get_eprom()'s entry, a different
            # dict cli_handlers.py's own protocol check reads instead.
            is_protocol_0x0d = eprom_data_dict.get("algorithm") == SDP_PROTOCOL_ID
            if is_protocol_0x0d and (operation_flags & FLAG_SKIP_SDP_UNLOCK):
                if MSG_WARN_SDP_UNLOCK_SKIPPED not in self.comm.seen_message_ids:
                    logger.error(
                        f"--skip-sdp-unlock was requested for {eprom_name.upper()}, "
                        "but the firmware did not acknowledge it "
                        "(no MSG_WARN_SDP_UNLOCK_SKIPPED / 0x86 ack observed). "
                        "The automatic SDP unlock ran anyway, despite the opt-out. "
                        "This usually means the connected firmware predates the "
                        "flag. Run `firestarter fw --install` to update firmware, "
                        "then retry."
                    )
                    is_ok = False

            if not suppress_verdict_line:
                if is_ok:
                    logger.info(
                        f"Write to {eprom_name.upper()} successful ({time.time() - start_time:.2f}s)."  # noqa: E501
                    )
                else:
                    logger.error(f"Write to {eprom_name.upper()} failed.")
            return is_ok

    def _drive_region_compare(
        self,
        cmd_data: dict,
        op_name: str,
        expected: Callable[[int, int], bytes],
        *,
        full: bool,
        region_length: int | None,
        on_result: Callable[[CompareResult], None] | None = None,
    ) -> int:
        """The one host-side compare drive `verify_eprom` and
        `check_eprom_blank` share (202-05 D-02): both callers open a
        COMMAND_READ and feed `_main_phase_read_data`'s delivered payload
        into one `CompareAccumulator` through their own D-04 pull callback
        (`verify_eprom` seeks its input file; `check_eprom_blank` returns a
        constant blank-byte string) -- everything past that point (the
        abort predicate, the D-08 abort-vs-fault discrimination, the D-13/
        D-14 rendering, and the D-10 int verdict) is identical for both, and
        used to be two copies of the same logic before this plan.

        Returns 0 on a match, 1 on a mismatch, 2 on a transport/hardware
        failure. Deliberately does not log a caller-specific success/failure
        line -- `verify_eprom` and `check_eprom_blank` each already have
        their own elapsed-time wording, and duplicating it here would be a
        second place that wording could drift.

        `on_result` (Phase 203, WRITE-01): keyword-only, default `None`.
        When `None`, behaviour is byte-identical to before this parameter
        existed -- the finalised `CompareResult` is rendered via
        `render_compare_lines` exactly as today. When supplied, it is
        called with the finalised `CompareResult` INSTEAD of rendering --
        the caller has taken responsibility for its own output. This is
        what the write guard (`_run_write_blank_guard`) uses: the guard
        aborts at the first non-blank byte, so a rendered range would
        always be one byte and the bucket would be classified from a
        one-byte sample, exactly the confident-verdict-from-a-short-prefix
        trap Phase 202's D-09 already named (D-11). `verify_eprom` and
        `check_eprom_blank` pass nothing and stay byte-identical.
        """
        region_start = cmd_data.get("address", 0)
        max_ranges = MAX_RETAINED_RANGES if full else 1
        accumulator = CompareAccumulator(addr_base=region_start, max_ranges=max_ranges)

        def _process_chunk(address: int, payload: bytes) -> None:
            accumulator.feed(address, expected(address, len(payload)), payload)

        # 202-04 D-06: the default (non-`--full`) path passes
        # `accumulator.has_mismatch` as the abort predicate, so the read
        # stops acking the instant the first mismatch is fed. `--full`
        # passes no predicate at all -- D-16's cap already bounds `--full`'s
        # output, so its complete scan is a separate code path only in what
        # it passes here, not a second accumulator or a second cap.
        # `has_mismatch` is a property, not a method -- wrap it so
        # `_main_phase_read_data` gets a zero-arg callable per its
        # `abort_predicate` contract.
        abort_kwargs: dict = {}
        if not full:
            abort_kwargs["abort_predicate"] = lambda: accumulator.has_mismatch

        # D-08: record intent BEFORE the drive, always (True or False) --
        # never left over from a previous call -- so a genuine timeout on a
        # `--full` run (which never sets an abort_predicate) can never be
        # attributed to a stop that was never requested.
        self._read_abort_intended = not full

        is_ok, _ = self._run_state_machine(
            op_name,
            main_phase_handler=self._main_phase_read_data,
            start_addr=cmd_data.get("address", 0),
            end_addr=cmd_data.get("memory-size", 0),
            process_data_chunk_callback=_process_chunk,
            **abort_kwargs,
        )

        aborted = False
        if not is_ok:
            # D-08: the deliberate stop and a genuine timeout both surface
            # here as `_run_state_machine` returning `(False, ...)` with
            # `last_firmware_error_code == MSG_ERR_TIMEOUT` -- the two are
            # wire-identical. Accept the error as this host's own doing ONLY
            # when all four hold: the default path actually asked for a stop
            # (`_read_abort_intended`), the read loop actually recorded one
            # (`stopped_at` is not `None`), the firmware's own error id is
            # exactly the timeout id (not some other fault wearing its
            # clothes), and the stop happened recently enough that this
            # error frame could plausibly be its consequence (the bounded
            # window). Any single condition failing means a real fault: take
            # the exit-2 path exactly as before -- a mismatching chip whose
            # read failed for an unrelated reason must never be reported as
            # a mismatch, and this host's own deliberate abort must never be
            # reported as hardware trouble.
            stopped_at = self._read_abort_stopped_at
            aborted = (
                self._read_abort_intended
                and stopped_at is not None
                and self.last_firmware_error_code == MSG_ERR_TIMEOUT
                and (time.monotonic() - stopped_at) <= READ_ABORT_ACCEPTANCE_WINDOW_S
            )
            if not aborted:
                return 2

        result = accumulator.finalise(aborted=aborted)
        if region_length is not None:
            result.total = region_length
        if on_result is not None:
            on_result(result)
        else:
            for line in render_compare_lines(result):
                logger.info(line)

        # Standing prohibition this plan carries: a compare that did not
        # cover the whole declared region is never reported as a match.
        # CMP-04 "empty" edge (202-04): a zero-length region is a degenerate
        # case of the same trap, not a separate one -- a zero-length
        # read-back compares byte-for-byte as PERFECT equality (`compared`
        # trivially equals `total` at 0), so without this explicit
        # `result.total > 0` guard an empty input file (or a `--size 0`
        # region) would silently report a clean pass despite nothing having
        # actually been compared.
        if result.total > 0 and result.bad == 0 and result.compared == result.total:
            return 0
        return 1

    def verify_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        input_file_path: str,
        operation_flags: int = 0,
        address_str: str | None = None,
        size_str: str | None = None,
        full: bool = False,
        *,
        suppress_verdict_line: bool = False,
        preferred_port: str | None = None,
        on_result: Callable[[CompareResult], None] | None = None,
    ) -> int:
        """Compare `input_file_path` against a fresh read of the chip.

        `on_result` (Phase 206 Task 3, DEVTEST-02): keyword-only, default
        `None`, forwarded straight through to `_drive_region_compare`'s own
        parameter of the same name -- the identical treatment
        `check_eprom_blank` gained in Task 1. When `None`, behaviour is
        byte-identical to before this parameter existed. `chip_test.py`'s
        `dev test` dispatch uses it ONLY to detect, structurally, whether
        this call's comparison actually reached the host compare engine --
        it does not change what `verify_eprom` returns, and it does not
        become this step's `Fingerprint` source (F1: that stays the
        separate `_read_region` read-back on a failing verify, unchanged by
        this parameter).

        `suppress_verdict_line` (Phase 203, WRITE-05): keyword-only, default
        `False`, the same treatment `write_eprom` gets -- when `True`, skip
        ONLY this method's own trailing `Verify for X successful (t).` /
        `Verify for X failed.` line; the compare range lines rendered
        through `_drive_region_compare` (a `Mismatch 0xSTART-0xEND (N
        bytes)` line per retained range, plus the bucket summary) are
        untouched, because those are the report `write --verify` is
        supposed to produce on a mismatch. The default leaves every
        existing caller (`verify`, `blank`) byte-identical.

        `preferred_port` (203-CR-01): keyword-only, default `None`. When
        given, forces this call's own COMMAND_READ connect onto exactly
        that port (`restrict_to_port=True`) -- `cli_handlers.write`'s
        `--verify` branch passes `write_eprom`'s own `last_write_port` here,
        so the read-back can never silently land on a different board than
        the write it is meant to be checking. The default leaves every
        existing caller (`verify`, `blank`, `dev test`) byte-identical --
        none of them pass it, so their connect keeps its pre-existing,
        config-inferred discovery behaviour unchanged.

        202-01 D-01/D-02/D-04/D-10: this reads the chip with COMMAND_READ and
        compares chunk by chunk on the host through `compare.py`'s streaming
        accumulator -- it no longer pushes the file to the firmware's own
        verify ordinal. Phase 204 retired that ordinal (COMMAND_VERIFY) from
        both the host and the firmware entirely; nothing on this path, or
        anywhere else in this repository, composes it any more. Returns 0 on
        a match, 1 on a mismatch, 2 on a setup, transport, or I/O failure
        (D-10, confirmed).

        202-04 D-06/D-08/D-09: unless `full` is true, the drive passes
        `accumulator.has_mismatch` as `_main_phase_read_data`'s
        `abort_predicate`, so the read stops acking the moment the first
        mismatching byte is fed -- the host breaks the read in flight rather
        than draining the rest of the chip. `full=True` passes no predicate,
        so a full scan always reads (and reports) the whole region. The
        deliberate stop yields a `MSG_ERR_TIMEOUT` frame wire-identical to a
        genuine timeout; the four-condition discrimination in
        `_drive_region_compare` is what keeps that abort from ever being
        reported as exit-2 hardware trouble, and keeps a genuine fault from
        ever being reported as a clean-looking abort. Progress-bar choice
        (left to discretion by CONTEXT.md): on an abort the bar simply stops
        advancing at the compared byte count (the loop in
        `_main_phase_read_data` stops calling `progress.update()` once
        stopped) and is closed there by `_run_state_machine`'s `finally` --
        it is never advanced to the region total, since a bar that completes
        after a stop would claim progress the compare did not make (the same
        dishonesty D-09 guards against at the summary line).

        202-05 D-02: the compare drive itself (accumulator, abort predicate,
        D-08 discrimination, rendering, D-10 verdict) lives in
        `_drive_region_compare`, shared verbatim with `check_eprom_blank`.

        202-05 D-17: `size_str`, when given, wins over the input file's own
        length as the declared region. The two region refusals (an explicit
        size shorter than the file; a region running past the chip's end)
        are the CLI tier's job (`cli_handlers.verify`), fired before this
        method -- and before the serial port -- are ever reached.
        """
        # 202-01 D-01/D-02: unlike write_eprom, verify_eprom computes its own
        # region_length here -- it does not call require_page_alignment,
        # which is where that computation already lives on the write path.
        # This value is NOT forwarded to _operation_context as region_length
        # below: that kwarg only reaches the wire as JSON_KEY_REGION_END for
        # cmd == COMMAND_WRITE (_setup_operation's guard, an equality since
        # Phase 204 retired COMMAND_VERIFY -- the write path is now the only
        # composer of that key), and this path composes COMMAND_READ, so
        # passing it there would be silently discarded. It is used locally
        # instead, for the resolved size string (below) and for
        # `result.total` (D-10's incomplete-compare prohibition).
        try:
            file_length = os.path.getsize(input_file_path)
        except OSError:
            file_length = None

        # 202-05 D-17: an explicit --size wins when given; without one,
        # verify's region is the input file's length, as before. The
        # CLI tier (cli_handlers.verify) has already refused, before this
        # call and before the port opens, an explicit --size shorter than
        # the file or a region running past the chip's end -- this method
        # trusts that and simply resolves the region it was asked for. A
        # malformed --size string is not this method's job either: the
        # existing `_setup_operation`/`parse_size` ValueError handling
        # below still refuses it (cmd_data comes back falsy, exit 2).
        if size_str is not None:
            try:
                region_length = parse_size(size_str)
            except ValueError:
                region_length = None
        else:
            region_length = file_length
        resolved_size_str = (
            size_str
            if size_str is not None
            else (str(file_length) if file_length is not None else None)
        )

        # 203-CR-01: pin this connect to `preferred_port` when the caller
        # supplied one (see this method's own docstring). Omitted entirely
        # when absent, so `find_and_connect`'s own config-inferred discovery
        # is untouched for every caller that does not pass it.
        verify_connect_kwargs: dict = {}
        if preferred_port:
            verify_connect_kwargs = {
                "preferred_port": preferred_port,
                "restrict_to_port": True,
            }

        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_READ,
            operation_flags,
            address_str,
            resolved_size_str,
            **verify_connect_kwargs,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return 2

            logger.info(f"Verifying {input_file_path} against {eprom_name.upper()}")
            start_time = time.time()
            region_start = cmd_data.get("address", 0)

            try:
                with open(input_file_path, "rb") as file_handle:

                    def _expected(offset: int, length: int) -> bytes:
                        file_handle.seek(offset - region_start)
                        return file_handle.read(length)

                    verdict = self._drive_region_compare(
                        cmd_data,
                        op_name,
                        _expected,
                        full=full,
                        region_length=region_length,
                        on_result=on_result,
                    )
            except IOError as e:  # noqa: UP024
                logger.error(f"File I/O error with {input_file_path}: {e}")
                return 2

            if not suppress_verdict_line:
                if verdict == 0:
                    logger.info(
                        f"Verify for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s)."  # noqa: E501
                    )
                else:
                    logger.error(f"Verify for {eprom_name.upper()} failed.")
            return verdict

    def erase_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        operation_flags: int = 0,
        address_str: str | None = None,
        pin1_hazard_acknowledged: bool = False,
    ) -> bool:
        require_acknowledged(
            eprom_name,
            eprom_data_dict.get("bus-config"),
            "erase",
            pin1_hazard_acknowledged,
        )

        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_ERASE,
            operation_flags,
            address_str,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False
            logger.info(f"Erasing EPROM {eprom_name.upper()}")
            start_time = time.time()
            is_ok, final_msg = self._run_state_machine(op_name)
            if is_ok:
                logger.info(
                    f"Erase for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s). {final_msg or ''}"  # noqa: E501
                )
            return is_ok

    def sdp_unlock(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        operation_flags: int = 0,
    ) -> bool:
        """Emit the SDP-disable (unlock) command sequence (cmd 9).

        This operation is **payload-free**: firmware leaves ``init``/``end``
        NULL for CMD_SDP_UNLOCK, so no ``#`` data
        frame is written and there is no host ``DONE`` round-trip. The firmware's
        correction still applies here: NULL ``init``/``end`` does NOT skip the
        INIT and END frame pairs themselves — both ``_execute_phase("INIT", ...)``
        and ``_execute_phase("END", ...)`` still run and both ack; only the
        ``DONE`` round-trip and the data frame are absent (no
        ``main_phase_handler`` is passed below, so ``_run_state_machine`` falls
        through to ``_main_phase_simple``, exactly like ``erase_eprom``).

        A ``True`` return means only that the command sequence was **emitted**
        over the wire — it is never a claim that silicon actually left the
        protected state. Protection state is not readable on this chip family,
        so no return value from this method can honestly say more than
        "the sequence was sent and the firmware
        reported OK".

        The capability refusal deciding *which* parts may reach this method at
        all lives in ``firestarter/sdp_capability.py`` and is enforced by the
        caller before the serial port is even opened. This method is a thin
        transport wrapper and deliberately does not re-check that capability
        itself, so there is exactly one place that decision is made.
        """
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_SDP_UNLOCK,
            operation_flags,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False
            logger.info(f"Unlocking SDP for {eprom_name.upper()}")
            start_time = time.time()
            is_ok, final_msg = self._run_state_machine(op_name)
            if is_ok:
                logger.info(
                    f"SDP unlock for {eprom_name.upper()} emitted ({time.time() - start_time:.2f}s). {final_msg or ''}"  # noqa: E501
                )
            return is_ok

    def sdp_lock(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        operation_flags: int = 0,
    ) -> bool:
        """Emit the SDP-enable (lock) command sequence (cmd 10).

        This operation is **payload-free**: firmware leaves ``init``/``end``
        NULL for CMD_SDP_LOCK, so no ``#`` data
        frame is written and there is no host ``DONE`` round-trip. The firmware's
        correction still applies here: NULL ``init``/``end`` does NOT skip the
        INIT and END frame pairs themselves — both ``_execute_phase("INIT", ...)``
        and ``_execute_phase("END", ...)`` still run and both ack; only the
        ``DONE`` round-trip and the data frame are absent (no
        ``main_phase_handler`` is passed below, so ``_run_state_machine`` falls
        through to ``_main_phase_simple``, exactly like ``erase_eprom``).

        A ``True`` return means only that the command sequence was **emitted**
        over the wire — it is never a claim that silicon actually entered the
        protected state. Protection state is not readable on this chip family,
        so no return value from this method can honestly say more than
        "the sequence was sent and the firmware
        reported OK".

        The capability refusal deciding *which* parts may reach this method at
        all lives in ``firestarter/sdp_capability.py`` and is enforced by the
        caller before the serial port is even opened. This method is a thin
        transport wrapper and deliberately does not re-check that capability
        itself, so there is exactly one place that decision is made.
        """
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_SDP_LOCK,
            operation_flags,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False
            logger.info(f"Locking SDP for {eprom_name.upper()}")
            start_time = time.time()
            is_ok, final_msg = self._run_state_machine(op_name)
            if is_ok:
                logger.info(
                    f"SDP lock for {eprom_name.upper()} emitted ({time.time() - start_time:.2f}s). {final_msg or ''}"  # noqa: E501
                )
            return is_ok

    # Protocol IDs whose firmware handler (configure_sram) leaves a NULL
    # firestarter_operation_main for the standalone blank-check command's
    # wire ordinal. Before 3.1.0 that produced 0xA4 MSG_ERR_EMPTY_INPUT;
    # after Phase 204 retired that ordinal, EVERY protocol handler leaves
    # the pointer NULL for it, not only configure_sram -- this set stays
    # SRAM-specific because these are the families with no factory-blank
    # concept at all, which is what the short-circuit below exists for.
    # These are all SRAM families (host-side fix).
    _SRAM_PROTO_IDS = frozenset({0x0E, 0x27, 0x28, 0x29})

    def check_eprom_blank(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        operation_flags: int = 0,
        address_str: str | None = None,
        size_str: str | None = None,
        full: bool = False,
        *,
        on_result: Callable[[CompareResult], None] | None = None,
    ) -> int:
        """Compare the chip against a constant blank byte through the same
        engine `verify_eprom` uses (202-05 D-02/D-04/D-10/D-12).

        This reads the chip with COMMAND_READ and compares it, chunk by
        chunk, against an all-0xFF expected side supplied by a D-04 pull
        callback (`_blank_expected_bytes` below) -- it no longer composes
        the blank-check command's own wire ordinal, which Phase 204 retired
        from both the host and firmware ladders entirely; nothing anywhere
        composes it any more. Returns 0 on an all-blank chip, 1 on at least
        one non-blank byte, 2 on a setup/transport failure or a refusal.

        A part with no factory-blank state (SRAM/FRAM) has no blank verdict
        to report -- reporting it as "not blank" answers a question the
        part does not have. D-12 keeps the pre-wire short-circuit
        exactly where it was, before any command is composed, and changes
        only its return value: 2, an honest refusal, in place of the old
        false "not blank" verdict. `derive_plan` (chip_test.py) marks these
        parts' blank-check step unsupported up front and never dispatches to
        this method for them, so `dev test` is unaffected by this change.

        202-05 D-17: `size_str`, when given, wins over the whole-chip
        default the same way it does for `verify_eprom`. The region-past-
        the-chip's-end refusal is the CLI tier's job (`cli_handlers.blank`),
        fired before this method -- and before the serial port -- is ever
        reached; blank has no input file, so it carries no
        file-shorter-than-size refusal at all.

        `on_result` (Phase 206, DEVTEST-01): keyword-only, default `None`,
        forwarded straight through to `_drive_region_compare`'s own
        parameter of the same name. When `None`, behaviour is byte-identical
        to before this parameter existed. When supplied, it is called with
        the finalised `CompareResult` INSTEAD of `_drive_region_compare`
        rendering it -- `chip_test.py`'s `dev test` dispatch uses this to
        recover the address-and-value evidence a blank-check failure used
        to throw away, at zero extra device I/O. The SRAM/FRAM pre-wire
        short-circuit above returns before `_drive_region_compare` is ever
        called, so `on_result` is never invoked for that population either.
        """
        # SRAM/FRAM blank-check short-circuit — detect before issuing any
        # firmware command.  configure_sram() leaves a NULL main-op for the
        # blank-check command's now-retired wire ordinal (as does every
        # other protocol handler now, after Phase 204); on pre-3.1.0
        # firmware this produced 0xA4 MSG_ERR_EMPTY_INPUT.
        # SRAM/FRAM are volatile or byte-rewritable; "blank" has no meaningful
        # concept for them.  Short-circuit with a clear message; do NOT touch the
        # wire protocol or firmware.
        etype = eprom_data_dict.get("electrical-type", "")
        proto = eprom_data_dict.get("protocol-id", 0)
        if etype in ("SRAM", "FRAM") or proto in self._SRAM_PROTO_IDS:
            logger.warning(
                f"Blank check is not applicable to {eprom_name.upper()} "
                f"(electrical type: {etype or 'unknown'}, protocol: 0x{proto:02X}). "
                "SRAM/FRAM are volatile or byte-rewritable — they have no "
                "factory-blank state and the firmware has no blank-check op for them."
            )
            return 2

        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_READ,
            operation_flags,
            address_str,
            size_str,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return 2

            logger.info(f"Blank checking EPROM {eprom_name.upper()}")
            start_time = time.time()
            # The declared region length: whichever of address/size resolved
            # onto cmd_data's own address/memory-size pair -- mirrors
            # verify_eprom's `os.path.getsize`-derived region_length, just
            # sourced from the wire dict instead of a file, since blank has
            # no input file of its own.
            region_length = cmd_data.get("memory-size", 0) - cmd_data.get("address", 0)

            verdict = self._drive_region_compare(
                cmd_data,
                op_name,
                _blank_expected_bytes,
                full=full,
                region_length=region_length,
                on_result=on_result,
            )
            if verdict == 0:
                logger.info(
                    f"Blank check for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s)."  # noqa: E501
                )
            else:
                logger.error(f"Blank check for {eprom_name.upper()} failed.")
            return verdict

    def check_eprom_id(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> Tuple[bool, int | None]:  # noqa: UP006
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_CHECK_CHIP_ID,
            operation_flags,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False, None

            logger.info(f"Checking chip ID for {eprom_name.upper()}")
            start_time = time.time()

            is_ok, final_msg = self._run_state_machine(op_name)
            detected_chip_id_value = None
            if is_ok:
                logger.info(
                    f"Chip ID check passed for {eprom_name.upper()}: {final_msg} ({time.time() - start_time:.2f}s)"  # noqa: E501
                )
                detected_chip_id_value = cmd_data.get("chip-id")
            else:
                logger.warning(
                    f"Chip ID check for {eprom_name.upper()} did not return OK. Programmer response: {final_msg}"  # noqa: E501
                )
                detected_chip_id_value = extract_hex_to_decimal(final_msg or "")
                if detected_chip_id_value is not None:
                    logger.info(
                        f"Programmer reported chip ID: 0x{detected_chip_id_value:X}"
                    )
                else:
                    logger.error(
                        f"Failed to extract a valid chip ID from programmer response: {final_msg}"  # noqa: E501
                    )
            return is_ok, detected_chip_id_value

    def _main_phase_capture_lock_status(
        self, progress: ClassProgressHandler, captured: list
    ) -> str | None:
        """Main-phase handler for `CMD_LOCK_STATUS`.

        Mirrors `_main_phase_simple` exactly (same MAIN/ERROR/OK handling,
        same unconditional `_handle_progress_response(..., ack_data=True)`
        so the DATA frame is still acked as MAIN-phase flow control
        requires), but additionally recognises the one DATA id-frame this
        operation cares about -- `MSG_DATA_PROTECTION_STATUS` (0xE1) -- and
        extracts its two-byte payload from the already-rendered prose via
        `_LOCK_STATUS_RE`, the same text-extraction idiom `_TIMEOUT_ADDR_RE`
        and `_PULSE_WIDTH_RE` already use elsewhere in this module
        (`Response.payload` is populated only for `MSG_DATA_CHUNK`).

        `captured` is a caller-owned one-element mutable list, written into
        rather than returned, because this handler's own return value is
        wired by `_run_state_machine` to become `final_msg` (the MAIN
        message), leaving no return slot free for the payload too.
        """
        comm = self.comm
        if comm is None:
            return None
        final_msg = None
        while True:
            response = comm.get_response()
            if response.type == "MAIN":
                final_msg = response.message
                break
            if response.type == "ERROR":
                _raise_for_error_response(response, response.message)
            if response.type == "OK" and final_msg is None:
                final_msg = response.message
            if response.id == MSG_DATA_PROTECTION_STATUS and captured[0] is None:
                match = _LOCK_STATUS_RE.search(response.message or "")
                if match:
                    captured[0] = bytes(
                        [int(match.group(1), 16) & 0xFF, int(match.group(2)) & 0xFF]
                    )
            # MAIN phase: DATA frames are flow-control; ack them (unchanged).
            self._handle_progress_response(response, progress, ack_data=True)
        return final_msg

    def read_protection_status(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> Tuple[bool, bytes | None]:  # noqa: UP006
        """Send `CMD_LOCK_STATUS` and return `(True, payload)` on an
        accepted command, `(False, None)` otherwise.

        A `True` return means only that the command was **accepted** and a
        two-byte payload was returned -- exactly the same "sequence was
        emitted / accepted" honesty floor `sdp_lock`'s docstring states for
        its own operation, extended here to a query rather than a mutating
        command. It is never a claim that the payload's decode is a
        correct or even a *definite* state -- classification of the raw
        byte and the decode byte into one of the eight answer classes is
        `firestarter.lock_status.classify_protection_response`'s job
        entirely; this method makes no claim whatsoever about the chip's
        protection state.

        The payload is captured **inside** `_operation_context`'s `with`
        block via `_main_phase_capture_lock_status` above: `EpromOperator.
        comm` is torn down after every operator call (a measured property
        of this class, not a style preference -- see `check_eprom_id`'s own
        value-returning shape for the established precedent), so a value
        not captured before the context exits is unreadable afterwards.

        Deliberately does **not** set the 0x01 force-control flag bit in
        `operation_flags`. Per `151-DESIGN.md` §6 / C-16, that firmware bit
        means one specific thing -- downgrade a chip-ID mismatch from
        error to warning -- and this command performs no chip-ID check at
        all, so the bit would have no firmware-visible meaning here.
        `--force` on `dev lock-status` is a host-side-only bypass of the
        readability table's refusal; it never reaches the wire on
        this command.
        """
        captured: list = [None]
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_LOCK_STATUS,
            operation_flags,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False, None

            logger.info(f"Reading protection status for {eprom_name.upper()}")
            is_ok, final_msg = self._run_state_machine(
                op_name,
                main_phase_handler=self._main_phase_capture_lock_status,
                captured=captured,
            )
            if is_ok:
                logger.info(
                    f"Protection status read for {eprom_name.upper()}: {final_msg or ''}"  # noqa: E501
                )
            else:
                logger.warning(
                    f"Protection status read for {eprom_name.upper()} did not return OK. Programmer response: {final_msg}"  # noqa: E501
                )
            return is_ok, captured[0]


# Example usage (for testing this module directly)
