"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 86 — D-09 / VAR-04 EVIDENCE wire-stability oracle (Wave-0).

The Phase-86 classifier rewrite (Plan 02 deletes build_db.py Rule 1/2/3 and
re-derives electrical.type/algorithm/pinout from a principled
classify(type,proto,pm_idx,flags,...)) must NOT silently move the wire values
of the on-hand, bench-proven chips. D-09: the v1.15 EVIDENCE chips keep their
`programming.algorithm`, `electrical.vpp_mv`, and `pinout`, OR any moved value
is flagged for a Leonardo + RURP Rev 2.0 re-bench before Phase 90.

This test is the no-silent-move gate. It:
  - sources the EVIDENCE chip identifiers from
    .planning/v1.15/bench/EVIDENCE.json `cells` at test time (NOT hand-copied),
  - scopes the stability assertions to the 10 UPSTREAM-DECODED EVIDENCE chips
    (every EVIDENCE chip EXCEPT 2516 — the chips that come from the infoic.xml
    decode), and
  - for each such chip present in the GENERATED chip_database.json, asserts
    algorithm / vpp_mv / pinout equal the values in the OLD baseline
    (tools/baseline/chip_database.baseline.json).

2516 is DEFERRED to Plan 86-04: 2516 is the ONLY EVIDENCE chip absent from
infoic.xml; per operator directive D-10/D-11 it is introduced first-class via a
non-upstream supplement in Plan 86-04 (which runs AFTER the Plan-02 regen and
asserts its UNVERIFIED status + wire-value stability there). This test makes NO
assertion about 2516's presence/absence — its stability is owned by Plan 86-04.

The test reads BOTH the current DB and the OLD baseline via path constants with
the FIRESTARTER_DB_FILE / FIRESTARTER_BASELINE_FILE env seams so it can run pre-
and post-regen. Against the current (pre-regen) DB it passes trivially (each
upstream-decoded chip already equals the baseline). After the Plan-02 regen it
asserts each upstream-decoded EVIDENCE chip is unchanged OR fails loudly and
surfaces the moved chip for the Plan-02/03 re-bench flag (D-09).
"""

import json
import os

import pytest

# ---------------------------------------------------------------------------
# Path seams (mirror the FIRESTARTER_DB_FILE idiom from test_build_db_inclusion)
# ---------------------------------------------------------------------------
_DB_FILE = os.environ.get(
    "FIRESTARTER_DB_FILE",
    os.path.join(
        os.path.dirname(__file__), "..", "firestarter", "data", "chip_database.json"
    ),
)

_BASELINE_FILE = os.environ.get(
    "FIRESTARTER_BASELINE_FILE",
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "tools",
        "baseline",
        "chip_database.baseline.json",
    ),
)

# EVIDENCE.json lives in the meta repo (outside this submodule); the env seam
# lets the meta-repo test runner point at it. The default relative path resolves
# when the submodule is checked out inside the meta repo working tree.
_EVIDENCE_FILE = os.environ.get(
    "FIRESTARTER_EVIDENCE_FILE",
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        ".planning",
        "v1.15",
        "bench",
        "EVIDENCE.json",
    ),
)

# The single EVIDENCE chip that is ABSENT from infoic.xml — its stability is
# owned by Plan 86-04 (non-upstream supplement, UNVERIFIED). EXCLUDED here.
_UPSTREAM_ABSENT = {"2516"}

# Map an EVIDENCE `chip` label to the DB/baseline alias-set used to resolve its
# record (EVIDENCE uses bench/CLI labels; the DB stores comma-joined part_number
# aliases). ST M27C512's CLI name is "M27C512"; W27E040 ships joined with W27C040.
_EVIDENCE_ALIAS = {
    "W27C512": {"W27C512"},
    "W27E512": {"W27E512"},
    "SST27SF512": {"SST27SF512"},
    "W27E040": {"W27E040", "W27C040"},
    "SST39SF040": {"SST39SF040"},
    "W29C020": {"W29C020"},
    "W29C040": {"W29C040"},
    "FM1608": {"FM1608"},
    "ST M27C512": {"M27C512"},
    "AM27C020": {"AM27C020"},
}

_WIRE_FIELDS = ("algorithm", "vpp_mv", "pinout")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _all_chips(db):
    for mfg, chips in db.items():
        if isinstance(chips, list):
            for chip in chips:
                yield mfg, chip


def _aliases(chip):
    pn = chip.get("part_number", "")
    return {a.split("@")[0].strip() for a in pn.split(",") if a.strip()}


def _wire(chip):
    """Extract the three D-09-protected wire fields from a chip record."""
    return {
        "algorithm": chip.get("programming", {}).get("algorithm"),
        "vpp_mv": chip.get("electrical", {}).get("vpp_mv"),
        "pinout": chip.get("pinout"),
    }


def _find(db, alias_set):
    """Return the wire-field dict of the first chip whose aliases intersect."""
    for _mfg, chip in _all_chips(db):
        if _aliases(chip) & alias_set:
            return _wire(chip)
    return None


def _upstream_decoded_evidence_chips():
    """Source the EVIDENCE chip labels from EVIDENCE.json `cells` at test time,
    excluding 2516 (the only upstream-absent chip, owned by Plan 86-04).

    Returns a sorted list of distinct EVIDENCE `chip` labels (the `cells` list
    contains duplicate entries per chip — one per bench op).
    """
    if not os.path.exists(_EVIDENCE_FILE):
        pytest.skip(
            f"EVIDENCE.json not found at {_EVIDENCE_FILE} "
            "(meta-repo artifact; set FIRESTARTER_EVIDENCE_FILE to run)"
        )
    ev = _load(_EVIDENCE_FILE)
    labels = set()
    for cell in ev.get("cells", []):
        label = cell.get("chip")
        if not label or label in _UPSTREAM_ABSENT:
            continue
        labels.add(label)
    return sorted(labels)


# ---------------------------------------------------------------------------
# D-09 / VAR-04: upstream-decoded EVIDENCE wire-stability (no silent move)
# ---------------------------------------------------------------------------
class TestEvidenceWireStability:
    """D-09: the 10 upstream-decoded EVIDENCE chips keep algorithm/vpp_mv/pinout
    against the OLD baseline. (2516 excluded — owned by Plan 86-04.)"""

    def test_evidence_labels_sourced_and_2516_excluded(self):
        """The EVIDENCE chip labels are sourced from EVIDENCE.json and 2516 is
        excluded (Plan-86-04-owned). Guards against silently forgetting 2516 or
        drifting the alias map out of sync with the bench evidence."""
        labels = _upstream_decoded_evidence_chips()
        assert "2516" not in labels, "2516 must be excluded (owned by Plan 86-04)"
        # Every sourced upstream-decoded label must have an alias mapping so a
        # missing DB record is a loud failure, not a silent skip.
        unmapped = [lbl for lbl in labels if lbl not in _EVIDENCE_ALIAS]
        assert not unmapped, (
            f"EVIDENCE labels with no alias mapping (update _EVIDENCE_ALIAS): {unmapped}"
        )
        # Sanity: the 10 expected upstream-decoded chips.
        assert set(labels) == set(_EVIDENCE_ALIAS), (
            f"sourced EVIDENCE labels {set(labels)} != mapped {set(_EVIDENCE_ALIAS)}"
        )

    def test_upstream_evidence_wire_values_stable_vs_baseline(self):
        """For each of the 10 upstream-decoded EVIDENCE chips, the GENERATED
        chip_database.json wire values (algorithm, vpp_mv, pinout) must equal the
        OLD baseline (tools/baseline/chip_database.baseline.json).

        Pre-regen: passes trivially (DB == baseline). Post-Plan-02-regen: any
        moved value fails loudly and surfaces the chip for the D-09 re-bench flag.
        """
        labels = _upstream_decoded_evidence_chips()
        db = _load(_DB_FILE)
        base = _load(_BASELINE_FILE)

        drift = []
        missing = []
        for label in labels:
            alias_set = _EVIDENCE_ALIAS[label]
            db_wire = _find(db, alias_set)
            base_wire = _find(base, alias_set)
            if db_wire is None:
                missing.append(
                    f"{label} (aliases {sorted(alias_set)}) absent from current DB"
                )
                continue
            if base_wire is None:
                missing.append(
                    f"{label} (aliases {sorted(alias_set)}) absent from baseline"
                )
                continue
            for field in _WIRE_FIELDS:
                if db_wire[field] != base_wire[field]:
                    drift.append(
                        f"{label}: {field} moved {base_wire[field]!r} -> {db_wire[field]!r} "
                        f"(D-09: flag for Leonardo + RURP Rev 2.0 re-bench before Phase 90)"
                    )

        assert not missing, (
            "EVIDENCE chip(s) could not be resolved (alias map drift?):\n  "
            + "\n  ".join(missing)
        )
        assert not drift, (
            f"{len(drift)} upstream-decoded EVIDENCE wire value(s) moved vs OLD baseline "
            f"(D-09 no-silent-move gate):\n  " + "\n  ".join(drift)
        )
