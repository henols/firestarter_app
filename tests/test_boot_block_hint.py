"""
FIX-01b (Phase 94 Plan 03) — boot-block-locked heuristic hint tests.

Tests that a flash4 (protocol 0x05) write-timeout whose failing address
falls in the first or last 16K of the chip surfaces a clear boot-block-locked
inference hint, and that a mid-chip address does NOT emit the hint.

Three behaviour cases (per plan task 1 acceptance criteria):
  1. first-16K  address (< 0x4000)               → hint emitted
  2. last-16K   address (>= mem_size - 0x4000)   → hint emitted
  3. mid-region address (e.g. 0x040000)           → NO hint (bare timeout only)

T-94-MISLABEL mitigation (STRIDE register):
  The hint uses inference language ("may be locked", "§6.6 boot-block lockout",
  "irreversible", ">=0x4000") — see assertion list in each test.
  It does NOT claim confirmation (only the firmware DETECT read can confirm
  via the FF/FE bit; host heuristic is address-range only).

Test approach: pure-host, no serial I/O.
  Drive _boot_block_hint_message() directly with synthetic Response objects
  constructed from frame_parser.Response (a namedtuple). The function under
  test is a module-level helper in eprom_operations.py that accepts a
  Response, a protocol id, and a mem_size, and returns either a hint string
  or None.

Phase 93 RCA boundary evidence:
  0x3F00 FAIL / 0x4000 PASS → boundary is exactly 16K = 0x4000.
"""

from firestarter.eprom_operations import _boot_block_hint_message
from firestarter.frame_parser import Response
from firestarter.messages import MSG_ERR_FL4_VERIFY_TIMEOUT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROTO_FLASH4 = 5  # protocol 0x05 (FLASH_AMD_STD)
_MEM_SIZE_W29C040 = 524288  # 512 KB


def _make_timeout_response(failing_addr: int) -> Response:
    """Build a synthetic MSG_ERR_FL4_VERIFY_TIMEOUT Response.

    Format: "Timeout verifying 0x%02x at 0x%06lx (got 0x%02x)"
    (expected_byte, address, observed_byte).

    We synthesise the decoded text directly to match what codec.decode_id_frame
    would produce — avoiding a real wire frame and serial path.
    """
    message = f"Timeout verifying 0x00 at 0x{failing_addr:06x} (got 0x00)"
    return Response(
        type="ERROR",
        message=message,
        payload=None,
        id=MSG_ERR_FL4_VERIFY_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_boot_block_hint_first_16k() -> None:
    """flash4 timeout at first-16K address (< 0x4000) emits the boot-block hint."""
    resp = _make_timeout_response(0x0000FF)  # last byte of first page (Phase 93 repro)
    hint = _boot_block_hint_message(resp, _PROTO_FLASH4, _MEM_SIZE_W29C040)
    assert hint is not None, (
        "FIX-01b: boot-block hint must be returned for first-16K address 0x0000ff"
    )
    # Inference substrings required by the plan (A3 / T-94-MISLABEL)
    assert "may be" in hint, "hint must use inference language 'may be'"
    assert "6.6" in hint, "hint must reference the datasheet §6.6 section"
    assert "irreversible" in hint.lower(), (
        "hint must describe the lockout as irreversible"
    )
    assert "0x4000" in hint, (
        "hint must inform operator that writes to >=0x4000 should succeed"
    )


def test_boot_block_hint_first_16k_boundary_inclusive() -> None:
    """Address 0x3FFF (last byte in first 16K) still emits the hint."""
    resp = _make_timeout_response(0x3FFF)
    hint = _boot_block_hint_message(resp, _PROTO_FLASH4, _MEM_SIZE_W29C040)
    assert hint is not None, (
        "FIX-01b: boot-block hint must be returned for address 0x3FFF "
        "(last byte of first 16K boot block)"
    )


def test_boot_block_hint_first_16k_boundary_exclusive() -> None:
    """Address 0x4000 (first address OUTSIDE first 16K) must NOT emit the hint.

    Phase 93 RCA: 0x3F00 = FAIL (locked), 0x4000 = PASS (unlocked).
    The boundary is at 0x4000, which is the first writable address.
    """
    resp = _make_timeout_response(0x4000)
    hint = _boot_block_hint_message(resp, _PROTO_FLASH4, _MEM_SIZE_W29C040)
    assert hint is None, (
        "FIX-01b: no boot-block hint must be returned for address 0x4000 "
        "(first address outside first 16K boot block — unlocked per Phase 93 RCA)"
    )


def test_boot_block_hint_last_16k() -> None:
    """flash4 timeout at last-16K address emits the boot-block hint."""
    # 512KB chip: last 16K = 0x7C000–0x7FFFF; pick a mid-last-block address
    last_16k_start = _MEM_SIZE_W29C040 - 0x4000  # 0x7C000
    resp = _make_timeout_response(last_16k_start + 0x100)
    hint = _boot_block_hint_message(resp, _PROTO_FLASH4, _MEM_SIZE_W29C040)
    assert hint is not None, (
        "FIX-01b: boot-block hint must be returned for last-16K address "
        f"0x{last_16k_start + 0x100:06x}"
    )
    assert "may be" in hint
    assert "6.6" in hint
    assert "irreversible" in hint.lower()


def test_boot_block_hint_mid_region_no_hint() -> None:
    """flash4 timeout at a mid-chip address must NOT emit the boot-block hint.

    T-94-MISLABEL: unrelated write faults in the middle of the chip must not
    be labelled as boot-block issues.
    """
    resp = _make_timeout_response(0x040000)  # well inside writable region
    hint = _boot_block_hint_message(resp, _PROTO_FLASH4, _MEM_SIZE_W29C040)
    assert hint is None, (
        "FIX-01b (T-94-MISLABEL): boot-block hint must NOT be returned for "
        "mid-chip address 0x040000; only first/last 16K should trigger the hint"
    )


def test_boot_block_hint_non_flash4_protocol_no_hint() -> None:
    """Non-flash4 protocol (e.g. 0x07 EPROM) must NOT emit the boot-block hint.

    The boot-block lockout feature is W29C040-class §6.6 specific (flash4 / 0x05).
    EPROM protocols do not have boot blocks.
    """
    resp = _make_timeout_response(0x0000FF)  # first-16K address
    hint = _boot_block_hint_message(resp, 0x07, _MEM_SIZE_W29C040)
    assert hint is None, (
        "FIX-01b: boot-block hint must NOT be returned for non-flash4 protocol 0x07"
    )


def test_boot_block_hint_non_timeout_id_no_hint() -> None:
    """A different error ID (not MSG_ERR_FL4_VERIFY_TIMEOUT) must NOT emit the hint.

    Only MSG_ERR_FL4_VERIFY_TIMEOUT (0xB3) triggers the address-range heuristic.
    """
    from firestarter.messages import MSG_ERR_VERIFY

    resp = Response(
        type="ERROR",
        message="Timeout verifying 0x00 at 0x000000 (got 0x00)",
        payload=None,
        id=MSG_ERR_VERIFY,
    )
    hint = _boot_block_hint_message(resp, _PROTO_FLASH4, _MEM_SIZE_W29C040)
    assert hint is None, (
        "FIX-01b: boot-block hint must NOT be returned for non-timeout error IDs"
    )
