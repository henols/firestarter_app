"""HOST-04 transport-half tests for the ``write_eprom`` ``pulse_us`` override
(Phase 143 Plan 04, `143-04-PLAN.md` Task 3).

**This module is authored in two halves by two plans, and HOST-04 spans
both -- neither plan may mark it Complete.** This half (143-04) proves the
``write_eprom``-level transport: the override rides the existing
``"pulse-delay"`` DB-dict key onto the wire with no new key and no
mutation of the caller's dict, driving ``write_eprom`` DIRECTLY (no CLI).
Plan 143-07 extends this SAME module with the CLI (``--pulse-us``) half:
the ``IntRange`` bounds (D-15), the D-17 default-visible report line, and
``click.testing.CliRunner`` cases. ``requirements: []`` in both plans'
frontmatter is deliberate -- see ``143-04-PLAN.md``'s "Requirement
ownership" paragraph.

**Real chip used:** ``w27c512`` (``W27C512,W27E512`` in
``firestarter/data/chip_database.json``, algorithm 7 / protocol ``0x07``
EPROM_STD, database ``pulse-delay`` value 100 us) -- the same real 27C
part ``tests/test_write_response_budget.py`` uses, and already the shared
"non-0x0D" fixture chip in ``tests/test_write_skip_sdp_unlock.py``.

D-14's four recorded points (mirrored at the implementation site,
``firestarter/eprom_operations.py``'s ``write_eprom``):
(a) this is ``consistency_check_eprom``'s ``read_settling_us`` /
    ``read_strobe_us`` shape verbatim;
(b) ``"pulse-delay"`` is already emitted unconditionally by
    ``database.py``'s ``convert_to_programmer``, so the override REPLACES
    a value rather than adding a field -- structurally how "no new wire
    field and no new command" (HOST-04) is satisfied;
(c) the shallow copy exists so a caller that reuses its programmer dict
    for a second chip in a batch loop is unaffected;
(d) the ``1..65535`` bound and the D-17 report line are NOT this module's
    job -- they belong to plan 143-07's CLI half.
"""

from __future__ import annotations

import copy
from unittest.mock import patch

from firestarter.chip_resolver import resolve_chip
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.messages import (
    MSG_END_DONE,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_REQ_DATA,
)
from firestarter.serial_comm import SerialCommunicator

from .conftest import _FakeSerial, build_frame

# Real 27C part used throughout this module -- see the module docstring.
_REAL_27C_CHIP = "w27c512"


def _w27c512_programmer_dict() -> dict:
    """A real w27c512 programmer dict via resolve_chip (protocol 0x07 / algorithm 7)."""
    db = EpromDatabase(skip_local_override=True)
    return resolve_chip(_REAL_27C_CHIP, db=db)


def _fresh_serial_and_comm():
    """Build an independent ``(fake_serial, make_comm)`` pair.

    Mirrors ``tests/conftest.py``'s ``fake_serial``/``make_comm`` fixtures.
    Needed only by ``test_no_new_wire_field_is_added``, which drives two
    full writes: a successful write's ``SerialCommunicator`` closes its
    fake serial port at the end (``_FakeSerial.close()`` sets
    ``is_open = False``), so the second drive cannot reuse the same
    fixture-injected ``fake_serial`` instance. Precedent:
    ``tests/test_write_skip_sdp_unlock.py``'s own ``_fresh_serial_and_comm``.
    """
    serial = _FakeSerial()

    def _factory():
        instance = SerialCommunicator.__new__(SerialCommunicator)
        instance.connection = serial
        instance.port_name = "/dev/null"
        instance.baud_rate = 250000
        instance.timeout = 0.1
        instance.programmer_info = None
        instance._fault_inject_outgoing = None
        instance.firmware_buffer_size = None
        instance.firmware_max_chunk = None
        instance.firmware_identity = None
        instance.hw_revision = None
        instance.write_block_budget_s = None
        instance.seen_message_ids = set()
        return instance

    return serial, _factory


def _drive_write_for_pulse_override(
    tmp_path,
    make_comm,
    fake_serial,
    eprom_data_dict: dict,
    *,
    pulse_us: int = 0,
):
    """Drive a full, otherwise-successful ``write_eprom()`` against a real
    27C chip through a fake serial port, capturing the composed
    ``command_dict`` at the transport boundary
    (``SerialCommunicator.find_and_connect``).

    Mirrors ``tests/test_write_skip_sdp_unlock.py``'s ``_drive_write``,
    adapted to call ``write_eprom`` DIRECTLY -- no ``CliRunner`` here (plan
    143-07 adds the CLI cases to this same module).

    Returns ``(ok, captured)`` where ``captured["command_dict"]`` is the
    dict ``SerialCommunicator.find_and_connect`` received.
    """
    input_file = tmp_path / f"{_REAL_27C_CHIP}.bin"
    input_file.write_bytes(b"\x01\x02\x03\x04")

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_OK_REQ_DATA, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    captured: dict = {}

    def _fake_find_and_connect(command_dict, config, **kwargs):
        captured["command_dict"] = command_dict
        return make_comm()

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake_find_and_connect,
    ):
        ok = operator.write_eprom(
            _REAL_27C_CHIP,
            eprom_data_dict,
            str(input_file),
            pulse_us=pulse_us,
        )
    return ok, captured


def test_override_rides_the_db_dict(tmp_path, make_comm, fake_serial) -> None:
    """HOST-04 / D-14: ``write_eprom(..., pulse_us=1234)`` rides the
    existing ``"pulse-delay"`` wire key -- the composed ``command_dict``
    carries the override value verbatim.
    """
    ok, captured = _drive_write_for_pulse_override(
        tmp_path, make_comm, fake_serial, _w27c512_programmer_dict(), pulse_us=1234
    )
    assert ok is True, "HOST-04: the driven write must complete successfully"
    assert captured["command_dict"]["pulse-delay"] == 1234, (
        "HOST-04/D-14: pulse_us must override the wire 'pulse-delay' value "
        f"verbatim; got {captured['command_dict'].get('pulse-delay')!r}"
    )


def test_override_does_not_mutate_the_caller_dict(
    tmp_path, make_comm, fake_serial
) -> None:
    """D-14(c): the override must rebind a SHALLOW COPY, never mutate the
    caller's dict -- a mutation would silently change every subsequent
    chip in a batch loop that reuses the same programmer dict.
    """
    programmer_dict = _w27c512_programmer_dict()
    snapshot = copy.deepcopy(programmer_dict)

    ok, _captured = _drive_write_for_pulse_override(
        tmp_path, make_comm, fake_serial, programmer_dict, pulse_us=1234
    )
    assert ok is True, "HOST-04: the driven write must complete successfully"

    assert programmer_dict == snapshot, (
        "D-14: write_eprom's pulse_us override must not mutate the "
        "caller's dict in place -- a batch loop reusing this dict for a "
        f"later chip would silently inherit the override. Before: "
        f"{snapshot!r}; after: {programmer_dict!r}"
    )
    assert programmer_dict["pulse-delay"] == 100, (
        "D-14: the caller's own dict must still carry the database pulse "
        "value (100 us for w27c512), not the override; got "
        f"{programmer_dict.get('pulse-delay')!r}"
    )


def test_no_new_wire_field_is_added(tmp_path, make_comm, fake_serial) -> None:
    """HOST-04: the override adds ZERO new keys to the composed
    ``command_dict`` -- ``"pulse-delay"`` already existed. Drives
    ``write_eprom`` TWICE (with and without the override, on independent
    ``(serial, comm)`` pairs -- a successful write closes its fake serial
    port) and compares the two runs' key sets.
    """
    without_ok, without_captured = _drive_write_for_pulse_override(
        tmp_path, make_comm, fake_serial, _w27c512_programmer_dict(), pulse_us=0
    )
    assert without_ok is True, "HOST-04: the driven write must complete successfully"

    fresh_serial, fresh_comm = _fresh_serial_and_comm()
    with_ok, with_captured = _drive_write_for_pulse_override(
        tmp_path, fresh_comm, fresh_serial, _w27c512_programmer_dict(), pulse_us=1234
    )
    assert with_ok is True, "HOST-04: the driven write must complete successfully"

    without_keys = set(without_captured["command_dict"])
    with_keys = set(with_captured["command_dict"])
    assert with_keys == without_keys, (
        "HOST-04: --pulse-us must add NO new wire key -- the override "
        f"run's key set {with_keys} must equal the no-override run's key "
        f"set {without_keys}"
    )
    assert "pulse-us" not in with_keys and "pulse_us" not in with_keys, (
        "HOST-04: neither the CLI spelling ('pulse-us') nor the Python "
        f"spelling ('pulse_us') may ever become a wire key; got {with_keys}"
    )


def test_absent_flag_leaves_db_pulse(tmp_path, make_comm, fake_serial) -> None:
    """HOST-04: with ``pulse_us`` at its default (``0``, "not supplied"),
    the wire ``"pulse-delay"`` value is exactly the database's own value --
    unchanged.
    """
    programmer_dict = _w27c512_programmer_dict()
    db_pulse_delay = programmer_dict["pulse-delay"]

    ok, captured = _drive_write_for_pulse_override(
        tmp_path, make_comm, fake_serial, programmer_dict, pulse_us=0
    )
    assert ok is True, "HOST-04: the driven write must complete successfully"
    assert captured["command_dict"]["pulse-delay"] == db_pulse_delay, (
        "HOST-04: an absent --pulse-us must leave the database pulse-delay "
        f"value ({db_pulse_delay}) unchanged; got "
        f"{captured['command_dict'].get('pulse-delay')!r}"
    )
