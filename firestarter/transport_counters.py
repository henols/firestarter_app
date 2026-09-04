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
"""

from __future__ import annotations

_counters: dict[str, int] = {
    "decode_failures": 0,
}


def record_decode_failure() -> None:
    """Increment `decode_failures` by one.

    Called from `SerialCommunicator._decode_id_frame` when
    `codec.decode_id_frame` returns `None` -- an inbound id frame the host
    received and could not decode.
    """
    _counters["decode_failures"] += 1


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
