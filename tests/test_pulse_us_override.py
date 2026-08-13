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

**143-07's own half (appended below the four transport tests above):** six
``click.testing.CliRunner`` cases proving HOST-04's end-to-end CLI path plus
all of HOST-05 -- the option's ``click.IntRange(1, 65535)`` bounds refusing
at parse time with exit 2 (D-15, corrected mechanism: nothing in ``cli()``
or ``AppContext`` construction opens a port, NOT the stated-but-false
"before ``AppContext`` builds"), the mandatory D-17 report line, the
Pitfall-3 ``default=None`` regression guard, and the write-only scope
(D-18). Uses its own CLI-level ``_drive_write_via_cli`` driver (distinct
from this half's ``write_eprom``-direct ``_drive_write_for_pulse_override``
above) and its own local ``make_app_context()``, both modelled on
``tests/test_write_skip_sdp_unlock.py`` per that module's own docstring
rationale (a REAL ``EpromOperator``, not a ``Mock``, because these tests
prove a value reaches the composed wire ``command_dict``, which only a real
``EpromOperator`` composes).
"""

from __future__ import annotations

import copy
import logging
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from firestarter.chip_resolver import resolve_chip
from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.messages import (
    MSG_END_DONE,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_REQ_DATA,
)
from firestarter.serial_comm import SerialCommunicator

from .conftest import _FakeSerial, build_frame
from .conftest import make_app_context as _make_app_context

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


# ---------------------------------------------------------------------------
# Plan 143-07: the CLI half -- the `--pulse-us` option itself, its
# `click.IntRange(1, 65535)` bounds (D-15/HOST-05), the mandatory D-17
# report line, and the write-only scope (D-18). Six `CliRunner` cases.
# ---------------------------------------------------------------------------


def make_app_context(
    *,
    db: EpromDatabase | Mock | None = None,
    config_manager: ConfigManager | Mock | None = None,
    eprom_operator: EpromOperator | Mock | None = None,
    hardware_manager: HardwareManager | Mock | None = None,
    firmware_manager: FirmwareManager | Mock | None = None,
    eprom_presenter: EpromConsolePresenter | Mock | None = None,
) -> AppContext:
    """Typed local delegate onto ``tests/conftest.py``'s shared factory --
    copied from ``tests/test_write_skip_sdp_unlock.py``'s own module-level
    helper of the same name (not imported across test modules; duplicated,
    matching this module's own existing ``_fresh_serial_and_comm``
    precedent above). Preserves that helper's one non-default behaviour:
    ``eprom_operator`` defaults to a REAL ``EpromOperator``, not a
    ``Mock``, because ``_drive_write_via_cli`` below proves a value reaches
    the composed wire ``command_dict``, which only a real ``EpromOperator``
    composes.
    """
    if config_manager is None:
        config_manager = ConfigManager()
    if eprom_operator is None:
        eprom_operator = EpromOperator(config_manager)
    return _make_app_context(
        db=db,
        config_manager=config_manager,
        eprom_operator=eprom_operator,
        hardware_manager=hardware_manager,
        firmware_manager=firmware_manager,
        eprom_presenter=eprom_presenter,
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _drive_write_via_cli(
    runner: CliRunner,
    tmp_path,
    make_comm,
    fake_serial,
    extra_args: list | None = None,
):
    """Invoke ``firestarter write <chip> <file> [extra_args]`` end to end
    through ``click.testing.CliRunner``, capturing the composed
    ``command_dict`` at the transport boundary
    (``SerialCommunicator.find_and_connect``).

    Distinct from this module's ``_drive_write_for_pulse_override`` (143-04
    half, above): that driver calls ``write_eprom`` DIRECTLY with no CLI in
    the loop, so it cannot exercise Click's own ``--pulse-us`` option, its
    ``IntRange`` bounds, or the D-17 report line -- all three are this
    plan's job. Modelled on ``tests/test_write_skip_sdp_unlock.py``'s
    ``_drive_write``: the same four-frame ``INIT_DONE`` -> ``OK_REQ_DATA``
    -> ``MAIN_DONE`` -> ``END_DONE`` feed sequence and the same
    ``captured["command_dict"]`` closure. That dict stays EMPTY if the
    write is refused before ``find_and_connect`` is ever called -- the
    ready-made oracle HOST-05's no-port-opened claim needs (case 4, below).

    Always passes ``obj=app`` (a pre-built ``AppContext``): ``cli()``'s own
    group callback special-cases this (``if ctx.obj is not None and
    isinstance(ctx.obj, AppContext): return``) and returns immediately,
    BEFORE constructing a real ``EpromDatabase``/``HardwareManager``/
    ``FirmwareManager`` -- so even the refusal-path tests below (cases 3, 4
    and 6) stay hardware- and disk-free regardless of which subcommand they
    target.

    Returns ``(result, captured)``.
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

    app = make_app_context()
    args = ["write", _REAL_27C_CHIP, str(input_file), *(extra_args or [])]
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake_find_and_connect,
    ):
        result = runner.invoke(cli, args, obj=app)
    return result, captured


def test_override_reaches_the_wire_through_the_cli(
    runner: CliRunner, tmp_path, make_comm, fake_serial
) -> None:
    """HOST-04, proven end to end through the REAL CLI -- complementing
    plan 143-04's direct ``write_eprom``-level test (above) with the same
    claim reached via ``firestarter write <chip> <file> --pulse-us N``.
    """
    result, captured = _drive_write_via_cli(
        runner, tmp_path, make_comm, fake_serial, extra_args=["--pulse-us", "1234"]
    )
    assert result.exit_code == 0, (
        f"expected a successful write; got exit {result.exit_code}, "
        f"output:\n{result.output}"
    )
    assert captured["command_dict"]["pulse-delay"] == 1234, (
        "HOST-04: --pulse-us must reach the wire's existing 'pulse-delay' "
        f"key verbatim through the real CLI; got "
        f"{captured['command_dict'].get('pulse-delay')!r}"
    )


def test_override_always_reports(
    runner: CliRunner, tmp_path, make_comm, fake_serial, caplog
) -> None:
    """D-17: every ``--pulse-us`` invocation prints a MANDATORY,
    default-visible provenance line -- no ``-v`` needed -- naming BOTH the
    database pulse it replaced and the override that replaced it, plus the
    chip. Reason (verbatim intent): a bench artifact or log captured
    without the command line beside it cannot otherwise tell you the pulse
    was not the database's, and Phase 145's evidence will be read by
    strangers. Per-substring assertions, each with its own message, rather
    than one opaque string compare -- so a future wording tweak that
    silently drops one required fact fails on exactly that fact, not an
    unrelated one.
    """
    caplog.set_level(logging.DEBUG)
    result, _captured = _drive_write_via_cli(
        runner, tmp_path, make_comm, fake_serial, extra_args=["--pulse-us", "1234"]
    )
    assert result.exit_code == 0, (
        f"expected a successful write; output:\n{result.output}"
    )
    output = result.output

    assert "W27C512" in output, (
        f"D-17: the report line must name the chip; output:\n{output}"
    )
    assert "1234" in output, (
        f"D-17: the report line must name the override value (1234); output:\n{output}"
    )
    assert "100" in output, (
        "D-17: the report line must name the database pulse it replaced "
        f"(100 us for w27c512); output:\n{output}"
    )
    assert "not the database" in output.lower(), (
        "D-17: the report line must state plainly that this run's timing "
        f"is NOT the database's; output:\n{output}"
    )

    # S-6 / D-17: the line must be produced by click.echo, never
    # logger.info -- this invocation passed NO -v flag, and no log record
    # captured at DEBUG level (the lowest possible threshold) contains the
    # D-17 line's own distinguishing phrase, so the line cannot have
    # travelled through the logging module at all. NOTE: a substring as
    # loose as "pulse" is NOT safe here -- the write pipeline's own
    # pre-existing DEBUG logging (_setup_operation) logs the whole EPROM
    # data dict, which legitimately contains the "pulse-delay" key; that is
    # unrelated production logging, not the D-17 line, so the needle must
    # be specific to the D-17 line's own wording.
    assert not any(
        "overrides the database" in r.getMessage().lower() for r in caplog.records
    ), (
        "D-17: the report line must not be emitted via the logging module "
        f"at all; unexpected log records: {[r.getMessage() for r in caplog.records]}"
    )


# RESEARCH Pattern 7's measured message shape (click 8.4.2, verified in
# .venv/ci-replica) -- see the module-level table cited by 143-07-PLAN.md's
# "The one measured trap" section. `abc`'s message never restates the
# numeric bound (it fails TYPE conversion, not a bound check), so its
# expected substrings are deliberately narrower than the two numeric cases'.
_REFUSAL_EXPECTED_SUBSTRINGS = {
    "0": ["0", "not in the range", "1<=x<=65535"],
    "65536": ["65536", "not in the range", "1<=x<=65535"],
    "abc": ["abc", "not a valid integer range"],
}


def test_out_of_range_is_refused_at_parse_time(
    runner: CliRunner, tmp_path, make_comm, fake_serial
) -> None:
    """HOST-05: an out-of-range or non-integer ``--pulse-us`` is refused at
    CLICK PARSE TIME with exit code 2 -- NOT 1, the app's usual
    ``sys.exit(1)`` failure code. Click's ``IntRange`` refusal is a
    ``click.UsageError``, raised during parameter processing BEFORE
    ``write()``'s body (and therefore before ``@map_typed_errors``, which
    decorates the body and never sees a parse-time exception) ever runs --
    that is why the assertion below is 2, not the app's usual 1.

    Three sub-cases, one test function (each gets its own message so a
    failure names exactly which value regressed): ``0`` and ``65536`` are
    numeric bound violations; ``abc`` fails ``IntRange``'s own integer
    conversion before the bound is ever checked.
    """
    for bad_value in ("0", "65536", "abc"):
        result, _captured = _drive_write_via_cli(
            runner,
            tmp_path,
            make_comm,
            fake_serial,
            extra_args=["--pulse-us", bad_value],
        )
        assert result.exit_code == 2, (
            f"HOST-05: --pulse-us {bad_value!r} must be refused at parse "
            f"time with exit 2 (a click.UsageError), not the app's usual "
            f"exit 1; got {result.exit_code}, output:\n{result.output}"
        )
        for substring in _REFUSAL_EXPECTED_SUBSTRINGS[bad_value]:
            assert substring in result.output, (
                f"HOST-05: refusing --pulse-us {bad_value!r} must mention "
                f"{substring!r} (an actionable message naming the "
                f"offending value and the accepted range); "
                f"output:\n{result.output}"
            )


def test_refusal_opens_no_port(
    runner: CliRunner, tmp_path, make_comm, fake_serial
) -> None:
    """HOST-05's wording, verbatim: a refused ``--pulse-us`` value must
    open NO serial port at all -- ``find_and_connect`` must never be
    called, so no serial byte is ever sent. Same three sub-cases as
    ``test_out_of_range_is_refused_at_parse_time``, one test function.

    Corrected mechanism (D-15): the guarantee is NOT "before ``AppContext``
    builds" -- ``cli()``'s group callback runs FIRST, before ``write()``'s
    own parameters are type-converted, so ``AppContext`` already exists by
    the time Click refuses ``--pulse-us``. The guarantee that actually
    holds is that NOTHING in ``cli()`` or ``AppContext`` construction opens
    a port -- port-opening is confined to
    ``SerialCommunicator.find_and_connect``, called only from inside
    ``write_eprom``'s own body, which a parse-time refusal never reaches.
    A parse-time refusal is therefore structurally before any serial byte,
    for this reason, not the stated-but-false one.
    """
    for bad_value in ("0", "65536", "abc"):
        _result, captured = _drive_write_via_cli(
            runner,
            tmp_path,
            make_comm,
            fake_serial,
            extra_args=["--pulse-us", bad_value],
        )
        assert captured == {}, (
            f"HOST-05: --pulse-us {bad_value!r} must be refused before "
            f"find_and_connect is ever called -- no serial byte may be "
            f"sent; got captured={captured!r}"
        )


def test_write_without_pulse_us_still_works(
    runner: CliRunner, tmp_path, make_comm, fake_serial
) -> None:
    """RESEARCH Pitfall 3 regression guard: ``firestarter write CHIP
    file.bin`` with NO ``--pulse-us`` must still exit 0.
    ``click.IntRange(1, 65535)`` type-casts the option's DEFAULT, not just
    a user-supplied value -- ``default=0`` would be out of range and would
    raise a ``UsageError`` before ``write()``'s body ever runs, breaking
    EVERY ``write`` invocation that supplies no flag at all. The CI smoke
    step (``firestarter --help``) never invokes ``write``, so nothing else
    in this tree catches a ``default=0`` regression -- this test is the
    only thing that does.
    """
    result, captured = _drive_write_via_cli(runner, tmp_path, make_comm, fake_serial)
    assert result.exit_code == 0, (
        f"Pitfall 3 regression: a bare `write` with no --pulse-us must "
        f"exit 0; got {result.exit_code}, output:\n{result.output}"
    )
    assert captured["command_dict"]["pulse-delay"] == 100, (
        "with no --pulse-us, the wire 'pulse-delay' must equal the "
        f"database value (100 us for w27c512); got "
        f"{captured['command_dict'].get('pulse-delay')!r}"
    )


def test_flag_is_write_only(runner: CliRunner, tmp_path) -> None:
    """D-18: ``--pulse-us`` is exposed on ``write`` ONLY -- ``read``,
    ``verify``, ``blank`` and ``erase`` emit no program pulse, so there is
    nothing to override (mirrors the reasoning that kept
    ``--skip-sdp-unlock`` on ``write`` alone, v1.22 D-17/D-18).

    Two independent proofs: (a) a runtime ``CliRunner`` invocation of each
    of the other four commands with ``--pulse-us`` exits 2 as an
    unrecognised option -- no serial fixture needed, since Click refuses
    before the handler runs; (b) a source-level check of the Click
    ``Command`` objects' own registered parameter names, which survives a
    future change to Click's error wording.
    """
    app = make_app_context()
    other_commands = {
        "read": ["read", _REAL_27C_CHIP],
        "verify": ["verify", _REAL_27C_CHIP, "dummy.bin"],
        "blank": ["blank", _REAL_27C_CHIP],
        "erase": ["erase", _REAL_27C_CHIP],
    }
    for name, base_args in other_commands.items():
        result = runner.invoke(cli, [*base_args, "--pulse-us", "100"], obj=app)
        assert result.exit_code == 2, (
            f"D-18: `{name} --pulse-us` must be refused as an unrecognised "
            f"option (exit 2); got {result.exit_code}, "
            f"output:\n{result.output}"
        )
        assert "no such option" in result.output.lower(), (
            f"D-18: `{name} --pulse-us` refusal must name the unrecognised "
            f"option; output:\n{result.output}"
        )

    param_names = {
        name: {p.name for p in cmd.params}
        for name, cmd in cli.commands.items()
        if name in ("write", "read", "verify", "blank", "erase")
    }
    assert "pulse_us" in param_names["write"], (
        "D-18: write's own Click Command object must register a "
        f"'pulse_us' parameter; got {param_names['write']}"
    )
    for name in ("read", "verify", "blank", "erase"):
        assert "pulse_us" not in param_names[name], (
            f"D-18: {name}'s Click Command object must NOT register a "
            f"'pulse_us' parameter; got {param_names[name]}"
        )
