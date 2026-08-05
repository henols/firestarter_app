"""Test fixture for check_diagnostic_report_claims.py -- NOT wired into any
CLI surface, never imported by production code.

Deliberately-violating fixture (CLOSE-03, v1.30 Phase 137 plan 137-02): a
small, standalone, syntactically-valid Python module -- NOT a copy of the
real `diagnostic_report.py` -- containing exactly one string literal that
trips the `dev-test-proves-unqualified` forbidden-phrase label. This file
must never be imported; it exists only as AST-scan input for the paired
pytest (`tests/test_check_diagnostic_report_claims.py`), injected via the
`FIRESTARTER_DIAGREPORT_SRC` env-override seam -- a real subprocess-level
planted violation, not an in-process synthetic.
"""

_CLEAN_LABEL = "field"
_CLEAN_VALUE = "not measured"

# Deliberately combines two forbidden labels in one sentence
# ("dev-test-proves-unqualified" and "lock-held-unqualified") -- the paired
# test only asserts "non-zero exit, FAIL: present, label present", not an
# exact single-label match, so this is fine.
_NOTICE = "dev test proves the lock held"
