"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 36 Plan 04 — Bug characterization suite (TEST-05).

Pins two latent bugs using pytest.mark.xfail(strict=True) asserting the
CORRECTED behavior. Each test auto-flips to XPASS when the fix lands
(strict=True makes XPASS a suite ERROR, forcing marker removal).

BUG-1: main.py:497 build_arg_flags uses 'in' not getattr -> fix Phase 41 (CLI-03)
  The expression `args.force if "force" in args else False` calls `__contains__`
  on the args object. argparse.Namespace supports this (coincidentally), but any
  plain Python object lacking `__contains__` raises TypeError. Click-provided
  objects (Phase 41) will not be argparse.Namespace, making this a forward
  compatibility bug. Corrected pattern: `getattr(args, "force", False)`.

BUG-2: eprom_operations.py:265 EpromOperationError lumped with SerialError ->
  fix Phase 42 (ERR-01)
  The except clause `except (SerialError, SerialTimeoutError, EpromOperationError)`
  logs all three as "Communication error during <op>". EpromOperationError means
  the firmware executed the command and reported a hardware failure — the serial
  link is healthy. Lumping it with transport errors produces a misleading
  "Communication error" log entry. Corrected behaviour: split the except clause
  so EpromOperationError is logged as "Programmer error during <op>".
  Operator-reported: "app always reports that the communication is broken when
  the hw returns an error."

Each test asserts the CORRECTED behavior. With the bug present the test fails
(XFAIL, suite stays green). When the fix lands the test passes (XPASS) and
strict=True makes XPASS a suite ERROR, forcing the Phase 41 / Phase 42 plan
executor to remove the corresponding xfail marker.
"""

import logging

import pytest

from firestarter.constants import FLAG_FORCE
from firestarter.main import build_arg_flags
from firestarter.messages import MSG_ERR_SETUP

from .conftest import build_frame


def test_build_arg_flags_force_truthiness_not_existence():
    """Live contract (BUG-1 fixed Phase 41, CLI-03): build_arg_flags uses
    getattr(args, 'force', False) — not 'force' in args. The 'in' operator
    raises TypeError on non-Namespace objects (e.g. a plain class, as Click
    provides after Phase 41 migration). With the getattr pattern, force=False
    -> FLAG_FORCE is NOT set.
    """

    class PlainArgs:
        """Non-Namespace args object — 'in' operator raises TypeError on this.

        argparse.Namespace implements __contains__ (via vars(ns).__contains__),
        so `"force" in args` works coincidentally for argparse. But a plain
        Python class has no __contains__, making `"force" in args` raise
        TypeError: argument of type 'PlainArgs' is not iterable.
        """

        blank_check = True
        verbose = False
        vpe_as_vpp = False
        force = False  # force is False — FLAG_FORCE should NOT be set

    flags = build_arg_flags(PlainArgs())
    assert (flags & FLAG_FORCE) == 0  # force=False => FLAG_FORCE not set


@pytest.mark.xfail(
    strict=True,
    reason="BUG: eprom_operations.py:265 conflates EpromOperationError with SerialError; fix lands Phase 42 (ERR-01)",  # noqa: E501
)
def test_eprom_operation_error_not_labeled_as_communication_error(
    make_comm, fake_serial, caplog
):
    """Corrected behavior: when firmware reports an operational error
    (EpromOperationError raised inside _run_state_machine), it must be logged
    as "Programmer error during <op>" — NOT as "Communication error during <op>",
    which implies a serial transport failure when the transport is actually healthy.

    # BUG: eprom_operations.py:265 — fix lands Phase 42 (ERR-01)
    Operator-reported: "app always reports that the communication is broken
    when the hw returns an error."

    How the bug manifests:
      The _execute_phase helper raises EpromOperationError when it receives a
      firmware ERROR: response. The outer _run_state_machine catches it in the
      combined `except (SerialError, SerialTimeoutError, EpromOperationError)`
      clause and logs `"Communication error during <op>: ..."` for all three
      exception types. Only SerialError / SerialTimeoutError represent true
      transport failures; EpromOperationError is a firmware-level programmer
      error that should be labeled differently.

    After Phase 42 (ERR-01) the except clause is split so EpromOperationError
    logs "Programmer error during <op>: ..." instead.
    """
    from firestarter.config import ConfigManager
    from firestarter.eprom_operations import EpromOperator

    # Build an EpromOperator with the fake serial injected
    config = ConfigManager()
    operator = EpromOperator(config)
    operator.comm = make_comm()  # fake comm wired to fake_serial

    # Feed an ERROR response frame so _execute_phase raises EpromOperationError.
    # MSG_ERR_SETUP (0xA2) has no parameters — simplest error that goes through
    # the _execute_phase -> EpromOperationError -> _run_state_machine except path.
    fake_serial.feed(build_frame(MSG_ERR_SETUP, b""))

    with caplog.at_level(logging.ERROR, logger="EpromOperator"):
        _result, _msg = operator._run_state_machine("test_operation")

    # Corrected behavior: EpromOperationError must NOT be labeled as a
    # communication error in the log output. It should carry "Programmer error"
    # (or similar operational framing) so the user knows the serial link is fine.
    # BUG: eprom_operations.py:265 — fix lands Phase 42 (ERR-01)
    comm_error_logged = any(
        "Communication error" in record.message for record in caplog.records
    )
    assert not comm_error_logged, (
        "EpromOperationError must NOT be logged as 'Communication error'; "
        "it is a programmer/hardware error, not a transport failure. "
        "Fix: split the except clause in _run_state_machine (Phase 42 ERR-01)."
    )
