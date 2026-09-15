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

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from firestarter import py32_dfu
from firestarter.py32_dfu import ImageError, Py32DfuFlasher

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


# The path is resolved through `fw_path()` -- a hand-built relative path out
# of `tests/` is deliberately never constructed here. `fw_path` raises
# `MissingScanTargetError` when the sibling repo is present but this file is
# not, so a Phase-129 rename of the linker script becomes a hard failure at
# collection/call time, never a silent skip (research finding A-7: a
# firmware rename previously flipped five gate legs PASS->SKIP at exit 0
# with a false "firmware absent" reason). `@requires_fw` -- imported from
# tests/fw_presence.py, which reuses `FW_ABSENT_REASON` -- is the ONLY skip
# marker this module uses, and it fires only when the sibling repo itself
# is genuinely absent (no `../firestarter/.git` marker), never on a
# present-but-renamed scan target.

# _REGION_RE and _parse_regions below are byte-identical copies of
# firestarter/tests/test_py32_flash_map.py's own `_REGION_RE` (~line 172)
# and `_parse_regions` (~line 234) -- kept textually identical on purpose,
# so the two repos' parsers can never quietly diverge from each other.
# Two properties that make a naive regex fail: `ORIGIN` is always a hex
# literal, but `LENGTH` is a DECIMAL integer with an optional `K`/`M` suffix
# (`120K`, not `0x1E000`) -- a hex-only regex would silently match nothing
# (test 8 below pins this); and the internal spacing before the colon
# varies between region lines, which is why the regex tolerates `\s*`
# rather than a fixed column count.
_REGION_RE = re.compile(
    r"^\s*(\w+)\s*\([A-Za-z]+\)\s*:\s*ORIGIN\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*,"
    r"\s*LENGTH\s*=\s*(\d+)\s*([KkMm]?)\s*$",
    re.MULTILINE,
)


def _parse_regions(text: str) -> dict[str, tuple[int, int]]:
    """Returns a dict of region name -> (origin: int, length: int), parsed
    from the MEMORY { ... } block. K/M suffixes on LENGTH are normalised to
    bytes. Copied verbatim from firestarter/tests/test_py32_flash_map.py."""
    m = re.search(r"MEMORY\s*\{(.*?)\n\}", text, re.DOTALL)
    if not m:
        return {}
    block = m.group(1)
    regions: dict[str, tuple[int, int]] = {}
    for name, origin_s, length_s, suffix in _REGION_RE.findall(block):
        origin = int(origin_s, 0)
        length = int(length_s)
        if suffix.lower() == "k":
            length *= 1024
        elif suffix.lower() == "m":
            length *= 1024 * 1024
        regions[name] = (origin, length)
    return regions


def _assert_non_vacuous(regions: dict[str, tuple[int, int]], source: str) -> None:
    """Non-vacuity guard (research finding A-7), run BEFORE any value is
    compared: a parse that found neither FLASH nor CONFIG must be an
    AssertionError, never a silent pass -- an empty (or partial) region
    dict would make every downstream comparison VACUOUSLY TRUE."""
    assert "FLASH" in regions and "CONFIG" in regions, (
        f"parsed {len(regions)} region(s) ({sorted(regions)!r}) from {source} "
        "-- expected to find both FLASH and CONFIG. A parse that found "
        "neither region would make every downstream comparison vacuously "
        "true (research finding A-7)."
    )


def _load_regions(path: Path) -> dict[str, tuple[int, int]]:
    return _parse_regions(path.read_text())


def _assert_config_origin_matches(regions: dict[str, tuple[int, int]]) -> None:
    config_origin, _config_length = regions["CONFIG"]
    assert config_origin == py32_dfu.APP_REGION_END, (
        f"linker CONFIG origin 0x{config_origin:08X} != host "
        f"APP_REGION_END 0x{py32_dfu.APP_REGION_END:08X} -- D-13's host "
        "constant has drifted from the firmware's linker script."
    )


def _git_porcelain(path: Path) -> str:
    git_bin = shutil.which("git")
    assert git_bin is not None, (
        "`git` binary not found on PATH. This must FAIL the suite, never "
        "be silently skipped."
    )
    result = subprocess.run(
        [git_bin, "-C", str(path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout
