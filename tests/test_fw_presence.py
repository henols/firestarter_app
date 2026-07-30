"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Subprocess proofs for tests/fw_presence.py (BASE-02, D-12; Phase 123 Plan
07). Proves that a present firmware repo with a MISSING scan target is a
hard failure (`MissingScanTargetError`), never a skip -- the fix for A-7's
measured defect where a firmware rename flips gate legs PASS -> SKIP at
exit 0 with a false "firmware absent" reason.

Every case that needs a `FW_ROOT` other than this workspace's real sibling
runs `fw_presence`'s constants in a **subprocess**, with `FIRESTARTER_FW_ROOT`
set in the child process's environment. This is not a style preference:
`FW_ROOT`, `FW_REPO_MARKER`, `FW_REPO_PRESENT`, `FW_ABSENT_REASON` and
`requires_fw` all bind once, at import / collection time. An in-process
pytest environment-variable fixture patch runs after that binding has
already happened, so it cannot change any of them (RESEARCH Correction
C-15) -- a test that tried that shape would be green and prove nothing.

Coverage:
  1. Present repo, present target resolves (subprocess, real sibling path).
  2. Present repo, MISSING target is a hard failure: exits non-zero, names
     `MissingScanTargetError` and the resolved path, and contains no skip
     token (subprocess, materialised fake sibling).
  3. Absent repo is an honest skip: `FW_REPO_PRESENT` false, `requires_fw`
     would skip, `fw_path` on a missing path returns without raising
     (subprocess, empty tmp_path root with no `.git`).
  4. `FW_ABSENT_REASON` is exactly one non-empty string naming the marker
     path -- the single canonical reason the Phase 123-09 allow-list keys on.
  5. The marker is really named `.git`, and no environment variable controls
     the marker name -- only the root path is overridable.
  6. The committed fixture tree (`tests/fixtures/fake_firestarter/`, not the
     tmp_path copy) is genuinely incomplete: `include/firestarter.h` exists,
     `src/proms/eeprom_28c.cpp` does not -- so a future well-meaning
     "completion" of the fixture fails loudly here instead of silently
     disarming test 2.
  7. The committed fixture tree carries no path component named `.git`
     anywhere -- the measured git constraint pinned into an assertion.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_check_no_log_in_sdp_window.py:61.
_FA_DIR = Path(__file__).parent.parent
_FIXTURE_DIR = _FA_DIR / "tests" / "fixtures" / "fake_firestarter"
_MISSING_TARGET = "src/proms/eeprom_28c.cpp"


def _materialise_fake_sibling(tmp_path: Path) -> Path:
    """Copy the committed, deliberately-incomplete fake sibling into
    `tmp_path` and write a one-line `.git` gitfile into the COPY only.

    Git refuses to store any path component named `.git` at exit 0 while
    staging nothing (measured, see tests/fixtures/fake_firestarter/README.md),
    so the marker cannot live in the committed tree. `fw_presence.py` only
    ever calls `.exists()` on the marker -- it never shells out to `git` --
    so a one-line file pointing at a nonexistent gitdir is entirely
    sufficient to make the copy read as "repo present"; no real git
    repository is needed.
    """
    fake = tmp_path / "firestarter"
    shutil.copytree(_FIXTURE_DIR, fake)
    (fake / ".git").write_text("gitdir: /nonexistent\n")
    return fake


def _run_fw_presence_probe(
    fw_root: Path, *check_paths: str
) -> subprocess.CompletedProcess[str]:
    """Run a child process that imports tests.fw_presence with
    FIRESTARTER_FW_ROOT set to `fw_root`, then attempts fw_path() on each of
    `check_paths`. Prints FW_REPO_PRESENT and, for each path, either
    "RESOLVED: <path>" or "MISSING: <exc>" -- never raises in the child
    itself except via fw_path's own MissingScanTargetError, whose repr is
    printed and whose exit propagates as the checker's own exit status.
    """
    script = (
        "import sys\n"
        "from tests.fw_presence import FW_REPO_PRESENT, FW_ABSENT_REASON, fw_path, MissingScanTargetError\n"
        "print('FW_REPO_PRESENT=' + str(FW_REPO_PRESENT))\n"
        "print('FW_ABSENT_REASON=' + FW_ABSENT_REASON)\n"
        "for raw in sys.argv[1:]:\n"
        "    parts = raw.split('/')\n"
        "    try:\n"
        "        p = fw_path(*parts)\n"
        "        print('RESOLVED:' + str(p))\n"
        "    except MissingScanTargetError as e:\n"
        "        print('MissingScanTargetError:' + str(e))\n"
        "        sys.exit(1)\n"
    )
    env = {**os.environ, "FIRESTARTER_FW_ROOT": str(fw_root)}
    return subprocess.run(
        [sys.executable, "-c", script, *check_paths],
        cwd=str(_FA_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Test 1: present repo, present target resolves
# ---------------------------------------------------------------------------


def test_present_repo_present_target_resolves(tmp_path: Path) -> None:
    """With the seam pointed at the materialised fake sibling, a child
    process resolving the PRESENT stub `include/firestarter.h` succeeds and
    reports FW_REPO_PRESENT true."""
    fake = _materialise_fake_sibling(tmp_path)
    result = _run_fw_presence_probe(fake, "include/firestarter.h")
    assert result.returncode == 0, (
        f"expected success resolving a present target.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FW_REPO_PRESENT=True" in result.stdout
    assert "RESOLVED:" in result.stdout


# ---------------------------------------------------------------------------
# Test 2: present repo, MISSING target is a hard failure, never a skip
# ---------------------------------------------------------------------------


def test_present_repo_missing_target_is_hard_failure(tmp_path: Path) -> None:
    """With the seam pointed at the materialised fake sibling, resolving the
    deliberately-absent `src/proms/eeprom_28c.cpp` must exit non-zero,
    naming MissingScanTargetError and the resolved path -- and the output
    must contain no skip token, so a future refactor cannot silently
    convert this hard failure into a skip and still pass."""
    fake = _materialise_fake_sibling(tmp_path)
    result = _run_fw_presence_probe(fake, _MISSING_TARGET)
    assert result.returncode != 0, (
        "a present repo with a missing scan target must FAIL, not succeed.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "MissingScanTargetError" in combined
    expected_resolved = str(fake / _MISSING_TARGET)
    assert expected_resolved in combined, combined
    assert "SKIP" not in combined.upper()


# ---------------------------------------------------------------------------
# Test 3: absent repo is an honest skip, never a raise
# ---------------------------------------------------------------------------


def test_absent_repo_is_honest_skip(tmp_path: Path) -> None:
    """Point the seam at an empty tmp_path subdirectory with no `.git`.
    FW_REPO_PRESENT must be false, and fw_path on a missing path must
    return WITHOUT raising -- the caller is expected to be behind
    requires_fw already, and raising here too would turn an honest skip
    into a collection error."""
    empty_root = tmp_path / "no_firmware_here"
    empty_root.mkdir()
    result = _run_fw_presence_probe(empty_root, _MISSING_TARGET)
    assert result.returncode == 0, (
        f"an absent repo must not raise.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "FW_REPO_PRESENT=False" in result.stdout
    assert "RESOLVED:" in result.stdout
    assert "MissingScanTargetError" not in result.stdout


# ---------------------------------------------------------------------------
# Test 4: exactly one canonical reason string
# ---------------------------------------------------------------------------


def test_fw_absent_reason_is_one_canonical_string() -> None:
    """FW_ABSENT_REASON is a single non-empty string naming the marker path.
    The Phase 123-09 allow-list keys on this exact value."""
    from tests.fw_presence import FW_ABSENT_REASON, FW_REPO_MARKER

    assert isinstance(FW_ABSENT_REASON, str)
    assert FW_ABSENT_REASON != ""
    assert str(FW_REPO_MARKER) in FW_ABSENT_REASON


# ---------------------------------------------------------------------------
# Test 5: the marker really is `.git`, and only the root is overridable
# ---------------------------------------------------------------------------


def test_marker_name_is_git_and_not_env_overridable() -> None:
    """FW_REPO_MARKER.name is `.git`, and the module exposes no environment
    variable controlling the marker name -- only FIRESTARTER_FW_ROOT (the
    root path) is a seam."""
    from tests.fw_presence import FW_REPO_MARKER

    assert FW_REPO_MARKER.name == ".git"
    src = (_FA_DIR / "tests" / "fw_presence.py").read_text()
    assert src.count("os.environ.get") == 1
    assert "FIRESTARTER_FW_MARKER" not in src


# ---------------------------------------------------------------------------
# Test 6: the committed fixture is genuinely incomplete
# ---------------------------------------------------------------------------


def test_committed_fixture_is_genuinely_incomplete() -> None:
    """Assert directly against the COMMITTED tree (not a tmp_path copy) that
    the present stub exists and the deliberately-omitted target does not --
    so a future well-meaning "completion" of the fixture fails loudly here
    instead of silently disarming test 2."""
    assert (_FIXTURE_DIR / "include" / "firestarter.h").exists()
    assert (_FIXTURE_DIR / "doc" / "PROTOCOLS.md").exists()
    assert not (_FIXTURE_DIR / _MISSING_TARGET).exists()


# ---------------------------------------------------------------------------
# Test 7: the fixture carries no `.git` path component
# ---------------------------------------------------------------------------


def test_committed_fixture_has_no_git_path_component() -> None:
    """Walk the committed fixture tree and assert no entry is named `.git`
    -- pins the measured git constraint (git refuses `.git` path components
    at exit 0) into an executable assertion rather than prose."""
    offenders = [p for p in _FIXTURE_DIR.rglob("*") if p.name == ".git"]
    assert offenders == [], f"unexpected .git path component(s): {offenders}"
