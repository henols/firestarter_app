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
import sys
from pathlib import Path

import pytest

from firestarter import py32_dfu
from firestarter.py32_dfu import ImageError, Py32DfuFlasher
from tests.fw_presence import FW_ROOT, fw_path, requires_fw

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


# ---------------------------------------------------------------------------
# D-14: the fail-closed cross-repo linker-script parity gate.
#
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
# ---------------------------------------------------------------------------
_LINKER_SCRIPT = fw_path("platform", "py32f071", "linker", "PY32F071xB_FLASH.ld")

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


def _git_hash_object(path: Path) -> str:
    """Resolve `git` fail-closed and hash-object `path` inside FW_ROOT."""
    git_bin = shutil.which("git")
    assert git_bin is not None, (
        "`git` binary not found on PATH. This must FAIL the suite, never "
        "be silently skipped."
    )
    result = subprocess.run(
        [git_bin, "-C", str(FW_ROOT), "hash-object", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


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


class TestLinkerScriptParity:
    """D-14's five live parity tests. Each re-parses the real linker script
    (no caching across tests -- the file is small and this keeps every test
    independently readable) and runs the shared non-vacuity guard before
    comparing any value."""

    @requires_fw
    def test_parse_is_non_vacuous(self) -> None:
        """Coverage 1 -- run first and separately, before any value
        comparison. Records the region count in the failure message."""
        regions = _load_regions(_LINKER_SCRIPT)
        _assert_non_vacuous(regions, str(_LINKER_SCRIPT))

    @requires_fw
    def test_config_origin_matches_app_region_end(self) -> None:
        regions = _load_regions(_LINKER_SCRIPT)
        _assert_non_vacuous(regions, str(_LINKER_SCRIPT))
        _assert_config_origin_matches(regions)

    @requires_fw
    def test_flash_length_and_origin_match_app_region(self) -> None:
        regions = _load_regions(_LINKER_SCRIPT)
        _assert_non_vacuous(regions, str(_LINKER_SCRIPT))
        flash_origin, flash_length = regions["FLASH"]
        assert flash_length == py32_dfu.APP_REGION_SIZE, (
            f"linker LENGTH(FLASH) {flash_length} != host APP_REGION_SIZE "
            f"{py32_dfu.APP_REGION_SIZE}"
        )
        assert flash_origin == py32_dfu.FLASH_BASE, (
            f"linker ORIGIN(FLASH) 0x{flash_origin:08X} != host FLASH_BASE "
            f"0x{py32_dfu.FLASH_BASE:08X}"
        )

    @requires_fw
    def test_config_length_and_in_script_adjacency(self) -> None:
        """LENGTH(CONFIG) matches the host, and ORIGIN(FLASH) + LENGTH(FLASH)
        == ORIGIN(CONFIG) IN THE SCRIPT ITSELF -- the two-way check that
        catches a map edit moving both sides consistently but disagreeing
        with the host."""
        regions = _load_regions(_LINKER_SCRIPT)
        _assert_non_vacuous(regions, str(_LINKER_SCRIPT))
        flash_origin, flash_length = regions["FLASH"]
        config_origin, config_length = regions["CONFIG"]
        assert config_length == py32_dfu.CONFIG_REGION_SIZE, (
            f"linker LENGTH(CONFIG) {config_length} != host "
            f"CONFIG_REGION_SIZE {py32_dfu.CONFIG_REGION_SIZE}"
        )
        assert flash_origin + flash_length == config_origin, (
            "the linker script's own FLASH and CONFIG regions are not "
            f"adjacent: ORIGIN(FLASH)+LENGTH(FLASH) = "
            f"0x{flash_origin + flash_length:08X} != ORIGIN(CONFIG) = "
            f"0x{config_origin:08X}"
        )

    @requires_fw
    def test_bootloader_seam_present_at_zero_length(self) -> None:
        """BOOTLOADER must exist with LENGTH 0. Its disappearance or its
        acquiring a non-zero length both MOVE the application's ORIGIN --
        this is the tripwire Phase 129 will trip deliberately; see
        127-CONTEXT.md <deferred>."""
        regions = _load_regions(_LINKER_SCRIPT)
        _assert_non_vacuous(regions, str(_LINKER_SCRIPT))
        assert "BOOTLOADER" in regions, (
            "no BOOTLOADER region found in the MEMORY block -- its "
            "disappearance moves the application's ORIGIN. See "
            "127-CONTEXT.md <deferred>."
        )
        _bootloader_origin, bootloader_length = regions["BOOTLOADER"]
        assert bootloader_length == 0, (
            f"BOOTLOADER LENGTH is {bootloader_length}, not 0 -- a non-zero "
            "length MOVES the application's ORIGIN (every previously "
            "flashed unit's vector-table address changes, on a part with "
            "no VTOR). This is the tripwire Phase 129 will trip "
            "deliberately -- see 127-CONTEXT.md <deferred>."
        )


class TestLinkerScriptParityFailsClosedOnBadInput:
    """The three RED demonstrations (D-14). None carries `@requires_fw`."""

    def test_empty_parse_trips_the_non_vacuity_guard(self) -> None:
        """`_parse_regions` over text with no MEMORY block returns an empty
        mapping, and `_assert_non_vacuous` raises `AssertionError` on it --
        proving the gate cannot pass on a failed parse."""
        regions = _parse_regions("this text has no MEMORY block in it at all")
        assert regions == {}
        with pytest.raises(AssertionError, match="vacuously true"):
            _assert_non_vacuous(regions, "synthetic text")

    def test_planted_mutated_config_origin_is_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A planted, deliberately mutated copy of the real linker script (a
        different CONFIG ORIGIN) must be detected -- proving the parity
        check can actually fail, not merely pass by construction. The
        plant is written to tmp_path and reached by monkeypatching this
        module's path constant; the real file's blob SHA (and the firmware
        repo's porcelain status) are asserted unchanged before and after,
        proving the plant never touched the source of truth -- the shape
        firestarter/tests/test_py32_flash_map.py::
        test_helper_reports_violations_on_planted_copies uses."""
        real_path = _LINKER_SCRIPT  # captured BEFORE any monkeypatch
        before_blob = _git_hash_object(real_path)
        real_text = real_path.read_text()

        mutated_text = real_text.replace(
            "CONFIG (r)  : ORIGIN = 0x0801E000, LENGTH = 8K",
            "CONFIG (r)  : ORIGIN = 0x0801FE00, LENGTH = 8K",
        )
        assert mutated_text != real_text, (
            "planted mutation did not actually differ from the real text "
            "-- the replacement target string was not found (the real "
            "linker script's formatting may have changed)"
        )

        planted_path = tmp_path / "planted-PY32F071xB_FLASH.ld"
        planted_path.write_text(mutated_text)
        monkeypatch.setattr(sys.modules[__name__], "_LINKER_SCRIPT", planted_path)

        regions = _load_regions(_LINKER_SCRIPT)
        _assert_non_vacuous(regions, str(_LINKER_SCRIPT))
        with pytest.raises(AssertionError, match="APP_REGION_END"):
            _assert_config_origin_matches(regions)

        after_blob = _git_hash_object(real_path)
        assert after_blob == before_blob, (
            "the planted mutation touched the REAL linker script -- it "
            "must only ever be written under tmp_path"
        )
        assert _git_porcelain(FW_ROOT) == "", (
            "the firmware repo's working tree is no longer clean after "
            "the planted-copy test -- it is a read-only input to this "
            "phase"
        )

    def test_k_suffix_is_normalised_to_bytes(self) -> None:
        """Documents that suffix normalisation is load-bearing: LENGTH
        values in this linker script are decimal with an optional K/M
        suffix (`120K`), never hex -- a hex-only regex would silently
        match nothing."""
        text = "MEMORY\n{\n    FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 120K\n}\n"
        regions = _parse_regions(text)
        assert regions["FLASH"] == (0x08000000, 122880)
