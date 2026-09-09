#!/usr/bin/env python3
"""
Generator for the committed plan-shape pin (Phase 175, D-10/D-11/D-16, plan
175-03) -- the frozen half of D-10's no-drop proof.

An alignment-only no-drop proof (`len(results) == len(plan.steps)`, the
execution half plan 175-04 builds) cannot catch the change PRUNE-05 forbids:
if a future phase prunes unsupported steps inside `derive_plan`, both sides
of that equality shrink together and alignment still holds, while the six
SDP ballast steps on 40 chips' live leg -- and the NA ballast on the other
637 -- silently vanish from the dedup hash. This pin is what reddens in that
case, because it is measured independently of `derive_plan`'s own alignment
invariant.

The pin is at the `(op, supported)` grain, not the op-only grain. This is a
deliberate STRENGTHENING of CONTEXT's D-10 rather than a departure from it:
D-10's "the 4 distinct op-sequences" is a correct measurement of the op-only
grain, and the finer grain costs nothing in artifact size while catching an
SDP `supported` flip on 40 chips -- an op-only sequence stays byte-identical
across that flip, so PRUNE-05's own concern is invisible at that grain.

`firestarter/data/chip_database.json` is GENERATED, never hand-edited. When
this pin reddens because the database was regenerated, the fix is to re-run
this generator and commit the new artifact with a narrated reason -- never
to hand-edit the database and never to relax the pin.

Exit codes:
  0 -- derivation valid, artifact emitted successfully (or --check found no
       drift)
  1 -- derived aggregate failed validation, or --check found the committed
       artifact stale or missing
  2 -- the corpus could not be derived (empty corpus, or the database could
       not be read)
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).parent
_APP_ROOT = _TOOLS_DIR.parent
_TARGET_DEFAULT = _APP_ROOT / "tests" / "fixtures" / "plan_shapes.json"

sys.path.insert(0, str(_APP_ROOT))

_PLANTED_FAULTS = ("chip-count-skew", "orphan-family", "empty-chips")


class DerivationError(Exception):
    """The corpus could not be derived: empty corpus, or the shipped
    database could not be read (exit code 2)."""


class ValidationError(Exception):
    """A derived payload failed a validate-before-emit invariant (exit code
    1)."""


def _step_token(op: str, supported: bool) -> str:
    return op if supported else f"{op}(NA)"


def _family_token(plan) -> str:
    """The chip's `(op, supported)` shape, collapsed to four hyphen-joined
    tokens, computed from the chip's single reachable-scope plan (181-02
    D-08/D-09: `partial` for a UV chip, `full` otherwise -- the same rule
    `tests/plan_corpus.py`'s `plan_corpus()` uses). This scheme is
    bijective on the shipped database's reachable-scope plans (`validate`
    proves it rather than assuming it, and also proves no family mixes a
    UV and a non-UV member -- their write op strings ("write-partial" vs
    "write") would otherwise disagree under the same family label):
    knowing the four tokens fully determines the step sequence, because
    `derive_plan`'s own op ORDER is a function of exactly these same four
    facts (id-check presence, blank-check position, erase presence, SDP-leg
    presence), and every step's `supported` flag is one of these four flags
    or a constant.
    """
    from firestarter.chip_test import (
        OP_BLANK_CHECK,
        OP_ERASE,
        OP_ID,
        OP_SDP_LOCK,
        OP_WRITE,
        OP_WRITE_PARTIAL,
    )

    steps = plan.steps

    id_step = next(s for s in steps if s.op == OP_ID)
    id_token = "id" if id_step.supported else "noid"

    write_idx = next(
        i for i, s in enumerate(steps) if s.op in (OP_WRITE, OP_WRITE_PARTIAL)
    )

    bc_indices = [i for i, s in enumerate(steps) if s.op == OP_BLANK_CHECK]
    if not bc_indices:
        bc_token = "nobc"
    else:
        bc_idx = bc_indices[0]
        bc_step = steps[bc_idx]
        position = "bcpre" if bc_idx < write_idx else "bcpost"
        bc_token = position if bc_step.supported else f"{position}NA"

    erase_steps = [s for s in steps if s.op == OP_ERASE]
    if not erase_steps:
        erase_token = "nonerase"
    else:
        erase_token = "erase" if erase_steps[0].supported else "eraseNA"

    sdp_steps = [s for s in steps if s.op == OP_SDP_LOCK]
    if not sdp_steps:
        sdp_token = "nosdp"
    else:
        sdp_token = "sdp" if sdp_steps[0].supported else "sdpNA"

    return f"{id_token}-{bc_token}-{erase_token}-{sdp_token}"


def derive() -> dict:
    from tests.plan_corpus import PART_NUMBERS, REAL_DB, all_rows, plan_corpus

    try:
        corpus = plan_corpus()
    except Exception as exc:
        raise DerivationError(f"could not derive the plan corpus: {exc}") from exc

    if not corpus or not PART_NUMBERS:
        raise DerivationError("the derived plan corpus is empty")

    chips: dict[str, str] = {}
    shape_families: dict[str, dict] = {}
    total_steps = 0
    unsupported_steps = 0

    for name in PART_NUMBERS:
        plan = corpus[name]

        total_steps += len(plan.steps)
        unsupported_steps += sum(1 for s in plan.steps if not s.supported)

        family = _family_token(plan)
        chips[name] = family

        if family not in shape_families:
            shape_families[family] = {
                "chip_count": 0,
                "steps": [_step_token(s.op, s.supported) for s in plan.steps],
            }
        shape_families[family]["chip_count"] += 1

    aggregate = {
        "rows": len(all_rows(REAL_DB)),
        "distinct_part_numbers": len(chips),
        "plans": len(chips),
        "distinct_shape_families": len(shape_families),
        "total_steps": total_steps,
        "unsupported_steps": unsupported_steps,
    }

    return {
        "_generated_by": "tools/measure_plan_shapes.py",
        "aggregate": aggregate,
        "shape_families": shape_families,
        "chips": chips,
    }


def _apply_planted_fault(payload: dict, fault: str) -> dict:
    """Mutate a derived payload to deliberately fail `validate` -- the
    override seam this generator's own fail-closed legs need. The generator
    has no external input other than the shipped database, which must not be
    mutated, so the fault is planted here rather than through an --issues-
    style file argument.
    """
    if fault == "chip-count-skew":
        family = next(iter(payload["shape_families"]))
        payload["shape_families"][family]["chip_count"] -= 1
    elif fault == "orphan-family":
        chip = next(iter(payload["chips"]))
        payload["chips"][chip] = "not-a-real-family-id"
    elif fault == "empty-chips":
        payload["chips"] = {}
    return payload


def validate(payload: dict) -> None:
    chips = payload["chips"]
    shape_families = payload["shape_families"]
    aggregate = payload["aggregate"]

    if not chips:
        raise ValidationError("the chips map is empty")

    for chip, family in chips.items():
        if family not in shape_families:
            raise ValidationError(
                f"chip {chip!r} names family {family!r}, which is not a key "
                "of shape_families"
            )

    counted = collections.Counter(chips.values())
    for family, entry in shape_families.items():
        expected = counted.get(family, 0)
        if entry["chip_count"] != expected:
            raise ValidationError(
                f"family {family!r} declares chip_count "
                f"{entry['chip_count']}, but {expected} chips point at it"
            )

    if aggregate["distinct_part_numbers"] != len(chips):
        raise ValidationError(
            f"aggregate distinct_part_numbers {aggregate['distinct_part_numbers']} "
            f"!= len(chips) {len(chips)}"
        )
    if aggregate["plans"] != len(chips):
        raise ValidationError(
            f"aggregate plans {aggregate['plans']} != len(chips) {len(chips)}"
        )
    if aggregate["distinct_shape_families"] != len(shape_families):
        raise ValidationError(
            f"aggregate distinct_shape_families {aggregate['distinct_shape_families']} "
            f"!= len(shape_families) {len(shape_families)}"
        )

    from firestarter.chip_test import is_uv_eprom
    from tests.plan_corpus import PART_NUMBERS, REAL_DB, plan_corpus

    corpus = plan_corpus()
    recomputed_total_steps = 0
    recomputed_unsupported_steps = 0
    seen_shapes: dict[str, tuple[str, ...]] = {}
    seen_uv: dict[str, bool] = {}
    for name in PART_NUMBERS:
        plan = corpus[name]
        recomputed_total_steps += len(plan.steps)
        recomputed_unsupported_steps += sum(1 for s in plan.steps if not s.supported)

        family = chips.get(name)
        if family is None or family not in shape_families:
            continue

        is_uv = is_uv_eprom(REAL_DB.get_eprom(name))
        if family in seen_uv and seen_uv[family] != is_uv:
            raise ValidationError(
                f"family {family!r} mixes a UV and a non-UV chip -- their "
                f"write op strings ('write-partial' vs 'write') disagree "
                f"under the same family label; chip {name!r} broke it"
            )
        seen_uv[family] = is_uv

        shape = tuple(_step_token(s.op, s.supported) for s in plan.steps)
        if family in seen_shapes and seen_shapes[family] != shape:
            raise ValidationError(
                f"family {family!r} is not bijective with its shape: chip "
                f"{name!r} disagrees with an earlier member of the same "
                "family"
            )
        seen_shapes[family] = shape

    for family, entry in shape_families.items():
        recorded = tuple(entry["steps"])
        if family in seen_shapes and seen_shapes[family] != recorded:
            raise ValidationError(
                f"family {family!r}'s recorded shape does not match its "
                "members' actual shape"
            )

    if aggregate["total_steps"] != recomputed_total_steps:
        raise ValidationError(
            f"aggregate total_steps {aggregate['total_steps']} != "
            f"recomputed {recomputed_total_steps}"
        )
    if aggregate["unsupported_steps"] != recomputed_unsupported_steps:
        raise ValidationError(
            f"aggregate unsupported_steps {aggregate['unsupported_steps']} != "
            f"recomputed {recomputed_unsupported_steps}"
        )


def render(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="measure_plan_shapes.py",
        description=(
            "Measure every plan shape the shipped database produces, at "
            "the (op, supported) grain, and emit the committed pin."
        ),
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=_TARGET_DEFAULT,
        help=(
            "Output path for the plan-shape artifact (default: "
            "tests/fixtures/plan_shapes.json)."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Derive, validate, render, and compare against the existing "
            "--target file. Return non-zero on mismatch without writing."
        ),
    )
    parser.add_argument(
        "--planted-fault",
        choices=_PLANTED_FAULTS,
        default=None,
        help=(
            "Deliberately corrupt the derived payload before validation, to "
            "prove the validator can fail. Never use this to produce a "
            "committed artifact."
        ),
    )
    return parser


def main() -> int:
    args = _build_argparser().parse_args()

    try:
        payload = derive()
    except DerivationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.planted_fault is not None:
        payload = _apply_planted_fault(payload, args.planted_fault)

    try:
        validate(payload)
    except ValidationError as exc:
        print(f"ERROR: derivation validation failed: {exc}", file=sys.stderr)
        return 1

    output = render(payload)

    if args.check:
        if not args.target.is_file():
            print(f"DRIFT: target does not exist: {args.target}", file=sys.stderr)
            return 1
        existing = args.target.read_text(encoding="utf-8")
        if existing != output:
            print(
                f"DRIFT: {args.target} differs from a fresh regeneration "
                f"({len(existing)} bytes committed vs {len(output)} bytes "
                f"regenerated)",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {args.target} matches a fresh regeneration")
        return 0

    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.target.write_text(output, encoding="utf-8", newline="\n")
    print(f"OK: wrote {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
