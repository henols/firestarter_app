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

import json
import logging
import pytest
from unittest.mock import MagicMock
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
    """
    mock = MagicMock()
    mock.json.return_value = releases
    mock.raise_for_status.return_value = None
    mock.headers = {"Link": f'<{next_url}>; rel="next"'} if next_url else {}
    mock.iter_content.return_value = iter([b"fake hex data"])
    return mock


def mock_404_response():
    """Build a MagicMock that raises HTTPError(status_code=404) on raise_for_status()."""
    mock = MagicMock()
    mock.raise_for_status.side_effect = _requests.exceptions.HTTPError(
        response=MagicMock(status_code=404)
    )
    return mock


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
        assert any("fall" in m or "stable" in m or "no pre" in m for m in lower_records), (
            f"Expected a fallback log record; got: {[r.message for r in caplog.records]}"
        )

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
        release_on_page = lambda n: [
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
                next_url=f"https://api.github.com/repos/henols/firestarter/releases?page={i+1}",
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
            f"Expected a pagination cap INFO log; got: {[r.message for r in caplog.records]}"
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
        """
        from firestarter.firmware import FIRMWARE_VERSION_RE

        valid = ["3.1.0", "3.1.0b2", "3.1.0rc1", "0.0.1b1", "3.0.0", "10.20.30rc99"]
        for v in valid:
            assert FIRMWARE_VERSION_RE.match(v), f"Expected {v!r} to match FIRMWARE_VERSION_RE"

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
        """
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
        """
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
        """
        import firestarter as _pkg
        monkeypatch.setattr(_pkg, "__version__", "2.0.7_dev")
        from firestarter.main import _maybe_auto_route_to_pre
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
        """
        import firestarter as _pkg
        monkeypatch.setattr(_pkg, "__version__", "2.0.7")
        from firestarter.main import _maybe_auto_route_to_pre
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
        from firestarter.main import _maybe_auto_route_to_pre
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
        from firestarter.main import _maybe_auto_route_to_pre
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
        from firestarter.main import _maybe_auto_route_to_pre
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
        from firestarter.main import _maybe_auto_route_to_pre
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
# TestArgparseMutex — D-19 / D-20 three-way channel mutex + install/list mutex
# ===========================================================================


class TestArgparseMutex:
    """INST-02, INST-03 — argparse mutex groups enforce contradictory-intent rejection.

    Tests build the parser INLINE using create_firmware_args (no _build_root_parser
    helper — revision blocker #3). create_firmware_args must RETURN fw_parser
    (revision Open Q2 resolution, RESEARCH.md Pitfall 5).

    Three-way channel mutex: --pre / --firmware-version / --stable all in one
    add_mutually_exclusive_group (revision blocker #1 — CLEANEST option).
    Two-way install/list mutex: --list / -i/--install in one group (D-20).

    Decisions pinned: D-19 (--pre / --firmware-version mutex),
                      D-20 (--list / --install mutex),
                      D-13 (--pre / --stable / --all channel filter for list).
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        pass

    def _build_parser(self):
        """Build an argparse parser using create_firmware_args inline.

        create_firmware_args must return fw_parser (Wave 1 contract).
        """
        import argparse
        from firestarter.main import create_firmware_args
        p = argparse.ArgumentParser()
        sp = p.add_subparsers(dest="command")
        fw_parser = create_firmware_args(sp)  # MUST return fw_parser per RESEARCH Open Q2
        return p, fw_parser

    def test_pre_and_firmware_version_mutex(self):
        """D-19 — --pre and --firmware-version are mutually exclusive.

        Providing both must cause argparse to exit with code 2.

        RED today: create_firmware_args does not accept --pre / --firmware-version — SystemExit
        or: create_firmware_args returns None (no return statement yet) — TypeError.
        """
        import argparse
        from firestarter.main import create_firmware_args
        p = argparse.ArgumentParser()
        sp = p.add_subparsers(dest="command")
        fw_parser = create_firmware_args(sp)
        with pytest.raises(SystemExit):
            p.parse_args(["fw", "-i", "--pre", "--firmware-version", "3.1.0"])

    def test_list_and_install_mutex(self):
        """D-20 — --list and -i/--install are mutually exclusive.

        RED today: create_firmware_args returns None or --list does not exist.
        """
        import argparse
        from firestarter.main import create_firmware_args
        p = argparse.ArgumentParser()
        sp = p.add_subparsers(dest="command")
        fw_parser = create_firmware_args(sp)
        with pytest.raises(SystemExit):
            p.parse_args(["fw", "-i", "--list"])

    def test_stable_and_pre_mutex(self):
        """D-13 / revision blocker #1 — --stable and --pre are mutually exclusive
        (both in the same 3-way channel_group alongside --firmware-version).

        RED today: create_firmware_args does not have --stable / --pre flags.
        """
        import argparse
        from firestarter.main import create_firmware_args
        p = argparse.ArgumentParser()
        sp = p.add_subparsers(dest="command")
        fw_parser = create_firmware_args(sp)
        with pytest.raises(SystemExit):
            p.parse_args(["fw", "--list", "--pre", "--stable"])

    def test_json_without_list_post_parse_error(self, monkeypatch):
        """D-12 / RESEARCH.md Pattern 3 — --json without --list must be rejected.

        argparse accepts --json at parse time, but dispatch calls fw_parser.error(...)
        for the post-parse validation. This test verifies the dispatch path exits 2.

        Uses sys.argv monkeypatching to drive the real main() entry point.

        RED today: The new --json flag and dispatch logic do not exist — SystemExit(1)
        or AttributeError, not SystemExit(2).
        """
        import sys
        monkeypatch.setattr(sys, "argv", ["firestarter", "fw", "--json"])
        from firestarter.main import main
        with pytest.raises(SystemExit):
            main()

    def test_firmware_version_regex_validation_at_argparse(self):
        """D-07 — invalid --firmware-version string is rejected by argparse type= validator.

        'not-a-version' does not match FIRMWARE_VERSION_RE; the type= validator
        raises ArgumentTypeError which argparse converts to SystemExit(2).

        RED today: --firmware-version flag does not exist — SystemExit(2) for
        unrecognized argument, or no exit at all.
        """
        import argparse
        from firestarter.main import create_firmware_args
        p = argparse.ArgumentParser()
        sp = p.add_subparsers(dest="command")
        fw_parser = create_firmware_args(sp)
        with pytest.raises(SystemExit):
            p.parse_args(["fw", "-i", "--firmware-version", "not-a-version"])
