"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 127 Plan 05 -- D-13 and D-14. Both carry no HOST id: they are a
deliberate in-scope addition closing a host/linker divergence Phase 126
created, in the only repo that can close it (`requirements: [HOST-03]` is
recorded on the plan for schema validity and traceability to the same
install path's pre-write safety only -- this module does NOT discharge
HOST-03; Plans 127-08 and 127-09 do).

**The risk, precisely.** DfuSe erase is payload-scoped, so a legitimate
<=120 KiB application image never reaches Sector 15 (the firmware's
reserved config region) anyway. The defect D-13 closes is that the host's
*guard* (`Py32DfuFlasher._check_envelope`) was bounded on the 128 KiB
physical part size -- looser than the map it claims to enforce -- so a
rogue 128 KiB image would have been ACCEPTED, not that a legitimate image's
erase would ever have strayed into CONFIG. The envelope refusal is
deliberately non-overridable: no force flag, no environment escape exists
in `firestarter/py32_dfu.py`, and this module adds no override either.

This file has two independent halves:

  1. **Envelope behaviour** (below) -- runs everywhere, needs no sibling
     firmware repo, no skip marker.
  2. **Cross-repo linker-script parity gate** (D-14, added by Task 3) --
     `@requires_fw`-gated tests that parse the live linker script, plus
     fail-closed RED demonstrations that need no firmware sibling either.

The four expected addresses below are written as INDEPENDENT LITERALS with
citing comments naming the linker script
(`platform/py32f071/linker/PY32F071xB_FLASH.ld`) -- never imported from
`firestarter.py32_dfu` to build an expectation, so this module cannot
accidentally validate the module under test against itself.
"""

from __future__ import annotations

import pytest

from firestarter import py32_dfu
from firestarter.py32_dfu import ImageError, Py32DfuFlasher

# ---------------------------------------------------------------------------
# Independent literals (D-13). Cited from platform/py32f071/linker/PY32F071xB_FLASH.ld,
# read live at Plan 127-05 authoring time:
#   FLASH   (rx) : ORIGIN = 0x08000000, LENGTH = 120K
#   CONFIG  (r)  : ORIGIN = 0x0801E000, LENGTH = 8K
#   BOOTLOADER (rx) : ORIGIN = 0x08000000, LENGTH = 0  (named zero-length seam)
# Never imported from firestarter.py32_dfu -- these are this module's own
# independently-typed expectation.
# ---------------------------------------------------------------------------
_EXPECTED_FLASH_BASE = 0x08000000
_EXPECTED_APP_REGION_SIZE = 122880  # 120 * 1024
_EXPECTED_APP_REGION_END = 0x0801E000
_EXPECTED_CONFIG_REGION_SIZE = 8192  # 8 * 1024


class TestEnvelopeBehaviour:
    """D-13: _check_envelope bounded on the application region, not the
    physical part size. Calls _check_envelope directly -- it needs no
    device, no interface, no fixture beyond the flasher instance itself."""

    def test_exactly_app_region_size_is_accepted(self) -> None:
        """The largest legitimate application image (exactly
        APP_REGION_SIZE bytes, based at FLASH_BASE) must not be refused --
        this is the boundary a fencepost error would break."""
        flasher = Py32DfuFlasher()
        flasher._check_envelope(py32_dfu.FLASH_BASE, py32_dfu.APP_REGION_SIZE)

    def test_one_byte_over_app_region_size_is_refused(self) -> None:
        flasher = Py32DfuFlasher()
        with pytest.raises(ImageError, match="outside"):
            flasher._check_envelope(py32_dfu.FLASH_BASE, py32_dfu.APP_REGION_SIZE + 1)

    def test_image_ending_inside_config_is_refused(self) -> None:
        """Starts inside the application region and ends inside CONFIG."""
        flasher = Py32DfuFlasher()
        with pytest.raises(ImageError, match="outside"):
            flasher._check_envelope(0x0801D000, 8192)

    def test_image_based_at_app_region_end_is_refused(self) -> None:
        """Entirely inside CONFIG -- the base address itself is already
        past the accepted span."""
        flasher = Py32DfuFlasher()
        with pytest.raises(ImageError, match="outside"):
            flasher._check_envelope(py32_dfu.APP_REGION_END, 1)

    def test_image_based_below_flash_base_is_refused(self) -> None:
        """The lower bound still holds."""
        flasher = Py32DfuFlasher()
        with pytest.raises(ImageError, match="outside"):
            flasher._check_envelope(py32_dfu.FLASH_BASE - 1, 16)

    def test_zero_length_image_is_refused(self) -> None:
        """Unchanged behaviour: the empty-image refusal is separate from
        the envelope bound."""
        flasher = Py32DfuFlasher()
        with pytest.raises(ImageError, match="empty"):
            flasher._check_envelope(py32_dfu.FLASH_BASE, 0)

    def test_rogue_128kib_image_is_now_refused(self) -> None:
        """Regression pin on the OLD bound: an image of FLASH_SIZE (the
        physical 128 KiB part size) bytes at FLASH_BASE was ACCEPTED by the
        pre-D-13 guard, because the guard was bounded on the physical part
        size rather than the application region. This is the assertion
        D-13 exists to add -- the rogue image that reaches into the
        firmware's reserved config region must now be refused."""
        flasher = Py32DfuFlasher()
        with pytest.raises(ImageError, match="outside"):
            flasher._check_envelope(py32_dfu.FLASH_BASE, py32_dfu.FLASH_SIZE)

    def test_constants_match_independent_literals(self) -> None:
        """Internal-consistency check, plus each constant compared against
        this module's own independently-written literal (not imported from
        the module under test to form the expectation)."""
        assert py32_dfu.FLASH_BASE == _EXPECTED_FLASH_BASE
        assert py32_dfu.APP_REGION_SIZE == _EXPECTED_APP_REGION_SIZE
        assert py32_dfu.APP_REGION_END == _EXPECTED_APP_REGION_END
        assert py32_dfu.CONFIG_REGION_SIZE == _EXPECTED_CONFIG_REGION_SIZE
        assert (
            py32_dfu.APP_REGION_END + py32_dfu.CONFIG_REGION_SIZE
            == py32_dfu.FLASH_BASE + py32_dfu.FLASH_SIZE
        )
