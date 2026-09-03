"""
Drift gate for the raw-CLI-token -> `part_number` delta artifact (Phase 174,
D-14/D-15/D-16, GATE-04, plan 174-04 task 3).

Mirrors `tests/test_sdp_bus_config_drift.py`'s four-leg shape, adapted for an
artifact that lives IN THIS REPO rather than in the sibling firmware repo:
the committed target is resolved from `Path(__file__).parent`, one level,
not the analog's two-level sibling-repo climb, and this module carries no
skip marker at all. The analog's firmware-path skip markers exist because
its artifact lives in `henols/firestarter`; copying them here would build
precisely the fail-open gate D-16 exists to prevent.

Leg 2 asserts the eleven aggregate numbers ABSOLUTELY, not only for drift.
The whole point of GATE-04 is that these are MEASURED values rather than
assumed ones -- a gate that only checked self-consistency would let a
silently-changed measurement through as long as it stayed consistent with
itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_APP_DIR = Path(__file__).parent.parent
_GEN_SCRIPT = _APP_DIR / "tools" / "measure_part_number_delta.py"
_COMMITTED_ARTIFACT = _APP_DIR / "tests" / "fixtures" / "part_number_delta.json"
_REAL_ISSUES = _APP_DIR / "tests" / "fixtures" / "devtest_issue_corpus.json"

_EXPECTED_AGGREGATE = {
    "rows": 746,
    "vendors": 59,
    "distinct_part_numbers": 677,
    "part_numbers_with_comma": 234,
    "distinct_aliases": 953,
    "aliases_token_differs_from_part_number": 942,
    "aliases_token_equals_part_number": 11,
    "aliases_resolving_to_comma_joined": 514,
    "aliases_chip_not_implemented": 16,
    "aliases_chip_not_found": 0,
    "part_numbers_not_lowercase_published_proxy": 732,
}


def test_committed_artifact_exists_and_carries_the_generated_by_banner():
    """The committed delta artifact must exist and name its generator --
    JSON has no comment syntax, so the `_generated_by` key is the
    do-not-edit banner."""
    assert _COMMITTED_ARTIFACT.exists(), (
        f"part_number_delta.json not found: {_COMMITTED_ARTIFACT}\n"
        "Run: cd firestarter_app && python tools/measure_part_number_delta.py"
    )
    payload = json.loads(_COMMITTED_ARTIFACT.read_text(encoding="utf-8"))
    assert payload.get("_generated_by"), "missing _generated_by banner key"


def test_aggregate_numbers_are_asserted_absolutely_not_only_for_drift():
    """The eleven measured aggregate numbers, asserted absolutely.

    GATE-04's whole point is that this delta is a MEASURED artifact rather
    than an assumed one; a drift-only gate would let a silently-changed
    measurement through as long as it stayed internally consistent."""
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
    assert len(payload["aliases"]) == 953
    assert len(payload["filed_issues"]) == 26
    assert all(row["differs"] for row in payload["filed_issues"]), (
        "all 26 filed-issue rows must have token != resolved part_number"
    )


def test_codegen_produces_byte_identical_output():
    """Re-running the generator against the real --issues input must
    produce a byte-identical copy of the committed artifact (drift gate)."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_GEN_SCRIPT),
                "--target",
                str(tmp_path),
                "--issues",
                str(_REAL_ISSUES),
            ],
            capture_output=True,
            text=True,
            cwd=str(_APP_DIR),
        )
        assert result.returncode == 0, (
            f"measure_part_number_delta.py failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        regenerated = tmp_path.read_bytes()
        committed = _COMMITTED_ARTIFACT.read_bytes()

        assert regenerated == committed, (
            "part_number_delta.json is STALE -- re-run to update:\n"
            "  cd firestarter_app && python tools/measure_part_number_delta.py\n"
            f"\nRegenerated output ({len(regenerated)} bytes) differs from "
            f"committed artifact ({len(committed)} bytes)."
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def test_planted_nonexistent_chip_token_fails_closed_and_writes_nothing():
    """Non-vacuity leg: plant a corpus row whose raw token resolves to no
    chip in the shipped database, point the generator's --issues override
    seam at it, and assert it exits non-zero and writes nothing to
    --target (the validate-before-emit property)."""
    real_issues = json.loads(_REAL_ISSUES.read_text(encoding="utf-8"))
    assert real_issues["rows"][0]["raw_token"] == "fm1608", (
        "test fixture assumption stale -- the first corpus row's raw_token "
        "is no longer 'fm1608'; update this planted-fault value"
    )
    broken_issues = json.loads(json.dumps(real_issues))
    broken_issues["rows"][0]["raw_token"] = "totally-nonexistent-eprom-xyz"

    with tempfile.NamedTemporaryFile(
        suffix=".json", delete=False, mode="w", encoding="utf-8"
    ) as bad_issues_file:
        json.dump(broken_issues, bad_issues_file)
        bad_issues_path = Path(bad_issues_file.name)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_out = Path(tmp.name)
        tmp_out.unlink()

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_GEN_SCRIPT),
                "--issues",
                str(bad_issues_path),
                "--target",
                str(tmp_out),
            ],
            capture_output=True,
            text=True,
            cwd=str(_APP_DIR),
        )
        assert result.returncode != 0, (
            f"Expected a non-zero exit code for a planted nonexistent chip "
            f"token, got {result.returncode}\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert not tmp_out.exists(), (
            "Script wrote output even though derivation was invalid "
            "(must validate before emission)"
        )
    finally:
        bad_issues_path.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)
