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
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple  # noqa: UP035

import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from firestarter.address_parser import parse_address, parse_size
from firestarter.config import ConfigManager
from firestarter.constants import (
    COMMAND_BLANK_CHECK,
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
    COMMAND_VERIFY,
    COMMAND_WRITE,
    FLAG_FORCE,
    FLAG_SKIP_BLANK_CHECK,
    FLAG_SKIP_ERASE,
    FLAG_SKIP_SDP_UNLOCK,
    FLAG_VERBOSE,
    FLAG_VPE_AS_VPP,
    JSON_KEY_READ_SETTLING_DELAY,
    JSON_KEY_READ_STROBE_US,
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
from firestarter.messages import MSG_DATA_PROTECTION_STATUS, MSG_WARN_SDP_UNLOCK_SKIPPED
from firestarter.sdp_capability import SDP_PROTOCOL_ID
from firestarter.serial_comm import (
    DEFAULT_RESPONSE_TIMEOUT,
    WRITE_BUDGET_MAX_S,
    SerialCommunicator,
)
from firestarter.utils import extract_hex_to_decimal

logger = logging.getLogger("EpromOperator")

bar_format = "{l_bar}{bar}| {n:#06x}/{total:#06x} bytes "

# Parent folder that groups the auto-named per-run output directories produced by
# consistency_check_eprom / write_cycle_eprom when the caller does not pass an
# explicit --output-dir. Created relative to the current working directory, so a
# bench session keeps all diagnostic runs under one subfolder
# (e.g. ./firestarter-runs/consistency-check-<chip>-<board>-<TS>/) instead of
# scattering timestamped folders directly in the launch directory.
DEFAULT_RUN_OUTPUT_DIR = "firestarter-runs"


def _raise_for_error_response(response, message: str) -> None:
    """Raise ProtocolNotImplementedError for id 0xBB, EpromOperationError otherwise.

    Centralises typed-exception dispatch for all ERROR-branch sites in the
    state machine so id-keyed detection is not duplicated per raise site.

    `response` is read for its `id` field to dispatch to the typed subclass.
    `message` is the already-composed exception message string (callers may
    prepend a phase-name prefix for EpromOperationError framing; the raw
    firmware text is passed through unchanged for ProtocolNotImplementedError
    so firmware owns rendering per D-02).
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

# HOST-01 / D-09-D-10: fallback write-path response timeout for when the
# firmware does not advertise a per-block write-time budget (CAP-03,
# SerialCommunicator.write_block_budget_s). DERIVED, not picked: the worst
# shipped-database block time under the new per-byte loop with no
# advertisement is 0x0B at 50 ms/byte x 1024 B = 51.2 s (the energy cap
# lands on exactly 50 ms for every shipped 0x0B width), and 0x07/0x08 at
# 25 x 1000 us x 1024 B = 25.6 s -- so 120 s is more than 2x the worst
# shipped case. Sharper still: 120 s covers EVERY REACHABLE 0x0B width,
# because that protocol's per-byte bound can never exceed 99998 us and
# 99998 x 1024 = 102.4 s. Residual non-claim: the gap is 0x07/0x08 only, at
# 120 / (25 x 1024) = 4687 us on a Leonardo and 120 / (25 x 512) = 9375 us
# on an Uno; and the reachable absent-advertisement cases are a released
# beta firmware (has CAP-02, lacks CAP-03) or a v1.31 build after the CAP-02
# port but before CAP-03 lands -- NOT a mid-milestone v1.31 build, which
# cannot connect at all (BF-1). DEFAULT_RESPONSE_TIMEOUT's own value is
# untouched by this constant -- it is imported only to resolve
# _main_phase_send_data's new response_timeout kwarg below.
WRITE_BLOCK_TIMEOUT_FALLBACK_S = 120.0

# HOST-03 / D-20: the three per-byte program-budget ids _budget_failure_hint_
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
# MSG_DATA_PROTECTION_STATUS (0xE1, plan 151-05/151-08/151-11) messages.
# Format: "Lock status probe: raw=0x%02X decode=%u" -- same rationale as
# _TIMEOUT_ADDR_RE/_PULSE_WIDTH_RE immediately above: Response.payload is
# populated only for MSG_DATA_CHUNK (W-04); every other id-frame's decoded
# param values reach the caller only as already-rendered prose, so this is
# the established way to recover them.
_LOCK_STATUS_RE = re.compile(r"raw=0x([0-9A-Fa-f]{2}) decode=(\d+)")


def _boot_block_hint_message(response, protocol: int, mem_size: int) -> Optional[str]:
    """Return a boot-block-locked inference hint string, or None.

    FIX-01b: when a flash4 (protocol 0x05) write fails with
    MSG_ERR_FL4_VERIFY_TIMEOUT and the failing address is in the first or last
    16K of the chip, the operator cannot distinguish a silicon boot-block lockout
    from a firmware bug without additional context.  This function returns a
    hint string that can be appended to the error message so the operator
    understands the probable root cause.

    Returns a hint string (to be appended to the error message) when:
      - response.id == MSG_ERR_FL4_VERIFY_TIMEOUT (0xB3), AND
      - protocol == 5 (flash4 / FLASH_AMD_STD), AND
      - the failing address is in the first 16K (< 0x4000) OR
        the last 16K (>= mem_size - 0x4000).

    Returns None for all other cases (different protocol, different error id,
    or mid-chip address) so unrelated faults are not mislabelled (T-94-MISLABEL).

    Wording per A3 / STRIDE T-94-MISLABEL: the hint INFERS the lockout from the
    address range; it does NOT confirm it (only the firmware §6.6 DETECT read
    can read the FF/FE lockout bit and confirm).
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


def _budget_failure_hint_message(response) -> Optional[str]:
    """Return a per-byte program-budget-failure disposition hint, or None.

    HOST-03 / D-19: mirrors `_boot_block_hint_message`'s shape immediately
    above -- a pure, module-level function keyed on `response.id` first,
    with the message-id names imported LOCALLY to avoid the same import
    cycle that function's docstring names. The firmware's own catalog
    format already interpolates the failing address (`MSG_ERR_MAX_PULSES`
    (0xBD) / `MSG_ERR_ENERGY_CAP` (0xBE), `firestarter/messages.py`) or the
    refused pulse width (`MSG_ERR_PULSE_TOO_WIDE`, 0xAE), so this hint adds
    *disposition*, not location or value -- it explains what the id MEANS
    for the write in progress, not where it happened or how wide the pulse
    was.

    D-21 (`.planning/phases/141-per-byte-program-loop/141-LOOP-RECORD.md`
    §4, traced against live firmware source this session, not this plan's
    paraphrase of it): on a budget failure,
    `eprom_internal_write_execute_body` (src/proms/eprom.cpp) returns
    before `handle->address` is ever advanced; `_process_incoming_data`
    (src/eprom_operations.cpp) sees that failure and returns `false`
    immediately, never reaching its own `handle->address +=
    handle->data_size` line; `command_done()` (src/firestarter.cpp) then
    zeroes `CONTROL_REGISTER`, `LEAST_SIGNIFICANT_BYTE` and
    `MOST_SIGNIFICANT_BYTE` and sets `handle->cmd = CMD_IDLE`. The write
    stopping and the firmware refusing every later block for that write are
    the SAME event, not two claims that happen to coincide -- so for
    `MSG_ERR_MAX_PULSES` / `MSG_ERR_ENERGY_CAP` this hint gives no advice to
    attempt the failed block again and implies no firmware-side
    continuation: starting the write over is a fresh run of the whole file,
    never a pick-up-where-it-stopped.

    D-20: deliberately keyed on `_BUDGET_FAILURE_IDS` only -- in particular
    never on error id 0xB1 (the OLD, now-retired per-block loop's own
    failure id -- see `_BUDGET_FAILURE_IDS`'s own comment, above, for its
    name), which F-141-06 confirms is emitted by nothing on the 27C write
    path any more (the per-byte loop reports 0xBD/0xBE instead, with a
    different, smaller payload shape). A hint keyed on 0xB1 here would
    never fire.

    D-16: `MSG_ERR_PULSE_TOO_WIDE` is the firmware's pre-flight refusal for
    a host-legal (`click.IntRange(1, 65535)`, plan 143-07), firmware-
    refused `--pulse-us` on protocol 0x0B -- plan 143-07 deliberately left
    this window unmirrored host-side, to avoid duplicating
    `energy_cap_us`'s single definition site. This hint is what makes that
    refusal actionable instead of opaque: it fires before any high voltage
    is enabled, so unlike the other two ids, no byte was touched and a
    smaller `--pulse-us` is legitimate remediation -- unlike a byte that
    will not converge no matter how many more times it is pulsed.

    Returns None for any id not in `_BUDGET_FAILURE_IDS`, and also for a
    `MSG_ERR_PULSE_TOO_WIDE` response whose message does not carry a
    parsable width (mirrors `_boot_block_hint_message`'s own "no hint
    without a parsable address" precedent immediately above).
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
    flags = 0
    if not blank_check:
        flags |= FLAG_SKIP_BLANK_CHECK
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
    # FLAG_CHIP_ENABLE are in cli_handlers._build_op_flags — D-19: every wire
    # flag bit stays mapped in the one function that maps wire flags.
    # Emitted unconditionally when requested: firmware never reads this bit on
    # a protocol other than 0x0D, so no per-protocol branch belongs in a
    # flag-mapping function. D-18's "warn and proceed" for a non-0x0D chip is
    # the handler's job, not this function's.
    if skip_sdp_unlock:
        flags |= FLAG_SKIP_SDP_UNLOCK

    return flags


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
        self, config: ConfigManager, progress_callback: Optional[Callable] = None
    ):
        self.comm: SerialCommunicator | None = None
        self.config = config
        self.progress_callback = progress_callback
        # Debug session w27c512-devtest-all-bad. The firmware's own
        # explanation of a failure -- its message id and rendered text --
        # had NO route out of this class. `_run_state_machine` catches
        # `EpromOperationError` (which carries `error_code=response.id`) and
        # returns `(False, str(e))`; `write_eprom`/`verify_eprom` then
        # discard that string (`is_ok, _ = ...`) and every one of the four
        # mutation methods returns a bare bool. So `chip_test`'s
        # `except EpromOperationError: ... error_code=exc.error_code`
        # handler is structurally unreachable for write/verify/erase/
        # blank-check, and every failing step in a `dev test` report carried
        # `error_code: null, reason: ""` -- which is how issue #41 came to
        # read as four independent faults with no evidence attached, when
        # the firmware had already named the offending address and byte via
        # MSG_ERR_NOT_BLANK.
        #
        # These two attributes are that route. Deliberately NOT a signature
        # change on the four methods: their `-> bool` contract is relied on
        # directly by cli_handlers.py's `write` / `verify` / `blank` /
        # `erase` commands, each of which ends in
        # `sys.exit(0 if ok else 1)` on the bare bool, by chip_test.py, and
        # by a large body of test doubles. Widening it would be a far bigger
        # blast radius than this bug warrants. (Verified against origin/beta,
        # not a feature branch: the exit code on those four paths is 1. The
        # `exit 2` this file's other comments mention belongs to
        # `consistency_check_eprom`'s own mapping, a different path.)
        #
        # Lifetime, stated because it is the whole reason an instance
        # attribute works here: `_operation_context`'s `finally` tears down
        # `self.comm`, but never the operator itself, so these survive the
        # call that set them and are readable by the caller immediately
        # after. `_run_state_machine` CLEARS them on entry, so a value can
        # never be stale from an earlier operation.
        #
        # Scope, deliberately narrow: only the `EpromOperationError` arm
        # sets these -- a real firmware ERROR frame. The transport arm
        # (`SerialError`/`SerialTimeoutError`) does not: its text can carry
        # a host device path, it has no firmware message id to report, and
        # `chip_test._run_step` already has its own handler for the
        # transport exceptions that escape.
        self.last_firmware_error_code: Optional[int] = None
        self.last_firmware_error_message: Optional[str] = None

    def _calculate_buffer_size(self) -> int:
        # CAP-01: firmware_max_chunk is populated by the
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
        # CAP-01 safe Uno-floor default: absent advertisement -> 512.
        return 512

    def _write_block_timeout(self) -> float:
        """Return the per-response wait for a write's MAIN phase, in seconds.

        HOST-01 / D-09: the firmware's advertised ``write_block_budget_s``
        (CAP-03, decoded in ``serial_comm.py``) is used VERBATIM -- the
        firmware already padded it (its own ``delay(500)`` VPE settle, the
        final full-block verify pass and the per-pulse settle are all
        folded in), so the host applies no multiplier of its own on top.

        D-10: an absent, truncated or implausible advertisement returns the
        derived ``WRITE_BLOCK_TIMEOUT_FALLBACK_S`` instead -- never an error
        and never a refusal. Mirrors ``_calculate_buffer_size``'s precedent
        directly above: Phase 54's ``FirmwareOutdatedError`` was reversed
        into exactly this "safe default on absence" shape, which is the
        standing argument against refusing the write here too.

        The ``[1, WRITE_BUDGET_MAX_S]`` range test is a second line of
        defence behind ``serial_comm``'s own decode-time plausibility
        clamp: a value outside that range can only reach
        ``write_block_budget_s`` if something bypassed the decoder, and
        this method must not trust it even then -- both a too-small
        (``0``) and an implausibly-large (``> WRITE_BUDGET_MAX_S``) value
        fall back identically, so a corrupt or hostile ack can never
        install either a too-tight or an unbounded host wait.

        MUST be called from inside ``write_eprom``'s ``_operation_context``
        ``with`` block: that block's ``finally`` sets ``self.comm`` to
        ``None`` on exit, so a call after it exits would always take the
        None-comm branch below.
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
        address: Optional[str] = None,
        size: Optional[str] = None,
        fault_inject_outgoing: Optional[Callable[[bytes], bytes]] = None,
    ) -> Tuple[Optional[Dict], int]:  # noqa: UP006
        """
        Prepares for an EPROM operation: uses pre-fetched EPROM data, sets up command, and connects.
        Returns (eprom_data_for_command, buffer_size) or (None, 0) on failure.
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

        try:
            self.comm = SerialCommunicator.find_and_connect(
                command_dict,
                self.config,
                fault_inject_outgoing=fault_inject_outgoing,
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
        address: Optional[str] = None,
        size: Optional[str] = None,
        fault_inject_outgoing: Optional[Callable[[bytes], bytes]] = None,
    ):
        """A context manager to handle EPROM operation setup and teardown.

        ``fault_inject_outgoing`` (Phase 53-04 / XACT-02, dev-only) is forwarded to
        ``find_and_connect`` so the setup command frame can be corrupted at connection
        time. Default None keeps the production path byte-identical.
        """
        command_dict, buffer_size = self._setup_operation(
            eprom_name,
            eprom_data_dict,
            cmd,
            operation_flags,
            address,
            size,
            fault_inject_outgoing=fault_inject_outgoing,
        )
        if not command_dict or not self.comm:
            yield None, None, None  # Yield None to indicate setup failure
            return

        operation_name = COMMAND_NAMES[cmd]
        try:
            # Yield the necessary data to the 'with' block
            yield command_dict, buffer_size, operation_name
        finally:
            # This block ensures disconnection happens even if errors occur
            self._disconnect_programmer()

    def _disconnect_programmer(self):
        if self.comm:
            self.comm.disconnect()
            self.comm = None

    # --- Unified State Machine ---

    def _run_state_machine(
        self,
        operation_name: str,
        main_phase_handler: Optional[Callable] = None,
        **handler_kwargs,
    ) -> Tuple[bool, Optional[str]]:  # noqa: UP006
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
    ) -> Optional[str]:
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

    def _main_phase_simple(self, progress: ClassProgressHandler) -> Optional[str]:
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
        """Render an intra-block MSG_DATA_PROGRESS (0xE0) frame on the write
        path. Returns True when a position was actually applied -- the
        caller (``_main_phase_send_data``) uses this to latch the
        chunk-handoff ``update()`` off once the firmware starts driving the
        bar itself (HOST-02 / Pitfall 1). Returns False when the frame's
        message was absent or unparsable, so the latch never engages on a
        malformed frame.

        D-04: applies the frame's ``current`` and IGNORES its ``total`` --
        and performs ``set_progress``'s final three operations DIRECTLY
        rather than calling it. ``set_progress(current, total)`` calls
        ``self.start(total)`` whenever the frame's total differs from the
        bar's, and ``start()`` CLOSES AND RE-CREATES the tqdm bar and zeroes
        ``current_step``. The write bar is started with ``file_size`` while
        ``0xE0`` carries ``handle->mem_size`` -- for a short input file or
        an ``--address``-offset write these differ, so every single frame
        would tear the bar down and rebuild it if routed through
        ``set_progress``. This method never reaches that arm.

        D-04's arithmetic: ``0xE0`` carries an ABSOLUTE chip address, but
        the write bar's origin is the write's own start address, not 0.
        Getting this wrong shows up as a bar that starts mid-way (or beyond
        100%) on an ``--address`` write.

        D-05: this method NEVER acks. Callers must not route a write-path
        DATA frame through ``_handle_progress_response`` instead of this
        method -- that helper's ``ack_data`` defaults to True and its DATA
        arm calls ``set_progress`` directly, which is exactly the rebuild
        path this method exists to avoid.

        Scope: fixing ``set_progress``'s rebuild-on-differing-total at its
        source is a deferred idea, out of scope here -- it is shared code on
        the read and blank-check paths this plan does not own.
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
        eprom_data_dict: Optional[dict] = None,
        response_timeout: Optional[float] = None,
    ) -> None:
        """Main phase handler for writing or verifying data.

        ``eprom_data_dict`` is forwarded from the write/verify caller so that
        the boot-block-locked heuristic hint (FIX-01b) can be appended
        to MSG_ERR_FL4_VERIFY_TIMEOUT errors when the failing address is in the
        first or last 16K of a flash4 (protocol 0x05) chip.  Passing None (the
        default) keeps behaviour identical to pre-FIX-01b for all other callers.

        ``response_timeout`` is the write-only per-response
        wait: ``write_eprom`` passes ``self._write_block_timeout()`` from
        inside its ``_operation_context`` ``with`` block; ``verify_eprom``
        does not pass it at all, so the default of ``None`` (which resolves
        to ``DEFAULT_RESPONSE_TIMEOUT`` below) keeps ``verify_eprom`` byte
        -identical to its pre-HOST-01 behaviour -- exactly the same
        default-preserves-old-callers contract the ``eprom_data_dict``
        paragraph above already uses. ``get_response(timeout)`` is an
        already-supported call form (``expect_ack`` uses it); this is the
        ONLY timeout change on the write path -- ``_read_and_parse_lines``
        and its timeout-reset semantics are untouched (GATE-1.8d).
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

            # HOST-02 / D-04: _setup_operation sets command_dict["address"]
            # ONLY when an --address was supplied, so .get("address", 0) is
            # exactly right for a full-chip write's start address (0) too --
            # write_eprom already forwards eprom_data_dict=cmd_data.
            start_addr = (eprom_data_dict or {}).get("address", 0)
            # HOST-02 / Pitfall 1: latches True on the first successfully
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
                    # HOST-03 / D-19: the boot-block hint (0xB3, flash4-only)
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
                    # HOST-02 / D-05: a mid-block MSG_DATA_PROGRESS frame is
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
                    # (atomic-write mandate, ADR §4.1 / T-50-05 SAFE-01 timing guard).
                    self.comm.send_bytes(frame)
                    if not firmware_drives_bar:
                        # HOST-02 / Pitfall 1: the two progress sources measure
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
    ):
        """Main phase handler for reading data.

        Phase 8 W-04: the firmware now wraps each chip-byte chunk inside a
        MSG_DATA_CHUNK ID frame instead of emitting raw bytes after a DATA:
        text prefix.  The response loop distinguishes:
          - DATA response with payload set → MSG_DATA_CHUNK; extract raw bytes.
          - DATA response with no payload  → MSG_DATA_SENDING (zero-param batch
            starter, which arrives before the chunk frame); skip and continue.
        """
        from firestarter.messages import (
            MSG_DATA_CHUNK,  # local import avoids circular  # noqa: F401
        )

        data_size = end_addr - start_addr
        if data_size > 0:
            progress.start(data_size)

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
                    process_data_chunk_callback(start_addr, payload)
                    start_addr += len(payload)
                    progress.update(len(payload))
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
        output_file: Optional[str] = None,
        operation_flags: int = 0,
        address_str: Optional[str] = None,
        size_str: Optional[str] = None,
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
        output_dir: Optional[str] = None,
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

        This is the ONLY EpromOperator method that returns int rather than bool;
        the 3-way verdict (PASS / FAIL / hardware-error) cannot fit in a bool.
        Same exit-code convention as grep(1). Precedent for non-bool return:
        check_eprom_id() returns Tuple[bool, Optional[int]] above.

        Reuses _run_state_machine + _main_phase_read_data verbatim (per Phase 26
        CONTEXT.md D-03 reuse-not-duplicate rule) -- the diagnostic exercises
        the same code path the read bug lives in. Do NOT refactor into a
        parallel read implementation.

        REPRO-03 (Phase 26 / Plan 26-01).
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

                # Reuse the EXACT code path read_eprom uses (D-03 reuse-not-duplicate)
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

            # Cleanup (D-10 #5)
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
        output_dir: Optional[str] = None,
        operation_flags: int = 0,
    ) -> int:
        """Erase → write source image → read-back N times; compare each read-back
        against source image SHA-256 (D-06 independent host-side compare).

        Returns:
            0 -- all N read-backs match source image SHA-256 (PASS)
            1 -- any read-back SHA-256 != source image SHA-256 (FAIL / mismatch)
            2 -- erase_eprom or write_eprom returned False, read-back state machine
                 returned is_ok=False, or EpromOperationError raised (hw-error)

        The 3-way verdict (PASS / FAIL / hw-error) mirrors consistency_check_eprom.
        hw-error is NEVER collapsed to mismatch (verdict 2 != verdict 1).

        Reuses _operation_context + _run_state_machine + _main_phase_read_data
        verbatim from consistency_check_eprom (per reuse-not-duplicate rule).
        Do NOT refactor the read-back block into a parallel read implementation.

        XACT-01 / Phase 53 Plan 02.
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
        output_dir: Optional[str] = None,
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
            a fresh clean transfer then succeeds. (The prior wiring set the hook AFTER
            setup, so it never fired — a false negative; see
            .planning/debug/fault-inject-harness-outgoing.md.)
        direction="incoming": corrupt fw→host frame via FaultInjectingSerialCommunicator
            on an established connection; the host decoder catches it and the same
            connection recovers on the clean follow-on (Pitfall 2).

        Writes fault-inject-<direction>-log.txt with the measured error latency so the
        sub-second clean error (no 2 s cascade) XACT-02 requires can be confirmed.

        XACT-02 / Phase 53 Plan 02 (harness fix: 53-04).
        """
        from firestarter.serial_comm import FaultInjectingSerialCommunicator

        if output_dir is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            output_dir = f"fault-inject-{eprom_name}-{direction}-{timestamp}"
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Build fault hooks for the outgoing path (D-02 fault forms)
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
        #   after setup" wiring never fired (false negative; see
        #   .planning/debug/fault-inject-harness-outgoing.md). The firmware rejects the
        #   corrupt frame (CRC8-before-parse / inter-byte timeout) -> connection setup
        #   fails with a bounded error == the expected outcome.
        # Incoming: connect cleanly, swap to FaultInjectingSerialCommunicator, run the
        #   read; the host decoder catches the mutated fw->host frame.
        # error_latency_s captures wall-clock from corrupted-attempt start to the
        #   surfaced error so XACT-02's "sub-second clean error, no 2 s cascade" can be
        #   measured (the harness reports it; the firmware's actual latency decides it).
        corrupted_ok = False
        error_latency_s: Optional[float] = None
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
        # clean error (no 2 s cascade) XACT-02 requires.
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
        error_latency_s: Optional[float],
        detail: str,
    ) -> None:
        """Write the XACT-02 fault-injection log (one per cycle).

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
                    f"# XACT-02 fault-injection log ({direction}, {fault_form})\n"
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
        output_dir: Optional[str] = None,
        port: Optional[str] = None,
    ) -> bool:
        """XACT-02 outgoing PER-FRAME latency measurement on an ESTABLISHED single-port
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
        nak_latency_s: Optional[float] = None
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
        nak_latency_s: Optional[float],
        detail: str,
    ) -> None:
        """Write the per-frame NAK latency log (53-04 harness refinement)."""
        log_path = output_path / f"fault-inject-{fault_form}-latency.txt"
        latency_str = (
            f"{nak_latency_s:.3f}s" if nak_latency_s is not None else "unmeasured"
        )
        # Sub-second is the XACT-02 fast-fail bar for a complete corrupt frame; a
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
                    "# XACT-02 per-frame NAK latency (established single-port connection)\n"
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

    def dev_read_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        address_str: Optional[str] = None,
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
        address_str: Optional[str],
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

    def write_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        input_file_path: str,
        operation_flags: int = 0,
        address_str: Optional[str] = None,
        pulse_us: int = 0,  # per-run pulse-width override (us; 0=not supplied, use the database value)
    ) -> bool:
        # HOST-04 / D-14: per-run pulse override, riding the existing
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

        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_WRITE,
            operation_flags,
            address_str,
        ) as (cmd_data, buf_size, op_name):
            if not cmd_data:
                return False

            logger.info(f"Writing {input_file_path} to {eprom_name.upper()}")
            start_time = time.time()

            # HOST-01 / D-09-D-10: _write_block_timeout() MUST be read here,
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

            if is_ok:
                logger.info(
                    f"Write to {eprom_name.upper()} successful ({time.time() - start_time:.2f}s)."  # noqa: E501
                )
            else:
                logger.error(f"Write to {eprom_name.upper()} failed.")
            return is_ok

    def verify_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        input_file_path: str,
        operation_flags: int = 0,
        address_str: Optional[str] = None,
    ) -> bool:
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_VERIFY,
            operation_flags,
            address_str,
        ) as (cmd_data, buf_size, op_name):
            if not cmd_data:
                return False

            logger.info(f"Verifying {input_file_path} against {eprom_name.upper()}")
            start_time = time.time()

            is_ok, _ = self._run_state_machine(
                op_name,
                main_phase_handler=self._main_phase_send_data,
                input_file_path=input_file_path,
                buffer_size=buf_size,
            )

            if is_ok:
                logger.info(
                    f"Verify for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s)."  # noqa: E501
                )
            else:
                logger.error(f"Verify for {eprom_name.upper()} failed.")
            return is_ok

    def erase_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        operation_flags: int = 0,
        address_str: Optional[str] = None,
    ) -> bool:
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
        frame is written and there is no host ``DONE`` round-trip. Phase 119's
        correction still applies here: NULL ``init``/``end`` does NOT skip the
        INIT and END frame pairs themselves — both ``_execute_phase("INIT", ...)``
        and ``_execute_phase("END", ...)`` still run and both ack; only the
        ``DONE`` round-trip and the data frame are absent (no
        ``main_phase_handler`` is passed below, so ``_run_state_machine`` falls
        through to ``_main_phase_simple``, exactly like ``erase_eprom``).

        A ``True`` return means only that the command sequence was **emitted**
        over the wire — it is never a claim that silicon actually left the
        protected state. Protection state is not readable on this chip family
        (Phase 119 D-12, Phase 117 D-05), so no return value from this method
        can honestly say more than "the sequence was sent and the firmware
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
        frame is written and there is no host ``DONE`` round-trip. Phase 119's
        correction still applies here: NULL ``init``/``end`` does NOT skip the
        INIT and END frame pairs themselves — both ``_execute_phase("INIT", ...)``
        and ``_execute_phase("END", ...)`` still run and both ack; only the
        ``DONE`` round-trip and the data frame are absent (no
        ``main_phase_handler`` is passed below, so ``_run_state_machine`` falls
        through to ``_main_phase_simple``, exactly like ``erase_eprom``).

        A ``True`` return means only that the command sequence was **emitted**
        over the wire — it is never a claim that silicon actually entered the
        protected state. Protection state is not readable on this chip family
        (Phase 119 D-12, Phase 117 D-05), so no return value from this method
        can honestly say more than "the sequence was sent and the firmware
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
    # firestarter_operation_main for CMD_BLANK_CHECK, causing 0xA4
    # MSG_ERR_EMPTY_INPUT.  These are all SRAM families (D-30 host-side fix).
    _SRAM_PROTO_IDS = frozenset({0x0E, 0x27, 0x28, 0x29})

    def check_eprom_blank(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> bool:
        # SRAM/FRAM blank-check short-circuit — detect before issuing any
        # firmware command.  configure_sram() leaves a NULL main-op for
        # CMD_BLANK_CHECK, so the firmware emits 0xA4 MSG_ERR_EMPTY_INPUT.
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
            return False

        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_BLANK_CHECK,
            operation_flags,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False
            logger.info(f"Blank checking EPROM {eprom_name.upper()}")
            start_time = time.time()
            is_ok, final_msg = self._run_state_machine(op_name)
            if is_ok:
                logger.info(
                    f"Blank check for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s). {final_msg or ''}"  # noqa: E501
                )
            return is_ok

    def check_eprom_id(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> Tuple[bool, Optional[int]]:  # noqa: UP006
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
    ) -> Optional[str]:
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
    ) -> Tuple[bool, Optional[bytes]]:  # noqa: UP006
        """Send `CMD_LOCK_STATUS` and return `(True, payload)` on an
        accepted command, `(False, None)` otherwise.

        A `True` return means only that the command was **accepted** and a
        two-byte payload was returned -- exactly the same "sequence was
        emitted / accepted" honesty floor `sdp_lock`'s docstring states for
        its own operation, extended here to a query rather than a mutating
        command. It is never a claim that the payload's decode is a
        correct or even a *definite* state -- classification of the raw
        byte and the decode byte into one of D-09's eight answer classes is
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
