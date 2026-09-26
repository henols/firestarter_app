"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Process-lifetime transport-health counter sink -- the only mutable
module-level state in this package.

Per-instance state cannot serve this purpose: `EpromOperator.comm` is set to
`None` after every operator call (see 112-02-SUMMARY.md), so a counter living
on a `SerialCommunicator` is destroyed before `firestarter dev test` ever
reads the report back. The counters here instead live for the whole host
process, exactly the lifetime `ConfigManager` (`firestarter/config.py:84-97`)
already demonstrates is safe from deep inside the connect path
(`serial_comm.py:913` writes into it from `_probe_port`). Unlike
`ConfigManager`, nothing here is ever persisted to disk; it is reset
explicitly by the caller that owns a measurement window, never read across
process restarts.

Import purity: this module imports nothing from `firestarter.serial_comm` and
nothing from `firestarter.diagnostic_report` -- that is what keeps the import
graph acyclic and the `diagnostic_report.py` orchestrator-only contract
intact.

`record_response_timeout()` routes a single `get_response` timeout to one of
two counters depending on whether the sink is currently inside `probe_scope()`:
`probe_timeouts` while scoped, `timeouts` otherwise. The routing exists because
`get_response`'s timeout fires once per wrong candidate port on every ordinary
connect that has to walk past one -- at the 32 connects a single at28c256 run
costs, an unscoped global counter would cross a threshold of 5 by the sixth
wrong-port probe and report a healthy multi-board rig as transport-suspect.
`probe_scope()` wraps only the single `_probe_port` call inside
`find_and_connect`, so the scope exits the instant probing succeeds and every
`get_response` on the connection handed back is correctly outside probe scope.

`record_resync_length_missing()` and `record_resync_body_truncated()` are
unconditional increments -- unlike `record_response_timeout()`, neither is
routed by `probe_scope()`. A re-sync inside `_read_and_parse_lines` is a
wire-level event whose meaning does not depend on whether a probe is in
progress: a magic preamble with no valid frame behind it is exactly as
abnormal during port discovery as on an established connection, so both
counters are wired to fire the same way in either context.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

_counters: dict[str, int] = {
    "decode_failures": 0,
    "probe_timeouts": 0,
    "resync_body_truncated": 0,
    "resync_length_missing": 0,
    "timeouts": 0,
}

_in_probe_scope = False


def record_decode_failure() -> None:
    """Increment `decode_failures` by one.

    Called from `SerialCommunicator._decode_id_frame` when
    `codec.decode_id_frame` returns `None` -- an inbound id frame the host
    received and could not decode.
    """
    _counters["decode_failures"] += 1


def record_resync_length_missing() -> None:
    """Increment `resync_length_missing` by one.

    Called from `SerialCommunicator._read_and_parse_lines` when a magic
    preamble is seen but fewer than two length bytes arrive before the
    generator's timeout. Unconditional -- see the module docstring.
    """
    _counters["resync_length_missing"] += 1


def record_resync_body_truncated() -> None:
    """Increment `resync_body_truncated` by one.

    Called from `SerialCommunicator._read_and_parse_lines` when the declared
    frame body does not arrive in full before the generator's timeout.
    Unconditional -- see the module docstring.
    """
    _counters["resync_body_truncated"] += 1


def record_response_timeout() -> None:
    """Increment `probe_timeouts` while inside `probe_scope()`, `timeouts`
    otherwise.

    Called from `SerialCommunicator.get_response` immediately before it
    raises `SerialTimeoutError`. The routing is what keeps `timeouts` a
    genuine established-connection signal and keeps ordinary port-discovery
    misses from ever contributing to `transport_suspect`.
    """
    key = "probe_timeouts" if _in_probe_scope else "timeouts"
    _counters[key] += 1


@contextmanager
def probe_scope() -> Iterator[None]:
    """Mark the sink as "inside a port-discovery probe" for the duration of
    the `with` block, restoring the PREVIOUS state on exit -- including when
    the body raises -- rather than a hard `False`.

    Restoring the previous value rather than clearing it is what keeps a
    probe scope entered from inside another probe scope, or one that exits
    via an exception, from ever leaving the sink stuck in probe mode.
    """
    global _in_probe_scope
    previous = _in_probe_scope
    _in_probe_scope = True
    try:
        yield
    finally:
        _in_probe_scope = previous


def reset() -> None:
    """Zero every counter in the sink.

    Called once at the start of a measurement window (`dev test` resets
    immediately before the identity read), so the counts that follow mean
    "since this window began", never "since the process started".
    """
    for key in _counters:
        _counters[key] = 0


def snapshot() -> dict[str, int]:
    """Return a fresh dict of every counter, keyed in fixed sorted order.

    A new dict is built on every call, so a caller mutating the returned
    object can never reach back into the sink -- key order is deterministic
    (sorted), never insertion-dependent.
    """
    return {key: _counters[key] for key in sorted(_counters)}
