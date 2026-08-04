"""D-17's synthetic nonzero-chip-id fixture (v1.30 Phase 134, plan 134-10).

WHAT THIS IS: a thin `EpromDatabase` subclass that answers `get_eprom(name)`
for exactly ONE chip name with a COPY of that chip's real, shipped DB entry
-- with only its `chip-id` field overridden to a NONZERO synthetic value.
Every other field (`name`, `protocol-id`/`algorithm`, `memory-size`,
`pin-count`, `bus-config`, `electrical-type`, ...) stays the real,
DB-derived value; this fixture invents nothing except the one field its
whole purpose is to vary. It is TEST INPUT ONLY -- never imported by any
production module -- and, unlike the AST-scan-only planted fixtures beside
it in this directory (`planted_permit_by_default.py` et al.), it IS meant
to be imported and instantiated at test run time: it drives the real CLI
through `derive_plan`/`resolve_chip`/`run_plan`, not a text scanner.

WHY THIS EXISTS (D-17, measured 2026-08-04, re-measured live at this plan's
own Task 1): every one of the shipped database's SDP-ALLOW chips has
`chip-id == 0` (see `tests/test_dev_test_cmd.py`'s own live re-measurement,
which iterates the real database via `sdp_capability` rather than
restating a count here). `derive_plan` reads that sentinel and emits
`Step(op=OP_ID, supported=False, reason="no chip-id in DB entry")` for
every one of them (`firestarter/chip_test.py` derive_plan's id-check arm),
and `_id_step_closes_gate` never fires on an NA id step -- there is nothing
to compare. This makes the chip-ID destructive gate STRUCTURALLY VACUOUS
for the entire SDP-ALLOW population: no real `dev test` run against a
shipped chip can ever exercise the id-step-mismatch -> gate-closes ->
`sdp_lock` refused causal chain (laundering routes R1/R2, LEG-17). Without
a synthetic nonzero chip-id, that chain can only be asserted about in
prose, never actually driven end to end.

WHAT THIS IS NOT, stated for the record (the v1.22 C-5 overclaim class):
this fixture does NOT make routes R1/R2 reachable in production today.
Every test built on it is UNREACHABLE-IN-PRODUCTION-TODAY, correct and
ready if the shipped database ever gains a nonzero chip-id on an SDP-ALLOW
entry, but pure DEFENCE-IN-DEPTH until then -- never live protection.
Neither this file, nor any test importing it, nor any requirement/report
text may describe the chip-ID gate as what protects an SDP-ALLOW chip
TODAY -- it protects nothing today, by measurement. What actually protects
an SDP-ALLOW chip today is D-08's baseline write/read-back gate and D-12's
recovery wording; this fixture's own existence, and the vacuousness it
exists to compensate for, is *why* those two are more load-bearing, not
less.
"""

from __future__ import annotations

from typing import Any

from firestarter.database import EpromDatabase

# The real ALLOW chip this fixture derives from. Measured at plan time via
# the shipped database: `protocol-id`/`algorithm` 13 (SDP_PROTOCOL_ID),
# `chip-id` 0, `memory-size` 32768, `pin-count` 28 -- every field copied
# verbatim from the real entry; only `chip-id` is overridden below.
SYNTHETIC_CHIP_NAME = "AT28C256"

# Any nonzero value -- the real entry's own `chip-id` is 0 (the sentinel
# `derive_plan` reads as "no chip-id in DB entry"). Deliberately NOT a real
# minipro chip-id value scraped from some other chip's entry: this constant
# must read, in-source, as synthetic, never as a second real chip's
# identity smuggled in under AT28C256's name.
SYNTHETIC_CHIP_ID = 0xBEEF


class SyntheticNonzeroChipIdDatabase(EpromDatabase):
    """`EpromDatabase` subclass that overrides `get_eprom` for ONE chip name.

    Every other method -- `get_eprom_config` (whose raw config
    `chip_resolver.resolve_chip` reads for its `support_status`/`algorithm`
    guards), `convert_to_programmer`, `get_eproms`, ... -- is inherited
    unchanged from the real class, so this fixture varies only the single
    field its module docstring names. `convert_to_programmer` is pure with
    respect to the dict it is handed, so a chip-id override made here in
    `get_eprom` survives unchanged into the programmer dict `derive_plan`
    reads `chip-id` from.
    """

    def __init__(
        self,
        *,
        chip_name: str = SYNTHETIC_CHIP_NAME,
        synthetic_chip_id: int = SYNTHETIC_CHIP_ID,
    ) -> None:
        super().__init__(skip_local_override=True)
        self._synthetic_chip_name = chip_name
        self._synthetic_chip_id = synthetic_chip_id

    def get_eprom(self, chip_name: str) -> dict[str, Any] | None:
        full = super().get_eprom(chip_name)
        if full is not None and chip_name == self._synthetic_chip_name:
            full = dict(full)
            full["chip-id"] = self._synthetic_chip_id
        return full
