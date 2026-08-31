"""Whole-tree regression gate for the historical AT28C16/AT28C64/AT28C256
SDP-capability shorthand (Phase 136.1 Plan 03, PROV-05).

**168-09 note (2026-08-31):** this module originally also gated the
"Lockable PROMs" wiki page's own section 17 text (the AT28C64B/AT28C256
SDP-capable row and the AT28C16/plain-AT28C64 not-SDP-capable row). Those
three legs read the app repository's own copy of the Lockable PROMs
reference (deleted here as part of MIGRATE-02); the content they gated now lives on the
`Lockable-PROMs` wiki page and is checked there, going forward, by the
HONEST-01/HONEST-02 wiki-truth tooling instead of by this repository's test
suite. The three deleted legs
(`test_section_17_states_at28c64b_at28c256_are_sdp_capable`,
`test_section_17_states_at28c16_and_plain_at28c64_are_not_sdp_capable`,
`test_no_wrong_blanket_shorthand_anywhere_in_doc`) are named here, not
silently dropped -- see 168-09-SUMMARY.md for what replaces each one.

The one leg below survives unchanged: it is a tree-wide search over
`firestarter_app`'s own source files (never the wiki), so it has nothing to
do with the deleted directory and remains this project's only durable,
automated guard against the historical wrong-claim shorthand
("AT28C16 / 64 / 256", naming all three part numbers as one uniformly
SDP-capable group) reappearing anywhere in the repository.
"""

import re
from pathlib import Path

_FA_DIR = Path(__file__).parent.parent

# The precise, narrow wrong-claim shape this gate refuses to let back in:
# the historical shorthand naming AT28C16/64/256 as a single uniformly
# SDP-capable group, e.g. "AT28C16 / 64 / 256" or "AT28C16/64/256"
# (REQUIREMENTS.md's own PROV-05 text quotes this exact shorthand). This is
# a real, checkable negative -- it fired historically (pre-c3c9424) and is
# absent today; it is not a vague aspiration that could never trigger.
_WRONG_BLANKET_SHORTHAND_RE = re.compile(r"AT28C16\s*/\s*64\s*/\s*256")


def test_no_wrong_blanket_shorthand_elsewhere_in_tree() -> None:
    """Whole-tree check: no file under firestarter_app repeats the wrong
    blanket-claim shorthand. Scoped to text-ish source files to avoid false
    hits on generated JSON blobs that may coincidentally contain adjacent
    numeric substrings."""
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
        f"outside the wiki, in: {offenders}"
    )
