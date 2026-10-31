"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Pytest unit tests for `firestarter dev consistency-check <chip>` (REPRO-03).

Closes Phase 26 / Plan 26-01 D-10 cases #1-#6 plus a TestDispatchChain
integration test and a stdout-verdict-block-format regex regression test
(Phase 29 forward-compatibility contract pin).

Test taxonomy (8 tests total):

  D-10 Test 1  test_all_runs_identical_pass_exit_0           -> exit 0
  D-10 Test 2  test_one_byte_differs_in_run_2_exit_1         -> exit 1 (first-divergence offset)
  D-10 Test 3  test_full_scramble_three_distinct_shas        -> exit 1 (3 distinct SHAs)
  D-10 Test 4  test_serial_timeout_exit_2                    -> exit 2 (hardware error)
  D-10 Test 5  test_no_keep_files_removes_output_dir         -> exit 0 + dir removed
  D-10 Test 6  test_runs_boundary_rejected                   -> exit 2 (runs < 2)
  forward-compat (regex pin)  test_stdout_verdict_block_format
  dispatch integration        TestDispatchChain::test_main_dispatch_invokes_consistency_check

Every run is a real read: `connect_via` hands the operator a real
`SerialCommunicator` on a fake port per run, and each port replies with the
firmware frames of one complete read of a controlled payload. The one
exception is variant (b) of D-10 #4: the real state machine converts every
error into a `(False, msg)` return, so an `EpromOperationError` that escapes
it can only be produced by replacing `_run_state_machine`.

References:
  - .planning/phases/26-cross-board-reproduction-diagnostic-tooling/26-CONTEXT.md
    §D-01..D-13 (locked decisions; tests pin every contract surface)
  - .planning/phases/26-cross-board-reproduction-diagnostic-tooling/26-RESEARCH.md
    §Pitfall 4 (state-machine exception -> (False, msg) return contract)
  - .planning/phases/26-cross-board-reproduction-diagnostic-tooling/26-VALIDATION.md
    §"Cross-tool Forward Compatibility" (stdout verdict block regex pin)
"""  # noqa: E501

import functools
import hashlib
import re
from unittest.mock import patch

import pytest

from firestarter.config import ConfigManager
from firestarter.eprom_operations import EpromOperationError, EpromOperator
from firestarter.messages import MSG_INIT_DONE

from .conftest import (
    PortDropSerial,
    _FakeSerial,
    build_frame,
    comm_on,
    connect_via,
    read_phase_frames,
)

# Shared helpers

_PAYLOAD_SIZE = 65536


def _identical_payload() -> bytes:
    """Return a deterministic 64 KB payload (0x00..0xFF repeating 256 times)."""
    return bytes(range(256)) * 256


@functools.cache
def _read_frames(payload: bytes) -> bytes:
    """The firmware replies for one complete read of `payload`, joined.

    Cached: the same 64 KB payloads recur across runs and tests, and
    framing one costs far more than the read itself.
    """
    return b"".join(read_phase_frames(payload))


def _port_reading(payload: bytes) -> _FakeSerial:
    """A fake port that replies with one complete read of `payload`."""
    port = _FakeSerial()
    port.feed(_read_frames(payload))
    return port


def _connections(*ports):
    """A communicator factory: the n-th connection is on `ports[n]`."""
    pending = iter(ports)
    return lambda: comm_on(next(pending))


def _consistency_check(connections, **kwargs):
    """Run `consistency_check_eprom` on a 64 KB chip over `connections`.

    Returns `(rc, captured)`: the exit code, and one command dict per
    connection the operator opened (one per run).
    """
    args = {
        "eprom_data_dict": {"memory-size": _PAYLOAD_SIZE},
        "runs": 3,
        "keep_files": True,
        "quiet": True,
    }
    args.update(kwargs)
    with connect_via(connections) as captured:
        rc = EpromOperator(ConfigManager()).consistency_check_eprom("TEST_CHIP", **args)
    return rc, captured


def _runs_reading(*payloads: bytes):
    """Connections whose n-th run reads `payloads[n]`."""
    return _connections(*(_port_reading(p) for p in payloads))


# Tests


class TestConsistencyCheck:
    """D-10 locked test cases for `EpromOperator.consistency_check_eprom` (REPRO-03)."""

    def test_all_runs_identical_pass_exit_0(self, tmp_path):
        """D-10 Test 1 (REPRO-03): identical 65,536-byte stream every run -> exit 0.

        Every run reads the same payload; assert N=3 returns 0, all 3 SHAs
        equal, no first-divergence path executed.
        """
        identical = _identical_payload()
        out_dir = tmp_path / "out"
        rc, captured = _consistency_check(
            _runs_reading(identical, identical, identical), output_dir=str(out_dir)
        )

        assert rc == 0, "All-identical case must return PASS (exit 0) per D-05."
        assert len(captured) == 3, "All 3 runs must open their own read."
        shas = [
            hashlib.sha256((out_dir / f"run_{i:02d}.bin").read_bytes()).hexdigest()
            for i in (1, 2, 3)
        ]
        assert shas[0] == shas[1] == shas[2], "Identical input -> identical SHAs."

    def test_one_byte_differs_in_run_2_exit_1(self, tmp_path, capsys):
        """D-10 Test 2 (REPRO-03): one byte differs at offset 0x123 in run 2 -> exit 1.

        Assert FAIL exit code, first-divergence offset reported as 0x0123, and
        total divergent bytes between run_1 and run_2 reported as 1/65536.
        """
        identical = _identical_payload()
        mutated = bytearray(identical)
        mutated[0x123] = (mutated[0x123] + 1) & 0xFF  # any byte flip
        # If the natural payload already has 0xFF at 0x123 don't pick that twice;
        # the +1 above guarantees a difference.

        rc, _ = _consistency_check(
            _runs_reading(identical, bytes(mutated), identical),
            output_dir=str(tmp_path / "out"),
        )

        captured = capsys.readouterr()
        assert rc == 1, "One-byte divergence must return FAIL (exit 1) per D-05."
        assert "First divergence: offset 0x0123" in captured.out, (
            f"D-04 first-divergence line missing. Got stdout:\n{captured.out}"
        )
        assert "Total divergent bytes (run_1 vs run_2): 1 / 65536" in captured.out, (
            f"D-04 total-divergence line missing. Got stdout:\n{captured.out}"
        )

    def test_full_scramble_three_distinct_shas(self, tmp_path, capsys):
        """D-10 Test 3 (REPRO-03): three completely distinct payloads -> exit 1, 3 distinct SHAs.

        Asserts Distinct SHAs: 3 and that the first-divergence offset is 0x0000
        (the very first byte differs across runs 1 and 2).
        """  # noqa: E501
        p1 = bytes([0x00] * _PAYLOAD_SIZE)
        p2 = bytes([0xAA] * _PAYLOAD_SIZE)
        p3 = bytes([0x55] * _PAYLOAD_SIZE)

        rc, _ = _consistency_check(
            _runs_reading(p1, p2, p3), output_dir=str(tmp_path / "out")
        )

        captured = capsys.readouterr()
        assert rc == 1, "Full scramble must return FAIL (exit 1)."
        assert "Distinct SHAs: 3" in captured.out, (
            f"D-04 Distinct SHAs line missing. Got:\n{captured.out}"
        )
        assert "First divergence: offset 0x0000" in captured.out, (
            f"D-04 first-divergence at offset 0 missing. Got:\n{captured.out}"
        )

    def test_serial_timeout_exit_2(self, tmp_path, monkeypatch):
        """D-10 Test 4 (REPRO-03): hardware/serial error mid-stream -> exit 2.

        Per RESEARCH Pitfall 4: `_run_state_machine` catches
        SerialError/SerialTimeoutError/EpromOperationError internally and
        returns (False, msg). We exercise BOTH variants:
          (a) the port drops during run 2's read, so the state machine
              returns (False, msg);
          (b) the state machine raises EpromOperationError directly
              (defensive — the operator method must also map a propagated
              exception to exit 2). The real state machine never lets one
              escape, so this variant replaces `_run_state_machine`.
        """
        identical = _identical_payload()

        # Variant (a): run 2's port drops after INIT.
        dropped = PortDropSerial()
        dropped.feed(build_frame(MSG_INIT_DONE, b""))
        rc, captured = _consistency_check(
            _connections(_port_reading(identical), dropped),
            output_dir=str(tmp_path / "out_a"),
        )
        assert rc == 2, "State-machine (False, msg) must map to exit 2 (D-05)."
        assert len(captured) == 2, "The check must stop at the failed run."

        # Variant (b): state machine raises EpromOperationError directly
        def fake_sm_raises(self, op_name, **kwargs):
            raise EpromOperationError("timeout")

        monkeypatch.setattr(EpromOperator, "_run_state_machine", fake_sm_raises)
        rc2, _ = _consistency_check(
            _connections(_FakeSerial()), output_dir=str(tmp_path / "out_b")
        )
        assert rc2 == 2, "EpromOperationError must map to exit 2 (D-05)."

    def test_no_keep_files_removes_output_dir(self, tmp_path):
        """D-10 Test 5 (REPRO-03): keep_files=False removes the output dir after verdict."""  # noqa: E501
        identical = _identical_payload()
        out_dir = tmp_path / "out_no_keep"
        rc, _ = _consistency_check(
            _runs_reading(identical, identical, identical),
            output_dir=str(out_dir),
            keep_files=False,
        )
        assert rc == 0, "Identical runs with keep_files=False -> PASS (exit 0)."
        assert not out_dir.exists(), (
            f"Output dir must be removed when keep_files=False. "
            f"Still exists at: {out_dir}"
        )

    def test_default_output_dir_nested_under_runs_folder(self, tmp_path, monkeypatch):
        """When output_dir is omitted, the auto-named run folder is grouped under
        the DEFAULT_RUN_OUTPUT_DIR parent instead of being created directly in the
        launch (current working) directory.
        """
        from firestarter.eprom_operations import DEFAULT_RUN_OUTPUT_DIR

        identical = _identical_payload()
        # Run from an isolated cwd so the relative parent folder lands in tmp_path.
        monkeypatch.chdir(tmp_path)

        rc, _ = _consistency_check(
            _runs_reading(identical, identical, identical),
            output_dir=None,  # exercise the default-naming branch
        )

        assert rc == 0
        parent = tmp_path / DEFAULT_RUN_OUTPUT_DIR
        assert parent.is_dir(), (
            f"Default runs must be grouped under ./{DEFAULT_RUN_OUTPUT_DIR}/, "
            f"not dumped directly in the launch directory. Contents of cwd: "
            f"{sorted(p.name for p in tmp_path.iterdir())}"
        )
        run_dirs = [
            d for d in parent.iterdir() if d.name.startswith("consistency-check-")
        ]
        assert len(run_dirs) == 1, (
            f"Expected one auto-named consistency-check folder under "
            f"{DEFAULT_RUN_OUTPUT_DIR}/, found: {[d.name for d in run_dirs]}"
        )
        # No timestamped folder leaked directly into the launch directory.
        assert not list(tmp_path.glob("consistency-check-*")), (
            "Auto-named run folder must NOT be created directly in the cwd."
        )

    def test_runs_boundary_rejected(self, tmp_path, caplog):
        """D-10 Test 6 (REPRO-03): runs < 2 rejected with exit 2 BEFORE any read."""
        op = EpromOperator(ConfigManager())

        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect"
        ) as connect_mock:
            # runs=1
            with caplog.at_level("ERROR"):
                rc1 = op.consistency_check_eprom(
                    "TEST_CHIP",
                    eprom_data_dict={"memory-size": _PAYLOAD_SIZE},
                    runs=1,
                    output_dir=str(tmp_path / "out_r1"),
                    keep_files=True,
                    quiet=True,
                )
            assert rc1 == 2, "runs=1 must return exit 2 (cannot compare a single read)."

            # runs=0
            rc0 = op.consistency_check_eprom(
                "TEST_CHIP",
                eprom_data_dict={"memory-size": _PAYLOAD_SIZE},
                runs=0,
                output_dir=str(tmp_path / "out_r0"),
                keep_files=True,
                quiet=True,
            )
            assert rc0 == 2, "runs=0 must return exit 2."

        connect_mock.assert_not_called()

        # Operator-visible log message must mention the constraint
        all_logs = " ".join(rec.getMessage() for rec in caplog.records)
        assert "must be >= 2" in all_logs, (
            f"Operator-facing log must explain the constraint. Got: {all_logs!r}"
        )

    def test_stdout_verdict_block_format(self, tmp_path, capsys):
        """Phase 29 forward-compat regression pin (REPRO-03 / Cross-tool contract).

        Per 26-VALIDATION.md "Cross-tool Forward Compatibility" the following
        substrings are LOAD-BEARING and must not drift between v1.6 and v1.7+:

          * "Consistency check: PASS"  /  "Consistency check: FAIL"
          * "Distinct SHAs: <int>"
          * "Runs: N=<int>"
          * "First divergence: offset 0x<HHHH>"  (FAIL only)

        Phase 29 scripts grep these strings to gate the milestone; any drift
        trips this test before the milestone gate runs.
        """
        # PASS scenario: all identical
        identical = _identical_payload()
        rc, _ = _consistency_check(
            _runs_reading(identical, identical, identical),
            output_dir=str(tmp_path / "out_pass"),
        )
        assert rc == 0
        out_pass = capsys.readouterr().out
        assert re.search(r"Consistency check: PASS", out_pass), (
            f"PASS verdict line missing. Got:\n{out_pass}"
        )
        assert re.search(r"Distinct SHAs: \d+", out_pass), (
            f"Distinct SHAs line missing. Got:\n{out_pass}"
        )
        assert re.search(r"Runs: N=\d+", out_pass), (
            f"Runs: N= line missing. Got:\n{out_pass}"
        )

        # FAIL scenario: first-divergence path must also format correctly
        p1 = bytes([0x00] * _PAYLOAD_SIZE)
        p2 = bytes([0xFF] * _PAYLOAD_SIZE)
        rc2, _ = _consistency_check(
            _runs_reading(p1, p2, p1), output_dir=str(tmp_path / "out_fail")
        )
        assert rc2 == 1
        out_fail = capsys.readouterr().out
        assert re.search(r"Consistency check: FAIL", out_fail), (
            f"FAIL verdict line missing. Got:\n{out_fail}"
        )
        assert re.search(r"First divergence: offset 0x[0-9A-F]+", out_fail), (
            f"First-divergence line missing/malformed. Got:\n{out_fail}"
        )


class TestDispatchChain:
    """Integration test pinning main.py argparse -> EpromOperator wiring (REPRO-03).

    Verifies the new `dev consistency-check` subparser exists, parses the
    locked D-01 flag set, and dispatches to `EpromOperator.consistency_check_eprom`
    with the right kwargs.
    """

    def test_main_dispatch_invokes_consistency_check(self, monkeypatch):
        """Parse argv through main()'s argparse then assert
        `consistency_check_eprom` was invoked with the expected kwargs
        (runs=3, keep_files=False, max_diffs=10).
        """
        import sys

        from firestarter import main as main_mod
        from firestarter.database import EpromDatabase
        from firestarter.eprom_operations import EpromOperator

        captured = {}

        def fake_method(
            self,
            eprom_name,
            eprom_data_dict,
            runs=3,
            output_dir=None,
            keep_files=True,
            max_diffs=10,
            quiet=False,
            operation_flags=0,
            read_settling_us=0,
            read_strobe_us=0,
        ):
            captured["eprom_name"] = eprom_name
            captured["eprom_data_dict"] = eprom_data_dict
            captured["runs"] = runs
            captured["output_dir"] = output_dir
            captured["keep_files"] = keep_files
            captured["max_diffs"] = max_diffs
            captured["quiet"] = quiet
            captured["operation_flags"] = operation_flags
            captured["read_settling_us"] = read_settling_us
            captured["read_strobe_us"] = read_strobe_us
            return 0

        monkeypatch.setattr(
            EpromOperator, "consistency_check_eprom", fake_method, raising=False
        )

        monkeypatch.setattr(
            EpromDatabase,
            "get_eprom_config",
            lambda self, name: (
                {
                    "part_number": name,
                    "support_status": "supported",
                    "programming": {"algorithm": 7},
                },
                "TEST",
            ),
        )
        monkeypatch.setattr(
            EpromDatabase,
            "get_eprom",
            lambda self, name: {"name": name, "memory-size": _PAYLOAD_SIZE},
        )
        monkeypatch.setattr(
            EpromDatabase,
            "convert_to_programmer",
            lambda self, full: {
                "memory-size": _PAYLOAD_SIZE,
                "pin-count": 28,
                "vpp_mv": 12000,
                "address": 0,
                "flags": 0,
            },
        )

        argv_saved = sys.argv
        try:
            sys.argv = [
                "firestarter",
                "-p",
                "/dev/null",
                "dev",
                "consistency-check",
                "TEST_CHIP",
                "--runs",
                "3",
                "--no-keep-files",
            ]
            with pytest.raises(SystemExit) as exc_info:
                main_mod.main()
            rc = exc_info.value.code
        finally:
            sys.argv = argv_saved

        assert rc == 0, (
            "main() must exit with the operator method's exit code (0 here)."
        )
        assert captured.get("eprom_name") == "TEST_CHIP"
        assert captured.get("runs") == 3
        assert captured.get("keep_files") is False
        assert captured.get("max_diffs") == 10

    @pytest.mark.parametrize("option", ["--read-settling", "--read-strobe"])
    def test_read_timing_above_the_frame_limit_is_a_usage_error(
        self, monkeypatch, capsys, option
    ):
        """The frame refuses a read timing above READ_TIMING_MAX_US. Click
        refuses it first, before the operator creates an output directory or
        opens a port, so it is never reported as a hardware error."""
        import sys

        from firestarter import command_frame as cf
        from firestarter import main as main_mod
        from firestarter.eprom_operations import EpromOperator

        called = []
        monkeypatch.setattr(
            EpromOperator,
            "consistency_check_eprom",
            lambda *a, **kw: called.append(True) or 0,
            raising=False,
        )
        argv_saved = sys.argv
        try:
            sys.argv = [
                "firestarter",
                "-p",
                "/dev/null",
                "dev",
                "consistency-check",
                "TEST_CHIP",
                option,
                str(cf.READ_TIMING_MAX_US + 1),
            ]
            with pytest.raises(SystemExit) as exc_info:
                main_mod.main()
        finally:
            sys.argv = argv_saved

        assert exc_info.value.code == 2
        assert called == []
        assert f"0<=x<={cf.READ_TIMING_MAX_US}" in capsys.readouterr().err
