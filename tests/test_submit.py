"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Unit tests for firestarter.submit (v1.21 Phase 113).

No PATH, network, or browser is ever touched -- every seam (`which_fn`,
`run_fn`) is injected with a `Mock`.

Phase 121 Plan 11 (DEVTEST-05/06) added: `find_prior_report`/`comment_via_gh`
unit legs (D-09/D-11); `submit_report`'s dedup-first/always-ask/comment-on-
duplicate behavioural legs (D-09/D-10/D-11); and a deny-set widening of the
negative-argv idiom covering both `gh` paths' short forms (DEVTEST-06,
RESEARCH Pitfall 6) -- see the "Task 3: deny-set negative argv" section
below for the deliberate-break proof demonstrating the single-flag
assertion it replaces would have missed the short `-l` form.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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
            {"op": "id", "verdict": "OK", "reason": "", "duration_s": 0.03},
            {
                "op": "write",
                "verdict": "BAD",
                "reason": "port /dev/tty<redacted> gone",
                "duration_s": 41.875,
            },
            # No `duration_s` key at all: a pre-1.5 report replayed through
            # build_body must not KeyError, it must render `-`.
            {"op": "erase", "verdict": "NA", "reason": ""},
        ]
    }
    body = submit.build_body(sanitized, [], include_json=False)
    assert "| Step | Verdict | Took | Reason |" in body
    assert "| id | OK | 0.03s | - |" in body
    assert "| write | BAD | 41.9s | port /dev/tty<redacted> gone |" in body
    assert "| erase | NA | - | - |" in body
    assert "```json" not in body


def test_build_body_includes_json_by_default():
    sanitized = {"steps": [{"op": "id", "verdict": "OK", "reason": ""}], "chip": "X"}
    body = submit.build_body(sanitized, [])
    assert "```json" in body
    assert '"chip": "X"' in body


def test_build_issue_url_targets_hardcoded_repo():
    url = submit.build_issue_url("My Title", "My Body")
    assert url.startswith(f"https://github.com/{submit.SUBMIT_REPO}/issues/new?")


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
    # Literal on purpose: the project-wide tracker per firestarter_prom#6,
    # NOT the repo this code lives in. A silent retarget must fail here.
    assert submit.SUBMIT_REPO == "henols/firestarter_prom"


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
            stdout="https://github.com/henols/firestarter_prom/issues/1\n",
        )
    )
    result = submit.submit_via_gh("My Title", "My Body", run_fn=run_fn)
    run_fn.assert_called_once_with(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            submit.SUBMIT_REPO,
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
    assert result == "https://github.com/henols/firestarter_prom/issues/1"


def test_submit_via_gh_returns_none_on_failure():
    run_fn = Mock(return_value=Mock(returncode=1, stdout="", stderr=""))
    result = submit.submit_via_gh("t", "b", run_fn=run_fn)
    assert result is None


def test_submit_via_gh_argv_carries_nothing_permission_gated():
    # D-1/T-ahy-05: the ONE assertion a mocked run_fn can honestly make
    # about the real-world failure -- no permission-gated argument is ever
    # sent on the create path. A mocked run_fn cannot prove GitHub accepts
    # the create call; it CAN prove the argv never carries the label flag
    # or the GSD_INBOX_LABEL value.
    run_fn = Mock(
        return_value=Mock(
            returncode=0,
            stdout="https://github.com/henols/firestarter_prom/issues/1\n",
        )
    )
    submit.submit_via_gh("My Title", "My Body", run_fn=run_fn)
    argv = run_fn.call_args[0][0]
    assert isinstance(argv, list)
    assert argv[0] == "gh"
    assert "--label" not in argv
    assert submit.GSD_INBOX_LABEL not in argv
    assert "gsd-inbox" not in " ".join(argv)
    assert "shell" not in run_fn.call_args.kwargs


def test_submit_via_gh_argv_targets_the_project_wide_tracker():
    # 120-12: a repo-target-specific negative leg (Idiom B). A mocked run_fn
    # cannot prove GitHub actually accepts issues at henols/firestarter_prom
    # -- that requires a live create against the real API. What it CAN
    # honestly prove is that the create-path argv never carries the wrong
    # repo slug (`henols/firestarter_app`, the repo this code lives in, per
    # firestarter_prom#6) and always carries `--repo henols/firestarter_prom`
    # immediately adjacent, with no `shell=True` escape hatch alongside it.
    run_fn = Mock(
        return_value=Mock(
            returncode=0,
            stdout="https://github.com/henols/firestarter_prom/issues/1\n",
        )
    )
    submit.submit_via_gh("My Title", "My Body", run_fn=run_fn)
    argv = run_fn.call_args[0][0]
    assert isinstance(argv, list)
    repo_idx = argv.index("--repo")
    assert argv[repo_idx + 1] == "henols/firestarter_prom"
    assert "henols/firestarter_app" not in " ".join(argv)
    assert "shell" not in run_fn.call_args.kwargs


def test_submit_via_gh_failure_prints_captured_stderr():
    run_fn = Mock(
        return_value=Mock(
            returncode=1,
            stdout="",
            stderr="GraphQL: Resource not accessible by personal access token",
        )
    )
    console = Mock()
    printed: list[str] = []
    console.print.side_effect = lambda msg: printed.append(msg)
    result = submit.submit_via_gh("t", "b", run_fn=run_fn, console=console)
    assert result is None
    assert any(
        "GraphQL: Resource not accessible by personal access token" in m
        for m in printed
    )


def test_submit_via_gh_failure_with_blank_stderr_still_reports():
    run_fn = Mock(return_value=Mock(returncode=3, stdout="", stderr=""))
    console = Mock()
    printed: list[str] = []
    console.print.side_effect = lambda msg: printed.append(msg)
    result = submit.submit_via_gh("t", "b", run_fn=run_fn, console=console)
    assert result is None
    assert any(m.strip() and "3" in m for m in printed)
    assert not any("Mock" in m for m in printed)


def test_submit_via_gh_success_prints_nothing():
    run_fn = Mock(
        return_value=Mock(
            returncode=0,
            stdout="https://github.com/henols/firestarter_prom/issues/1\n",
        )
    )
    console = Mock()
    submit.submit_via_gh("t", "b", run_fn=run_fn, console=console)
    console.print.assert_not_called()


def test_gsd_inbox_label_constant_retained():
    # D-1: the label constant survives for MAINTAINER-side triage
    # (`gh issue edit <n> --add-label gsd-inbox`), even though it is no
    # longer sent on the community-tester create path.
    assert submit.GSD_INBOX_LABEL == "gsd-inbox"


# ---------------------------------------------------------------------------
# Task 3: deny-set negative argv on BOTH gh paths (DEVTEST-06, D-09/D-11,
# RESEARCH Pitfall 6) -- widens the single-flag idiom above, does not
# replace it. `gh issue create`'s write/triage-gated flags are broader than
# `--label` alone: `-l`/`--label`, `-a`/`--assignee`, `-m`/`--milestone`,
# `-p`/`--project` (the last explicitly requires the `project` OAuth scope
# per `gh issue create --help`). `gh issue comment` has NO label/assignee/
# milestone/project flag at all -- its meaningful negatives are the
# mutating/hijacking flags: `--delete-last`, `--edit-last`, `--yes`,
# `-w`/`--web`, `-e`/`--editor`.
# ---------------------------------------------------------------------------

_CREATE_DENY_SET = [
    "-l",
    "--label",
    "-a",
    "--assignee",
    "-m",
    "--milestone",
    "-p",
    "--project",
]

_COMMENT_DENY_SET = [
    "--delete-last",
    "--edit-last",
    "--yes",
    "-w",
    "--web",
    "-e",
    "--editor",
]


@pytest.mark.parametrize("flag", _CREATE_DENY_SET)
def test_gh_create_argv_carries_no_permission_gated_flag(flag):
    # Deny-set, not equality: an equality assertion against a fixed expected
    # argv silently stops protecting the moment someone updates that list.
    run_fn = Mock(
        return_value=Mock(
            returncode=0,
            stdout="https://github.com/henols/firestarter_prom/issues/1\n",
        )
    )
    submit.submit_via_gh("My Title", "My Body", run_fn=run_fn)
    argv = run_fn.call_args[0][0]
    assert isinstance(argv, list)
    assert flag not in argv
    # The retained value-absence + list-argv + no-shell assertions (the
    # pre-existing idiom this widens).
    assert submit.GSD_INBOX_LABEL not in argv
    assert "gsd-inbox" not in " ".join(argv)
    assert "shell" not in run_fn.call_args.kwargs


@pytest.mark.parametrize("flag", _COMMENT_DENY_SET)
def test_gh_comment_argv_carries_no_mutating_flag(flag):
    run_fn = Mock(
        return_value=Mock(
            returncode=0, stdout="https://github.com/x/y/issues/1#issuecomment-1\n"
        )
    )
    submit.comment_via_gh("https://github.com/x/y/issues/1", "My Body", run_fn=run_fn)
    argv = run_fn.call_args[0][0]
    assert isinstance(argv, list)
    assert flag not in argv
    assert "shell" not in run_fn.call_args.kwargs


def test_gh_comment_argv_targets_the_project_wide_tracker():
    run_fn = Mock(
        return_value=Mock(
            returncode=0, stdout="https://github.com/x/y/issues/1#issuecomment-1\n"
        )
    )
    submit.comment_via_gh("https://github.com/x/y/issues/1", "My Body", run_fn=run_fn)
    argv = run_fn.call_args[0][0]
    assert isinstance(argv, list)
    repo_idx = argv.index("--repo")
    assert argv[repo_idx + 1] == submit.SUBMIT_REPO


def test_gh_comment_body_arrives_on_stdin():
    run_fn = Mock(
        return_value=Mock(
            returncode=0, stdout="https://github.com/x/y/issues/1#issuecomment-1\n"
        )
    )
    submit.comment_via_gh("https://github.com/x/y/issues/1", "My Body", run_fn=run_fn)
    argv = run_fn.call_args[0][0]
    assert "--body-file" in argv
    body_idx = argv.index("--body-file")
    assert argv[body_idx + 1] == "-"
    # Body arrives on stdin (`input=`), never as an inline argument.
    assert "My Body" not in argv
    assert run_fn.call_args.kwargs["input"] == "My Body"


def test_dedup_query_argv_is_read_only():
    # Keeps a future edit from turning the read-only dedup probe into
    # something that writes: no create/edit/comment/close/delete
    # subcommand token, and no write-gated flag from either deny-set.
    run_fn = Mock(return_value=Mock(returncode=0, stdout="[]"))
    submit.find_prior_report("abc123def456", run_fn=run_fn)
    argv = run_fn.call_args[0][0]
    assert isinstance(argv, list)
    for mutating_token in ("create", "edit", "comment", "close", "delete"):
        assert mutating_token not in argv
    for flag in _CREATE_DENY_SET + _COMMENT_DENY_SET:
        assert flag not in argv
    assert "shell" not in run_fn.call_args.kwargs


@pytest.mark.parametrize(
    "returncode,stdout,expected",
    [
        (
            0,
            '[{"number": 18, "title": "t", "url": "https://x/18"}]',
            ("https://x/18", True),
        ),
        (0, "[]", (None, True)),
        (4, "", (None, False)),
        (1, "", (None, False)),
    ],
    ids=[
        "duplicate-found",
        "no-duplicate",
        "unauthenticated-exit-4",
        "generic-nonzero-exit-1",
    ],
)
def test_dedup_distinguishes_all_three_signals(returncode, stdout, expected):
    # Pins that the exit code alone is NEVER the discriminator: exit 0
    # covers both "duplicate found" and "no duplicate" -- only the parsed
    # payload tells them apart.
    run_fn = Mock(return_value=Mock(returncode=returncode, stdout=stdout))
    result = submit.find_prior_report("abc123def456", run_fn=run_fn)
    assert result == expected


def test_every_interactive_run_asks_even_when_the_check_fails():
    report = _make_report()
    find_prior_report_fn = Mock(return_value=(None, False))
    confirm_fn = Mock(return_value=False)
    printed: list[str] = []
    console = Mock()
    console.print.side_effect = lambda msg: printed.append(msg)

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(),
        run_fn=Mock(),
        browser_open=Mock(),
        isatty_fn=Mock(return_value=True),
        confirm_fn=confirm_fn,
        console=console,
        find_prior_report_fn=find_prior_report_fn,
    )

    confirm_fn.assert_called_once()
    assert any("could not run" in m.lower() for m in printed)


# ---------------------------------------------------------------------------
# Task 1: submit_via_browser -- D-05 oversize escalation (small/mid/huge)
# ---------------------------------------------------------------------------


def _small_body() -> str:
    return submit.build_body(
        {"steps": [{"op": "id", "verdict": "OK", "reason": "-"}], "chip": "X"},
        [],
    )


def test_browser_tier_small_body_opens_once():
    browser_open = Mock()
    saved = SimpleNamespace(name="dev-test-x.json")
    url = submit.submit_via_browser(
        "My Title", _small_body(), saved, browser_open=browser_open
    )
    browser_open.assert_called_once_with(url)
    assert url is not None
    assert url.startswith(f"https://github.com/{submit.SUBMIT_REPO}/issues/new?")


def test_browser_tier_under_cap_returns_the_url():
    browser_open = Mock()
    saved = SimpleNamespace(name="dev-test-x.json")
    url = submit.submit_via_browser(
        "t", _small_body(), saved, browser_open=browser_open
    )
    assert url == browser_open.call_args[0][0]


def _oversize_json_only_body(repeats: int = 183) -> str:
    # A "payload" key lives ONLY in the JSON block (build_body's table is
    # sourced from "steps", never other top-level keys) -- so dropping the
    # fenced JSON removes essentially all of the bulk. Space-heavy content
    # is used because a space percent-encodes to `%20` (3 bytes for 1 raw
    # char), which is what pushes the ENCODED url over the escalate
    # threshold while the RAW body char count stays comfortably under it
    # -- proving the measurement keys on the encoded URL, not the raw body
    # (Pitfall 3).
    sanitized = {
        "steps": [{"op": "id", "verdict": "OK", "reason": "-"}],
        "payload": "a b c d e f g h i j " * repeats,
    }
    return submit.build_body(sanitized, [], include_json=True)


def test_oversize_drops_json_past_escalate_threshold():
    body = _oversize_json_only_body()
    assert len(body) < submit._URL_ESCALATE_BYTES  # fits by raw char count
    full_url = submit.build_issue_url("t", body)
    assert len(full_url.encode("utf-8")) > submit._URL_ESCALATE_BYTES  # but not encoded

    browser_open = Mock()
    saved = SimpleNamespace(name="dev-test-x.json")
    url = submit.submit_via_browser("t", body, saved, browser_open=browser_open)

    assert url is not None
    browser_open.assert_called_once_with(url)
    # decode back to confirm the JSON block itself is gone from the sent body
    from urllib.parse import parse_qs, urlparse

    sent_body = parse_qs(urlparse(url).query)["body"][0]
    assert "```json" not in sent_body
    assert "a b c d" not in sent_body


def test_oversize_note_names_filename_not_full_path():
    from pathlib import Path

    body = _oversize_json_only_body()
    saved = Path("/home/alice/.firestarter/reports/dev-test-x.json")
    browser_open = Mock()
    url = submit.submit_via_browser("t", body, saved, browser_open=browser_open)

    from urllib.parse import parse_qs, urlparse

    sent_body = parse_qs(urlparse(url).query)["body"][0]
    assert "dev-test-x.json" in sent_body
    assert "/home/alice" not in sent_body
    assert str(saved) not in sent_body


def test_oversize_hard_stop_no_open_past_cap(capsys):
    # Neither the table nor the (would-be-dropped) JSON fits under the hard
    # cap even after escalation -- the browser must never open.
    huge_reason = "r" * 9000
    sanitized = {
        "steps": [{"op": "id", "verdict": "OK", "reason": huge_reason}],
    }
    body = submit.build_body(sanitized, [], include_json=False)
    browser_open = Mock()
    saved = SimpleNamespace(name="dev-test-x.json")
    result = submit.submit_via_browser("t", body, saved, browser_open=browser_open)

    assert result is None
    browser_open.assert_not_called()
    captured = capsys.readouterr()
    assert "dev-test-x.json" in captured.out or str(saved) in captured.out
    assert "gh" in captured.out.lower()


def test_oversize_hard_stop_uses_console_when_given():
    huge_reason = "r" * 9000
    sanitized = {"steps": [{"op": "id", "verdict": "OK", "reason": huge_reason}]}
    body = submit.build_body(sanitized, [], include_json=False)
    browser_open = Mock()
    console = Mock()
    saved = SimpleNamespace(name="dev-test-x.json")
    result = submit.submit_via_browser(
        "t", body, saved, browser_open=browser_open, console=console
    )
    assert result is None
    browser_open.assert_not_called()
    console.print.assert_called_once()


def test_oversize_hard_stop_no_json_fence_still_hard_stops():
    # No fenced JSON block exists at all -- the escalation branch has
    # nothing to drop, but the hard-stop must still fire on a huge table.
    huge_reason = "q" * 9000
    sanitized = {"steps": [{"op": "id", "verdict": "OK", "reason": huge_reason}]}
    body = submit.build_body(sanitized, [], include_json=False)
    assert "```json" not in body
    browser_open = Mock()
    saved = SimpleNamespace(name="dev-test-x.json")
    result = submit.submit_via_browser("t", body, saved, browser_open=browser_open)
    assert result is None
    browser_open.assert_not_called()


def test_browser_unreachable_returns_none_and_prints_url_and_local_path():
    from pathlib import Path

    browser_open = Mock(return_value=False)
    saved = Path("/home/alice/.firestarter/reports/dev-test-x.json")
    console = Mock()
    printed: list[str] = []
    console.print.side_effect = lambda msg: printed.append(msg)

    result = submit.submit_via_browser(
        "My Title", _small_body(), saved, browser_open=browser_open, console=console
    )

    browser_open.assert_called_once()
    assert result is None
    joined = "\n".join(printed)
    assert "issues/new" in joined
    assert str(saved) in joined


def test_browser_reachable_true_returns_the_url():
    browser_open = Mock(return_value=True)
    saved = SimpleNamespace(name="dev-test-x.json")
    console = Mock()
    url = submit.submit_via_browser(
        "t", _small_body(), saved, browser_open=browser_open, console=console
    )
    assert url is not None
    assert url.startswith(f"https://github.com/{submit.SUBMIT_REPO}/issues/new?")
    console.print.assert_not_called()


# ---------------------------------------------------------------------------
# Task 2: submit_report -- D-03 refuse gate + D-04 TTY/off-TTY dispatch
# ---------------------------------------------------------------------------


def _make_report(*, chip="W27C512", protocol="7", host_version="3.0.0b11", pii=None):
    auto_capture = SimpleNamespace(
        chip=chip,
        protocol=protocol,
        host_version=host_version,
        fw_board_identity=None,
        hw_revision=None,
        chip_id_expected=None,
        chip_id_actual=None,
        chip_id_mismatch_reason=None,
    )
    reason = pii if pii is not None else "-"
    results = [_step("id", "OK")]
    steps_dict = [{"op": "id", "verdict": "OK", "reason": reason}]
    to_dict_value = {
        "dedup_fingerprint": "abc123def456",
        "steps": steps_dict,
        "auto_capture": {
            "chip": chip,
            "protocol": protocol,
            "host_version": host_version,
        },
    }
    report = SimpleNamespace(
        auto_capture=auto_capture,
        results=results,
        to_dict=lambda: to_dict_value,
    )
    return report


def test_refuse_missing_protocol_prints_field_and_does_not_send():
    report = _make_report(protocol=None)
    which_fn = Mock()
    run_fn = Mock()
    browser_open = Mock()
    isatty_fn = Mock(return_value=True)
    confirm_fn = Mock(return_value=True)
    saved = SimpleNamespace(name="dev-test-w27c512.json")

    printed: list[str] = []
    console = Mock()
    console.print.side_effect = lambda msg: printed.append(msg)

    submit.submit_report(
        report,
        "W27C512",
        saved,
        which_fn=which_fn,
        run_fn=run_fn,
        browser_open=browser_open,
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
        console=console,
    )

    assert any("protocol" in m for m in printed)
    which_fn.assert_not_called()
    run_fn.assert_not_called()
    browser_open.assert_not_called()
    confirm_fn.assert_not_called()
    isatty_fn.assert_not_called()


def test_refuse_missing_chip_names_chip():
    report = _make_report(chip=None)
    console = Mock()
    printed: list[str] = []
    console.print.side_effect = lambda msg: printed.append(msg)
    submit.submit_report(
        report,
        "",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(),
        run_fn=Mock(),
        browser_open=Mock(),
        isatty_fn=Mock(return_value=True),
        confirm_fn=Mock(return_value=True),
        console=console,
    )
    assert any("chip" in m for m in printed)


def test_offtty_prints_url_not_body_and_never_sends():
    """Retargeted by quick task 260821-spg: `submit_report` used to echo
    the sanitized body to the console off-TTY; that echo is gone. This
    test now asserts the INVERSE -- the markdown table line the body
    carries never reaches anything printed -- which is meaningful rather
    than vacuous because `build_body` is still called and `body` still
    reaches `build_issue_url` below (proven by the URL assertion staying):
    the test proves the ECHO went, not that the body stopped being built.
    """
    # find_prior_report_fn IS still invoked off-TTY (D-09: the dedup check
    # runs before any ask, on every path) -- injected as a Mock here so
    # run_fn/which_fn stay provably untouched by the FILING seams, which is
    # this test's actual concern.
    report = _make_report()
    which_fn = Mock()
    run_fn = Mock()
    browser_open = Mock()
    confirm_fn = Mock()
    isatty_fn = Mock(return_value=False)
    find_prior_report_fn = Mock(return_value=(None, True))
    printed: list[str] = []
    console = Mock()
    console.print.side_effect = lambda msg: printed.append(msg)
    saved = SimpleNamespace(name="dev-test-w27c512.json")

    submit.submit_report(
        report,
        "W27C512",
        saved,
        which_fn=which_fn,
        run_fn=run_fn,
        browser_open=browser_open,
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
        console=console,
        find_prior_report_fn=find_prior_report_fn,
    )

    assert not any("| id | OK |" in m for m in printed)
    assert any(f"github.com/{submit.SUBMIT_REPO}/issues/new" in m for m in printed)
    find_prior_report_fn.assert_called_once()
    browser_open.assert_not_called()
    run_fn.assert_not_called()
    confirm_fn.assert_not_called()
    which_fn.assert_not_called()


def test_tty_prints_no_body_before_the_confirm_prompt():
    """The second removed echo (quick task 260821-spg): on the interactive
    path, `submit_report` used to print the sanitized body before reaching
    the filing confirm prompt. No existing test covered that echo's
    absence -- this one does: the confirm prompt is still reached (and
    declined, so nothing is filed), but the body's markdown table line is
    never printed."""
    report = _make_report()
    which_fn = Mock()
    run_fn = Mock()
    browser_open = Mock()
    confirm_fn = Mock(return_value=False)
    isatty_fn = Mock(return_value=True)
    find_prior_report_fn = Mock(return_value=(None, True))
    printed: list[str] = []
    console = Mock()
    console.print.side_effect = lambda msg: printed.append(msg)
    saved = SimpleNamespace(name="dev-test-w27c512.json")

    submit.submit_report(
        report,
        "W27C512",
        saved,
        which_fn=which_fn,
        run_fn=run_fn,
        browser_open=browser_open,
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
        console=console,
        find_prior_report_fn=find_prior_report_fn,
    )

    assert not any("| id | OK |" in m for m in printed)
    confirm_fn.assert_called_once()
    browser_open.assert_not_called()
    run_fn.assert_not_called()


def test_tty_decline_aborts_without_sending():
    report = _make_report()
    which_fn = Mock()
    run_fn = Mock()
    browser_open = Mock()
    confirm_fn = Mock(return_value=False)
    isatty_fn = Mock(return_value=True)
    find_prior_report_fn = Mock(return_value=(None, True))
    saved = SimpleNamespace(name="dev-test-w27c512.json")

    submit.submit_report(
        report,
        "W27C512",
        saved,
        which_fn=which_fn,
        run_fn=run_fn,
        browser_open=browser_open,
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
        console=Mock(),
        find_prior_report_fn=find_prior_report_fn,
    )

    confirm_fn.assert_called_once()
    browser_open.assert_not_called()
    run_fn.assert_not_called()


def test_tty_confirm_gh_available_dispatches_to_gh_not_browser():
    report = _make_report()
    which_fn = Mock(return_value="/usr/bin/gh")
    run_fn = Mock(
        side_effect=[
            Mock(returncode=0),  # gh auth status
            Mock(
                returncode=0,
                stdout="https://github.com/henols/firestarter_prom/issues/9\n",
            ),  # gh issue create
        ]
    )
    browser_open = Mock()
    confirm_fn = Mock(return_value=True)
    isatty_fn = Mock(return_value=True)
    find_prior_report_fn = Mock(return_value=(None, True))
    saved = SimpleNamespace(name="dev-test-w27c512.json")

    submit.submit_report(
        report,
        "W27C512",
        saved,
        which_fn=which_fn,
        run_fn=run_fn,
        browser_open=browser_open,
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
        console=Mock(),
        find_prior_report_fn=find_prior_report_fn,
    )

    assert run_fn.call_count == 2
    browser_open.assert_not_called()


def test_tty_confirm_gh_success_echoes_the_created_issue_url():
    # Step 6: the URL submit_via_gh returns must reach the tester. Before this,
    # a successful submission printed nothing -- indistinguishable from a
    # failed one (proven live: firestarter_prom#18 was filed with no output).
    report = _make_report()
    created = f"https://github.com/{submit.SUBMIT_REPO}/issues/18"
    run_fn = Mock(
        side_effect=[
            Mock(returncode=0),  # gh auth status
            Mock(returncode=0, stdout=created + "\n"),  # gh issue create
        ]
    )
    browser_open = Mock()
    console = Mock()
    printed: list[str] = []
    console.print.side_effect = lambda msg: printed.append(msg)

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="dev-test-w27c512.json"),
        which_fn=Mock(return_value="/usr/bin/gh"),
        run_fn=run_fn,
        browser_open=browser_open,
        isatty_fn=Mock(return_value=True),
        confirm_fn=Mock(return_value=True),
        console=console,
        find_prior_report_fn=Mock(return_value=(None, True)),
    )

    assert any(created in m for m in printed)
    # A success must never be narrated as a degradation.
    assert not any("degrad" in m.lower() for m in printed)
    browser_open.assert_not_called()


def test_tty_confirm_gh_success_with_blank_stdout_still_confirms():
    # returncode 0 means gh created it; blank stdout must not read as silence.
    report = _make_report()
    run_fn = Mock(
        side_effect=[Mock(returncode=0), Mock(returncode=0, stdout="  \n")],
    )
    console = Mock()
    printed: list[str] = []
    console.print.side_effect = lambda msg: printed.append(msg)

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="dev-test-w27c512.json"),
        which_fn=Mock(return_value="/usr/bin/gh"),
        run_fn=run_fn,
        browser_open=Mock(),
        isatty_fn=Mock(return_value=True),
        confirm_fn=Mock(return_value=True),
        console=console,
        find_prior_report_fn=Mock(return_value=(None, True)),
    )

    assert any(submit.SUBMIT_REPO in m and "filed" in m.lower() for m in printed)


def test_tty_confirm_gh_unavailable_dispatches_to_browser():
    report = _make_report()
    which_fn = Mock(return_value=None)
    run_fn = Mock()
    browser_open = Mock()
    confirm_fn = Mock(return_value=True)
    isatty_fn = Mock(return_value=True)
    find_prior_report_fn = Mock(return_value=(None, True))
    saved = SimpleNamespace(name="dev-test-w27c512.json")

    submit.submit_report(
        report,
        "W27C512",
        saved,
        which_fn=which_fn,
        run_fn=run_fn,
        browser_open=browser_open,
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
        console=Mock(),
        find_prior_report_fn=find_prior_report_fn,
    )

    browser_open.assert_called_once()
    run_fn.assert_not_called()


def test_tty_confirm_gh_create_fails_falls_back_to_browser():
    report = _make_report()
    which_fn = Mock(return_value="/usr/bin/gh")
    run_fn = Mock(
        side_effect=[
            Mock(returncode=0),  # gh auth status: authed
            Mock(returncode=1, stdout=""),  # gh issue create: fails
        ]
    )
    browser_open = Mock()
    confirm_fn = Mock(return_value=True)
    isatty_fn = Mock(return_value=True)
    find_prior_report_fn = Mock(return_value=(None, True))
    saved = SimpleNamespace(name="dev-test-w27c512.json")

    submit.submit_report(
        report,
        "W27C512",
        saved,
        which_fn=which_fn,
        run_fn=run_fn,
        browser_open=browser_open,
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
        console=Mock(),
        find_prior_report_fn=find_prior_report_fn,
    )

    browser_open.assert_called_once()
    assert run_fn.call_count == 2


def test_submit_report_gh_failure_surfaces_stderr_before_browser_fallback():
    # Honest ordering: the stderr-narrating print from Task 1's gh-failure
    # path, plus submit_report's own degradation statement, must both
    # appear BEFORE the browser_open fallback call -- not merely alongside
    # it. A single shared `printed` list (fed by both console.print AND a
    # browser_open side_effect sentinel) is the only way a mocked test can
    # honestly prove ordering across two different injected seams.
    report = _make_report()
    which_fn = Mock(return_value="/usr/bin/gh")
    run_fn = Mock(
        side_effect=[
            Mock(returncode=0),  # gh auth status: authed
            Mock(
                returncode=1,
                stdout="",
                stderr="GraphQL: Resource not accessible by personal access token",
            ),  # gh issue create: fails
        ]
    )
    confirm_fn = Mock(return_value=True)
    isatty_fn = Mock(return_value=True)
    find_prior_report_fn = Mock(return_value=(None, True))
    saved = SimpleNamespace(name="dev-test-w27c512.json")

    printed: list[str] = []
    console = Mock()
    console.print.side_effect = lambda msg: printed.append(msg)

    def _browser_open_sentinel(url):
        printed.append("BROWSER_OPEN_SENTINEL")
        return True

    browser_open = Mock(side_effect=_browser_open_sentinel)

    submit.submit_report(
        report,
        "W27C512",
        saved,
        which_fn=which_fn,
        run_fn=run_fn,
        browser_open=browser_open,
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
        console=console,
        find_prior_report_fn=find_prior_report_fn,
    )

    stderr_idx = next(
        i
        for i, m in enumerate(printed)
        if "GraphQL: Resource not accessible by personal access token" in m
    )
    degrade_idx = next(
        i for i, m in enumerate(printed) if "degrad" in m.lower() and "GraphQL" not in m
    )
    sentinel_idx = printed.index("BROWSER_OPEN_SENTINEL")

    assert stderr_idx < sentinel_idx
    assert degrade_idx < sentinel_idx


def test_tty_body_sent_to_gh_is_sanitized():
    # A PII vector present in a step reason must never reach the seam
    # unscrubbed (end-to-end sanitize integration).
    report = _make_report(pii="failed reading /home/alice/scratch/file.bin")
    which_fn = Mock(return_value="/usr/bin/gh")
    run_fn = Mock(
        side_effect=[
            Mock(returncode=0),
            Mock(returncode=0, stdout="https://github.com/x/y/issues/1\n"),
        ]
    )
    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=which_fn,
        run_fn=run_fn,
        browser_open=Mock(),
        isatty_fn=Mock(return_value=True),
        confirm_fn=Mock(return_value=True),
        console=Mock(),
        find_prior_report_fn=Mock(return_value=(None, True)),
    )
    create_call = run_fn.call_args_list[1]
    sent_body = create_call.kwargs["input"]
    assert "alice" not in sent_body
    assert "/home/<user>/scratch/file.bin" in sent_body


def test_tty_body_sent_to_browser_is_sanitized():
    report = _make_report(pii="port /dev/ttyACM0 gone")
    browser_open = Mock()
    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(return_value=None),
        run_fn=Mock(),
        browser_open=browser_open,
        isatty_fn=Mock(return_value=True),
        confirm_fn=Mock(return_value=True),
        console=Mock(),
    )
    from urllib.parse import parse_qs, urlparse

    sent_url = browser_open.call_args[0][0]
    sent_body = parse_qs(urlparse(sent_url).query)["body"][0]
    assert "ttyACM0" not in sent_body
    assert "/dev/tty<redacted>" in sent_body


def test_refuse_never_calls_isatty():
    # D-03 refuse must short-circuit before the D-04 TTY gate is even
    # consulted.
    report = _make_report(host_version=None)
    isatty_fn = Mock(return_value=True)
    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(),
        run_fn=Mock(),
        browser_open=Mock(),
        isatty_fn=isatty_fn,
        confirm_fn=Mock(),
        console=Mock(),
    )
    isatty_fn.assert_not_called()


# ---------------------------------------------------------------------------
# Task 2: submit_report -- D-09/D-10/D-11 dedup-first, always-ask, comment
# ---------------------------------------------------------------------------


def test_dedup_seam_invoked_before_confirm_fn_on_every_ask_path():
    # D-09: the dedup check runs BEFORE any ask. Assert relative call
    # order, not merely that both were called.
    report = _make_report()
    order: list[str] = []
    find_prior_report_fn = Mock(
        side_effect=lambda *a, **k: (order.append("dedup"), (None, True))[1]
    )
    confirm_fn = Mock(side_effect=lambda *a, **k: (order.append("confirm"), True)[1])

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(return_value=None),
        run_fn=Mock(),
        browser_open=Mock(),
        isatty_fn=Mock(return_value=True),
        confirm_fn=confirm_fn,
        console=Mock(),
        find_prior_report_fn=find_prior_report_fn,
    )

    assert order == ["dedup", "confirm"]


def test_no_duplicate_asks_once_and_dispatches_to_create_on_yes():
    report = _make_report()
    run_fn = Mock(
        side_effect=[
            Mock(returncode=0),  # gh auth status
            Mock(
                returncode=0,
                stdout="https://github.com/henols/firestarter_prom/issues/9\n",
            ),  # gh issue create
        ]
    )
    confirm_fn = Mock(return_value=True)
    find_prior_report_fn = Mock(return_value=(None, True))
    comment_via_gh_fn = Mock()

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(return_value="/usr/bin/gh"),
        run_fn=run_fn,
        browser_open=Mock(),
        isatty_fn=Mock(return_value=True),
        confirm_fn=confirm_fn,
        console=Mock(),
        find_prior_report_fn=find_prior_report_fn,
        comment_via_gh_fn=comment_via_gh_fn,
    )

    confirm_fn.assert_called_once()
    ask_text = confirm_fn.call_args[0][0]
    assert "Submit this report" in ask_text
    comment_via_gh_fn.assert_not_called()
    assert run_fn.call_count == 2


def test_duplicate_found_asks_comment_question_and_dispatches_to_comment_on_yes():
    report = _make_report()
    prior_url = "https://github.com/henols/firestarter_prom/issues/18"
    find_prior_report_fn = Mock(return_value=(prior_url, True))
    comment_via_gh_fn = Mock(return_value=prior_url + "#issuecomment-1")
    confirm_fn = Mock(return_value=True)
    run_fn = Mock()

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(return_value="/usr/bin/gh"),
        run_fn=run_fn,
        browser_open=Mock(),
        isatty_fn=Mock(return_value=True),
        confirm_fn=confirm_fn,
        console=Mock(),
        find_prior_report_fn=find_prior_report_fn,
        comment_via_gh_fn=comment_via_gh_fn,
    )

    confirm_fn.assert_called_once()
    ask_text = confirm_fn.call_args[0][0]
    assert prior_url in ask_text
    assert "comment" in ask_text.lower()
    comment_via_gh_fn.assert_called_once()
    assert comment_via_gh_fn.call_args[0][0] == prior_url
    # No new-issue create call is ever made on the duplicate branch.
    run_fn.assert_not_called()


def test_duplicate_comment_decline_does_not_comment():
    report = _make_report()
    prior_url = "https://github.com/henols/firestarter_prom/issues/18"
    find_prior_report_fn = Mock(return_value=(prior_url, True))
    comment_via_gh_fn = Mock()
    confirm_fn = Mock(return_value=False)

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(return_value="/usr/bin/gh"),
        run_fn=Mock(),
        browser_open=Mock(),
        isatty_fn=Mock(return_value=True),
        confirm_fn=confirm_fn,
        console=Mock(),
        find_prior_report_fn=find_prior_report_fn,
        comment_via_gh_fn=comment_via_gh_fn,
    )

    comment_via_gh_fn.assert_not_called()


def test_duplicate_comment_fails_falls_back_to_browser_on_existing_issue():
    report = _make_report()
    prior_url = "https://github.com/henols/firestarter_prom/issues/18"
    find_prior_report_fn = Mock(return_value=(prior_url, True))
    comment_via_gh_fn = Mock(return_value=None)
    browser_open = Mock()

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(return_value="/usr/bin/gh"),
        run_fn=Mock(),
        browser_open=browser_open,
        isatty_fn=Mock(return_value=True),
        confirm_fn=Mock(return_value=True),
        console=Mock(),
        find_prior_report_fn=find_prior_report_fn,
        comment_via_gh_fn=comment_via_gh_fn,
    )

    browser_open.assert_called_once()


def test_dedup_check_failed_still_asks_and_prints_could_not_run_line():
    report = _make_report()
    find_prior_report_fn = Mock(return_value=(None, False))
    confirm_fn = Mock(return_value=False)
    printed: list[str] = []
    console = Mock()
    console.print.side_effect = lambda msg: printed.append(msg)

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(return_value=None),
        run_fn=Mock(),
        browser_open=Mock(),
        isatty_fn=Mock(return_value=True),
        confirm_fn=confirm_fn,
        console=console,
        find_prior_report_fn=find_prior_report_fn,
    )

    confirm_fn.assert_called_once()
    ask_text = confirm_fn.call_args[0][0]
    assert "Submit this report" in ask_text
    assert any("could not run" in m.lower() for m in printed)


def test_off_tty_names_existing_issue_when_duplicate_found():
    report = _make_report()
    prior_url = "https://github.com/henols/firestarter_prom/issues/18"
    find_prior_report_fn = Mock(return_value=(prior_url, True))
    printed: list[str] = []
    console = Mock()
    console.print.side_effect = lambda msg: printed.append(msg)

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(),
        run_fn=Mock(),
        browser_open=Mock(),
        isatty_fn=Mock(return_value=False),
        confirm_fn=Mock(),
        console=console,
        find_prior_report_fn=find_prior_report_fn,
    )

    assert any(prior_url in m for m in printed)


def test_off_tty_prints_could_not_run_line_when_dedup_check_failed():
    report = _make_report()
    find_prior_report_fn = Mock(return_value=(None, False))
    printed: list[str] = []
    console = Mock()
    console.print.side_effect = lambda msg: printed.append(msg)

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(),
        run_fn=Mock(),
        browser_open=Mock(),
        isatty_fn=Mock(return_value=False),
        confirm_fn=Mock(),
        console=console,
        find_prior_report_fn=find_prior_report_fn,
    )

    assert any("could not run" in m.lower() for m in printed)


def test_comment_body_sent_is_sanitized():
    # A PII vector present in a step reason must never reach comment_via_gh
    # unscrubbed (mirrors test_tty_body_sent_to_gh_is_sanitized for the
    # duplicate-comment branch).
    report = _make_report(pii="failed reading /home/alice/scratch/file.bin")
    prior_url = "https://github.com/henols/firestarter_prom/issues/18"
    find_prior_report_fn = Mock(return_value=(prior_url, True))
    comment_via_gh_fn = Mock(return_value=prior_url + "#issuecomment-1")

    submit.submit_report(
        report,
        "W27C512",
        SimpleNamespace(name="x.json"),
        which_fn=Mock(return_value="/usr/bin/gh"),
        run_fn=Mock(),
        browser_open=Mock(),
        isatty_fn=Mock(return_value=True),
        confirm_fn=Mock(return_value=True),
        console=Mock(),
        find_prior_report_fn=find_prior_report_fn,
        comment_via_gh_fn=comment_via_gh_fn,
    )

    sent_body = comment_via_gh_fn.call_args[0][1]
    assert "alice" not in sent_body
    assert "/home/<user>/scratch/file.bin" in sent_body
