"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Unit tests for firestarter.submit (v1.21 Phase 113).

No PATH, network, or browser is ever touched -- every seam (`which_fn`,
`run_fn`) is injected with a `Mock`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from firestarter import submit

# ---------------------------------------------------------------------------
# Task 1: sanitize_dict -- one test per leak vector (A3 fails-open discipline)
# ---------------------------------------------------------------------------


def test_sanitize_home_dir_path():
    d = {"reason": "failed reading /home/alice/scratch/file.bin"}
    out = submit.sanitize_dict(d, user="somebody")
    assert "/home/<user>/scratch/file.bin" in out["reason"]
    assert "alice" not in out["reason"]


def test_sanitize_users_path():
    d = {"reason": "failed at /Users/alice/Desktop/dump.bin"}
    out = submit.sanitize_dict(d, user="somebody")
    assert "/Users/<user>/Desktop/dump.bin" in out["reason"]
    assert "alice" not in out["reason"]


def test_sanitize_windows_users_path():
    d = {"reason": r"temp file at C:\Users\alice\AppData\Local\Temp\x.bin"}
    out = submit.sanitize_dict(d, user="somebody")
    assert r"C:\Users\<user>\AppData\Local\Temp\x.bin" in out["reason"]
    assert "alice" not in out["reason"]


def test_sanitize_windows_users_path_case_insensitive_drive():
    d = {"reason": r"temp file at c:\Users\alice\AppData"}
    out = submit.sanitize_dict(d, user="somebody")
    assert "alice" not in out["reason"]
    assert "<user>" in out["reason"]


def test_sanitize_dev_tty_acm():
    d = {"reason": "no response from /dev/ttyACM0"}
    out = submit.sanitize_dict(d, user="somebody")
    assert out["reason"] == "no response from /dev/tty<redacted>"


def test_sanitize_dev_tty_usb():
    d = {"reason": "port /dev/ttyUSB1 timed out"}
    out = submit.sanitize_dict(d, user="somebody")
    assert "/dev/tty<redacted>" in out["reason"]
    assert "ttyUSB1" not in out["reason"]


def test_sanitize_dev_tty_macos():
    d = {"reason": "port /dev/tty.usbserial-A1 timed out"}
    out = submit.sanitize_dict(d, user="somebody")
    assert "/dev/tty<redacted>" in out["reason"]
    assert "usbserial-A1" not in out["reason"]


def test_sanitize_com_port():
    d = {"reason": "no response from COM3"}
    out = submit.sanitize_dict(d, user="somebody")
    assert out["reason"] == "no response from COM<redacted>"


def test_sanitize_tmp_path():
    d = {"reason": "wrote scratch file to /tmp/firestarter-xyz123/dump.bin"}
    out = submit.sanitize_dict(d, user="somebody")
    assert out["reason"] == "wrote scratch file to /tmp/<redacted>"


def test_sanitize_username():
    d = {"reason": "run by alicetest on this machine"}
    out = submit.sanitize_dict(d, user="alicetest")
    assert out["reason"] == "run by <user> on this machine"


def test_sanitize_username_too_short_not_scrubbed():
    # len(user) < 3 -- guard against over-scrubbing a short/common token.
    d = {"reason": "the value is ab and stays ab"}
    out = submit.sanitize_dict(d, user="ab")
    assert out["reason"] == "the value is ab and stays ab"


def test_sanitize_bytes_leaf_base64_encoded():
    d = {"raw": b"\x00\x01\x02binary"}
    out = submit.sanitize_dict(d, user="somebody")
    assert isinstance(out["raw"], str)
    assert out["raw"] == "AAECYmluYXJ5"


def test_sanitize_clean_value_passes_through():
    d = {"chip": "W27C512", "count": 3, "flag": True, "nothing": None}
    out = submit.sanitize_dict(d, user="somebody")
    assert out == d


def test_sanitize_nested_structure():
    d = {
        "steps": [
            {"op": "read", "reason": "ok at /home/alice/x"},
            {"op": "write", "reason": "port /dev/ttyACM0 gone"},
        ],
        "nested": {"deep": {"path": "/tmp/scratch.bin"}},
    }
    out = submit.sanitize_dict(d, user="alice")
    assert out["steps"][0]["reason"] == "ok at /home/<user>/x"
    assert out["steps"][1]["reason"] == "port /dev/tty<redacted> gone"
    assert out["nested"]["deep"]["path"] == "/tmp/<redacted>"


def test_sanitize_does_not_mutate_input():
    original = {"reason": "path /home/alice/x", "steps": [{"reason": "COM3 gone"}]}
    snapshot = {"reason": "path /home/alice/x", "steps": [{"reason": "COM3 gone"}]}
    submit.sanitize_dict(original, user="alice")
    assert original == snapshot


def test_sanitize_uses_getpass_default_when_user_omitted(monkeypatch):
    monkeypatch.setattr(submit.getpass, "getuser", lambda: "ciuser")
    d = {"reason": "run by ciuser here"}
    out = submit.sanitize_dict(d)
    assert out["reason"] == "run by <user> here"


# ---------------------------------------------------------------------------
# Task 2: overall_verdict / build_title / build_body / build_issue_url
# ---------------------------------------------------------------------------


def _step(op: str, verdict: str, fingerprint_cls: str | None = None):
    fp = SimpleNamespace(classification=fingerprint_cls) if fingerprint_cls else None
    return SimpleNamespace(op=op, verdict=verdict, fingerprint=fp)


def test_overall_verdict_all_ok_is_pass():
    results = [_step("id", "OK"), _step("read", "OK")]
    assert submit.overall_verdict(results) == "PASS"


def test_overall_verdict_marginal_is_inconclusive():
    results = [_step("id", "OK"), _step("write", "marginal")]
    assert submit.overall_verdict(results) == "INCONCLUSIVE"


def test_overall_verdict_bad_dominates_marginal():
    # FAIL-dominant ordering (D-02) -- distinct from the exit-code max()
    # ordering where marginal(2) > BAD(1).
    results = [_step("write", "marginal"), _step("verify", "BAD")]
    assert submit.overall_verdict(results) == "FAIL"


def test_overall_verdict_bad_alone_is_fail():
    results = [_step("id", "BAD")]
    assert submit.overall_verdict(results) == "FAIL"


def test_title_contains_shorthash_and_chip():
    report = Mock()
    report.to_dict.return_value = {"dedup_fingerprint": "abc123def456"}
    report.results = [_step("id", "OK")]
    title = submit.build_title(report, "W27C512")
    assert "abc123def456" in title
    assert "W27C512" in title
    assert "[dev test]" in title
    assert "PASS" in title


def test_title_reflects_fail_verdict():
    report = Mock()
    report.to_dict.return_value = {"dedup_fingerprint": "deadbeef0000"}
    report.results = [_step("verify", "BAD")]
    title = submit.build_title(report, "AM27C020")
    assert "FAIL" in title
    assert "deadbeef0000" in title


def test_build_body_table_from_sanitized_steps():
    sanitized = {
        "steps": [
            {"op": "id", "verdict": "OK", "reason": ""},
            {
                "op": "write",
                "verdict": "BAD",
                "reason": "port /dev/tty<redacted> gone",
            },
        ]
    }
    body = submit.build_body(sanitized, [], include_json=False)
    assert "| id | OK | - |" in body
    assert "| write | BAD | port /dev/tty<redacted> gone |" in body
    assert "```json" not in body


def test_build_body_includes_json_by_default():
    sanitized = {"steps": [{"op": "id", "verdict": "OK", "reason": ""}], "chip": "X"}
    body = submit.build_body(sanitized, [])
    assert "```json" in body
    assert '"chip": "X"' in body


def test_build_issue_url_targets_hardcoded_repo():
    url = submit.build_issue_url("My Title", "My Body")
    assert url.startswith("https://github.com/henols/firestarter_app/issues/new?")


def test_build_issue_url_percent_encodes():
    url = submit.build_issue_url("a b", "c&d")
    assert "a%20b" in url
    assert "c%26d" in url


def test_build_issue_url_has_no_labels_param():
    url = submit.build_issue_url("t", "b")
    assert "labels=" not in url


def test_build_issue_url_not_derived_from_git_remote():
    # D-01/T-113-05: SUBMIT_REPO is a hardcoded constant, never inferred.
    url = submit.build_issue_url("t", "b")
    assert submit.SUBMIT_REPO in url
    assert submit.SUBMIT_REPO == "henols/firestarter_app"


# ---------------------------------------------------------------------------
# Task 3: gh_available + submit_via_gh (list argv, stdin body)
# ---------------------------------------------------------------------------


def test_gh_tier_available_when_present_and_authed():
    which_fn = Mock(return_value="/usr/bin/gh")
    run_fn = Mock(return_value=Mock(returncode=0))
    assert submit.gh_available(which_fn=which_fn, run_fn=run_fn) is True
    run_fn.assert_called_once_with(
        ["gh", "auth", "status"], capture_output=True, text=True, check=False
    )


def test_gh_tier_absent_short_circuits_no_run_fn_call():
    which_fn = Mock(return_value=None)
    run_fn = Mock()
    assert submit.gh_available(which_fn=which_fn, run_fn=run_fn) is False
    run_fn.assert_not_called()


def test_gh_tier_present_but_not_authed():
    which_fn = Mock(return_value="/usr/bin/gh")
    run_fn = Mock(return_value=Mock(returncode=1))
    assert submit.gh_available(which_fn=which_fn, run_fn=run_fn) is False


def test_submit_via_gh_exact_argv_and_stdin_body():
    run_fn = Mock(
        return_value=Mock(
            returncode=0,
            stdout="https://github.com/henols/firestarter_app/issues/1\n",
        )
    )
    result = submit.submit_via_gh("My Title", "My Body", run_fn=run_fn)
    run_fn.assert_called_once_with(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            "henols/firestarter_app",
            "--label",
            "gsd-inbox",
            "--title",
            "My Title",
            "--body-file",
            "-",
        ],
        input="My Body",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result == "https://github.com/henols/firestarter_app/issues/1"


def test_submit_via_gh_returns_none_on_failure():
    run_fn = Mock(return_value=Mock(returncode=1, stdout=""))
    result = submit.submit_via_gh("t", "b", run_fn=run_fn)
    assert result is None
