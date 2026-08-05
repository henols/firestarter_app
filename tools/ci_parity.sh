#!/usr/bin/env bash
# tools/ci_parity.sh -- CI-parity recipe (GATE-09, Phase 131 plan 06, D-07..D-10).
#
# WHAT THIS IS FOR. This devcontainer cannot see three whole classes of
# defect the milestone can ship, all invisible locally and all real in
# standalone CI:
#   1. It runs Python 3.12 against CI's 3.11 -- a mypy/stdlib-API version gap.
#   2. It has the `../firestarter` sibling firmware checkout that standalone
#      CI lacks -- a defect hidden by an accidentally-present directory.
#   3. A live board on /dev/ttyACM*|/dev/ttyUSB* beats a `comports=[]` patch
#      -- a port-discovery test can go green locally (with a real port
#      opened) and red in CI, or the reverse.
# Three CI-only sibling-checkout test defects fired simultaneously on the
# real b15 push and were invisible in this devcontainer until then. This
# recipe mirrors ci.yml's gate steps locally so a developer can see all
# three classes before pushing, rather than discovering them on a real
# dispatch.
#
# WHAT THIS DELIBERATELY DOES NOT MIRROR (ci.yml has more steps than this
# recipe runs -- naming the boundary here keeps this an honest mirror rather
# than an implied full-CI claim):
#   - The two codegen-drift gates (messages.py, frame_vectors.py) and their
#     paired "catalog validity check" / "vector catalog validity check"
#     steps.
#   - pytest's `--cov=firestarter --cov-report=term-missing
#     --cov-fail-under=70` coverage gate (legs 1/2 below run the same suite
#     WITHOUT the coverage flags -- proving suite-pass, not coverage-floor).
#   - The `firestarter --help` entry-point smoke test.
#   - The separate, isolated `ci-py32` job (real-pyusb API surface tests) --
#     a distinct job in ci.yml, not part of the primary `ci` job this recipe
#     mirrors.
#
# WHY THE HOUSE MODULE-LEVEL ABSENCE-PROXY LINT CHECKER (D-10, tools/, its
# name deliberately not repeated here -- see 131-CI-PARITY.md) IS NOT A LEG.
# This recipe's contract is CI PARITY -- ci.yml runs no such step, and
# running it here would make the mirror unfaithful. Its behaviour is already
# covered by its own paired pytest module, which legs 1 and 2 below both
# run as part of the whole `tests/` suite. Phase 131 plan 06 instead runs
# that lint checker ONCE, separately, as a recorded one-time confirmation
# (see 131-CI-PARITY.md) -- never as a recipe leg.
#
# LEG 4'S EXPECTED LOCAL EXIT 2 (Phase 131 plan 06 correction). In any
# environment where an ambient numpy PEP-695 stub truncates mypy's run --
# this devcontainer included -- leg 4 exits 2. That is the Phase 131
# hardened gate (GATE-01/02/03/04) correctly refusing to report an error
# count for an incomplete run, NOT a defect in this script or in the
# checker. Do not "fix" it by weakening a guard, adding `|| true`, or
# excluding leg 4 to force a green aggregate exit. Phase acceptance does not
# require a zero aggregate exit -- see 131-CI-PARITY.md for the full
# explanation and CI's own current (pre-Phase-132) red state.
#
# Exit codes: 0 if every leg passed; non-zero (naming the failing legs) if
# any leg failed. This script never aborts early on a single failing
# command (deliberately not a strict-abort-on-error shell mode) and never
# swallows a leg's exit code -- all four legs always run, and the final
# summary prints each one.

set -u

# Resolve the repo root from this script's own location, so it behaves
# identically regardless of the caller's working directory -- the same
# anchoring discipline tools/check_mypy_watermark.py uses (REPO_ROOT =
# Path(__file__).resolve().parent.parent).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}" || { echo "FATAL: cannot cd to repo root ${REPO_ROOT}"; exit 2; }

# Leg 1's empty sibling root: created here, removed on exit via trap.
TMPROOT="$(mktemp -d)"
cleanup() {
  rm -rf "${TMPROOT}"
}
trap cleanup EXIT

banner() {
  echo "---------------------------------"
  echo "Leg ${1}: ${2}"
  echo "Proves: ${3}"
  echo "---------------------------------"
}

echo "CI-parity recipe (GATE-09) -- repo root: ${REPO_ROOT}"
echo

# --- Leg 1: pytest with an empty firmware-sibling root (standalone-CI shape) ---
banner 1 "FIRESTARTER_FW_ROOT=<empty dir> python3 -m pytest tests/ -q" \
  "the suite passes with the firmware sibling absent -- the standalone-CI condition."
FIRESTARTER_FW_ROOT="${TMPROOT}" python3 -m pytest tests/ -q
LEG1_EXIT=$?
echo "Leg 1 exit code: ${LEG1_EXIT}"
echo

# --- Leg 2: pytest with the real sibling present ---
banner 2 "python3 -m pytest tests/ -q" \
  "the suite passes with the firmware sibling present (this devcontainer's own layout)."
python3 -m pytest tests/ -q
LEG2_EXIT=$?
echo "Leg 2 exit code: ${LEG2_EXIT}"
echo

# --- Leg 3: ruff, at CI's exact path set -- neither wider nor narrower ---
banner 3 "ruff lint + ruff format --check, at ci.yml's exact path set" \
  "ruff is CI-scoped correctly today; the failure mode this leg guards against is running ruff locally at a different scope."
ruff check firestarter/ tests/
LEG3_CHECK_EXIT=$?
ruff format --check firestarter/ tests/
LEG3_FORMAT_EXIT=$?
if [ "${LEG3_CHECK_EXIT}" -eq 0 ] && [ "${LEG3_FORMAT_EXIT}" -eq 0 ]; then
  LEG3_EXIT=0
else
  LEG3_EXIT=1
fi
echo "Leg 3 exit code: ${LEG3_EXIT} (ruff check: ${LEG3_CHECK_EXIT}, ruff format --check: ${LEG3_FORMAT_EXIT})"
echo

# --- Leg 4: the hardened mypy watermark gate ---
banner 4 "python3 tools/check_mypy_watermark.py" \
  "the hardened watermark gate (GATE-01/02/03/04) reaches a legible terminal state. A local exit 2 here (ambient numpy PEP-695 stub truncating mypy) is the gate working correctly, not a script defect -- see this script's header and 131-CI-PARITY.md."
python3 tools/check_mypy_watermark.py
LEG4_EXIT=$?
echo "Leg 4 exit code: ${LEG4_EXIT}"
echo

# --- Board stamp (D-09): evidence metadata, not a fifth leg. Never opens a port. ---
BOARD_DEVICES=""
for pattern in /dev/ttyACM* /dev/ttyUSB*; do
  if [ -e "${pattern}" ]; then
    BOARD_DEVICES="${BOARD_DEVICES} ${pattern}"
  fi
done
BOARD_DEVICES="$(echo "${BOARD_DEVICES}" | xargs)"
if [ -z "${BOARD_DEVICES}" ]; then
  BOARD_STAMP="none"
else
  BOARD_STAMP="${BOARD_DEVICES}"
fi

# --- Final summary ---
echo "================================="
echo "CI-PARITY SUMMARY"
echo "================================="
echo "Leg 1 (pytest, empty sibling root):  exit ${LEG1_EXIT}"
echo "Leg 2 (pytest, sibling present):     exit ${LEG2_EXIT}"
echo "Leg 3 (ruff check + format --check): exit ${LEG3_EXIT}"
echo "Leg 4 (mypy watermark gate):         exit ${LEG4_EXIT}"
echo "BOARD-ATTACHED: ${BOARD_STAMP}"
echo "Python: $(python3 -V 2>&1)"

FAILED_LEGS=""
[ "${LEG1_EXIT}" -ne 0 ] && FAILED_LEGS="${FAILED_LEGS} 1"
[ "${LEG2_EXIT}" -ne 0 ] && FAILED_LEGS="${FAILED_LEGS} 2"
[ "${LEG3_EXIT}" -ne 0 ] && FAILED_LEGS="${FAILED_LEGS} 3"
[ "${LEG4_EXIT}" -ne 0 ] && FAILED_LEGS="${FAILED_LEGS} 4"
FAILED_LEGS="$(echo "${FAILED_LEGS}" | xargs)"

if [ -z "${FAILED_LEGS}" ]; then
  echo "CI-PARITY: PASS"
  exit 0
else
  echo "CI-PARITY: FAIL (legs:${FAILED_LEGS})"
  exit 1
fi
