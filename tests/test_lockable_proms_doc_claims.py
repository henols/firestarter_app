"""
Durable regression gate for `doc/lockable-proms.md` section 17's
AT28C16/AT28C64/AT28C256 SDP-capability claim (Phase 136.1 Plan 03, PROV-05).

**Stale-premise notice, recorded honestly:** PROV-05 was written from a
maintainer memory note claiming the doc's section 17 STILL said "Atmel
AT28C16 / 64 / 256" are uniformly SDP-capable. Measured 2026-08-05, against
the live tree: that claim is FALSE today. `doc/lockable-proms.md` section 17
(`:295-296`) ALREADY states the corrected fact --

  - `:295` -- **AT28C64B** and **AT28C256** (page-write EEPROMs, incl.
    BV/LV/HC/MC variants): "SDP can be enabled/disabled" (SDP-capable).
  - `:296` -- **AT28C16** (incl. AT28C16E/F) and **plain AT28C64**: "No -- no
    SDP command decoder at all... not SDP-capable, unlike AT28C64B/AT28C256
    above" (NOT SDP-capable).

This correction was landed by **Phase 121 plan 121-13**, commit
**`c3c9424f7a299c6ff3498a15620e5235cf72a782`**
("docs(121-13): ship the corrected lockable-proms reference and state the
always-writes reality in the tester-facing docs (GATE-02, D-04/D-16/D-17)"),
well before Phase 136.1 was even scoped (2026-08-05 planning, citing a
2026-08-03-scoped milestone). This test module does NOT re-author the
correction -- it did not need to be re-authored -- it VERIFIES the
already-landed fact is still present and makes it a durable, automated
regression so the mistake (per REQUIREMENTS.md's own PROV-05 note, "reproduced
twice from part-number familiarity") cannot recur a third time silently.

**Whole-tree stale-copy scan (measured 2026-08-05):** grepped every
`*.md`/`*.py`/`*.txt` file in the `firestarter_app` tree (SECURITY.md,
doc/pinout-safety-review.md, tests/test_build_db_inclusion.py,
tests/test_sdp_db_invariant.py, tests/test_sdp_capability.py,
tests/test_dev_test_cmd.py, tests/test_cli_handlers.py, tools/build_db.py,
tools/gen_sdp_bus_config.py, tools/variant-decode-diff.txt, and the data/
baseline JSON files) for the wrong blanket-claim shape -- three part numbers
(`AT28C16`, `AT28C64`, `AT28C256`) named together as uniformly SDP-capable
with NO B-suffix/non-B-suffix distinction, including the exact historical
shorthand `"AT28C16 / 64 / 256"` quoted in REQUIREMENTS.md's own PROV-05 text.
Result: **zero hits** for the wrong shape anywhere. The only place all three
part numbers co-occur on one line is `doc/lockable-proms.md:296` itself --
and that line explicitly DRAWS the distinction ("...unlike AT28C64B/AT28C256
above"), i.e. it is the corrected form, not a stale copy of the wrong one.
"""

import re
from pathlib import Path

_FA_DIR = Path(__file__).parent.parent
_DOC_FILE = _FA_DIR / "doc" / "lockable-proms.md"

# The precise, narrow wrong-claim shape this gate refuses to let back in:
# the historical shorthand naming AT28C16/64/256 as a single uniformly
# SDP-capable group, e.g. "AT28C16 / 64 / 256" or "AT28C16/64/256"
# (REQUIREMENTS.md's own PROV-05 text quotes this exact shorthand). This is
# a real, checkable negative -- it fired historically (pre-c3c9424) and is
# absent today; it is not a vague aspiration that could never trigger.
_WRONG_BLANKET_SHORTHAND_RE = re.compile(r"AT28C16\s*/\s*64\s*/\s*256")


def _read_doc_text() -> str:
    return _DOC_FILE.read_text(encoding="utf-8")


def test_section_17_states_at28c64b_at28c256_are_sdp_capable() -> None:
    """`:295`'s row: AT28C64B / AT28C256 (page-write EEPROMs) are described
    as SDP-capable ("SDP can be enabled/disabled"). Landed by c3c9424
    (121-13); this test durably gates it going forward."""
    text = _read_doc_text()
    assert "AT28C64B" in text and "AT28C256" in text, (
        "doc/lockable-proms.md must name AT28C64B and AT28C256 explicitly "
        "in section 17's SDP-capability table."
    )
    # Both part numbers must appear together on the same table row, with
    # wording that indicates SDP is an actual, controllable capability.
    capable_row_re = re.compile(
        r"AT28C64B.{0,80}AT28C256.{0,120}SDP can be enabled/disabled",
        re.DOTALL,
    )
    assert capable_row_re.search(text), (
        "Expected a single table row naming AT28C64B and AT28C256 together "
        "with 'SDP can be enabled/disabled' wording (section 17, historically "
        "line 295) -- not found. doc/lockable-proms.md may have regressed."
    )


def test_section_17_states_at28c16_and_plain_at28c64_are_not_sdp_capable() -> None:
    """`:296`'s row: AT28C16 (incl. AT28C16E/F) and plain AT28C64 are
    described as having NO SDP command decoder at all -- distinct from, and
    explicitly contrasted with, AT28C64B/AT28C256. Landed by c3c9424
    (121-13); this test durably gates it going forward."""
    text = _read_doc_text()
    assert "AT28C16" in text, (
        "doc/lockable-proms.md must name AT28C16 explicitly in section 17's "
        "SDP-capability table."
    )
    not_capable_row_re = re.compile(
        r"AT28C16.{0,80}plain AT28C64.{0,160}"
        r"No\s*—\s*no SDP command decoder at all",
        re.DOTALL,
    )
    assert not_capable_row_re.search(text), (
        "Expected a single table row naming AT28C16 (incl. AT28C16E/F) and "
        "'plain AT28C64' together with 'No — no SDP command decoder at "
        "all' wording (section 17, historically line 296) -- not found. "
        "doc/lockable-proms.md may have regressed."
    )
    # The row must explicitly distinguish itself from the SDP-capable row
    # above it (never silently re-conflate the two groups).
    assert "unlike AT28C64B/AT28C256" in text or "unlike AT28C64B / AT28C256" in text, (
        "The AT28C16/plain-AT28C64 row must explicitly contrast itself "
        "against AT28C64B/AT28C256 -- the distinguishing clause is missing."
    )


def test_no_wrong_blanket_shorthand_anywhere_in_doc() -> None:
    """The precise historical wrong-claim shorthand ('AT28C16 / 64 / 256',
    naming all three part numbers as one uniformly-capable group) must not
    appear anywhere in the doc -- this is the exact shape REQUIREMENTS.md's
    PROV-05 text quotes as the error."""
    text = _read_doc_text()
    hits = _WRONG_BLANKET_SHORTHAND_RE.findall(text)
    assert not hits, (
        f"Found the wrong blanket-claim shorthand 'AT28C16 / 64 / 256' "
        f"{len(hits)} time(s) in doc/lockable-proms.md -- this implies all "
        f"three parts are uniformly SDP-capable, which is false for AT28C16 "
        f"and plain AT28C64 (see section 17's corrected, distinguishing row)."
    )


def test_no_wrong_blanket_shorthand_elsewhere_in_tree() -> None:
    """Whole-tree check (not just this one doc): no OTHER file under
    firestarter_app repeats the wrong blanket-claim shorthand. Scoped to
    text-ish source files to avoid false hits on generated JSON blobs that
    may coincidentally contain adjacent numeric substrings."""
    _self_path = Path(__file__).resolve()
    offenders = []
    for pattern in ("*.md", "*.py", "*.txt"):
        for path in _FA_DIR.rglob(pattern):
            if ".git" in path.parts:
                continue
            if path.resolve() == _self_path:
                # This test module's own docstring quotes the wrong shape
                # verbatim (to name it precisely) -- exclude the gate's own
                # source from the scan it performs.
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if _WRONG_BLANKET_SHORTHAND_RE.search(text):
                offenders.append(str(path.relative_to(_FA_DIR)))
    assert not offenders, (
        f"Found the wrong blanket-claim shorthand 'AT28C16 / 64 / 256' "
        f"outside doc/lockable-proms.md, in: {offenders}"
    )
