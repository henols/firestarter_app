"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 190 Plan 190-01 (URL-01 / URL-04 / D-09 / D-10 / D-11 / D-12): pins the
failure-versus-empty split introduced in `FirmwareManager.list_releases` and
the `fw --list` handler that consumes it.

Every `CliRunner().invoke(cli, argv)` call in this module leaves the pre-built
context object out entirely — no injected `AppContext` is handed to `invoke`.
Supplying one short-circuits `cli()`'s group callback before `_setup_logging`
installs `SingleLineStatusHandler` (bound to stdout), so `logger.error` would
instead fall through to `logging.lastResort`, which writes to stderr. A
stderr assertion written against an invocation carrying an injected context
would therefore pass even if production routed the same message to stdout —
it measures the test harness's own fallback logger, not the shipped CLI. The
bare-invoke form has been measured byte-identical to a real subprocess run
(190-RESEARCH.md), so it is used everywhere a stream is asserted on, at the
cost of driving the real `AppContext` construction path — which is why the
CLI-level tests below patch `FirmwareManager.list_releases` at the class
rather than through an injected mock.

`result.stdout` and `result.stderr` are asserted separately, and the
attribute that concatenates both streams into one is never referenced
anywhere in this module: a concatenation cannot support a claim about which
stream a message landed on.

No assertion here compares `result.stdout` to the empty string on the failure
path. `list_releases`'s own `logger.error` line is retained by D-07 and still
reaches stdout in production through `SingleLineStatusHandler`. The contract
this module pins is narrower and exact: no JSON document appears on stdout on
the failure path, not that stdout is silent.
"""

import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from firestarter import firmware
from firestarter.cli_handlers import cli
from firestarter.firmware import FirmwareManager

_LEONARDO_ONLY_RELEASE = {
    "tag_name": "2.0.0",
    "draft": False,
    "prerelease": False,
    "published_at": "2026-01-01T00:00:00Z",
    "assets": [
        {
            "name": "firestarter_leonardo.hex",
            "browser_download_url": "https://example.invalid/firestarter_leonardo.hex",
        }
    ],
}


class _SuccessfulEmptyPageResponse:
    """The response shape `_fetch_all_releases` expects from a page carrying
    no release with a board-matching asset — a genuine "nothing for you"
    answer, not a transport failure."""

    def raise_for_status(self):
        return None

    def json(self):
        return [_LEONARDO_ONLY_RELEASE]

    headers: dict = {}


@pytest.fixture
def runner() -> CliRunner:
    """Fresh `CliRunner` per test. Unlike `test_cli_handlers.py`'s fixture of
    the same name, this docstring makes no `mix_stderr` claim: Click 8.2
    removed that kwarg, the installed Click is 8.5.0, and `result.stdout` /
    `result.stderr` are always separate streams here."""
    return CliRunner()


def test_list_releases_returns_none_on_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driving the real `list_releases` body with the HTTP seam raising a
    transport exception returns `None` — the fetch itself did not succeed."""

    def _raise(*_args, **_kwargs):
        raise firmware.requests.RequestException("blocked: no network in this test")

    monkeypatch.setattr(firmware.requests, "get", _raise)
    fm = FirmwareManager(config_manager=MagicMock())
    result = fm.list_releases(board="uno")
    assert result is None


def test_list_releases_returns_empty_list_on_no_matching_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driving the real `list_releases` body with a successful response whose
    only release carries a leonardo asset returns `[]` for board 'uno' — the
    fetch succeeded and nothing matched, which is a distinct value from a
    failed fetch."""
    monkeypatch.setattr(
        firmware.requests, "get", lambda *_a, **_kw: _SuccessfulEmptyPageResponse()
    )
    fm = FirmwareManager(config_manager=MagicMock())
    result = fm.list_releases(board="uno")
    assert result == []
    assert result is not None


def test_fw_list_plain_on_failure_reports_endpoint_and_board_to_stderr(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fw --list` with `list_releases` returning `None`: exit 1, the endpoint
    slug and the board name both appear on `result.stderr`, and no JSON
    document appears on `result.stdout`. Reverting the handler's `is None`
    guard makes this test fail: with the guard gone, `releases` is `None` and
    the plain-table branch's row loop over `None` raises `TypeError` instead
    of reaching this behaviour at all."""
    monkeypatch.setattr(FirmwareManager, "list_releases", lambda self, **_kw: None)
    result = runner.invoke(cli, ["fw", "--list", "--board", "uno"])
    assert result.exit_code == 1
    assert "henols/firestarter_fw" in result.stderr
    assert "uno" in result.stderr
    assert "[" not in result.stdout
    assert "null" not in result.stdout


def test_fw_list_json_on_failure_exits_1(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fw --list --json` with `list_releases` returning `None`: exit 1.
    Reverting the guard makes this test fail: the `json_output` branch would
    then run unconditionally and `json.dumps(None)` completes without error,
    so the command would exit 0 instead."""
    monkeypatch.setattr(FirmwareManager, "list_releases", lambda self, **_kw: None)
    result = runner.invoke(cli, ["fw", "--list", "--json", "--board", "uno"])
    assert result.exit_code == 1


def test_fw_list_json_on_failure_emits_no_document(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fw --list --json` with `list_releases` returning `None`:
    `result.stdout` does not parse as JSON at all — neither `[]` nor the
    `null` that `json.dumps(None)` would render. Reverting the guard makes
    this test fail: `json.dumps(None)` would render `null`, which is
    well-formed JSON and parses successfully to `None`, so the test would
    observe a parseable document instead of a parse failure."""
    monkeypatch.setattr(FirmwareManager, "list_releases", lambda self, **_kw: None)
    result = runner.invoke(cli, ["fw", "--list", "--json", "--board", "uno"])
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)


def test_fw_list_plain_on_genuine_empty_prints_header_and_board_line(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fw --list` with `list_releases` returning `[]`: exit 0, all four
    column labels present, a line naming the board present, and no error on
    `result.stderr`."""
    monkeypatch.setattr(FirmwareManager, "list_releases", lambda self, **_kw: [])
    result = runner.invoke(cli, ["fw", "--list", "--board", "uno"])
    assert result.exit_code == 0
    for column in ("Version", "Channel", "Published", "Asset URL"):
        assert column in result.stdout
    assert "uno" in result.stdout
    assert result.stderr == ""


def test_fw_list_json_on_genuine_empty_parses_to_empty_list(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fw --list --json` with `list_releases` returning `[]`: exit 0 and
    `result.stdout` parses as JSON to an empty list. Reverting the guard
    makes this test fail only incidentally (this path never enters the
    guard), but it is the D-13 companion this module carries so the two
    genuine-empty cases sit next to their failure-path counterparts."""
    monkeypatch.setattr(FirmwareManager, "list_releases", lambda self, **_kw: [])
    result = runner.invoke(cli, ["fw", "--list", "--json", "--board", "uno"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
