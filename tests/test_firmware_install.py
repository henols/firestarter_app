"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 18 Plan 01 — Wave 0 RED-gate scaffold for beta-aware firmware downloader.

Requirements covered: INST-01, INST-02, INST-03, INST-04
Decisions pinned: D-03..D-25 (see per-class docstrings for specific IDs)

Wave 0 contract:
- All tests in this file fail RED until Plan 18-02 implements the new symbols:
    FirmwareManager.fetch_release_info (firmware.py)
    FirmwareManager.list_releases (firmware.py)
    FIRMWARE_VERSION_RE (firmware.py)
    _maybe_auto_route_to_pre (main.py)
    create_firmware_args returning fw_parser (main.py, new return contract)
    --pre / --firmware-version / --list / --stable / --json flags (main.py)
- pytest collection SUCCEEDS because `from firestarter import firmware` and
  `from firestarter.firmware import FirmwareManager` import cleanly today.
- Imports of missing symbols are placed INSIDE test method bodies so that
  collection does not fail — only the individual test method fails at runtime.
- All network calls are mocked via monkeypatch.setattr(firmware.requests, "get", ...)
  matching the Phase 15 / Phase 6 monkeypatch.setattr style.
"""

import json  # noqa: F401
import logging
from unittest.mock import MagicMock

import pytest
import requests as _requests

from firestarter import firmware
from firestarter.firmware import FirmwareManager

# ---------------------------------------------------------------------------
# Module-local helpers — NOT in conftest.py (per VALIDATION.md line 60)
# ---------------------------------------------------------------------------


def mock_releases_factory(releases, next_url=None):
    """Build a MagicMock with the shape requests.get() returns for /releases endpoints.

    Args:
        releases: list of release dicts (GitHub API shape).
        next_url: if set, includes a Link: rel="next" header to simulate pagination.

    Returns a MagicMock with .json(), .raise_for_status(), .headers, .iter_content() set.
    """  # noqa: E501
    mock = MagicMock()
    mock.json.return_value = releases
    mock.raise_for_status.return_value = None
    mock.headers = {"Link": f'<{next_url}>; rel="next"'} if next_url else {}
    mock.iter_content.return_value = iter([b"fake hex data"])
    return mock


def mock_404_response():
    """Build a MagicMock that raises HTTPError(status_code=404) on raise_for_status()."""  # noqa: E501
    mock = MagicMock()
    mock.raise_for_status.side_effect = _requests.exceptions.HTTPError(
        response=MagicMock(status_code=404)
    )
    return mock


class _FakeAvrdude:
    """Captures Avrdude(...) constructor kwargs for Phase 23 D-06 assertions.

    Skips _find_avrdude_path / _get_avrdude_version / _configure_avrconf side
    effects (RESEARCH Pitfall 5). `command` is a str so the post-success
    `config_manager.set_value("avrdude-path", avrdude.command)` save at
    firmware.py:492 does not crash; `config` is None to match the avrdude>=7
    path that bypasses the `-C` flag branch at firmware.py:493.
    """

    def __init__(self, partno, programmer_id, baud_rate, port, **kw):
        self.partno = partno
        self.programmer_id = programmer_id
        self.baud_rate = baud_rate
        self.port = port
        self.command = "/fake/avrdude"  # str — _install_with_avrdude saves this
        self.config = None  # avrdude>=7 path (no -C arg)

    def flash_firmware(self, hex_file_path):
        return ("", 0)  # (stderr, returncode) — 0 = success


# ---------------------------------------------------------------------------
# Stable release fixture data
# ---------------------------------------------------------------------------

_STABLE_RELEASE_UNO = {
    "tag_name": "3.0.0",
    "prerelease": False,
    "draft": False,
    "published_at": "2026-05-15T11:00:00Z",
    "assets": [
        {
            "name": "firestarter_uno.hex",
            "browser_download_url": "https://example.com/uno_stable.hex",
        }
    ],
}

_STABLE_RELEASE_LEONARDO = {
    "tag_name": "3.0.0",
    "prerelease": False,
    "draft": False,
    "published_at": "2026-05-15T11:00:00Z",
    "assets": [
        {
            "name": "firestarter_leonardo.hex",
            "browser_download_url": "https://example.com/leonardo_stable.hex",
        }
    ],
}

# 3-asset stable release fixture for uno328pb-driven resolution.
# tag_name "3.0.1" distinguishes from the existing uno/leonardo fixtures
# (both at 3.0.0). Asset order [uno, uno328pb, leonardo] follows Phase 21
# D-08 section-order discipline (matches platformio.ini default_envs order
# D-01 landed).
_STABLE_RELEASE_UNO328PB = {
    "tag_name": "3.0.1",
    "prerelease": False,
    "draft": False,
    "published_at": "2026-05-22T11:00:00Z",
    "assets": [
        {
            "name": "firestarter_uno.hex",
            "browser_download_url": "https://example.com/uno_stable.hex",
        },
        {
            "name": "firestarter_uno328pb.hex",
            "browser_download_url": "https://example.com/uno328pb_stable.hex",
        },
        {
            "name": "firestarter_leonardo.hex",
            "browser_download_url": "https://example.com/leonardo_stable.hex",
        },
    ],
}


# ===========================================================================
# TestFirmwareInstallStable — INST-01 non-regression
# ===========================================================================


class TestFirmwareInstallStable:
    """INST-01 — stable-default non-regression.

    Ensures that bare `fw -i` on a stable-installed app continues to hit
    /releases/latest byte-identically, and that the new fetch_release_info
    router correctly delegates channel='stable' to the /releases/latest path.

    Decisions pinned: D-15 (preserve fetch_latest_release_info shim),
                      D-16 (channel='stable' delegates to /releases/latest).
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        """Clear any Phase 18 relevant env state before each test."""
        monkeypatch.delenv("FIRESTARTER_DEV_ALLOW_PRE_V12", raising=False)

    def test_stable_default_hits_releases_latest(self, monkeypatch):
        """INST-01 — fetch_release_info(channel='stable') returns (version, url).

        Mocks firmware.requests.get to return a single stable release.
        Asserts that the requested URL contains /releases/latest (stable path).
        Asserts that the returned tuple matches the asset for the 'uno' board.

        RED today: FirmwareManager has no fetch_release_info method — AttributeError.
        """
        calls = []
        stable_mock = mock_releases_factory([_STABLE_RELEASE_UNO])
        # Override json() to return a single release object (not a list), matching
        # /releases/latest API shape which returns a single release dict.
        stable_mock.json.return_value = _STABLE_RELEASE_UNO

        def recording_get(url, **kw):
            calls.append(url)
            return stable_mock

        monkeypatch.setattr(firmware.requests, "get", recording_get)
        fm = FirmwareManager(config_manager=MagicMock())
        v, url = fm.fetch_release_info(channel="stable", board="uno")
        assert v == "3.0.0"
        assert "uno_stable.hex" in url
        assert any("/releases/latest" in u for u in calls), (
            f"Expected a call to /releases/latest; got: {calls}"
        )

    def test_stable_path_returns_none_on_missing_board_asset(self, monkeypatch):
        """INST-01 — fetch_release_info(channel='stable') returns (None, None) when
        the board-matching asset is absent from the release.

        Only a leonardo asset is present; requesting 'uno' must return (None, None).

        RED today: FirmwareManager has no fetch_release_info method — AttributeError.
        """
        leonardo_only_mock = mock_releases_factory([])
        leonardo_only_mock.json.return_value = _STABLE_RELEASE_LEONARDO

        monkeypatch.setattr(
            firmware.requests, "get", lambda url, **kw: leonardo_only_mock
        )
        fm = FirmwareManager(config_manager=MagicMock())
        v, url = fm.fetch_release_info(channel="stable", board="uno")
        assert (v, url) == (None, None)


# ===========================================================================
# TestVersionComparator — INST-01 PEP 440 comparator fix
# ===========================================================================


class TestVersionComparator:
    """INST-01 — _compare_versions handles PEP 440 pre-release strings correctly.

    The current implementation uses tuple(map(int, v.split('.'))) which raises
    ValueError on any version with a non-integer component like '3.1.0b2'.
    Phase 18 refactors _compare_versions to use packaging.version.Version.

    Key correctness: b10 > b9 only with Version sort (string sort inverts this).

    Decisions pinned: D-15 (comparator refactored in-place, not replaced),
                      Pattern 4 from RESEARCH.md (VERIFIED via shell: packaging 26.2).
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        pass  # no env to clear for pure unit tests

    def test_stable_versions(self):
        """INST-01 — _compare_versions handles simple X.Y.Z comparisons correctly.

        These should pass both with the current and refactored implementation.
        Included to ensure refactor is non-regressive on the stable path.
        """
        fm = FirmwareManager(config_manager=MagicMock())
        assert fm._compare_versions("3.0.0", "3.0.0") is True
        assert fm._compare_versions("3.0.0", "2.9.9") is True
        assert fm._compare_versions("2.9.9", "3.0.0") is False

    def test_prerelease_versions(self):
        """INST-01 — _compare_versions handles pre-release ordering via PEP 440.

        b10 > b9 only when sorted by packaging.version.Version (not string sort).
        rc1 > b10 per PEP 440: devN < aN < bN < rcN < stable.
        3.0.0 > 3.1.0b2 is False because stable 3.0.0 < stable 3.1.0 but b2 < stable,
        so 3.0.0 < 3.1.0 stable > 3.1.0b2 > 3.0.0? Actually 3.0.0 < 3.1.0b2 so False.

        RED today: current _compare_versions calls tuple(map(int, ...)) which raises
        ValueError on '3.1.0b10' — these tests fail with ValueError until Wave 1.
        """
        fm = FirmwareManager(config_manager=MagicMock())
        # b10 > b9 requires PEP 440 sort (string sort would give b9 > b10)
        assert fm._compare_versions("3.1.0b10", "3.1.0b9") is True
        # rc1 > b10 per PEP 440 pre-release ordering
        assert fm._compare_versions("3.1.0rc1", "3.1.0b10") is True
        # 3.0.0 (stable) < 3.1.0b2 (pre-release of next minor): current < latest
        assert fm._compare_versions("3.0.0", "3.1.0b2") is False

    def test_dev_suffix_normalizes(self):
        """INST-01 — _compare_versions handles dev-suffix version strings.

        packaging.version.Version("2.0.7_dev") normalizes to 2.0.7.dev0 with
        is_prerelease=True. Per PEP 440 ordering: devN < stable, so
        "2.0.7_dev" < "2.0.7" (current dev < latest stable → not up-to-date).

        RED today: '2.0.7_dev'.split('.') gives ['2', '0', '7_dev'],
        then int('7_dev') raises ValueError.
        """
        fm = FirmwareManager(config_manager=MagicMock())
        # dev < stable: 2.0.7_dev is OLDER than 2.0.7 stable
        assert fm._compare_versions("2.0.7_dev", "2.0.7") is False
        # stable > dev: 2.0.7 stable IS newer than 2.0.7_dev
        assert fm._compare_versions("2.0.7", "2.0.7_dev") is True

    def test_invalid_version_returns_false(self):
        """INST-01 — _compare_versions returns False (not raises) on invalid input.

        Truly malformed strings like 'not-a-version' must not propagate as
        unhandled exceptions — they are logged WARN and return False.

        This partially passes today (ValueError is caught and returns False),
        but empty string may differ. Full correctness verified in Wave 1.
        """
        fm = FirmwareManager(config_manager=MagicMock())
        assert fm._compare_versions("not-a-version", "3.0.0") is False
        assert fm._compare_versions("", "3.0.0") is False


# ===========================================================================
# TestFirmwareInstallPreRelease — INST-02: --pre selection
# ===========================================================================


class TestFirmwareInstallPreRelease:
    """INST-02 — fetch_release_info(channel='pre') selects highest pre-release.

    Tests the paginated /releases path: filter prerelease=True, sort by PEP 440
    descending, take the highest. Falls back to stable when no pre-releases exist
    (mirrors pip install --pre semantics).

    Decisions pinned: D-03 (pre-release selection algorithm),
                      D-04 (pagination cap: 5 pages),
                      D-05 (silent fallback to stable when no pre-release exists),
                      D-06 (beta app always gets some firmware).
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        pass

    def test_pre_selects_highest_prerelease(self, monkeypatch):
        """INST-02 / D-03 — highest pre-release by PEP 440 sort wins.

        Two pages of releases mixing pre-release and stable. The rc1 should
        win over b10 and b9, and b10 should be preferred over b9 (string sort
        would incorrectly prefer b9).

        RED today: FirmwareManager has no fetch_release_info — AttributeError.
        """
        releases_page1 = [
            {
                "tag_name": "3.1.0b9",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-10T10:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/uno_b9.hex",
                    }
                ],
            },
            {
                "tag_name": "3.0.0",
                "prerelease": False,
                "draft": False,
                "published_at": "2026-05-01T10:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/uno_stable.hex",
                    }
                ],
            },
        ]
        releases_page2 = [
            {
                "tag_name": "3.1.0rc1",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-18T09:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/uno_rc1.hex",
                    }
                ],
            },
            {
                "tag_name": "3.1.0b10",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-15T08:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/uno_b10.hex",
                    }
                ],
            },
        ]
        page1_mock = mock_releases_factory(
            releases_page1,
            next_url="https://api.github.com/repos/henols/firestarter/releases?page=2",
        )
        page2_mock = mock_releases_factory(releases_page2)
        responses = iter([page1_mock, page2_mock])
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: next(responses))
        fm = FirmwareManager(config_manager=MagicMock())
        version, url = fm.fetch_release_info(channel="pre", board="uno")
        # rc1 is highest: rc > b per PEP 440
        assert version == "3.1.0rc1"
        assert "uno_rc1.hex" in url

    def test_pre_falls_back_to_stable_when_no_prerelease(self, monkeypatch, caplog):
        """INST-02 / D-05 — fallback to stable when no pre-release exists.

        If the paginated releases contain no prerelease=True entries, the method
        must silently fall back to /releases/latest and log INFO about the fallback.

        RED today: FirmwareManager has no fetch_release_info — AttributeError.
        """
        stable_only = mock_releases_factory([_STABLE_RELEASE_UNO])
        stable_latest_mock = mock_releases_factory([])
        stable_latest_mock.json.return_value = _STABLE_RELEASE_UNO

        call_count = [0]

        def multi_response_get(url, **kw):
            call_count[0] += 1
            if "latest" in url:
                return stable_latest_mock
            return stable_only

        monkeypatch.setattr(firmware.requests, "get", multi_response_get)
        fm = FirmwareManager(config_manager=MagicMock())
        with caplog.at_level(logging.INFO):
            version, url = fm.fetch_release_info(channel="pre", board="uno")
        # Falls back to stable release
        assert version == "3.0.0"
        assert url is not None
        # Must log something about falling back to stable
        lower_records = [r.message.lower() for r in caplog.records]
        assert any(
            "fall" in m or "stable" in m or "no pre" in m for m in lower_records
        ), f"Expected a fallback log record; got: {[r.message for r in caplog.records]}"

    def test_pre_filters_draft_releases(self, monkeypatch):
        """INST-02 / D-03 — draft releases are excluded from pre-release candidates.

        A draft release with draft=True must never be selected, even if it has the
        highest version number. The highest non-draft pre-release must win.

        RED today: FirmwareManager has no fetch_release_info — AttributeError.
        """
        releases = [
            {
                "tag_name": "9.9.9b1",
                "prerelease": True,
                "draft": True,  # <-- must be excluded
                "published_at": "2026-05-20T12:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/draft.hex",
                    }
                ],
            },
            {
                "tag_name": "3.1.0b2",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-19T11:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/uno_b2.hex",
                    }
                ],
            },
        ]
        mock = mock_releases_factory(releases)
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock)
        fm = FirmwareManager(config_manager=MagicMock())
        version, url = fm.fetch_release_info(channel="pre", board="uno")
        assert version == "3.1.0b2", f"Draft release must be skipped; got: {version}"
        assert "uno_b2.hex" in url

    def test_pre_pagination_cap(self, monkeypatch, caplog):
        """INST-02 / D-04 — pagination is capped at 5 pages.

        When 6+ pages are available via Link rel=next headers, only 5 pages
        should be fetched. A cap-hit INFO log must be emitted.

        RED today: FirmwareManager has no fetch_release_info — AttributeError.
        """
        release_on_page = lambda n: [  # noqa: E731
            {
                "tag_name": f"3.{n}.0b1",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-01T10:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": f"https://example.com/page{n}.hex",
                    }
                ],
            }
        ]

        pages = [
            mock_releases_factory(
                release_on_page(i),
                next_url=f"https://api.github.com/repos/henols/firestarter/releases?page={i + 1}",  # noqa: E501
            )
            for i in range(1, 7)  # 6 pages, last has a next_url too
        ]
        # Page 6 should NOT be fetched (cap is 5)
        page_iter = iter(pages)
        call_count = [0]

        def counting_get(url, **kw):
            call_count[0] += 1
            return next(page_iter)

        monkeypatch.setattr(firmware.requests, "get", counting_get)
        fm = FirmwareManager(config_manager=MagicMock())
        with caplog.at_level(logging.INFO):
            fm.fetch_release_info(channel="pre", board="uno")
        assert call_count[0] <= 5, (
            f"Expected at most 5 pages fetched; got {call_count[0]}"
        )
        lower_records = [r.message.lower() for r in caplog.records]
        assert any("cap" in m or "page" in m or "150" in m for m in lower_records), (
            f"Expected a pagination cap INFO log; got: {[r.message for r in caplog.records]}"  # noqa: E501
        )


# ===========================================================================
# TestFirmwareInstallPinned — INST-03: --firmware-version exact-tag install
# ===========================================================================


class TestFirmwareInstallPinned:
    """INST-03 — fetch_release_info(channel='pinned') fetches exact tag.

    Input validation via FIRMWARE_VERSION_RE happens before any network call.
    A 404 from /releases/tags/{tag} or a missing board asset both return (None, None).

    Decisions pinned: D-07 (FIRMWARE_VERSION_RE validates before network call),
                      D-08 (accepts stable AND pre-release version forms),
                      D-09 (404 or missing asset → fatal error, (None, None)).
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        pass

    def test_pinned_404_returns_none_none(self, monkeypatch):
        """INST-03 / D-09 — 404 from /releases/tags/{tag} returns (None, None).

        A 404 HTTPError from raise_for_status() must be caught and logged,
        returning (None, None) without propagating the exception.

        RED today: FirmwareManager has no fetch_release_info — AttributeError.
        """
        monkeypatch.setattr(
            firmware.requests, "get", lambda *a, **kw: mock_404_response()
        )
        fm = FirmwareManager(config_manager=MagicMock())
        v, url = fm.fetch_release_info(channel="pinned", version="9.9.9b1", board="uno")
        assert (v, url) == (None, None)

    def test_pinned_missing_board_asset_fatal(self, monkeypatch):
        """INST-03 / D-09 — missing board asset for pinned release returns (None, None).

        The release exists (200 OK) but only has a leonardo asset.
        Requesting 'uno' must return (None, None) and log an error containing both
        the tag name and the missing asset name.

        RED today: FirmwareManager has no fetch_release_info — AttributeError.
        """
        leonardo_release = {
            "tag_name": "3.1.0b2",
            "prerelease": True,
            "draft": False,
            "published_at": "2026-05-19T11:00:00Z",
            "assets": [
                {
                    "name": "firestarter_leonardo.hex",
                    "browser_download_url": "https://example.com/leonardo_b2.hex",
                }
            ],
        }
        mock = mock_releases_factory([])
        mock.json.return_value = leonardo_release
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock)
        fm = FirmwareManager(config_manager=MagicMock())
        v, url = fm.fetch_release_info(channel="pinned", version="3.1.0b2", board="uno")
        assert (v, url) == (None, None)

    def test_pinned_happy_path(self, monkeypatch):
        """INST-03 — pinned release with matching board asset returns (version, url).

        RED today: FirmwareManager has no fetch_release_info — AttributeError.
        """
        release = {
            "tag_name": "3.1.0b2",
            "prerelease": True,
            "draft": False,
            "published_at": "2026-05-19T11:00:00Z",
            "assets": [
                {
                    "name": "firestarter_uno.hex",
                    "browser_download_url": "https://example.com/uno_b2.hex",
                }
            ],
        }
        mock = mock_releases_factory([])
        mock.json.return_value = release
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock)
        fm = FirmwareManager(config_manager=MagicMock())
        v, url = fm.fetch_release_info(channel="pinned", version="3.1.0b2", board="uno")
        assert v == "3.1.0b2"
        assert url == "https://example.com/uno_b2.hex"

    def test_firmware_version_regex_accepts_pep440_forms(self):
        """INST-03 / D-07 / D-08 — FIRMWARE_VERSION_RE accepts valid and rejects invalid forms.

        Valid: X.Y.Z (stable), X.Y.ZbN (beta), X.Y.ZrcN (release candidate)
        Invalid: X.Y.Z-dev (dash separator), X.Y.Zbeta2 (full word), X.Y (missing patch),
                 latest (not a version), X.Y.Z.A.B (too many components)

        RED today: FIRMWARE_VERSION_RE does not exist in firestarter.firmware — ImportError.
        """  # noqa: E501
        from firestarter.firmware import FIRMWARE_VERSION_RE

        valid = ["3.1.0", "3.1.0b2", "3.1.0rc1", "0.0.1b1", "3.0.0", "10.20.30rc99"]
        for v in valid:
            assert FIRMWARE_VERSION_RE.match(v), (
                f"Expected {v!r} to match FIRMWARE_VERSION_RE"
            )

        invalid = ["3.1.0-dev", "3.1.0beta2", "3.1", "latest", "3.1.0.4.5", "", "abc"]
        for v in invalid:
            assert not FIRMWARE_VERSION_RE.match(v), (
                f"Expected {v!r} NOT to match FIRMWARE_VERSION_RE"
            )

        # CR-02: $ matches before trailing \n in Python; must use \Z so newlines
        # cannot smuggle into the URL template downstream.
        trailing_newline_inputs = ["3.1.0\n", "3.1.0b2\n", "3.1.0\r\n"]
        for v in trailing_newline_inputs:
            assert not FIRMWARE_VERSION_RE.match(v), (
                f"Expected {v!r} NOT to match (regex anchored with \\Z)"
            )


# ===========================================================================
# TestFirmwareList — INST-04: fw --list output
# ===========================================================================


class TestFirmwareList:
    """INST-04 — list_releases enumerates firmware releases in PEP 440 descending order.

    Returns a structured list of ReleaseInfo dicts with required keys.
    Omits releases without a matching board asset. Omits draft releases.
    Supports channel filtering (all / pre / stable).

    Decisions pinned: D-10 (plain text table default), D-11 (PEP 440 descending sort),
                      D-12 (--json outputs JSON array with 5 keys),
                      D-13 (--pre/--stable/--all channel filter mutex).
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        pass

    def _mixed_releases(self):
        """Returns a list of mixed stable + pre-release releases in shuffled order."""
        return [
            {
                "tag_name": "3.0.0",
                "prerelease": False,
                "draft": False,
                "published_at": "2026-05-15T11:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/stable.hex",
                    }
                ],
            },
            {
                "tag_name": "3.1.0b1",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-17T10:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/b1.hex",
                    }
                ],
            },
            {
                "tag_name": "3.1.0b10",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-19T10:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/b10.hex",
                    }
                ],
            },
            {
                "tag_name": "3.1.0rc1",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-20T09:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/rc1.hex",
                    }
                ],
            },
        ]

    def test_list_releases_sorted_descending(self, monkeypatch):
        """INST-04 / D-11 — list_releases returns PEP 440 descending order.

        rc1 > b10 > b1 > 3.0.0 stable (with PEP 440 sort, not string sort).
        Each returned element must have keys: version, tag, channel, published, asset_url.

        RED today: FirmwareManager has no list_releases method — AttributeError.
        """  # noqa: E501
        mock = mock_releases_factory(self._mixed_releases())
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock)
        fm = FirmwareManager(config_manager=MagicMock())
        releases = fm.list_releases(channel_filter="all", board="uno")
        assert len(releases) == 4
        # Check PEP 440 descending: rc1 must come first
        assert releases[0]["version"] == "3.1.0rc1"
        assert releases[1]["version"] == "3.1.0b10"  # b10 > b1 requires Version sort
        assert releases[2]["version"] == "3.1.0b1"
        assert releases[3]["version"] == "3.0.0"
        # Each element must have all 5 required keys
        required_keys = {"version", "tag", "channel", "published", "asset_url"}
        for r in releases:
            assert required_keys <= r.keys(), (
                f"Missing keys in release entry: {r.keys()} (expected {required_keys})"
            )

    def test_list_releases_omits_releases_without_board_asset(self, monkeypatch):
        """INST-04 / D-11 — releases without a matching board asset are silently omitted.

        RED today: FirmwareManager has no list_releases — AttributeError.
        """  # noqa: E501
        releases = [
            {
                "tag_name": "3.0.0",
                "prerelease": False,
                "draft": False,
                "published_at": "2026-05-15T11:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/stable_uno.hex",
                    }
                ],
            },
            {
                "tag_name": "2.9.9",
                "prerelease": False,
                "draft": False,
                "published_at": "2026-04-01T10:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_leonardo.hex",  # no uno asset
                        "browser_download_url": "https://example.com/old_leo.hex",
                    }
                ],
            },
        ]
        mock = mock_releases_factory(releases)
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock)
        fm = FirmwareManager(config_manager=MagicMock())
        result = fm.list_releases(channel_filter="all", board="uno")
        versions = [r["version"] for r in result]
        assert "3.0.0" in versions
        assert "2.9.9" not in versions, "Release without uno asset must be omitted"

    def test_list_releases_omits_drafts(self, monkeypatch):
        """INST-04 / D-11 — draft releases (draft=True) are omitted from list output.

        RED today: FirmwareManager has no list_releases — AttributeError.
        """
        releases = [
            {
                "tag_name": "9.9.9b1",
                "prerelease": True,
                "draft": True,  # must be excluded
                "published_at": "2026-05-20T12:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/draft.hex",
                    }
                ],
            },
            {
                "tag_name": "3.0.0",
                "prerelease": False,
                "draft": False,
                "published_at": "2026-05-15T11:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno.hex",
                        "browser_download_url": "https://example.com/stable.hex",
                    }
                ],
            },
        ]
        mock = mock_releases_factory(releases)
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock)
        fm = FirmwareManager(config_manager=MagicMock())
        result = fm.list_releases(channel_filter="all", board="uno")
        versions = [r["version"] for r in result]
        assert "9.9.9b1" not in versions, "Draft release must be excluded"
        assert "3.0.0" in versions

    def test_list_releases_channel_filter_pre(self, monkeypatch):
        """INST-04 / D-13 — channel_filter='pre' returns only prerelease entries.

        RED today: FirmwareManager has no list_releases — AttributeError.
        """
        mock = mock_releases_factory(self._mixed_releases())
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock)
        fm = FirmwareManager(config_manager=MagicMock())
        result = fm.list_releases(channel_filter="pre", board="uno")
        assert all(r["channel"] == "prerelease" for r in result), (
            f"Expected only prerelease channel; got: {[r['channel'] for r in result]}"
        )
        assert len(result) == 3  # rc1, b10, b1

    def test_list_releases_channel_filter_stable(self, monkeypatch):
        """INST-04 / D-13 — channel_filter='stable' returns only stable entries.

        RED today: FirmwareManager has no list_releases — AttributeError.
        """
        mock = mock_releases_factory(self._mixed_releases())
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock)
        fm = FirmwareManager(config_manager=MagicMock())
        result = fm.list_releases(channel_filter="stable", board="uno")
        assert all(r["channel"] == "stable" for r in result), (
            f"Expected only stable channel; got: {[r['channel'] for r in result]}"
        )
        assert len(result) == 1  # only 3.0.0


# ===========================================================================
# TestMagicDefault — D-21/D-22 beta-app auto-routing
# ===========================================================================


class TestMagicDefault:
    """INST-02 — magic default: beta-app bare fw -i auto-routes to --pre.

    When packaging.version.Version(firestarter.__version__).is_prerelease is True,
    bare 'firestarter fw -i' (no --pre, no --firmware-version) sets args.pre = True
    before calling manage_firmware_update.

    Stable-installed apps (Version.is_prerelease == False) see no change.

    The helper _maybe_auto_route_to_pre(args) takes NO logger param (revision
    warning #6). It uses logging.getLogger(__name__) internally so pytest's
    caplog fixture captures records automatically.

    Decisions pinned: D-21 (beta-app auto-routes to --pre),
                      D-22 (detection in main.py at dispatch time),
                      D-23 (stable-installed apps: no change),
                      D-24 (explicit --firmware-version opts out of magic),
                      D-25 (always logs INFO: 'Beta app detected ...').
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        """Restore firestarter.__version__ after each test."""
        import firestarter as _pkg

        monkeypatch.setattr(_pkg, "__version__", _pkg.__version__)

    def test_dev_suffix_is_prerelease(self, monkeypatch):
        """D-21 / D-22 — dev-suffix version triggers magic default.

        '2.0.7_dev' normalizes to 2.0.7.dev0 with is_prerelease=True via packaging.
        The helper must set args.pre = True.

        RED today: _maybe_auto_route_to_pre does not exist in firestarter.main — ImportError.
        """  # noqa: E501
        import firestarter as _pkg

        monkeypatch.setattr(_pkg, "__version__", "2.0.7_dev")
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        args = MagicMock()
        args.install = True
        args.pre = False
        args.firmware_version = None
        args.stable = False
        _maybe_auto_route_to_pre(args)
        assert args.pre is True, "Magic default must set args.pre=True on dev install"

    def test_stable_install_no_magic(self, monkeypatch):
        """D-23 — stable-installed app (Version.is_prerelease=False) sees no change.

        '2.0.7' is a stable version; is_prerelease=False. Magic default must NOT fire.
        args.pre must remain False after calling the helper.

        RED today: _maybe_auto_route_to_pre does not exist in firestarter.main — ImportError.
        """  # noqa: E501
        import firestarter as _pkg

        monkeypatch.setattr(_pkg, "__version__", "2.0.7")
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        args = MagicMock()
        args.install = True
        args.pre = False
        args.firmware_version = None
        args.stable = False
        _maybe_auto_route_to_pre(args)
        assert args.pre is False, "Stable install must NOT trigger magic default"

    def test_explicit_pre_no_double_log(self, monkeypatch, caplog):
        """D-22 — if args.pre is already True, helper should not log again.

        When the user explicitly passed --pre, the magic default must not
        double-log the 'Beta app detected' message.

        RED today: _maybe_auto_route_to_pre does not exist — ImportError.
        """
        import firestarter as _pkg

        monkeypatch.setattr(_pkg, "__version__", "2.0.7_dev")
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        args = MagicMock()
        args.install = True
        args.pre = True  # already set by user
        args.firmware_version = None
        args.stable = False
        with caplog.at_level(logging.INFO):
            _maybe_auto_route_to_pre(args)
        # Must NOT log "Beta app detected" when --pre is already set
        assert not any(
            "beta app detected" in r.message.lower() for r in caplog.records
        ), f"Must not double-log; got: {[r.message for r in caplog.records]}"

    def test_explicit_firmware_version_no_magic(self, monkeypatch):
        """D-24 — explicit --firmware-version opts out of magic default.

        Even on a dev install, if firmware_version is pinned, args.pre must
        remain False (explicit pin is the documented stable opt-out).

        RED today: _maybe_auto_route_to_pre does not exist — ImportError.
        """
        import firestarter as _pkg

        monkeypatch.setattr(_pkg, "__version__", "2.0.7_dev")
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        args = MagicMock()
        args.install = True
        args.pre = False
        args.firmware_version = "3.0.0"  # explicit stable pin
        _maybe_auto_route_to_pre(args)
        assert args.pre is False, (
            "Explicit --firmware-version must opt out of magic default"
        )

    def test_explicit_stable_flag_no_magic(self, monkeypatch):
        """CR-01 — explicit --stable opts out of magic default.

        revision blocker #1 added --stable to channel_group specifically so
        operators on beta-installed apps can pick stable explicitly. The guard
        must honor that intent and NOT auto-route to --pre when args.stable
        is True, even on a pre-release-installed app.
        """
        import firestarter as _pkg

        monkeypatch.setattr(_pkg, "__version__", "2.0.7_dev")  # prerelease
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        args = MagicMock()
        args.install = True
        args.pre = False
        args.firmware_version = None
        args.stable = True  # explicit "stay on stable"
        _maybe_auto_route_to_pre(args)
        assert args.pre is False, (
            "Explicit --stable must opt out of magic default even on beta-app install"
        )

    def test_magic_default_logs_info_line(self, monkeypatch, caplog):
        """D-25 — magic default must log INFO containing 'Beta app detected'
        and a hint about using --firmware-version X.Y.Z.

        The helper uses logging.getLogger(__name__) — pytest caplog captures
        all records automatically. No logger param is passed to the helper.

        RED today: _maybe_auto_route_to_pre does not exist — ImportError.
        """
        import firestarter as _pkg

        monkeypatch.setattr(_pkg, "__version__", "2.0.7_dev")
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        args = MagicMock()
        args.install = True
        args.pre = False
        args.firmware_version = None
        args.stable = False
        caplog.set_level(logging.INFO)
        _maybe_auto_route_to_pre(args)
        messages = [r.message for r in caplog.records]
        assert any("Beta app detected" in m for m in messages), (
            f"Expected 'Beta app detected' in log; got: {messages}"
        )
        assert any("--firmware-version" in m for m in messages), (
            f"Expected '--firmware-version' hint in log; got: {messages}"
        )


# ===========================================================================
# TestFirmwareCommandDispatch — --json without --list post-parse validation
# ===========================================================================
#
# (CLI-01..04) note: this class previously held 5
# argparse-form mutex/validator tests that imported `create_firmware_args`
# from `firestarter.main`. With the entry-point swap to Click, that argparse
# factory + its 14 sibling `create_*_args` factories are deleted outright.
# The equivalent Click-form contracts are pinned in
# `tests/test_cli_handlers.py` (W3 / Plan 41-03):
#   - test_fw_mutex_pre_and_firmware_version
#   - test_fw_mutex_stable_and_pre
#   - test_fw_mutex_firmware_version_and_stable
#   - test_fw_invalid_firmware_version
#   - (also): the Click `click.Choice` enforcement on --board renders
#     `test_argparse_accepts_uno328pb_board_choice` redundant — `click.Choice`
#     ships the contract structurally instead of via a per-value test.
# Only the sys.argv-driven `test_json_without_list_post_parse_error` survives
# here: it still pins the documented `--json requires --list` UsageError
# contract end-to-end through the Click entry point (the test invokes
# `from firestarter.main import main; main()`; `main = cli` re-export keeps
# the call shape valid through D-08).


class TestFirmwareCommandDispatch:
    """INST-02 / D-14 — `--json` without `--list` is rejected at dispatch time.

    The sole surviving argparse-era test in this class: it drives the real
    `main` entry point (re-exported as `cli` per D-08) via sys.argv
    monkeypatching, so the post-parse UsageError still raises SystemExit (
    `click.UsageError`'s exit_code=2). Phase 41 D-14 narrow upgrade replaces
    the argparse `fw_parser.error(...)` form with `raise click.UsageError(...)`.
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        pass

    def test_json_without_list_post_parse_error(self, monkeypatch):
        """D-14 — --json without --list must be rejected with SystemExit.

        Uses sys.argv monkeypatching to drive the real `main` (== `cli`) entry
        point. Click's `raise click.UsageError("--json requires --list")` maps
        to SystemExit(2), matching the prior argparse `fw_parser.error(...)`
        exit code.
        """
        import sys

        monkeypatch.setattr(sys, "argv", ["firestarter", "fw", "--json"])
        from firestarter.main import main

        with pytest.raises(SystemExit):
            main()


# ===========================================================================
# TestUno328pbResolution — Phase 23 INST-01/02/03 + D-01..D-06 + revised D-10
# ===========================================================================


class TestUno328pbResolution:
    """Phase 23 — INST-01/02/03 + D-01..D-06 + D-10(revised) board-driven
    asset resolution + avrdude profile resolution + argparse allowlist for
    uno328pb-reporting devices.

    Decisions pinned: D-01..D-06 (23-CONTEXT.md), D-10 revised 2026-05-21
    (main.py --board choices widening per RESEARCH Open Q1 resolution),
    D-07 GATE-01 non-regression (existing tests untouched).

    Expected RED status pre-Wave-2:
      tests 1-3: may pass green already (v1.4 INST-04 substrate is board-
                 string-generic); they pin the contract for uno328pb.
      test 4   : FAILS with AssertionError (default 'atmega328p' branch).
      test 5   : FAILS with SystemExit (choices=['uno','leonardo'] rejects).
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        monkeypatch.delenv("FIRESTARTER_DEV_ALLOW_PRE_V12", raising=False)

    def test_uno328pb_stable_path_resolves_correct_asset(self, monkeypatch):
        """INST-01 / D-01 — fetch_release_info(channel='stable', board='uno328pb')
        returns the uno328pb_stable.hex asset URL from a 3-asset release.

        Today: may pass green already because the v1.4 INST-04 resolver is
        board-string-generic (`firestarter_{board}.hex` lookup). Pins the
        contract for the uno328pb board name.
        """
        stable_mock = mock_releases_factory([_STABLE_RELEASE_UNO328PB])
        # Override json() to return a single release dict — /releases/latest
        # returns a single object, not a list (Pitfall 4).
        stable_mock.json.return_value = _STABLE_RELEASE_UNO328PB
        monkeypatch.setattr(firmware.requests, "get", lambda url, **kw: stable_mock)
        fm = FirmwareManager(config_manager=MagicMock())
        v, url = fm.fetch_release_info(channel="stable", board="uno328pb")
        assert v == "3.0.1"
        assert "uno328pb_stable.hex" in url
        # Must NOT pick the uno or leonardo asset — board-driven resolution.
        assert "uno_stable.hex" not in url
        assert "leonardo_stable.hex" not in url

    def test_uno328pb_pre_path_resolves_highest_prerelease(self, monkeypatch):
        """INST-02 / D-01 — fetch_release_info(channel='pre', board='uno328pb')
        selects highest pre-release with uno328pb asset (rc1 > b10 > b9 by
        PEP 440, NOT lexicographic where b9 > b10 would be wrong).
        """
        releases = [
            {
                "tag_name": "3.0.1b9",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-10T10:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno328pb.hex",
                        "browser_download_url": "https://example.com/uno328pb_b9.hex",
                    }
                ],
            },
            {
                "tag_name": "3.0.1rc1",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-20T09:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno328pb.hex",
                        "browser_download_url": "https://example.com/uno328pb_rc1.hex",
                    }
                ],
            },
            {
                "tag_name": "3.0.1b10",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-15T08:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno328pb.hex",
                        "browser_download_url": "https://example.com/uno328pb_b10.hex",
                    }
                ],
            },
        ]
        mock = mock_releases_factory(releases)
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock)
        fm = FirmwareManager(config_manager=MagicMock())
        version, url = fm.fetch_release_info(channel="pre", board="uno328pb")
        # rc > b per PEP 440; b10 > b9 requires Version sort (not string sort).
        assert version == "3.0.1rc1"
        assert "uno328pb_rc1.hex" in url

    def test_uno328pb_list_releases_enumerates_correctly(self, monkeypatch):
        """INST-03 / D-01 — list_releases(board='uno328pb') returns ReleaseInfo
        entries in PEP 440 descending order with the same 5-key shape as for
        uno/leonardo. Stable 3.0.1 wins over pre-release 3.0.1b2 per PEP 440.
        """
        releases = [
            _STABLE_RELEASE_UNO328PB,  # 3.0.1 stable
            {
                "tag_name": "3.0.1b2",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-05-18T10:00:00Z",
                "assets": [
                    {
                        "name": "firestarter_uno328pb.hex",
                        "browser_download_url": "https://example.com/uno328pb_b2.hex",
                    }
                ],
            },
        ]
        mock = mock_releases_factory(releases)
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock)
        fm = FirmwareManager(config_manager=MagicMock())
        out = fm.list_releases(channel_filter="all", board="uno328pb")
        assert len(out) == 2
        # Stable 3.0.1 > pre-release 3.0.1b2 per PEP 440 descending.
        assert out[0]["version"] == "3.0.1"
        assert out[1]["version"] == "3.0.1b2"
        required_keys = {"version", "tag", "channel", "published", "asset_url"}
        for entry in out:
            assert required_keys <= entry.keys(), (
                f"Missing keys in release entry: {entry.keys()} "
                f"(expected {required_keys})"
            )
            assert "uno328pb" in entry["asset_url"]

    def test_uno328pb_avrdude_profile_resolution(self, monkeypatch, tmp_path):
        """INST-01 / D-01..D-04 + GATE-01 anti-regression anchor.

        _install_with_avrdude(board='uno328pb') must pass
        (partno='atmega328pb', programmer_id='urclock', baud_rate=115200) to
        the Avrdude(...) constructor. This is THE load-bearing anchor pinning
        the bench-validated profile triple.

        programmer_id pinned to 'urclock' (NOT 'arduino') after Phase 23
        bench validation 2026-05-21: the operator's MiniCore-flashed 328PB-Uno
        ships Urclock as its stock bootloader; 'arduino' (stk500v1) fails to
        sync. Phase 23 CONTEXT D-02's initial 'arduino' guess was the
        documented 1-line contingency point — this is the swap.

        Mocks firmware.Avrdude (not avr_tool.Avrdude) — the symbol resolved
        at firmware.py:472 is the module-level import at firmware.py:30.
        """
        captured = {}

        def _capture_init(*args, **kwargs):
            captured.update(kwargs)
            return _FakeAvrdude(*args, **kwargs)

        monkeypatch.setattr(firmware, "Avrdude", _capture_init)
        fake_hex = tmp_path / "firestarter_uno328pb.hex"
        fake_hex.write_text(":00000001FF\n")  # minimal valid Intel HEX EOF

        fm = FirmwareManager(config_manager=MagicMock())
        ok = fm._install_with_avrdude(
            hex_file_path=str(fake_hex),
            board="uno328pb",
            avrdude_path_override=None,
            avrdude_config_override=None,
            target_port="/dev/ttyACM0",
        )
        assert ok is True
        assert captured["partno"] == "atmega328pb", (
            f"Expected partno='atmega328pb' (328PB signature 0x1E 0x95 0x16); "
            f"got {captured.get('partno')!r}. The 328P partno would abort "
            f"avrdude with a signature mismatch on real silicon."
        )
        assert captured["programmer_id"] == "urclock", (
            f"Expected programmer_id='urclock' (MiniCore stock bootloader on "
            f"operator's 328PB-Uno, bench-validated 2026-05-21). "
            f"Got {captured.get('programmer_id')!r}."
        )
        assert captured["baud_rate"] == 115200

    # note: `test_argparse_accepts_uno328pb_board_choice`
    # deleted on the entry-point swap. The Click form uses
    # `@click.option("-b", "--board", type=click.Choice(["uno", "uno328pb",
    # "leonardo"]))` which structurally enforces the allowlist — the contract
    # ships in cli_handlers.py (verified by `firestarter fw --help`) without
    # a separate test.


# ---------------------------------------------------------------------------
# ERR-03 coverage lift (D-14.3)
# Adds tests for _fetch_all_releases pagination/JSON parsing + _compare_versions
# PEP 440 edge cases not pinned by the TestVersionComparator block above.
# ---------------------------------------------------------------------------


class TestFetchAllReleasesJsonParsing:
    """D-14.3 — _fetch_all_releases parses the GitHub Releases API JSON shape."""

    def test_fetch_all_releases_single_page(self, monkeypatch):
        """Returns the page's release list when no pagination header is present."""
        releases = [{"tag_name": "v3.0.0", "prerelease": False, "draft": False}]
        monkeypatch.setattr(
            firmware.requests,
            "get",
            lambda *a, **kw: mock_releases_factory(releases),
        )
        fm = FirmwareManager(config_manager=MagicMock())
        result = fm._fetch_all_releases()
        assert result == releases

    def test_fetch_all_releases_follows_pagination(self, monkeypatch):
        """Follows Link: rel='next' to assemble multi-page results."""
        page1 = [{"tag_name": "v3.0.0", "prerelease": False, "draft": False}]
        page2 = [{"tag_name": "v2.9.0", "prerelease": False, "draft": False}]
        responses = [
            mock_releases_factory(page1, next_url="https://api.example.com/page2"),
            mock_releases_factory(page2),
        ]
        call_count = {"n": 0}

        def mock_get(*_args, **_kwargs):
            r = responses[call_count["n"]]
            call_count["n"] += 1
            return r

        monkeypatch.setattr(firmware.requests, "get", mock_get)
        fm = FirmwareManager(config_manager=MagicMock())
        result = fm._fetch_all_releases()
        assert len(result) == 2
        assert result[0]["tag_name"] == "v3.0.0"
        assert result[1]["tag_name"] == "v2.9.0"

    def test_fetch_all_releases_pagination_cap_logs_truncation(
        self, monkeypatch, caplog
    ):
        """Stops at max_pages and logs an INFO message about truncation."""
        # Every page returns a Link to itself (i.e. infinite pagination)
        page = [{"tag_name": "v1.0.0", "prerelease": False, "draft": False}]

        def mock_get(*_args, **_kwargs):
            return mock_releases_factory(page, next_url="https://api.example.com/next")

        monkeypatch.setattr(firmware.requests, "get", mock_get)
        fm = FirmwareManager(config_manager=MagicMock())
        with caplog.at_level(logging.INFO, logger="FirmwareManager"):
            result = fm._fetch_all_releases(max_pages=2)
        assert len(result) == 2  # 2 pages × 1 release each
        assert any("capped" in r.message.lower() for r in caplog.records)


class TestCompareVersionsAdditionalBranches:
    """D-14.3 — additional _compare_versions branch coverage.

    The TestVersionComparator class above covers the basic cases. These tests
    add coverage for the boundary conditions specifically called out in D-14.3:
    None inputs, mixed pre/stable ordering, and the InvalidVersion fall-through.
    """

    def test_compare_versions_none_current_returns_false(self):
        """current_version_str=None returns False (cannot compare)."""
        fm = FirmwareManager(config_manager=MagicMock())
        assert fm._compare_versions(None, "3.0.0") is False

    def test_compare_versions_none_latest_returns_false(self):
        """latest_version_str=None returns False."""
        fm = FirmwareManager(config_manager=MagicMock())
        assert fm._compare_versions("3.0.0", None) is False

    def test_compare_versions_both_none_returns_false(self):
        """Both None returns False."""
        fm = FirmwareManager(config_manager=MagicMock())
        assert fm._compare_versions(None, None) is False

    def test_compare_versions_pre_lower_than_stable(self):
        """A pre-release is considered LOWER than the stable form (PEP 440)."""
        fm = FirmwareManager(config_manager=MagicMock())
        # 3.1.0b2 < 3.1.0 per PEP 440
        assert fm._compare_versions("3.1.0b2", "3.1.0") is False
        assert fm._compare_versions("3.1.0", "3.1.0b2") is True

    def test_compare_versions_rc_higher_than_beta(self):
        """rc > b > a in pre-release ordering."""
        fm = FirmwareManager(config_manager=MagicMock())
        assert fm._compare_versions("3.1.0rc1", "3.1.0b9") is True

    def test_compare_versions_returns_false_on_invalid_strings_logs_warning(
        self, caplog
    ):
        """When both strings are unparseable, returns False and logs warning."""
        fm = FirmwareManager(config_manager=MagicMock())
        with caplog.at_level(logging.WARNING, logger="FirmwareManager"):
            result = fm._compare_versions("garbage_string", "another_garbage")
        assert result is False
        # The warning text references the strings being compared.
        assert any(
            "Could not parse" in r.message or "parse" in r.message.lower()
            for r in caplog.records
        )


# Keep `pytest` referenced to avoid an unused-import lint after Phase 42's
# extension (the original Phase 18 module imports pytest unconditionally for
# the @pytest.fixture decorators used in earlier blocks).
_ = pytest


class TestDownloadFirmwareFile:
    """D-14.3 fallback — _download_firmware_file network branch coverage."""

    def test_download_success_writes_file(self, monkeypatch, tmp_path):
        """A successful download writes the streamed content to ~/.firestarter/<name>.hex."""
        monkeypatch.setattr("firestarter.firmware.HOME_PATH", str(tmp_path))
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.iter_content.return_value = iter([b"fake ", b"hex ", b"data"])
        monkeypatch.setattr(firmware.requests, "get", lambda *a, **kw: mock_resp)

        fm = FirmwareManager(config_manager=MagicMock())
        path = fm._download_firmware_file("https://example.com/firestarter_uno.hex")
        assert path is not None
        with open(path, "rb") as f:
            assert f.read() == b"fake hex data"

    def test_download_request_exception_returns_none(self, monkeypatch, tmp_path):
        """A requests.RequestException returns None instead of crashing."""
        monkeypatch.setattr("firestarter.firmware.HOME_PATH", str(tmp_path))

        def mock_get(*_args, **_kwargs):
            raise _requests.RequestException("network down")

        monkeypatch.setattr(firmware.requests, "get", mock_get)
        fm = FirmwareManager(config_manager=MagicMock())
        path = fm._download_firmware_file("https://example.com/no.hex")
        assert path is None


class TestCheckCurrentFirmware:
    """D-14.3 fallback — check_current_firmware command + handshake parsing."""

    def test_check_current_firmware_programmer_not_found(self, monkeypatch):
        """ProgrammerNotFoundError → returns (None, None, None) without raising."""
        from firestarter.exceptions import ProgrammerNotFoundError

        def mock_connect(*_args, **_kwargs):
            raise ProgrammerNotFoundError("no port")

        monkeypatch.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            mock_connect,
        )
        fm = FirmwareManager(config_manager=MagicMock())
        port, version, board = fm.check_current_firmware()
        assert port is None
        assert version is None
        assert board is None

    def test_check_current_firmware_serial_error_returns_none_tuple(self, monkeypatch):
        """SerialError → returns the all-None tuple."""
        from firestarter.exceptions import SerialError

        def mock_connect(*_args, **_kwargs):
            raise SerialError("transport broke")

        monkeypatch.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            mock_connect,
        )
        fm = FirmwareManager(config_manager=MagicMock())
        assert fm.check_current_firmware() == (None, None, None)


class TestManageFirmwareUpdate:
    """D-14.3 fallback — manage_firmware_update high-level branches."""

    def test_manage_no_port_returns_false(self, monkeypatch):
        """When neither override nor detected port is available, returns False."""
        fm = FirmwareManager(config_manager=MagicMock())
        monkeypatch.setattr(
            fm, "check_current_firmware", lambda **kw: (None, None, None)
        )
        # No port_override, no detected port → returns False.
        result = fm.manage_firmware_update(install_flag=True)
        assert result is False

    def test_manage_already_up_to_date_returns_true(self, monkeypatch):
        """When current_version >= latest_version (and not --force), returns True."""
        fm = FirmwareManager(config_manager=MagicMock())
        monkeypatch.setattr(
            fm, "check_current_firmware", lambda **kw: ("/dev/ttyACM0", "3.1.0", "uno")
        )
        monkeypatch.setattr(
            fm,
            "fetch_release_info",
            lambda channel="stable", version=None, board="uno": (
                "3.1.0",
                "https://example.com/firestarter_uno.hex",
            ),
        )
        result = fm.manage_firmware_update(install_flag=True)
        assert result is True

    def test_manage_no_current_no_install_intent_returns_false(self, monkeypatch):
        """No current version + no --install + no --force → returns False."""
        fm = FirmwareManager(config_manager=MagicMock())
        monkeypatch.setattr(
            fm, "check_current_firmware", lambda **kw: ("/dev/ttyACM0", None, "uno")
        )
        monkeypatch.setattr(
            fm,
            "fetch_release_info",
            lambda channel="stable", version=None, board="uno": (
                "3.1.0",
                "https://example.com/firestarter_uno.hex",
            ),
        )
        # install_flag=False + no FLAG_FORCE in flags=0 → returns False
        result = fm.manage_firmware_update(install_flag=False, flags=0)
        assert result is False
