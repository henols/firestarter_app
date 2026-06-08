"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 36 — CLI characterization golden suite (TEST-01).

D-01 (subprocess black-box goldens): The subprocess harness invokes the real
     ``firestarter`` entry point for all board-independent CLI surface tests
     (--help, subcommand --help, DB-backed list/info/search, all usage/parse
     errors, hardware-absent path).  This harness is migration-transparent:
     it behaves identically before and after the Phase 41 argparse→Click
     migration, so the same committed snapshots prove GATE-1.8b byte-for-byte.

D-02 (in-process happy paths): read/write/verify/erase happy-paths are
     characterized in-process via the make_comm/fake_serial fixtures with
     canned firmware response frames.  A BytesIO fake cannot cross the
     subprocess boundary, so these are NOT routed through subprocess.

D-03 (broad scope): Both the board-independent surface and the E2E happy-paths
     live in this single file per the planning decision.

D-05a (determinism): All subprocess output is pre-processed by normalize_output()
     before being passed to syrupy, scrubbing version strings, /dev/tty* device
     names, and absolute paths.  syrupy matchers are NOT used for free-form
     strings.
"""

import os  # noqa: F401
import re
import shutil
import subprocess
import sys  # noqa: F401
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401

import pytest

from firestarter.messages import (
    MSG_DATA_CHUNK,
    MSG_END_DONE,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_READY,  # noqa: F401
    MSG_OK_REQ_DATA,
)

from .conftest import build_frame

# ---------------------------------------------------------------------------
# Entry-point resolution
# ---------------------------------------------------------------------------

_WHICH = shutil.which("firestarter")
FIRESTARTER = _WHICH if _WHICH is not None else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_output(s: str) -> str:
    """Scrub non-deterministic content before any == snapshot assertion.

    Applied to all subprocess stdout/stderr so snapshots are identical on
    CI (no board) and bench (board attached) across version bumps and
    different development environments.

    Scrubs:
    - ``Firestarter version: X.Y.Z`` → ``Firestarter version: <VERSION>``
    - ``/dev/tty...``                 → ``/dev/ttyXXX``
    - Absolute paths in tracebacks and elsewhere (handles quoted paths too)
    """
    # Version string (debug log lines): "Firestarter version: X"
    s = re.sub(
        r"Firestarter version: [\d.a-zA-Z+]+", "Firestarter version: <VERSION>", s
    )
    # Click --version output: "Firestarter, version X" (comma separator, distinct
    # from the "Firestarter version: X" debug-log format above). Scrubbing both
    # keeps the snapshot version-agnostic across beta bumps (e.g. b5 → b6 → b7).
    s = re.sub(
        r"Firestarter, version [\d.a-zA-Z+]+", "Firestarter, version <VERSION>", s
    )
    # Serial port device names
    s = re.sub(r"/dev/tty\w+", "/dev/ttyXXX", s)
    # Absolute paths — match the path portion only, stopping at quote/comma/space/newline.  # noqa: E501
    # This handles both bare paths and paths inside Python traceback strings like
    # File "/home/vscode/.local/bin/firestarter", line 8.
    # Broad root list so snapshots stay identical across dev containers, pipx/venv,
    # system, /opt, and CI installs (WR-02).
    s = re.sub(
        r"(?:/home|/workspaces|/tmp|/Users|/opt|/usr|/root|/var|/private|/Library|/srv|/mnt)"
        r'(?:/[^\s",\')]+)+',
        "<PATH>",
        s,
    )
    # Windows absolute paths (e.g. C:\Users\...\firestarter.exe)
    s = re.sub(r'[A-Za-z]:\\(?:[^\s",\')]+\\?)+', "<PATH>", s)
    # Traceback frame line numbers: "File "<PATH>", line 1514, in main" — the
    # line numbers point into third-party library internals (click, the console
    # entry-point shim) and shift between dependency versions, so they are not
    # portable across CI/dev environments. Scrub them so characterization
    # snapshots that pin a traceback depend only on the meaningful error type.
    s = re.sub(r'(File "<PATH>", line )\d+', r"\1N", s)
    return s


def run_firestarter(*args: str) -> tuple[str, str, int]:
    """Run the installed firestarter entry point as a subprocess.

    Returns (normalized_stdout, normalized_stderr, returncode).
    Skips the test if the entry point is not found on PATH.
    """
    if FIRESTARTER is None:
        pytest.skip("firestarter entry point not found on PATH; run `pip install -e .`")
    result = subprocess.run(
        [FIRESTARTER, *args],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return (
        normalize_output(result.stdout),
        normalize_output(result.stderr),
        result.returncode,
    )


# ---------------------------------------------------------------------------
# Top-level --help and --version
# ---------------------------------------------------------------------------


def test_help(snapshot):
    """Pin top-level --help output."""
    stdout, stderr, rc = run_firestarter("--help")
    assert rc == 0
    assert stdout == snapshot


def test_version(snapshot):
    """Pin --version output (version string scrubbed by normalize_output)."""
    stdout, stderr, rc = run_firestarter("--version")
    assert rc == 0
    assert stdout == snapshot


# ---------------------------------------------------------------------------
# Subcommand --help snapshots
# ---------------------------------------------------------------------------


def test_help_read(snapshot):
    stdout, stderr, rc = run_firestarter("read", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_write(snapshot):
    stdout, stderr, rc = run_firestarter("write", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_verify(snapshot):
    stdout, stderr, rc = run_firestarter("verify", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_erase(snapshot):
    stdout, stderr, rc = run_firestarter("erase", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_blank(snapshot):
    stdout, stderr, rc = run_firestarter("blank", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_id(snapshot):
    stdout, stderr, rc = run_firestarter("id", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_list(snapshot):
    stdout, stderr, rc = run_firestarter("list", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_info(snapshot):
    stdout, stderr, rc = run_firestarter("info", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_search(snapshot):
    stdout, stderr, rc = run_firestarter("search", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_fw(snapshot):
    stdout, stderr, rc = run_firestarter("fw", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_hw(snapshot):
    stdout, stderr, rc = run_firestarter("hw", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_config(snapshot):
    stdout, stderr, rc = run_firestarter("config", "--help")
    assert rc == 0
    assert stdout == snapshot


def test_help_dev(snapshot):
    stdout, stderr, rc = run_firestarter("dev", "--help")
    assert rc == 0
    assert stdout == snapshot


# ---------------------------------------------------------------------------
# DB-backed commands (list / info / search)
# These never call find_and_connect; no port mock needed.
# ---------------------------------------------------------------------------


def test_list(snapshot):
    """Pin full DB list output (board-independent)."""
    stdout, stderr, rc = run_firestarter("list")
    assert rc == 0
    assert stdout == snapshot


def test_info_known_chip(snapshot):
    """Pin info output for W27C512 (known chip).

    Verifies that firestarter info exits 0 and renders chip details
    including the corrected pulse_duration field from chip_database.json.
    Previously this crashed with a TypeError (vpp-pin list-vs-scalar) in
    ic_layout._generate_pin_names_for_display — that bug is now fixed.
    """
    stdout, stderr, rc = run_firestarter("info", "W27C512")
    assert rc == 0
    assert stderr == ""
    assert stdout == snapshot


def test_search_w27(snapshot):
    """Pin search results for 'W27' substring."""
    stdout, stderr, rc = run_firestarter("search", "W27")
    assert rc == 0
    assert stdout == snapshot


def test_search_no_results(snapshot):
    """Pin behavior when search returns no results."""
    stdout, stderr, rc = run_firestarter("search", "ZZZNORESULTS")
    assert rc == 1
    assert stdout == snapshot


# ---------------------------------------------------------------------------
# Usage / argument-parse errors
# ---------------------------------------------------------------------------


def test_error_unknown_command(snapshot):
    """Unknown subcommand → argparse usage error, exit 2."""
    stdout, stderr, rc = run_firestarter("foobar")
    assert rc == 2
    assert stderr == snapshot


def test_error_info_bad_chip(snapshot):
    """info with a chip not in the database → exit 1."""
    stdout, stderr, rc = run_firestarter("info", "NOTACHIP")
    assert rc == 1
    # Error message goes to stdout (via logger.error → INFO handler)
    assert stdout == snapshot


def test_error_read_missing_eprom(snapshot):
    """read with no arguments → missing required arg, exit 2."""
    stdout, stderr, rc = run_firestarter("read")
    assert rc == 2
    assert stderr == snapshot


def test_error_write_missing_args(snapshot):
    """write with no arguments → missing required args, exit 2."""
    stdout, stderr, rc = run_firestarter("write")
    assert rc == 2
    assert stderr == snapshot


def test_error_fw_pre_stable_mutex(snapshot):
    """fw --pre --stable → mutually exclusive, exit 2."""
    stdout, stderr, rc = run_firestarter("fw", "--pre", "--stable")
    assert rc == 2
    assert stderr == snapshot


def test_error_fw_pre_firmware_version_mutex(snapshot):
    """fw --pre --firmware-version → mutually exclusive, exit 2."""
    stdout, stderr, rc = run_firestarter("fw", "--pre", "--firmware-version", "3.0.0")
    assert rc == 2
    assert stderr == snapshot


def test_error_read_bad_address(snapshot):
    """read with bad --address value → invalid address, exit 1.

    Note: address validation happens after find_and_connect fails (no board),
    so this test triggers the serial error path first.  We pin current behavior.
    """
    stdout, stderr, rc = run_firestarter("read", "W27C512", "--address", "not_hex")
    # Behavior: invalid address → exit 1 (detected in _setup_operation)
    assert rc == 1
    assert stdout == snapshot


def test_error_read_bad_size(snapshot):
    """read with bad --size value → invalid size, exit 1."""
    stdout, stderr, rc = run_firestarter("read", "W27C512", "--size", "abc")
    assert rc == 1
    assert stdout == snapshot


def test_no_blank_check_polarity(snapshot):
    """write --no-blank-check: pin the help text that documents the flag polarity."""
    stdout, stderr, rc = run_firestarter("write", "--help")
    assert rc == 0
    # The snapshot is already captured in test_help_write; this test pins
    # that --no-blank-check appears in the help (polarity surface).
    assert "--no-blank-check" in stdout
    assert stdout == snapshot


# ---------------------------------------------------------------------------
# Hardware-absent path (D-05b / D-05c determinism)
#
# Monkeypatch serial.tools.list_ports.comports → [] so the "no programmer
# found" error path is identical with/without a board attached.
# This runs IN-PROCESS (monkeypatch cannot cross the subprocess boundary).
# ---------------------------------------------------------------------------


def test_no_programmer_found_read(monkeypatch):
    """Pin: read with no serial ports found → ProgrammerNotFoundError, returns False.

    Monkeypatches serial.tools.list_ports.comports to return [] so port
    discovery is deterministic (D-05b; Pitfall 2).  Exercises find_and_connect
    directly via EpromOperator._setup_operation.
    """
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [])

    from firestarter.config import ConfigManager
    from firestarter.database import EpromDatabase
    from firestarter.eprom_operations import EpromOperator

    config = ConfigManager()
    # skip_local_override=True is MANDATORY (phase 36 rule, Pitfall-4): a ~/.firestarter
    # override of W27C512 on an operator bench must not flip this assertion in CI.
    db = EpromDatabase(skip_local_override=True)
    eprom_data = db.get_eprom("W27C512")
    assert eprom_data is not None
    eprom_cmd = db.convert_to_programmer(eprom_data)

    operator = EpromOperator(config)
    # read_eprom returns False when no programmer is found
    result = operator.read_eprom("W27C512", eprom_cmd, output_file="/dev/null")
    assert result is False


def test_no_programmer_found_erase(monkeypatch):
    """Pin: erase with no serial ports found → ProgrammerNotFoundError, returns False."""  # noqa: E501
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [])

    from firestarter.config import ConfigManager
    from firestarter.database import EpromDatabase
    from firestarter.eprom_operations import EpromOperator

    config = ConfigManager()
    # skip_local_override=True is MANDATORY (phase 36 rule, Pitfall-4): a ~/.firestarter
    # override of W27C512 on an operator bench must not flip this assertion in CI.
    db = EpromDatabase(skip_local_override=True)
    eprom_data = db.get_eprom("W27C512")
    assert eprom_data is not None
    eprom_cmd = db.convert_to_programmer(eprom_data)

    operator = EpromOperator(config)
    result = operator.erase_eprom("W27C512", eprom_cmd)
    assert result is False


# ---------------------------------------------------------------------------
# In-process happy-path characterizations (D-02)
#
# These use make_comm / fake_serial fixtures with canned firmware responses.
# The EpromOperator._run_state_machine is called with operator.comm injected
# directly — bypassing find_and_connect entirely.
#
# State machine frame sequence (all simple operations):
#   → send_ack (start INIT)
#   ← MSG_INIT_DONE  (INIT complete)
#   → send_ack (start MAIN)
#   ← MSG_MAIN_DONE  (MAIN complete)  [or data exchange for read/write]
#   → send_ack (start END)
#   ← MSG_END_DONE   (END complete)
#   → send_ack (final ack)
# ---------------------------------------------------------------------------


def _make_operator_with_comm(comm):
    """Return an EpromOperator with the given comm already wired in."""
    from firestarter.eprom_operations import EpromOperator

    operator = EpromOperator.__new__(EpromOperator)
    operator.comm = comm
    operator.config = None
    operator.progress_callback = None
    return operator


def test_erase_happy_path(make_comm, fake_serial):
    """Characterize: erase happy-path via _run_state_machine (no hardware)."""
    comm = make_comm()
    # Feed INIT → MAIN → END frames
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    operator = _make_operator_with_comm(comm)
    success, msg = operator._run_state_machine("ERASE")
    assert success is True


def test_blank_check_happy_path(make_comm, fake_serial):
    """Characterize: blank check happy-path (simple state machine, same sequence)."""
    comm = make_comm()
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    operator = _make_operator_with_comm(comm)
    success, msg = operator._run_state_machine("BLANK_CHECK")
    assert success is True


def test_read_happy_path(make_comm, fake_serial, tmp_path):
    """Characterize: read happy-path via _run_state_machine with data chunks.

    Feeds a single MSG_DATA_CHUNK frame (4 bytes of data) followed by
    MSG_MAIN_DONE.  The read callback writes the bytes to a temp file.
    """
    comm = make_comm()

    # Feed INIT done
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    # Feed a data chunk frame: MSG_DATA_CHUNK carries raw bytes as 'bytes' param.
    # build_frame(MSG_DATA_CHUNK, payload_bytes) encodes them as the frame body.
    chunk_data = b"\xde\xad\xbe\xef"
    fake_serial.feed(build_frame(MSG_DATA_CHUNK, chunk_data))
    # Feed MAIN done (signals end of data transfer)
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    # Feed END done
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    output_file = tmp_path / "out.bin"  # noqa: F841
    received = bytearray()

    def _collect_data(address, data_chunk):
        received.extend(data_chunk)

    operator = _make_operator_with_comm(comm)

    from firestarter.eprom_operations import EpromOperator  # noqa: F401

    success, msg = operator._run_state_machine(
        "READ",
        main_phase_handler=operator._main_phase_read_data,
        start_addr=0,
        end_addr=len(chunk_data),
        process_data_chunk_callback=_collect_data,
    )
    assert success is True
    assert bytes(received) == chunk_data


def test_write_happy_path(make_comm, fake_serial, tmp_path):
    """Characterize: write happy-path via _run_state_machine.

    The write MAIN phase uses a pull protocol: firmware sends OK to request
    each data block; host sends the block; firmware sends MAIN_DONE when done.

    Feed sequence:
      INIT_DONE → OK_REQ_DATA (firmware requests chunk) → MAIN_DONE → END_DONE
    """
    comm = make_comm()

    # Create a small input file
    input_file = tmp_path / "test.bin"
    file_data = b"\x01\x02\x03\x04"
    input_file.write_bytes(file_data)

    # Feed INIT done
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    # Feed one OK (firmware requests data block)
    fake_serial.feed(build_frame(MSG_OK_REQ_DATA, b""))
    # Feed MAIN done (firmware signals write complete after receiving data)
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    # Feed END done
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    operator = _make_operator_with_comm(comm)

    from firestarter.eprom_operations import EpromOperator  # noqa: F401

    success, msg = operator._run_state_machine(
        "WRITE",
        main_phase_handler=operator._main_phase_send_data,
        input_file_path=str(input_file),
        buffer_size=512,
    )
    assert success is True


def test_verify_happy_path(make_comm, fake_serial, tmp_path):
    """Characterize: verify happy-path via _run_state_machine.

    Verify uses the same _main_phase_send_data handler as write.
    """
    comm = make_comm()

    input_file = tmp_path / "test.bin"
    file_data = b"\xaa\xbb\xcc\xdd"
    input_file.write_bytes(file_data)

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_OK_REQ_DATA, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    operator = _make_operator_with_comm(comm)

    success, msg = operator._run_state_machine(
        "VERIFY",
        main_phase_handler=operator._main_phase_send_data,
        input_file_path=str(input_file),
        buffer_size=512,
    )
    assert success is True
