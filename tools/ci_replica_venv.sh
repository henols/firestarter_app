#!/usr/bin/env bash
# tools/ci_replica_venv.sh -- numpy-free CI-replica venv, for a trustworthy local
# mypy count (RETIRE-06, phase 132, D-06/D-07).
#
# WHAT THIS IS FOR. In this devcontainer, the ambient `numpy` install ships a
# `.pyi` stub using PEP-695 `type` statement syntax that mypy's configured
# target (`python_version = "3.10"`) cannot parse. mypy aborts with exit 2
# before it can check a single project file, and Phase 131's hardened
# `tools/check_mypy_watermark.py` correctly refuses to report an error count
# for that truncated run (it does not silently report a wrong number) --
# `tools/ci_parity.sh`'s own leg-4 header already documents this exit 2 as
# expected, correct behaviour, not a defect. So the watermark gate is honest
# here; it is simply unable to produce a COUNT in this ambient environment.
# A numpy-free Python 3.11 interpreter is the only way to obtain that count
# locally, and this script builds one, once, and reuses it.
#
# WHY THIS IS NOT A LEG OF ci_parity.sh (D-07). tools/ci_parity.sh's own
# contract is "faithful CI mirror -- exactly CI's path set, neither wider nor
# narrower" (Phase 131 D-08); a local venv substitute for an ambient-package
# problem is not itself a CI step, and folding this into that script would
# rewrite a recipe that has shipped unmodified for 9 commits, whose own
# leg-4 header already explains the local exit 2 correctly. This script is
# ci_parity.sh's sibling, never its replacement, and never a leg inside it.
# Phases 133-136 hit the identical ambient-numpy wall, which is why this is a
# committed script rather than prose a later phase would silently re-derive
# or skip.
#
# WHAT THIS DELIBERATELY DOES NOT MIRROR (same boundary discipline as
# ci_parity.sh -- naming it here keeps this an honest mirror, not an implied
# full-CI claim):
#   - The two codegen-drift gates (messages.py, frame_vectors.py) and their
#     paired "catalog validity check" / "vector catalog validity check"
#     steps, and the `firestarter --help` entry-point smoke step. None of
#     them need a venv, and no change in Phase 132 touches any catalog or
#     generated file, so a divergence here cannot hide a Phase 132 defect.
#   - The separate, isolated `ci-py32` job (real-pyusb API surface tests) --
#     a distinct ci.yml job this script does not attempt to replicate.
#
# Shell shape: `set -u` (never `set -e` -- a failing leg must not abort the
# run) and no leg's exit code is ever swallowed; all five legs always run,
# and the final summary prints each one, mirroring ci_parity.sh's own
# discipline.
#
# Exit codes: 0 if every leg passed; non-zero (naming the failing legs) in
# the final `CI-REPLICA: FAIL (legs:...)` line if any leg failed. A leg-4
# (mypy watermark gate) failure alone, at the current pre-discharge error
# count, is the expected pre-Phase-132-fixes shape -- see 132-MYPY-LEDGER.md.

set -u

# Resolve the repo root from this script's own location (BASH_SOURCE), so
# behaviour is identical regardless of the caller's working directory --
# the same anchoring discipline tools/ci_parity.sh and
# tools/check_mypy_watermark.py both use.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}" || { echo "FATAL: cannot cd to repo root ${REPO_ROOT}"; exit 2; }

REFRESH=0
for arg in "$@"; do
  case "${arg}" in
    --refresh) REFRESH=1 ;;
  esac
done

# Venv location: default under .venv/ (already .gitignore'd -- line 4),
# overridable via CI_REPLICA_VENV. `is set` test, never truthiness: an
# explicitly-empty override is not the same as "unset", but for a filesystem
# path seam the only sane behaviours are "set to something" or "use the
# default" -- so this reads with `-n` after `${CI_REPLICA_VENV:-}`, per the
# plan's explicit instruction, rather than a bare truthiness test on an
# unset-tolerant expansion.
if [ -n "${CI_REPLICA_VENV:-}" ]; then
  VENV_DIR="${CI_REPLICA_VENV}"
else
  VENV_DIR="${REPO_ROOT}/.venv/ci-replica"
fi

# Containment guard (T-132-01): never create or `rm -rf` anything outside
# REPO_ROOT, regardless of what CI_REPLICA_VENV is set to.
case "${VENV_DIR}" in
  "${REPO_ROOT}"/*) : ;;
  *)
    echo "FATAL: VENV_DIR (${VENV_DIR}) does not resolve under REPO_ROOT (${REPO_ROOT}) -- refusing to create or remove anything there."
    exit 2
    ;;
esac

# Scratch root for pip's cache. Exported before any pip call: the recorded
# devcontainer failure mode is a pip cache write to an unwritable
# $HOME/.cache aborting the install with "Permission denied" (reproduced
# live at plan time: $HOME/.cache is root-owned, mode 755, not writable by
# this user). Cleanup is scoped to this scratch root only, never to
# VENV_DIR (T-132-01) -- the venv is removed only on an explicit --refresh.
TMPROOT="$(mktemp -d)"
cleanup() { rm -rf "${TMPROOT}"; }
trap cleanup EXIT
export PIP_CACHE_DIR="${TMPROOT}/pip-cache"
mkdir -p "${PIP_CACHE_DIR}"

banner() {
  echo "---------------------------------"
  echo "Leg ${1}: ${2}"
  echo "Proves: ${3}"
  echo "---------------------------------"
}

# --- Interpreter selection: python3.11 on PATH, then the known devcontainer
# location, then a bare python3. Never a silent substitution -- always
# stamped, and flagged when the resolved minor version is not 3.11. ---
resolve_base_python() {
  if command -v python3.11 >/dev/null 2>&1; then
    command -v python3.11
    return 0
  fi
  if [ -x "/home/vscode/.local/bin/python3.11" ]; then
    echo "/home/vscode/.local/bin/python3.11"
    return 0
  fi
  command -v python3
}
BASE_PYTHON="$(resolve_base_python)"
if [ -z "${BASE_PYTHON}" ]; then
  echo "FATAL: no python3.11 or python3 interpreter found on PATH."
  exit 2
fi
BASE_PYTHON_VERSION_STR="$("${BASE_PYTHON}" -V 2>&1)"
echo "INTERPRETER: ${BASE_PYTHON} ${BASE_PYTHON_VERSION_STR}"
case "${BASE_PYTHON_VERSION_STR}" in
  *\ 3.11.*) : ;;
  *)
    # mypy's error population is driven by `python_version = "3.10"` in
    # pyproject.toml -- a TARGET, not the running interpreter -- so this
    # divergence matters little for the error count itself. It is still
    # stamped rather than silently reassured away, because dependency
    # resolution (which wheels get installed) can differ across
    # interpreter minor versions even when the mypy target does not.
    echo "INTERPRETER-DIVERGENCE: using ${BASE_PYTHON_VERSION_STR#Python }, CI uses 3.11"
    ;;
esac

echo
echo "CI-replica venv recipe (RETIRE-06) -- repo root: ${REPO_ROOT}"
echo "VENV_DIR: ${VENV_DIR}"
echo

VENV_PY="${VENV_DIR}/bin/python"

# ---------------------------------------------------------------------------
# Leg 1: create-or-reuse the venv, then install the exact CI test-extra
# dependency closure. A second consecutive run without --refresh reuses the
# existing venv untouched.
# ---------------------------------------------------------------------------
banner 1 "create-or-reuse ${VENV_DIR}, then \"\${VENV_DIR}/bin/python\" -m pip install -e '.[test]'" \
  "the venv exists with CI's exact test-extra dependency closure installed, without a reinstall on every iteration."

GIT_STATUS_BEFORE_LEG1="$(git status --porcelain 2>/dev/null || true)"

if [ "${REFRESH}" -eq 1 ] && [ -d "${VENV_DIR}" ]; then
  echo "REFRESH requested: removing existing venv at ${VENV_DIR} (confirmed under REPO_ROOT above)."
  rm -rf "${VENV_DIR}"
fi

if [ -d "${VENV_DIR}" ] && [ -x "${VENV_PY}" ]; then
  echo "REUSED: ${VENV_DIR} already exists; skipping create + install (pass --refresh to force a reinstall)."
  LEG1_EXIT=0
else
  "${BASE_PYTHON}" -m venv "${VENV_DIR}"
  LEG1_CREATE_EXIT=$?
  if [ "${LEG1_CREATE_EXIT}" -ne 0 ]; then
    echo "FATAL: venv creation failed (exit ${LEG1_CREATE_EXIT})."
    LEG1_EXIT="${LEG1_CREATE_EXIT}"
  else
    "${VENV_PY}" -m pip install -e '.[test]'
    LEG1_EXIT=$?
  fi
fi

# Assert the run left no untracked/modified path outside the already-
# gitignored .venv/ -- compares against the snapshot taken before this leg,
# since this repo's tree may carry pre-existing, unrelated dirt that is not
# this script's to judge or to clean up.
GIT_STATUS_AFTER_LEG1="$(git status --porcelain 2>/dev/null || true)"
GIT_STATUS_NEW_LEG1="$(comm -13 <(printf '%s\n' "${GIT_STATUS_BEFORE_LEG1}" | sort) <(printf '%s\n' "${GIT_STATUS_AFTER_LEG1}" | sort))"
if [ -n "${GIT_STATUS_NEW_LEG1}" ]; then
  echo "FAIL: venv creation/install left new untracked or modified path(s) that were not present before this leg ran:"
  echo "${GIT_STATUS_NEW_LEG1}"
  LEG1_EXIT=1
fi

echo "Leg 1 exit code: ${LEG1_EXIT}"
echo

# ---------------------------------------------------------------------------
# Leg 2: prove numpy is absent from the venv. A present numpy means any mypy
# count taken here would be the exact truncated-run shape this script exists
# to avoid.
# ---------------------------------------------------------------------------
banner 2 "\"\${VENV_DIR}/bin/python\" -c 'importlib.util.find_spec(\"numpy\") is None'" \
  "numpy is absent from the venv, so mypy cannot hit the PEP-695 stub that truncates it in the ambient devcontainer environment."

if [ -x "${VENV_PY}" ]; then
  "${VENV_PY}" -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("numpy") is None else 1)'
  LEG2_EXIT=$?
else
  echo "FAIL: venv python (${VENV_PY}) is not executable -- leg 1 did not produce a usable venv."
  LEG2_EXIT=1
fi

if [ "${LEG2_EXIT}" -eq 0 ]; then
  NUMPY_PRESENT="no"
else
  NUMPY_PRESENT="yes"
  if [ -x "${VENV_PY}" ]; then
    echo "FAIL: numpy IS importable inside this venv. A mypy count taken here would be the truncated-run shape this script exists to avoid -- do not trust it."
  fi
fi
echo "Leg 2 exit code: ${LEG2_EXIT}"
echo

# ---------------------------------------------------------------------------
# Leg 3: ruff, at CI's exact path set -- neither wider nor narrower.
# ---------------------------------------------------------------------------
banner 3 "\"\${VENV_DIR}/bin/ruff\" check firestarter/ tests/ ; \"\${VENV_DIR}/bin/ruff\" format --check firestarter/ tests/" \
  "ruff lint and format are clean at ci.yml's exact scope, from inside the replica venv's own installed ruff."

if [ -x "${VENV_DIR}/bin/ruff" ]; then
  "${VENV_DIR}/bin/ruff" check firestarter/ tests/
  LEG3_CHECK_EXIT=$?
  "${VENV_DIR}/bin/ruff" format --check firestarter/ tests/
  LEG3_FORMAT_EXIT=$?
  if [ "${LEG3_CHECK_EXIT}" -eq 0 ] && [ "${LEG3_FORMAT_EXIT}" -eq 0 ]; then
    LEG3_EXIT=0
  else
    LEG3_EXIT=1
  fi
  echo "Leg 3 exit code: ${LEG3_EXIT} (ruff check: ${LEG3_CHECK_EXIT}, ruff format --check: ${LEG3_FORMAT_EXIT})"
else
  echo "FAIL: ${VENV_DIR}/bin/ruff is not executable -- leg 1 did not install it."
  LEG3_EXIT=1
  echo "Leg 3 exit code: ${LEG3_EXIT}"
fi
echo

# ---------------------------------------------------------------------------
# Leg 4: the hardened mypy watermark gate itself -- one single mypy
# invocation, reused for both the gate's own classification/enforcement AND
# the raw mypy completion-summary line a later plan must quote. This calls
# tools/check_mypy_watermark.py's own pure functions (run_mypy,
# classify_mypy_result, get_watermark, enforce_watermark) rather than
# shelling out to the script a second time or hand-rolling mypy's argv --
# so the count reported here is exactly what CI's gate would report, from
# exactly one mypy run. Never switches mypy to its JSON reporting mode
# (STACK §1): that mode emits no summary line, which would destroy the
# "(checked K source files)" clause this leg's second print depends on.
# ---------------------------------------------------------------------------
banner 4 "\"\${VENV_DIR}/bin/python\" -c '<tools/check_mypy_watermark.py's own run_mypy + classify_mypy_result + enforce_watermark, one mypy invocation>'" \
  "the watermark gate's classification AND mypy's own completion-summary line, from a single mypy invocation, using the gate's own pure functions -- never mypy's JSON reporting mode, never a hand-rolled argv."

if [ -x "${VENV_PY}" ]; then
  LEG4_OUTPUT="$("${VENV_PY}" -c "
import re
import sys

sys.path.insert(0, 'tools')
from check_mypy_watermark import (
    classify_mypy_result,
    enforce_watermark,
    get_watermark,
    run_mypy,
)

result = run_mypy()
output = result.stdout + result.stderr

_found_re = re.compile(
    r'^Found (\d+) errors? in \d+ files? \(checked \d+ source files?\)\$',
    re.MULTILINE,
)
_clean_re = re.compile(
    r'^Success: no issues found in \d+ source files?\$',
    re.MULTILINE,
)
m = _found_re.search(output) or _clean_re.search(output)
if m:
    print(m.group(0))
else:
    print(
        'NO-COMPLETION-CLAUSE: mypy produced no parseable completion '
        'line (neither Found-errors nor Success-clean) -- the '
        'truncated-run shape.'
    )

count = classify_mypy_result(result.returncode, output)
watermark = get_watermark()
enforce_watermark(count, watermark)
" 2>&1)"
  LEG4_EXIT=$?
  echo "${LEG4_OUTPUT}"
else
  echo "FAIL: venv python (${VENV_PY}) is not executable -- leg 1 did not produce a usable venv."
  LEG4_EXIT=1
fi
echo "Leg 4 exit code: ${LEG4_EXIT}"
echo

# ---------------------------------------------------------------------------
# Leg 5: pytest with CI's exact coverage invocation -- the gap
# tools/ci_parity.sh's own legs 1/2 deliberately do not cover (they run bare
# `pytest tests/ -q`, no --cov flags at all). Phase 132 deletes ~126 lines of
# covered production code plus ~550 lines of test; this leg is what would
# catch a resulting drop below the 70% floor.
# ---------------------------------------------------------------------------
banner 5 "\"\${VENV_DIR}/bin/python\" -m pytest tests/ --cov=firestarter --cov-report=term-missing --cov-fail-under=70" \
  "CI's exact pytest + coverage invocation, closing the coverage-floor gap ci_parity.sh's legs 1/2 do not cover."

if [ -x "${VENV_PY}" ]; then
  "${VENV_PY}" -m pytest tests/ --cov=firestarter --cov-report=term-missing --cov-fail-under=70
  LEG5_EXIT=$?
else
  echo "FAIL: venv python (${VENV_PY}) is not executable -- leg 1 did not produce a usable venv."
  LEG5_EXIT=1
fi
echo "Leg 5 exit code: ${LEG5_EXIT}"
echo

# ---------------------------------------------------------------------------
# Summary stamps and aggregate exit.
# ---------------------------------------------------------------------------
if [ -x "${VENV_PY}" ]; then
  MYPY_VERSION_STR="$("${VENV_PY}" -m mypy --version 2>&1)"
else
  MYPY_VERSION_STR="unavailable (no usable venv python)"
fi

echo "================================="
echo "CI-REPLICA SUMMARY"
echo "================================="
echo "INTERPRETER: ${BASE_PYTHON} ${BASE_PYTHON_VERSION_STR}"
echo "MYPY-VERSION: ${MYPY_VERSION_STR}"
echo "NUMPY-PRESENT: ${NUMPY_PRESENT}"
echo "Python: ${BASE_PYTHON_VERSION_STR}"
echo "Leg 1 (venv create-or-reuse + install): exit ${LEG1_EXIT}"
echo "Leg 2 (numpy absent):                   exit ${LEG2_EXIT}"
echo "Leg 3 (ruff check + format --check):    exit ${LEG3_EXIT}"
echo "Leg 4 (mypy watermark gate):             exit ${LEG4_EXIT}"
echo "Leg 5 (pytest --cov, CI's exact args):   exit ${LEG5_EXIT}"

FAILED_LEGS=""
[ "${LEG1_EXIT}" -ne 0 ] && FAILED_LEGS="${FAILED_LEGS} 1"
[ "${LEG2_EXIT}" -ne 0 ] && FAILED_LEGS="${FAILED_LEGS} 2"
[ "${LEG3_EXIT}" -ne 0 ] && FAILED_LEGS="${FAILED_LEGS} 3"
[ "${LEG4_EXIT}" -ne 0 ] && FAILED_LEGS="${FAILED_LEGS} 4"
[ "${LEG5_EXIT}" -ne 0 ] && FAILED_LEGS="${FAILED_LEGS} 5"
FAILED_LEGS="$(echo "${FAILED_LEGS}" | xargs)"

if [ -z "${FAILED_LEGS}" ]; then
  echo "CI-REPLICA: PASS"
  exit 0
else
  echo "CI-REPLICA: FAIL (legs:${FAILED_LEGS})"
  exit 1
fi
