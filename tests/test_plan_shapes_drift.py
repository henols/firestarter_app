"""
Drift gate for the committed plan-shape pin (Phase 175, D-10/D-11/D-16, plan
175-03) -- the frozen half of D-10's no-drop proof.

An alignment-only no-drop proof (`len(results) == len(plan.steps)`, the
execution half plan 175-04 builds) cannot catch the change PRUNE-05 forbids:
a future prune shrinks `Plan.steps` and the results list together, so
alignment still holds, while 40 chips' six SDP ballast steps silently vanish
from the dedup hash. This module is the frozen half that catches exactly
that case, and it exists because the alignment check structurally cannot.

Leg 2 asserts the six aggregate numbers ABSOLUTELY, not only for drift. A
drift-only gate would let a silently-changed measurement through as long as
it stayed internally consistent with itself -- the whole point of this pin
is that the numbers are MEASURED, not assumed.

This module carries NO skip marker of any kind. `tests/test_sdp_bus_config_drift.py`
guards an artifact that lives in the sibling firmware repo and skips when that
checkout is absent; this artifact is committed IN THIS REPO, so that idiom
does not apply here, and copying it would build the fail-open gate D-16
exists to prevent.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).parent
_APP_DIR = _HERE.parent
_COMMITTED_ARTIFACT = _HERE / "fixtures" / "plan_shapes.json"
_GEN_SCRIPT = _APP_DIR / "tools" / "measure_plan_shapes.py"

_EXPECTED_AGGREGATE = {
    "rows": 746,
    "distinct_part_numbers": 677,
    "plans": 1354,
    "distinct_shape_families": 8,
    "total_steps": 16248,
    "unsupported_steps": 9304,
}


def test_committed_artifact_exists_and_carries_the_generated_by_banner():
    """The committed plan-shape artifact must exist and name its generator
    -- JSON has no comment syntax, so `_generated_by` is the do-not-edit
    banner."""
    assert _COMMITTED_ARTIFACT.exists(), (
        f"plan_shapes.json not found: {_COMMITTED_ARTIFACT}\n"
        "Run: cd firestarter_app && python tools/measure_plan_shapes.py"
    )
    payload = json.loads(_COMMITTED_ARTIFACT.read_text(encoding="utf-8"))
    assert payload.get("_generated_by"), "missing _generated_by banner key"


def test_aggregate_numbers_are_asserted_absolutely_not_only_for_drift():
    """The six measured aggregate numbers, asserted absolutely.

    A drift-only gate would let a silently-changed measurement through as
    long as it stayed internally consistent with itself."""
    assert len(_EXPECTED_AGGREGATE) == 6
    payload = json.loads(_COMMITTED_ARTIFACT.read_text(encoding="utf-8"))
    aggregate = payload["aggregate"]
    assert set(aggregate) == set(_EXPECTED_AGGREGATE), (
        f"aggregate key set changed: {sorted(aggregate)} != "
        f"{sorted(_EXPECTED_AGGREGATE)}"
    )
    for key, expected in _EXPECTED_AGGREGATE.items():
        assert aggregate[key] == expected, (
            f"aggregate[{key!r}] = {aggregate[key]!r}, expected {expected!r} "
            f"-- a measured number moved without a deliberate regeneration"
        )


def test_codegen_produces_byte_identical_output():
    """Re-running the generator against the shipped database must produce a
    byte-identical copy of the committed artifact (drift gate)."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [sys.executable, str(_GEN_SCRIPT), "--target", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=str(_APP_DIR),
        )
        assert result.returncode == 0, (
            f"measure_plan_shapes.py failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        regenerated = tmp_path.read_bytes()
        committed = _COMMITTED_ARTIFACT.read_bytes()

        assert regenerated == committed, (
            "plan_shapes.json is STALE -- re-run to update:\n"
            "  cd firestarter_app && python tools/measure_plan_shapes.py\n"
            f"\nRegenerated output ({len(regenerated)} bytes) differs from "
            f"committed artifact ({len(committed)} bytes)."
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def test_planted_faults_exit_non_zero_and_write_nothing():
    """Non-vacuity leg: this generator's only input is the shipped database,
    which must not be mutated, so its three planted faults come through its
    own `--planted-fault` seam rather than an external file argument (unlike
    `measure_part_number_delta.py`'s `--issues` seam). Each fault must exit
    non-zero and leave the committed artifact byte-unchanged. Also proves a
    `--check` against a missing target is never a silent pass."""
    committed_before = _COMMITTED_ARTIFACT.read_bytes()

    for fault in ("chip-count-skew", "orphan-family", "empty-chips"):
        result = subprocess.run(
            [sys.executable, str(_GEN_SCRIPT), "--planted-fault", fault],
            capture_output=True,
            text=True,
            cwd=str(_APP_DIR),
        )
        assert result.returncode != 0, (
            f"Expected a non-zero exit code for planted fault {fault!r}, got "
            f"{result.returncode}\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert _COMMITTED_ARTIFACT.read_bytes() == committed_before, (
            f"Planted fault {fault!r} wrote to the committed artifact even "
            "though validation was invalid (must validate before emission)"
        )

    with tempfile.TemporaryDirectory() as empty_dir:
        missing_target = Path(empty_dir) / "nope.json"
        result = subprocess.run(
            [
                sys.executable,
                str(_GEN_SCRIPT),
                "--check",
                "--target",
                str(missing_target),
            ],
            capture_output=True,
            text=True,
            cwd=str(_APP_DIR),
        )
        assert result.returncode != 0, (
            f"Expected a non-zero exit code for --check against a missing "
            f"target, got {result.returncode}\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )


def test_family_and_chip_maps_are_closed_and_internally_consistent():
    """Every chip names a real family, every family's chip_count equals the
    number of chips pointing at it, and the counts sum to the aggregate's
    own distinct_part_numbers -- the anti-vacuous floor asserted first."""
    payload = json.loads(_COMMITTED_ARTIFACT.read_text(encoding="utf-8"))
    chips = payload["chips"]
    shape_families = payload["shape_families"]
    aggregate = payload["aggregate"]

    assert chips, "the chips map is empty"

    for chip, family in chips.items():
        assert family in shape_families, (
            f"chip {chip!r} names family {family!r}, which is not a key of "
            "shape_families"
        )

    counted = Counter(chips.values())
    total = 0
    for family, entry in shape_families.items():
        expected = counted.get(family, 0)
        assert entry["chip_count"] == expected, (
            f"family {family!r} declares chip_count {entry['chip_count']}, "
            f"but {expected} chips point at it"
        )
        total += entry["chip_count"]

    assert total == aggregate["distinct_part_numbers"]


def test_output_order_is_specified_and_stable():
    """The output order is specified, not incidental: `chips` sorts by
    `part_number` and `shape_families` sorts by family id, so a chip's
    position is fixed even when two chips compare equal on shape. A
    generator change that moves three chips between families then produces
    a three-line diff naming those three chips instead of reordering churn
    across the whole 677-entry map -- D-11's whole point."""
    payload = json.loads(_COMMITTED_ARTIFACT.read_text(encoding="utf-8"))
    assert list(payload["chips"]) == sorted(payload["chips"])
    assert list(payload["shape_families"]) == sorted(payload["shape_families"])

    raw = _COMMITTED_ARTIFACT.read_text(encoding="utf-8")
    assert raw.endswith("}\n") and not raw.endswith("}\n\n"), (
        "committed artifact must end with exactly one trailing newline"
    )
