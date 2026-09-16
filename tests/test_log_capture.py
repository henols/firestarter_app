"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Pytest unit tests for `firestarter/log_capture.py` (quick task 260916-nbb).

The sink is process-lifetime, module-level state, so every test that reads
or mutates it calls `log_capture.install()` first (mirroring
`transport_counters.reset()`'s own reset-before-measurement-window
contract) and the autouse fixture below guarantees `uninstall()` runs after
every test, so a failing test can never leak a handler into the rest of the
suite.

Every test drives the sink through the real `logging` API
(`logging.getLogger(...).warning(...)`, never the handler's own `emit`
method directly) -- the handler's level filter and root-logger propagation
are both part of what is under test.

Test taxonomy, matching the plan's behavior list:

  Normalization
    test_cr_lf_tab_collapse_to_single_space_and_strip
    test_non_printable_ascii_is_dropped
    test_backtick_fence_run_becomes_equal_length_single_quote_run
    test_whitespace_collapse_runs_before_truncation_not_after

  Length bound
    test_message_exactly_at_bound_is_untouched_and_not_truncated
    test_message_one_char_over_bound_is_cut_and_counted_once_in_truncated

  Consecutive-duplicate collapse
    test_two_consecutive_identical_records_collapse_into_one_entry_repeat_two
    test_a_third_identical_record_advances_repeat_to_three
    test_a_different_message_between_two_identical_ones_prevents_collapse

  Ring eviction
    test_overflow_evicts_the_oldest_entries_and_counts_them_dropped
    test_evicting_a_collapsed_entry_adds_its_whole_repeat_to_dropped

  Counters
    test_captured_counts_every_accepted_record_and_stays_gte_entry_count

  Level filter
    test_a_record_below_warning_is_not_captured_at_all
    test_warning_error_and_critical_records_are_all_captured

  Source attribution
    test_a_record_on_the_rurp_logger_is_tagged_firmware
    test_a_record_on_any_other_logger_name_is_tagged_host

  Install/uninstall idempotence
    test_install_called_twice_leaves_exactly_one_handler_instance
    test_uninstall_leaves_zero_handler_instances
    test_uninstall_on_an_uninstalled_sink_is_a_noop

  step_scope nesting
    test_step_scope_nested_two_deep_restores_the_outer_step_on_inner_exit
    test_step_scope_restores_the_outer_step_even_when_the_inner_body_raises

  snapshot isolation
    test_snapshot_returns_a_fresh_object_each_call

  Drift guard
    test_firmware_logger_name_matches_serial_comm_rurp_logger_name
"""

from __future__ import annotations

import logging

import pytest

from firestarter import log_capture
from firestarter.log_capture import MAX_ENTRIES, MAX_LINE_CHARS


@pytest.fixture(autouse=True)
def _isolate_log_capture_sink():
    log_capture.uninstall()
    yield
    log_capture.uninstall()


def test_cr_lf_tab_collapse_to_single_space_and_strip() -> None:
    log_capture.install()
    logging.getLogger("host.cr_lf_tab").warning(
        "\r\nline one\r\nline two\tline three\r\n"
    )
    entries = log_capture.snapshot()["entries"]
    assert entries[-1]["message"] == "line one line two line three"


def test_non_printable_ascii_is_dropped() -> None:
    log_capture.install()
    logging.getLogger("host.non_printable").warning("abc\x07def\x1b[31mXYZ")
    entries = log_capture.snapshot()["entries"]
    assert entries[-1]["message"] == "abcdef[31mXYZ"


def test_backtick_fence_run_becomes_equal_length_single_quote_run() -> None:
    log_capture.install()
    logging.getLogger("host.fence3").warning("before ```code``` after")
    logging.getLogger("host.fence5").warning("before `````` after")
    entries = log_capture.snapshot()["entries"]
    assert entries[0]["message"] == "before '''code''' after"
    assert entries[1]["message"] == "before '''''' after"


def test_whitespace_collapse_runs_before_truncation_not_after() -> None:
    log_capture.install()
    raw = "x" * 50 + "\n" * 300 + "y" * 50
    assert len(raw) > MAX_LINE_CHARS
    logging.getLogger("host.order").warning(raw)
    snap = log_capture.snapshot()
    message = snap["entries"][-1]["message"]
    assert message == "x" * 50 + " " + "y" * 50
    assert len(message) == 101
    assert snap["truncated"] == 0


def test_message_exactly_at_bound_is_untouched_and_not_truncated() -> None:
    log_capture.install()
    message = "A" * MAX_LINE_CHARS
    logging.getLogger("host.exact_bound").warning(message)
    snap = log_capture.snapshot()
    assert snap["entries"][-1]["message"] == message
    assert len(snap["entries"][-1]["message"]) == MAX_LINE_CHARS
    assert snap["truncated"] == 0


def test_message_one_char_over_bound_is_cut_and_counted_once_in_truncated() -> None:
    log_capture.install()
    message = "B" * (MAX_LINE_CHARS + 1)
    logging.getLogger("host.over_bound").warning(message)
    snap = log_capture.snapshot()
    stored = snap["entries"][-1]["message"]
    assert len(stored) == MAX_LINE_CHARS
    assert stored.endswith("...")
    assert stored == "B" * (MAX_LINE_CHARS - 3) + "..."
    assert snap["truncated"] == 1


def test_two_consecutive_identical_records_collapse_into_one_entry_repeat_two() -> None:
    log_capture.install()
    logger = logging.getLogger("host.dup_pair")
    logger.warning("same message")
    logger.warning("same message")
    snap = log_capture.snapshot()
    assert len(snap["entries"]) == 1
    assert snap["entries"][0]["repeat"] == 2


def test_a_third_identical_record_advances_repeat_to_three() -> None:
    log_capture.install()
    logger = logging.getLogger("host.dup_triple")
    logger.warning("same message")
    logger.warning("same message")
    logger.warning("same message")
    snap = log_capture.snapshot()
    assert len(snap["entries"]) == 1
    assert snap["entries"][0]["repeat"] == 3


def test_a_different_message_between_two_identical_ones_prevents_collapse() -> None:
    log_capture.install()
    logger = logging.getLogger("host.dup_broken")
    logger.warning("A")
    logger.warning("B")
    logger.warning("A")
    snap = log_capture.snapshot()
    assert len(snap["entries"]) == 3
    assert all(entry["repeat"] == 1 for entry in snap["entries"])
    assert [entry["message"] for entry in snap["entries"]] == ["A", "B", "A"]


def test_overflow_evicts_the_oldest_entries_and_counts_them_dropped() -> None:
    log_capture.install()
    logger = logging.getLogger("host.ring_overflow")
    total = MAX_ENTRIES + 5
    for i in range(total):
        logger.warning(f"msg-{i}")
    snap = log_capture.snapshot()
    assert len(snap["entries"]) == MAX_ENTRIES
    assert snap["dropped"] == 5
    messages = [entry["message"] for entry in snap["entries"]]
    assert messages[0] == "msg-5"
    assert messages[-1] == f"msg-{total - 1}"
    for evicted in range(5):
        assert f"msg-{evicted}" not in messages


def test_evicting_a_collapsed_entry_adds_its_whole_repeat_to_dropped() -> None:
    log_capture.install()
    logger = logging.getLogger("host.evict_collapsed")
    logger.warning("collapsed-msg")
    logger.warning("collapsed-msg")
    logger.warning("collapsed-msg")
    for i in range(MAX_ENTRIES - 1):
        logger.warning(f"filler-{i}")
    filled = log_capture.snapshot()
    assert len(filled["entries"]) == MAX_ENTRIES
    assert filled["dropped"] == 0

    logger.warning("final-eviction-trigger")
    snap = log_capture.snapshot()
    assert snap["dropped"] == 3
    messages = [entry["message"] for entry in snap["entries"]]
    assert "collapsed-msg" not in messages
    assert "final-eviction-trigger" in messages


def test_captured_counts_every_accepted_record_and_stays_gte_entry_count() -> None:
    log_capture.install()
    logger = logging.getLogger("host.captured_count")
    logger.warning("x")
    logger.warning("x")
    logger.warning("y")
    snap = log_capture.snapshot()
    assert snap["captured"] == 3
    assert snap["captured"] >= len(snap["entries"])


def test_a_record_below_warning_is_not_captured_at_all() -> None:
    log_capture.install()
    logger = logging.getLogger("host.below_warning")
    logger.setLevel(logging.DEBUG)
    logger.info("info line")
    logger.debug("debug line")
    snap = log_capture.snapshot()
    assert snap["captured"] == 0
    assert snap["entries"] == []


@pytest.mark.parametrize(
    ("level_name", "method_name"),
    [("WARNING", "warning"), ("ERROR", "error"), ("CRITICAL", "critical")],
)
def test_warning_error_and_critical_records_are_all_captured(
    level_name: str, method_name: str
) -> None:
    log_capture.install()
    logger = logging.getLogger(f"host.level_{level_name}")
    logger.setLevel(logging.DEBUG)
    getattr(logger, method_name)("a line")
    snap = log_capture.snapshot()
    assert snap["entries"][-1]["level"] == level_name


def test_a_record_on_the_rurp_logger_is_tagged_firmware() -> None:
    log_capture.install()
    logging.getLogger("RURP").warning("firmware line")
    snap = log_capture.snapshot()
    assert snap["entries"][-1]["source"] == "firmware"


def test_a_record_on_any_other_logger_name_is_tagged_host() -> None:
    log_capture.install()
    logging.getLogger("SomeOtherLogger").warning("host line")
    snap = log_capture.snapshot()
    assert snap["entries"][-1]["source"] == "host"


def _capture_handler_count() -> int:
    root = logging.getLogger()
    return sum(
        1
        for handler in root.handlers
        if isinstance(handler, log_capture._CaptureHandler)
    )


def test_install_called_twice_leaves_exactly_one_handler_instance() -> None:
    log_capture.install()
    log_capture.install()
    assert _capture_handler_count() == 1


def test_uninstall_leaves_zero_handler_instances() -> None:
    log_capture.install()
    log_capture.uninstall()
    assert _capture_handler_count() == 0


def test_uninstall_on_an_uninstalled_sink_is_a_noop() -> None:
    log_capture.uninstall()
    log_capture.uninstall()
    assert _capture_handler_count() == 0


def test_step_scope_nested_two_deep_restores_the_outer_step_on_inner_exit() -> None:
    log_capture.install()
    logger = logging.getLogger("host.step_nesting")
    with log_capture.step_scope("outer"):
        with log_capture.step_scope("inner"):
            logger.warning("inner msg")
        logger.warning("outer msg after inner exit")
    snap = log_capture.snapshot()
    steps = [entry["step"] for entry in snap["entries"]]
    assert steps == ["inner", "outer"]


def test_step_scope_restores_the_outer_step_even_when_the_inner_body_raises() -> None:
    log_capture.install()
    logger = logging.getLogger("host.step_raise")
    with log_capture.step_scope("outer"):
        try:
            with log_capture.step_scope("inner"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        logger.warning("after raise")
    snap = log_capture.snapshot()
    assert snap["entries"][-1]["step"] == "outer"


def test_snapshot_returns_a_fresh_object_each_call() -> None:
    log_capture.install()
    logging.getLogger("host.snapshot_fresh").warning("hello")
    first = log_capture.snapshot()
    first["entries"].append(
        {
            "step": None,
            "source": "host",
            "level": "WARNING",
            "message": "poison",
            "repeat": 1,
        }
    )
    first["captured"] = 999
    second = log_capture.snapshot()
    assert len(second["entries"]) == 1
    assert second["captured"] == 1


def test_firmware_logger_name_matches_serial_comm_rurp_logger_name() -> None:
    from firestarter import serial_comm

    assert log_capture.FIRMWARE_LOGGER_NAME == serial_comm.rurp_logger.name
