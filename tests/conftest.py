"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Shared pytest fixtures and helpers for the firestarter_app test suite.

Wire: MAGIC_PREAMBLE_REF, _ref_crc8_ccitt, id_frame_body, build_frame,
decoded_response, write_success_frames, read_phase_frames.
Port and comm: fake_serial, AnsweringSerial, PortDropSerial, port_opens,
make_comm, comm_on, fresh_serial_and_comm, connect_via.
CLI: runner, make_app_context, adapter_required_db.
Autouse: _offline_gh, _short_drain_window, _isolated_config.

The reference CRC implementation here is deliberately table-free (the
production code in firestarter.serial_comm uses a 256-byte lookup table).
A regression that mutates the production table off-spec — different
polynomial, wrong seed, accidental reflection — will mismatch this
reference and fail the test suite.
"""

from __future__ import annotations

import importlib.util
import os
import struct
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

# The six real-class annotations below
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

    from .fake_chip import FakeChip


# tests/test_pyusb_api_surface.py imports `usb.core` at module scope and is
# gated on an OPTIONAL DEPENDENCY rather than on a CLI-on-PATH probe
# (test_characterization.py). It is meant to run only in
# the `ci-py32` CI job, which installs the `[py32]` extra.
#
# `collect_ignore` is used deliberately instead of a skip marker, because it
# produces a NON-COLLECTION rather than a skip.
# Rejected alternative: `--ignore=` in `addopts` suppresses
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


# `dev test` runs a `gh issue list --author @me` dedup query through
# `submit.find_prior_report`, bound as a default argument, so patching the
# module attribute does not reach it. A stub `gh` first on PATH answers every
# call as "not authenticated" (exit 4): no test waits on GitHub, depends on the
# developer's gh login, or branches on their real issues.
@pytest.fixture(autouse=True)
def _offline_gh(tmp_path_factory, monkeypatch):
    bin_dir = tmp_path_factory.getbasetemp() / "offline-gh-bin"
    gh = bin_dir / "gh"
    if not gh.exists():
        bin_dir.mkdir(exist_ok=True)
        gh.write_text("#!/bin/sh\nexit 4\n")
        gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


# `disconnect()` drains the port with `consume_remaining_input(timeout=0.5)`.
# A fake serial port answers every read at once, so the drain only ends when
# the window runs out: 0.5 s of wall time per disconnect, a full second for a
# write with a guard read. Any parsed frame restarts the window, so a short one
# still drains everything a test fed. An explicit timeout is kept.
@pytest.fixture(autouse=True)
def _short_drain_window(monkeypatch):
    from firestarter.serial_comm import SerialCommunicator

    drain = SerialCommunicator.consume_remaining_input

    def _short_drain(self, timeout: float = 0.02) -> None:
        drain(self, timeout)

    monkeypatch.setattr(SerialCommunicator, "consume_remaining_input", _short_drain)


# Every test gets its own empty config directory and a fresh ConfigManager
# singleton, so no test reads the developer's ~/.firestarter (config, local
# database or pin-map overrides) or sees another test's settings.
@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    from firestarter import config, firmware

    config_dir = str(tmp_path / "firestarter-config")
    monkeypatch.setenv("FIRESTARTER_CONFIG_DIR", config_dir)
    monkeypatch.delenv("FIRESTARTER_DEV_ALLOW_PRE_V12", raising=False)
    monkeypatch.setattr(config, "HOME_PATH", config_dir)
    monkeypatch.setattr(config, "DATABASE_FILE", f"{config_dir}/database.json")
    monkeypatch.setattr(config, "PIN_MAP_FILE", f"{config_dir}/pin-maps.json")
    monkeypatch.setattr(firmware, "HOME_PATH", config_dir)
    monkeypatch.setattr(config.ConfigManager, "_instances", {})
    monkeypatch.setattr(config.ConfigManager, "_initialized_configs", {})


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


def id_frame_body(msg_id: int, params: bytes = b"") -> bytes:
    """The body of one id frame: id | params | crc8 (reference CRC)."""
    body = bytes([msg_id]) + params
    return body + bytes([_ref_crc8_ccitt(body)])


def build_frame(msg_id: int, params: bytes) -> bytes:
    """Assemble a wire frame: magic | len_u16 | id | params | crc | 0x0A.

    `len` (u16, big-endian) counts id + params + crc. The trailing 0x0A is a
    re-sync anchor (not a delimiter: the length is authoritative).
    """
    body = id_frame_body(msg_id, params)
    return MAGIC_PREAMBLE_REF + struct.pack(">H", len(body)) + body + b"\x0a"


def decoded_response(msg_id: int, params: bytes):
    """The Response the host builds for one id frame, through the real decoder."""
    from firestarter.codec import decode_id_frame
    from firestarter.frame_parser import Response

    body = id_frame_body(msg_id, params)
    decoded = decode_id_frame(len(body), body)
    assert decoded is not None, f"id 0x{msg_id:02x} did not decode"
    return Response(
        type=decoded.severity,
        message=decoded.text,
        payload=decoded.payload,
        id=decoded.id,
        params=decoded.params,
    )


class _FakeSerial:
    """Stand-in for a `serial.Serial` instance.

    The two directions are separate, as on a real port: `feed()` queues bytes
    for the host to read, and every host `write()` is recorded in `written`.
    Implements only the surface that `SerialCommunicator` uses: `read(n)`
    (b'' when empty, as a pyserial timeout), `readline()`, `in_waiting`,
    `is_open`, `port`, `timeout`, `write()`, `flush()` and `close()`.
    """

    def __init__(self) -> None:
        self._rx = bytearray()
        self.written: list[bytes] = []
        self.is_open = True
        self.port = "/dev/null"
        self.timeout = 0.1

    def read(self, n: int = 1) -> bytes:
        data = bytes(self._rx[:n])
        del self._rx[:n]
        return data

    def readline(self) -> bytes:
        end = self._rx.find(b"\n")
        return self.read(len(self._rx) if end < 0 else end + 1)

    @property
    def in_waiting(self) -> int:
        return len(self._rx)

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False

    def feed(self, data: bytes) -> None:
        """Queue bytes for the host to read (test-side injection)."""
        self._rx += data


@pytest.fixture
def fake_serial() -> _FakeSerial:
    """Return a fresh fake serial port."""
    return _FakeSerial()


class AnsweringSerial(_FakeSerial):
    """A fake port that queues one reply for each host write, in order.

    The board answers only after the command arrives, so the drain before
    the send finds nothing, as on a board that resets when the port opens.
    """

    def __init__(self, *replies: bytes) -> None:
        super().__init__()
        self._replies = list(replies)

    def write(self, data: bytes) -> int:
        written = super().write(data)
        if self._replies:
            self.feed(self._replies.pop(0))
        return written


class PortDropSerial(_FakeSerial):
    """A fake port that raises `serial.SerialException` once, on the first
    read after its fed bytes run out: a port drop mid-operation, not a plain
    timeout. After that it returns empty reads, so the disconnect drain does
    not see a second exception."""

    def __init__(self) -> None:
        super().__init__()
        self._raised_once = False

    def read(self, n: int = 1) -> bytes:
        data = super().read(n)
        if not data and not self._raised_once:
            self._raised_once = True
            import serial

            raise serial.SerialException("simulated port drop mid-operation")
        return data


@contextmanager
def port_opens(serial: _FakeSerial):
    """Every SerialCommunicator constructed in the block opens `serial`.

    The real `__init__` runs; only `serial.Serial` (the one I/O call in it)
    and the port-settle delay are replaced.
    """
    from unittest.mock import patch

    from firestarter import serial_comm

    with (
        patch.object(serial_comm.serial, "Serial", return_value=serial),
        patch.object(serial_comm, "CONNECTION_STABILIZE_DELAY", 0),
    ):
        yield serial


def comm_on(serial: _FakeSerial, *, port_name: str = "/dev/null"):
    """A real `SerialCommunicator` whose port is `serial`."""
    from firestarter.serial_comm import SerialCommunicator

    with port_opens(serial):
        return SerialCommunicator(port_name)


def fresh_serial_and_comm(*, port_name: str = "/dev/null"):
    """A new fake port and a factory of communicators on it.

    For tests that drive more than one operation: a finished operation closes
    its port, so each drive needs its own pair.
    """
    serial = _FakeSerial()
    return serial, lambda: comm_on(serial, port_name=port_name)


@pytest.fixture
def make_comm(fake_serial):
    """Factory: build a SerialCommunicator wired to the `fake_serial` port."""
    return lambda: comm_on(fake_serial)


@contextmanager
def connect_via(factory, on_connect=None):
    """Patch `SerialCommunicator.find_and_connect` to return `factory()`.

    Yields the list of command dicts the operator connected with, so a test
    can assert on what the host composed. `on_connect(comm)` runs on each new
    communicator before the operator uses it.
    """
    from unittest.mock import patch

    captured: list[dict] = []

    def _fake(command_dict, config, **kwargs):
        captured.append(dict(command_dict))
        comm = factory()
        if on_connect is not None:
            on_connect(comm)
        return comm

    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake,
    ):
        yield captured


def write_success_frames() -> list[bytes]:
    """Firmware replies for one complete, successful write operation."""
    from firestarter.messages import (
        MSG_END_DONE,
        MSG_INIT_DONE,
        MSG_MAIN_DONE,
        MSG_OK_REQ_DATA,
    )

    return [
        build_frame(MSG_INIT_DONE, b""),
        build_frame(MSG_OK_REQ_DATA, b""),
        build_frame(MSG_MAIN_DONE, b""),
        build_frame(MSG_END_DONE, b""),
    ]


def read_phase_frames(payload: bytes, chunk: int = 512) -> list[bytes]:
    """Firmware replies for one complete read that delivers `payload`.

    INIT_DONE, the payload as DATA_CHUNK frames of `chunk` bytes, MAIN_DONE,
    END_DONE.
    """
    from firestarter.messages import (
        MSG_DATA_CHUNK,
        MSG_END_DONE,
        MSG_INIT_DONE,
        MSG_MAIN_DONE,
    )

    chunks = [payload[i : i + chunk] for i in range(0, len(payload), chunk)] or [b""]
    return [
        build_frame(MSG_INIT_DONE, b""),
        *(build_frame(MSG_DATA_CHUNK, c) for c in chunks),
        build_frame(MSG_MAIN_DONE, b""),
        build_frame(MSG_END_DONE, b""),
    ]


@pytest.fixture
def runner():
    """A Click test runner."""
    from click.testing import CliRunner

    return CliRunner()


def make_app_context(
    *,
    db: EpromDatabase | Mock | None = None,
    config_manager: ConfigManager | Mock | None = None,
    eprom_operator: EpromOperator | Mock | FakeChip | None = None,
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
    # admitted test double -- Mock(spec=...) throughout, plus `FakeChip` for
    # `eprom_operator` (a hand-written stateful double modelling the real
    # absolute-offset read/write semantics a Mock cannot); the cast
    # declares that
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


# The shipped database has no adapter-required row since the DIP24_2816 family
# was promoted to 0x0D. The status and its host code path still exist: the
# generator guard can still emit one for a 24-pin erasable EPROM-proto row on
# another pin map. Tests of that path use this synthetic row.
ADAPTER_REQUIRED_TEST_CHIP = "ADAPTERTEST24"
ADAPTER_REQUIRED_TEST_REASON = (
    "adapter required: synthetic test row for the adapter-required host path"
)


def adapter_required_db() -> EpromDatabase:
    """A hardware-free database plus one synthetic adapter-required row.

    The row is a copy of the AT28C16 row with its own part number, so every
    field that the host reads is realistic. Only the name, the status and the
    reason differ.
    """
    import copy

    from firestarter.database import EpromDatabase

    db = EpromDatabase(skip_local_override=True)
    raw, manufacturer = db.get_eprom_config("AT28C16")
    row = copy.deepcopy(raw)
    row["part_number"] = ADAPTER_REQUIRED_TEST_CHIP
    row["support_status"] = "adapter-required"
    row["unsupported_reason"] = ADAPTER_REQUIRED_TEST_REASON
    db.proms[manufacturer].append(row)
    return db
