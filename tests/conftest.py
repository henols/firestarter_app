"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Shared pytest fixtures for the firestarter_app test suite.

This file is the host sub-repo's pytest entry point (first-ever pytest
infrastructure landed by Phase 6 Plan 03). It exposes:

    MAGIC_PREAMBLE_REF  — independent 4-byte magic preamble reference.
    _ref_crc8_ccitt     — table-free CRC8 reference (poly 0x07, seed 0x00).
    build_frame         — helper that assembles an ID-encoded wire frame.
    fake_serial         — fixture: BytesIO-backed serial port stand-in.
    make_comm           — fixture: factory for a SerialCommunicator bypassing
                          real serial I/O (uses __new__ + injected fake serial).
    collect_ignore      — Phase 127 / Plan 127-06 (HOST-04): conditional
                          collection gate excluding
                          tests/test_pyusb_api_surface.py when pyusb is not
                          importable.
    make_app_context    — Phase 132 Plan 05 (RETIRE-05, D-10): typed
                          keyword-only AppContext factory -- the shared
                          replacement for the four surviving per-module
                          untyped `**overrides` (typed `object`) copies.
    app_context         — fixture: thin no-argument wrapper around
                          make_app_context() for the common case.

The reference CRC implementation here is deliberately table-free (the
production code in firestarter.serial_comm uses a 256-byte lookup table).
A regression that mutates the production table off-spec — different
polynomial, wrong seed, accidental reflection — will mismatch this
reference and fail the test suite.
"""

from __future__ import annotations

import importlib.util
import io
import struct
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

# Phase 132 Plan 05 (RETIRE-05, D-10): the six real-class annotations below
# are for mypy only. Runtime imports of these same modules happen INSIDE
# make_app_context()'s body (see that function) -- never at module scope --
# because conftest's module-scope import set is deliberately free of any
# `firestarter` module. This tree has a recorded import-time-binding trap:
# the firmware-root constants, the board-choice list, and the prerelease-
# build check all freeze at import/collection time, so pulling a
# `firestarter` module into conftest's module scope would move when that
# freezing happens for the ENTIRE suite. `unittest.mock` is stdlib and is
# exempt from that concern.
if TYPE_CHECKING:
    from firestarter.cli_handlers import AppContext
    from firestarter.config import ConfigManager
    from firestarter.database import EpromDatabase
    from firestarter.eprom_info import EpromConsolePresenter
    from firestarter.eprom_operations import EpromOperator
    from firestarter.firmware import FirmwareManager
    from firestarter.hardware import HardwareManager


# ---------------------------------------------------------------------------
# Phase 127 / Plan 127-06 (HOST-04 / D-02) — optional-dependency collection
# gate.
#
# tests/test_pyusb_api_surface.py imports `usb.core` at module scope and is
# the FIRST test in this repo gated on an OPTIONAL DEPENDENCY rather than on
# cross-repo file presence (`tests.fw_presence.requires_fw`) or a
# CLI-on-PATH probe (test_characterization.py). It is meant to run only in
# the `ci-py32` CI job, which installs the `[py32]` extra.
#
# `collect_ignore` is used deliberately instead of a skip marker, because it
# produces a NON-COLLECTION rather than a skip -- so
# tests/test_skip_census.py's `ALLOWED_SKIP_REASONS` needs no fifth entry.
# Rejected alternatives: `pytest.importorskip("usb")` would emit a skip
# reason absent from that allow-list; `--ignore=` in `addopts` suppresses
# explicitly-named paths too, so `ci-py32` naming the file directly would
# need an `addopts` override just to run it.
#
# Fail-closed property (load-bearing): `collect_ignore` does NOT suppress a
# path named explicitly on the pytest command line. The `ci-py32` job
# invokes `pytest tests/test_pyusb_api_surface.py -q` -- naming the file
# directly -- so a missing `[py32]` extra surfaces there as a hard
# collection error, never a quiet pass.
#
# The `find_spec` probe is wrapped so a broken installation raising
# ImportError/ValueError is treated as ABSENT rather than propagating out of
# conftest import -- a conftest that raises takes the entire suite down.
def _pyusb_is_absent() -> bool:
    try:
        return importlib.util.find_spec("usb") is None
    except (ImportError, ValueError):
        return True


collect_ignore: list = []
if _pyusb_is_absent():
    collect_ignore.append("test_pyusb_api_surface.py")

# Module-level reference constants — independent of firestarter.serial_comm
# so tests do not pass tautologically on a bug in the production constant.
MAGIC_PREAMBLE_REF: bytes = b"\xaa\x55\xaa\x55"


def _ref_crc8_ccitt(data: bytes) -> int:
    """Reference CRC8 — poly 0x07, seed 0x00, no reflection, no final XOR.

    Table-FREE so tests catch a regression in the production lookup table.
    """
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_frame(msg_id: int, params: bytes) -> bytes:
    """Assemble a wire frame: magic | len_u16 | id | params | crc | 0x0A.

    `len` (u16, big-endian) counts (id + params + crc) per Phase 8 W-04.
    The trailing 0x0A is a re-sync anchor (not a delimiter — length is
    authoritative).
    """
    body = bytes([msg_id]) + params
    crc = _ref_crc8_ccitt(body)
    length = len(body) + 1  # id + params + crc
    return MAGIC_PREAMBLE_REF + struct.pack(">H", length) + body + bytes([crc, 0x0A])


class _FakeSerial:
    """BytesIO-backed stand-in for a `serial.Serial` instance.

    Implements only the surface that `SerialCommunicator._read_and_parse_lines`
    consumes: `read(n)` returning up to n bytes (b'' on empty — matches pyserial
    timeout-empty semantics), `is_open`, `in_waiting`, `port`, `timeout`,
    `write(...)`, `flush()`, and `close()`.
    """

    def __init__(self) -> None:
        self._buf = io.BytesIO()
        self._read_pos = 0
        self._write_pos = 0
        self.is_open = True
        self.port = "/dev/null"
        self.timeout = 0.1

    # --- read side ---
    def read(self, n: int = 1) -> bytes:
        self._buf.seek(self._read_pos)
        data = self._buf.read(n)
        self._read_pos = self._buf.tell()
        return data

    def readline(self) -> bytes:
        self._buf.seek(self._read_pos)
        data = self._buf.readline()
        self._read_pos = self._buf.tell()
        return data

    @property
    def in_waiting(self) -> int:
        end = self._write_pos
        return max(0, end - self._read_pos)

    # --- write side (unused by decoder tests, but kept for completeness) ---
    def write(self, data: bytes) -> int:
        self._buf.seek(self._write_pos)
        n = self._buf.write(data)
        self._write_pos = self._buf.tell()
        return n

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False

    # --- test-side helper ---
    def feed(self, data: bytes) -> None:
        """Append bytes to the readable buffer (test-side injection)."""
        self._buf.seek(self._write_pos)
        self._buf.write(data)
        self._write_pos = self._buf.tell()


@pytest.fixture
def fake_serial() -> _FakeSerial:
    """Return a fresh BytesIO-backed fake serial port."""
    return _FakeSerial()


@pytest.fixture
def make_comm(fake_serial):
    """Factory: build a SerialCommunicator wired to the fake serial port.

    Uses `__new__` to bypass `__init__` (which would try to open a real
    serial.Serial). Per PATTERNS §"firestarter_app/tests/test_decoder.py".
    """
    from firestarter.serial_comm import SerialCommunicator

    def _factory():
        instance = SerialCommunicator.__new__(SerialCommunicator)
        instance.connection = fake_serial
        instance.port_name = "/dev/null"
        instance.baud_rate = 250000
        instance.timeout = 0.1
        instance.programmer_info = None
        # Phase-53: mirror SerialCommunicator.__init__ attribute (T-53-03 default)
        instance._fault_inject_outgoing = None
        # Phase-53: firmware-advertised DATA_BUFFER_SIZE (None until probed)
        instance.firmware_buffer_size = None
        # Phase-54 (EVEN-01): firmware-advertised MAIN-path decode capacity (None until probed)
        instance.firmware_max_chunk = None
        # Phase-120 (D-15 / HOST-06): bounded per-connection observed-id record
        instance.seen_message_ids = set()
        return instance

    return _factory


def make_app_context(
    *,
    db: EpromDatabase | Mock | None = None,
    config_manager: ConfigManager | Mock | None = None,
    eprom_operator: EpromOperator | Mock | None = None,
    hardware_manager: HardwareManager | Mock | None = None,
    firmware_manager: FirmwareManager | Mock | None = None,
    eprom_presenter: EpromConsolePresenter | Mock | None = None,
) -> AppContext:
    """Construct a minimal, hardware-free AppContext (Phase 132, RETIRE-05, D-10).

    Keyword-only, six parameters, one per AppContext field. Each parameter
    defaults to `None`, meaning "build the hardware-free default": a
    database constructed with `skip_local_override=True`, a fresh
    ConfigManager, and a `Mock(spec=...)` double for each of the four
    managers. Passing a value uses it verbatim (identity, not a copy). No
    real serial port or bench access is ever opened, by any code path here.

    D-10 risk A (why every value passes through `cast(...)` below, once,
    here, and NOT at the call sites or the mock builders): a caller-supplied
    double is typically `Mock(spec=RealClass)`, whose STATIC type is `Mock`,
    not `RealClass`. Annotating a parameter as `RealClass | None` alone does
    not remove an error -- it MOVES it to every call site that passes a
    double (measured: ~25 such sites across the four surviving modules,
    23 of them in tests/test_dev_test_cmd.py alone). Casting at the mock
    BUILDERS instead was also rejected, measured rather than aesthetic:
    those variables are used for mock assertions afterward
    (`operator.write_eprom.assert_called_once_with(...)`), and a variable
    typed as the real class has no such attribute, so every assertion site
    becomes an [attr-defined] error instead -- a relocation, not a fix. The
    tree already carries the receipt: test_validate_family_cmd.py:221 has a
    `# type: ignore[attr-defined]` on exactly that shape, and
    test_dev_test_cmd.py:597-598 carry two live errors of that same class
    today. So: each parameter accepts the real type, a `Mock`, or `None`;
    each of the six values is narrowed to the real field type with an
    explicit `cast` at the one seam where a deliberately-substituted double
    is admitted into the container.

    What the cast buys, stated precisely so nobody overclaims it: a
    wrong-typed NON-double argument -- a string, an int, the wrong manager
    class entirely -- is still rejected at the call site, a property the
    old untyped `**overrides` (typed `object`) splat never had. What it does NOT buy: the
    cast does not verify that a double's `spec=` actually matches the field
    it is cast to -- that is a runtime check, and it stays on the `spec=`
    argument where it already lives, not here.

    D-10 risk B residual, stated plainly rather than left unexamined: this
    module (`tests.conftest`) deliberately does NOT join any mypy strict
    island. This factory's own body is checked anyway, because
    `check_untyped_defs` only ever governs the bodies of UNANNOTATED defs,
    and this def is fully annotated -- it is type-checked from birth, with
    zero `pyproject.toml` change. What that does NOT cover: conftest's
    pre-existing unannotated fixtures (e.g. `make_comm`'s inner `_factory`)
    remain unchecked. RETIRE-05's guarantee is specifically "this factory's
    body is checked, and a new module importing it cannot reproduce the
    old 30-error splat pattern" -- not "everything in conftest.py is
    type-checked".
    """
    # Deferred, in-body imports -- see the module-level comment above the
    # TYPE_CHECKING block for why these cannot move to module scope.
    from firestarter.cli_handlers import AppContext
    from firestarter.config import ConfigManager
    from firestarter.database import EpromDatabase
    from firestarter.eprom_info import EpromConsolePresenter
    from firestarter.eprom_operations import EpromOperator
    from firestarter.firmware import FirmwareManager
    from firestarter.hardware import HardwareManager

    if db is None:
        db = EpromDatabase(skip_local_override=True)
    if config_manager is None:
        config_manager = ConfigManager()
    if eprom_operator is None:
        eprom_operator = Mock(spec=EpromOperator)
    if hardware_manager is None:
        hardware_manager = Mock(spec=HardwareManager)
    if firmware_manager is None:
        firmware_manager = Mock(spec=FirmwareManager)
    if eprom_presenter is None:
        eprom_presenter = Mock(spec=EpromConsolePresenter)

    # The seam: each value here is either the real class or a deliberately
    # admitted test double (Mock(spec=...)); the cast declares that
    # substitution to mypy rather than suppressing a real defect. See the
    # docstring above for what this does and does not guarantee.
    return AppContext(
        db=cast("EpromDatabase", db),
        config_manager=cast("ConfigManager", config_manager),
        eprom_operator=cast("EpromOperator", eprom_operator),
        hardware_manager=cast("HardwareManager", hardware_manager),
        firmware_manager=cast("FirmwareManager", firmware_manager),
        eprom_presenter=cast("EpromConsolePresenter", eprom_presenter),
    )


@pytest.fixture
def app_context() -> AppContext:
    """Return a default AppContext for the common no-argument case.

    Serves only the no-variation case; per-test variation (a real operator,
    a fixed port, a caller-supplied database) goes through
    `make_app_context(...)` directly -- that variation is exactly why the
    four surviving test modules kept their own local factory instead of
    being forced onto a single shared fixture (D-10).
    """
    return make_app_context()
