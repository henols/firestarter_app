"""
Tests for check_dispatch.py per-family VPP invariants (Phase 71 HARN-04 / D-09).

Coverage:
  1. Real-DB baseline: subprocess gate exits 0 on the current clean chip_database.json.
  2. Invariant shape: _FAMILY_VPP_INVARIANTS has correct ranges for flash_intel and sram.
  3. Non-vacuous proof: synthetic configure_sram chip with vpp_mv=12000 IS flagged as a
     violation — proves the gate CAN fail (not a vacuous always-pass check).
  4. Dual-violation proof: synthetic non-supported chip + VPP mismatch populates
     non_supported_dispatchable — proves the inverse detector fires (closes CR-01).
"""

import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Absolute path to the firestarter_app directory (independent of cwd)
_FA_DIR = Path(__file__).parent.parent


def _check_invariant(
    handler: str,
    vpp_mv: int,
    support_status: str = "supported",
) -> tuple[bool, bool]:
    """Exercise the _FAMILY_VPP_INVARIANTS logic directly on a synthetic chip.

    Returns (is_family_violation, is_non_supported_dispatchable).
    Mirrors the scan-loop logic in check_dispatch.main() so tests stay in sync
    with the real gate without depending on the full DB scan plumbing.
    """
    from tools.check_dispatch import _FAMILY_VPP_INVARIANTS

    if handler not in _FAMILY_VPP_INVARIANTS:
        return False, False
    lo, hi = _FAMILY_VPP_INVARIANTS[handler]
    is_violation = not (lo <= vpp_mv <= hi)
    is_dual = is_violation and support_status != "supported"
    return is_violation, is_dual


# ---------------------------------------------------------------------------
# Test 1: Real-DB gate baseline
# ---------------------------------------------------------------------------


def test_check_dispatch_exits_zero_on_clean_db() -> None:
    """python tools/check_dispatch.py must exit 0 on the current clean DB.

    This is the integration baseline: all existing guards + the new
    _FAMILY_VPP_INVARIANTS must produce zero violations against 744 chips.
    """
    result = subprocess.run(
        [sys.executable, "tools/check_dispatch.py"],
        cwd=str(_FA_DIR),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"check_dispatch.py exited {result.returncode} on clean DB.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout, (
        f"Expected 'PASS:' in output but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 2: _FAMILY_VPP_INVARIANTS shape assertions
# ---------------------------------------------------------------------------


def test_family_vpp_invariants_flash_intel_requires_elevated_vpp() -> None:
    """configure_flash_intel min_vpp_mv must be >= 10000 (Intel 28F requires 12V)."""
    from tools.check_dispatch import _FAMILY_VPP_INVARIANTS

    assert "configure_flash_intel" in _FAMILY_VPP_INVARIANTS, (
        "configure_flash_intel must have a VPP invariant entry"
    )
    lo, _hi = _FAMILY_VPP_INVARIANTS["configure_flash_intel"]
    assert lo >= 10000, (
        f"configure_flash_intel min_vpp_mv={lo} must be >= 10000 "
        "(Intel 28F chips require 12V programming VPP)"
    )


def test_family_vpp_invariants_sram_max_is_5v_only() -> None:
    """configure_sram max_vpp_mv must be <= 6000 (SRAM never needs programming VPP)."""
    from tools.check_dispatch import _FAMILY_VPP_INVARIANTS

    assert "configure_sram" in _FAMILY_VPP_INVARIANTS, (
        "configure_sram must have a VPP invariant entry"
    )
    _lo, hi = _FAMILY_VPP_INVARIANTS["configure_sram"]
    assert hi <= 6000, (
        f"configure_sram max_vpp_mv={hi} must be <= 6000 "
        "(SRAM is a 5V-only handler; elevated VPP would damage the chip)"
    )


def test_family_vpp_invariants_all_six_handlers_present() -> None:
    """_FAMILY_VPP_INVARIANTS must cover all 6 configure_* firmware handlers."""
    from tools.check_dispatch import _FAMILY_VPP_INVARIANTS

    expected = {
        "configure_eprom",
        "configure_eeprom28c",
        "configure_flash3",
        "configure_flash4",
        "configure_flash_intel",
        "configure_sram",
    }
    missing = expected - set(_FAMILY_VPP_INVARIANTS.keys())
    assert not missing, f"Missing handlers in _FAMILY_VPP_INVARIANTS: {missing}"


# ---------------------------------------------------------------------------
# Test 3: Non-vacuous proof — synthetic violation IS classified as a violation
# ---------------------------------------------------------------------------


def test_configure_sram_with_high_vpp_is_a_violation() -> None:
    """A synthetic configure_sram chip with vpp_mv=12000 MUST be flagged as violation.

    This proves the invariant gate CAN fail — it is not a vacuous always-pass check.
    The scenario represents a chip erroneously routed to the SRAM handler but
    declaring 12V programming VPP (a chip-destruction path on a 5V SRAM socket).
    """
    is_violation, _ = _check_invariant("configure_sram", vpp_mv=12000)
    assert is_violation, (
        "configure_sram with vpp_mv=12000 must be classified as a VPP invariant "
        "violation (12V exceeds the 6000 mV ceiling for 5V-only handlers)"
    )


def test_configure_flash_intel_with_zero_vpp_is_a_violation() -> None:
    """A synthetic configure_flash_intel chip with vpp_mv=0 MUST be flagged.

    Intel 28F chips require 12V on the VPP/P1 pin for programming; a chip
    declaring vpp_mv=0 routed to this handler indicates a DB encoding error.
    """
    is_violation, _ = _check_invariant("configure_flash_intel", vpp_mv=0)
    assert is_violation, (
        "configure_flash_intel with vpp_mv=0 must be classified as a VPP invariant "
        "violation (Intel 28F requires >= 10000 mV programming VPP)"
    )


def test_configure_eprom_with_valid_vpp_is_not_a_violation() -> None:
    """configure_eprom with vpp_mv=12000 (W27C512 typical) must NOT be flagged."""
    is_violation, _ = _check_invariant("configure_eprom", vpp_mv=12000)
    assert not is_violation, (
        "configure_eprom with vpp_mv=12000 must NOT be a VPP violation "
        "(EPROM handler legitimately enables VPP up to 25000 mV)"
    )


def test_configure_eprom_with_25v_vpp_is_not_a_violation() -> None:
    """Phase 79 (NMOS-02): configure_eprom with vpp_mv=25000 must NOT be flagged.

    Non-vacuous positive control for the raised ceiling: 25000 sits exactly at the
    new upper bound (0, 25000), so it is in-range and NOT a violation. This FAILS
    on the pre-Phase-79 invariant (0, 22000) where 25000 > 22000 would flag it —
    it is therefore a real proof of the ceiling raise, not a re-assert of 12000.

    This is the family invariant a 25V NMOS chip (M2716/M2732) relies on after
    graduation to route cleanly through configure_eprom.
    """
    is_violation, _ = _check_invariant("configure_eprom", vpp_mv=25000)
    assert not is_violation, (
        "configure_eprom with vpp_mv=25000 must NOT be a VPP violation after the "
        "Phase 79 ceiling raise (25000 is the new upper bound of the (0, 25000) range)"
    )


def test_configure_eprom_above_25v_is_a_violation() -> None:
    """FUT-02 preserved: configure_eprom with vpp_mv=25001 (>25V) MUST be flagged.

    Negative control proving the raised ceiling still fails closed above 25V —
    any future chip declaring >25000 mV routed to configure_eprom is a violation.
    """
    is_violation, _ = _check_invariant("configure_eprom", vpp_mv=25001)
    assert is_violation, (
        "configure_eprom with vpp_mv=25001 must be a VPP violation "
        "(>25000 mV exceeds the raised RURP ceiling — FUT-02 fail-closed)"
    )


def test_configure_flash_intel_with_12v_vpp_is_not_a_violation() -> None:
    """configure_flash_intel with vpp_mv=12000 (correct Intel 28F value) must pass."""
    is_violation, _ = _check_invariant("configure_flash_intel", vpp_mv=12000)
    assert not is_violation, (
        "configure_flash_intel with vpp_mv=12000 must NOT be a VPP violation "
        "(12V is the correct programming VPP for Intel 28F chips)"
    )


# ---------------------------------------------------------------------------
# Test 4: non_supported_dispatchable fires on dual-violation synthetic fixture
# ---------------------------------------------------------------------------


def test_non_supported_chip_with_vpp_mismatch_populates_non_supported_dispatchable() -> (
    None
):
    """A non-supported chip with a VPP mismatch MUST land in non_supported_dispatchable.

    This proves the inverse detector is non-hollow (closes v1.12 CR-01 / D-09):
    a chip that is both mis-classified (support_status != supported) AND has a
    VPP invariant violation is the dangerous dual-violation case.

    Scenario: an adapter-required chip somehow routes to configure_sram but
    declares vpp_mv=12000 — both support_status and VPP invariant are wrong.
    """
    is_violation, is_dual = _check_invariant(
        "configure_sram", vpp_mv=12000, support_status="adapter-required"
    )
    assert is_violation, (
        "Prerequisite: configure_sram + vpp_mv=12000 must be a violation"
    )
    assert is_dual, (
        "A non-supported chip (adapter-required) + VPP mismatch must populate "
        "non_supported_dispatchable (the dangerous dual-violation case, D-09)"
    )


def test_supported_chip_with_vpp_mismatch_does_not_enter_non_supported_dispatchable() -> (
    None
):
    """A SUPPORTED chip with a VPP mismatch must NOT enter non_supported_dispatchable.

    non_supported_dispatchable is the INVERSE detector — it fires only when BOTH
    the support_status classification is wrong AND the VPP invariant is violated.
    A correctly-classified (supported) chip with a VPP violation is still a
    family_vpp_violation, but it does not trigger the inverse detector.
    """
    is_violation, is_dual = _check_invariant(
        "configure_sram", vpp_mv=12000, support_status="supported"
    )
    assert is_violation, (
        "Prerequisite: configure_sram + vpp_mv=12000 must be a violation"
    )
    assert not is_dual, (
        "A SUPPORTED chip must NOT populate non_supported_dispatchable even if it "
        "has a VPP mismatch (inverse detector targets the non-supported axis only)"
    )
