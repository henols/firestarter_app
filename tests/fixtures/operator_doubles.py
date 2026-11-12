"""
Bench-free `EpromOperator` doubles for the `chip_test` and `dev test` suites.

`mock_operator` answers every call with success and writes no file.
`Mock(spec=OPERATOR_METHODS)` is load-bearing: without `spec=`, an
out-of-spec attribute access on the double silently returns a truthy `Mock`
instead of raising, and a dispatch bug would read as a pass. The fixed
`check_eprom_id` return value `(True, 0x1234)` mismatches most real chips'
chip IDs, which closes the destructive gate `run_plan` consults before every
write or erase call on those chips.

`sdp_aware_operator` keeps an in-memory chip image and honours SDP
semantics, for plans whose SDP leg compares read-backs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

OPERATOR_METHODS = [
    "check_eprom_id",
    "read_eprom",
    "check_eprom_blank",
    "write_eprom",
    "verify_eprom",
    "erase_eprom",
    "sdp_lock",
    "sdp_unlock",
]


def mock_operator(**returns: Any) -> Mock:
    """A fixed-return operator double: every call reports success.

    Each keyword sets that method's return value and clears its side effect.
    """
    op = Mock(spec=OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    op.read_eprom.return_value = True
    # check_eprom_blank returns an int (0 == blank).
    op.check_eprom_blank.return_value = 0
    op.write_eprom.return_value = True
    # verify_eprom returns an int (0 == match); the multi-run dispatch's
    # `== 0` adapter reads this as success only at 0.
    op.verify_eprom.return_value = 0
    op.erase_eprom.return_value = True
    op.sdp_lock.return_value = True
    op.sdp_unlock.return_value = True
    for name, value in returns.items():
        getattr(op, name).return_value = value
        getattr(op, name).side_effect = None
    return op


def sdp_aware_operator() -> Mock:
    """A stateful, SDP-lock-AWARE operator double.

    `mock_operator`'s `read_eprom` returns success while writing NO file, so
    the SDP leg's read-back-equality oracle would see an empty read-back and
    report every leg step BAD via the length gate -- an "all-OK" assertion
    would then be a false negative that still reads green.

    This double keeps a small in-memory chip image and honours real SDP
    semantics: while `locked`, a write carrying `FLAG_SKIP_SDP_UNLOCK`
    (write-inhibited's own flag) is REJECTED -- the image is left unchanged,
    exactly what a protecting chip does -- while every other write (no skip
    flag, or the chip unlocked) applies normally.
    """
    from firestarter.constants import FLAG_SKIP_SDP_UNLOCK

    state = {"image": b"", "locked": False}

    op = Mock(spec=OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    # check_eprom_blank returns an int (0 == blank).
    op.check_eprom_blank.return_value = 0
    op.erase_eprom.return_value = True

    def _write_eprom(name, eprom_data, source_path, flags=0, address_str=None, **_kw):
        payload = Path(source_path).read_bytes()
        if state["locked"] and (flags & FLAG_SKIP_SDP_UNLOCK):
            # Blocked: the chip ignores the write while locked, and the
            # firmware's auto-unlock wrap was skipped on purpose.
            pass
        else:
            state["image"] = payload
        return True

    def _read_eprom(name, eprom_data, output_file=None, **kwargs):
        if output_file is not None:
            Path(output_file).write_bytes(state["image"])
        return True

    def _verify_eprom(name, eprom_data, source_path, *_args, **_kwargs):
        # int, 0 == match.
        expected = Path(source_path).read_bytes()
        return 0 if expected == state["image"] else 1

    def _sdp_lock(name, eprom_data):
        state["locked"] = True
        return True

    def _sdp_unlock(name, eprom_data):
        state["locked"] = False
        return True

    op.write_eprom.side_effect = _write_eprom
    op.read_eprom.side_effect = _read_eprom
    op.verify_eprom.side_effect = _verify_eprom
    op.sdp_lock.side_effect = _sdp_lock
    op.sdp_unlock.side_effect = _sdp_unlock
    return op
