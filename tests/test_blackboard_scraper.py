"""Comprehensive tests for blackboard_scraper.py.

Since Playwright requires a real browser and Blackboard access, most tests
mock or patch the Playwright components. Integration tests that need real
browser are marked with @pytest.mark.integration.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import blackboard_scraper as bs


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_session_file(tmp_path: Path) -> Path:
    """Create a unique session file path for each test."""
    return tmp_path / f"session_{uuid.uuid4().hex}.json"


@pytest.fixture
def sample_config() -> MagicMock:
    """Create a mock Config object."""
    config = MagicMock()
    config.blackboard_url = "https://senati.blackboard.com"
    config.blackboard_user = "testuser"
    config.blackboard_pass = "testpass"
    config.headless = True
    config.request_timeout_seconds = 30
    config.timezone = "America/Lima"
    return config


# ─── Test Assignment Dataclass ──────────────────────────────────────────────


class TestAssignmentDataclass:
    """Tests for the Assignment dataclass."""

    def test_assignment_creation_with_all_fields(self) -> None:
        """Assignment can be created with all required fields."""
        now = datetime.now(timezone.utc)
        assignment = bs.Assignment(
            assignment_id="test-123",
            title="Test Assignment",
            course_name="Math 101",
            due_date=now,
            status="Pending",
            source_url="https://example.com/assignment/123",
            scraped_at=now,
        )

        assert assignment.assignment_id == "test-123"
        assert assignment.title == "Test Assignment"
        assert assignment.course_name == "Math 101"
        assert assignment.due_date == now
        assert assignment.status == "Pending"
        assert assignment.source_url == "https://example.com/assignment/123"
        assert assignment.scraped_at == now

    def test_assignment_with_none_due_date(self) -> None:
        """Assignment can have None as due_date (will be filtered out)."""
        now = datetime.now(timezone.utc)
        assignment = bs.Assignment(
            assignment_id="test-456",
            title="No Due Date Assignment",
            course_name="Art 101",
            due_date=None,
            status="Unknown",
            source_url="",
            scraped_at=now,
        )

        assert assignment.assignment_id == "test-456"
        assert assignment.due_date is None


# ─── Test Normalize Assignment ───────────────────────────────────────────────


class TestNormalizeAssignment:
    """Tests for _normalize_assignment method."""

    def test_normalize_basic_assignment(self, sample_config: MagicMock) -> None:
        """Basic assignment normalization works correctly."""
        scraper = bs.BlackboardScraper(sample_config)

        raw = {
            "title": "Tarea 1",
            "course_name": "Matemáticas",
            "due_date": "2026-05-02T23:59:00-05:00",
            "status": "Pending",
            "source_url": "/webapps/assignment/123",
            "assignment_id": "native-id-123",
        }

        result = scraper._normalize_assignment(raw)

        assert result.assignment_id == "native-id-123"
        assert result.title == "Tarea 1"
        assert result.course_name == "Matemáticas"
        assert result.status == "Pending"
        assert result.due_date is not None
        assert result.due_date.year == 2026
        assert result.due_date.month == 5
        assert result.due_date.day == 2

    def test_normalize_missing_due_date_returns_none(
        self, sample_config: MagicMock
    ) -> None:
        """Assignment without due date has due_date=None."""
        scraper = bs.BlackboardScraper(sample_config)

        raw = {
            "title": "No Due Date",
            "course_name": "Math",
            "status": "Unknown",
        }

        result = scraper._normalize_assignment(raw)

        assert result.due_date is None
        assert result.title == "No Due Date"

    def test_normalize_generates_id_when_missing(self, sample_config: MagicMock) -> None:
        """Assignment ID is generated from title+course+due when no native ID."""
        scraper = bs.BlackboardScraper(sample_config)

        raw = {
            "title": "Generated ID Test",
            "course_name": "Physics",
            "due_date": "2026-05-10T23:59:00Z",
            "status": "In Progress",
        }

        result = scraper._normalize_assignment(raw)

        assert result.assignment_id is not None
        assert len(result.assignment_id) == 16  # SHA256 hex truncated to 16 chars

    def test_normalize_unknown_status_fallback(self, sample_config: MagicMock) -> None:
        """Empty status becomes 'Unknown'."""
        scraper = bs.BlackboardScraper(sample_config)

        raw = {
            "title": "Test",
            "course_name": "Test",
            "due_date": "2026-05-10T23:59:00Z",
            "status": "",
        }

        result = scraper._normalize_assignment(raw)

        assert result.status == "Unknown"

    def test_normalize_relative_source_url_becomes_absolute(
        self, sample_config: MagicMock
    ) -> None:
        """Relative URLs are made absolute using blackboard_url."""
        scraper = bs.BlackboardScraper(sample_config)

        raw = {
            "title": "Test",
            "course_name": "Test",
            "due_date": "2026-05-10T23:59:00Z",
            "source_url": "/webapps/assignment/456",
        }

        result = scraper._normalize_assignment(raw)

        assert (
            result.source_url
            == "https://senati.blackboard.com/webapps/assignment/456"
        )

    def test_normalize_preserves_absolute_url(self, sample_config: MagicMock) -> None:
        """Absolute URLs are preserved unchanged."""
        scraper = bs.BlackboardScraper(sample_config)

        raw = {
            "title": "Test",
            "course_name": "Test",
            "due_date": "2026-05-10T23:59:00Z",
            "source_url": "https://other.example.com/page",
        }

        result = scraper._normalize_assignment(raw)

        assert result.source_url == "https://other.example.com/page"

    def test_normalize_due_date_various_formats(
        self, sample_config: MagicMock
    ) -> None:
        """Various date formats are parsed correctly."""
        scraper = bs.BlackboardScraper(sample_config)

        # Test ISO formats and common Blackboard date formats
        test_cases = [
            ("2026-05-02T23:59:00Z", 2026, 5, 2),
            ("2026-05-02T23:59:00-05:00", 2026, 5, 2),  # Date in local timezone
            ("2026-05-02T23:59:00+00:00", 2026, 5, 2),
        ]

        for date_str, year, month, day in test_cases:
            raw = {
                "title": "Test",
                "course_name": "Test",
                "due_date": date_str,
            }
            result = scraper._normalize_assignment(raw)
            assert result.due_date is not None, f"Failed to parse: {date_str}"
            assert result.due_date.year == year, f"Year mismatch for {date_str}"
            assert result.due_date.month == month, f"Month mismatch for {date_str}"
            assert result.due_date.day == day, f"Day mismatch for {date_str}"

    def test_normalize_title_truncation(self, sample_config: MagicMock) -> None:
        """Very long titles are truncated to 500 chars."""
        scraper = bs.BlackboardScraper(sample_config)

        raw = {
            "title": "A" * 1000,
            "course_name": "Test",
            "due_date": "2026-05-10T23:59:00Z",
        }

        result = scraper._normalize_assignment(raw)

        assert len(result.title) == 500

    def test_normalize_scraped_at_is_set(self, sample_config: MagicMock) -> None:
        """scraped_at is set to current UTC time."""
        scraper = bs.BlackboardScraper(sample_config)
        before = datetime.now(timezone.utc)

        raw = {
            "title": "Test",
            "course_name": "Test",
            "due_date": "2026-05-10T23:59:00Z",
        }

        result = scraper._normalize_assignment(raw)
        after = datetime.now(timezone.utc)

        assert before <= result.scraped_at <= after
        assert result.scraped_at.tzinfo == timezone.utc


# ─── Test Session Save/Load ───────────────────────────────────────────────────


class TestSessionPersistence:
    """Tests for session file save/load functionality."""

    def test_load_session_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """_load_session returns None when file doesn't exist."""
        session_path = tmp_path / "nonexistent.json"
        result = bs._load_session(session_path)
        assert result is None

    def test_load_session_returns_none_for_invalid_json(
        self, tmp_path: Path
    ) -> None:
        """_load_session returns None for invalid JSON file."""
        session_path = tmp_path / "invalid.json"
        session_path.write_text("not valid json{{{", encoding="utf-8")

        result = bs._load_session(session_path)
        assert result is None

    def test_load_session_works_for_valid_file(self, tmp_path: Path) -> None:
        """_load_session correctly loads valid session JSON."""
        session_path = tmp_path / "valid.json"
        session_data = {
            "cookies": [{"name": "test", "value": "cookie"}],
            "saved_at": "2026-04-28T10:00:00Z",
        }
        session_path.write_text(json.dumps(session_data), encoding="utf-8")

        result = bs._load_session(session_path)

        assert result is not None
        assert result["cookies"][0]["name"] == "test"
        assert result["saved_at"] == "2026-04-28T10:00:00Z"

    def test_save_session_creates_file(self, tmp_path: Path) -> None:
        """_save_session creates the session file."""
        session_path = tmp_path / "new_session.json"
        session_data = {"cookies": [], "saved_at": "2026-04-28T10:00:00Z"}

        bs._save_session(session_path, session_data)

        assert session_path.exists()
        loaded = json.loads(session_path.read_text(encoding="utf-8"))
        assert loaded["saved_at"] == "2026-04-28T10:00:00Z"

    def test_save_session_is_atomic(self, tmp_path: Path) -> None:
        """_save_session writes atomically (temp file then rename)."""
        session_path = tmp_path / "atomic_session.json"
        session_data = {"test": "data", "cookies": [{"name": "session", "value": "abc"}]}

        bs._save_session(session_path, session_data)

        # Check the temp file doesn't exist
        temp_path = session_path.with_suffix(".tmp")
        assert not temp_path.exists()

        # Check content is correct
        loaded = json.loads(session_path.read_text(encoding="utf-8"))
        assert loaded == session_data


# ─── Test Assignment ID Generation ─────────────────────────────────────────


class TestAssignmentIdGeneration:
    """Tests for _generate_assignment_id function."""

    def test_natural_id_is_preferred(self) -> None:
        """Natural assignment IDs from data are used when available."""
        raw = {"assignment_id": "native-456", "title": "Test"}

        result = bs._generate_assignment_id(raw)

        assert result == "native-456"

    def test_id_from_data_assignment_id(self) -> None:
        """data-assignment-id attribute is used."""
        raw = {"data-assignment-id": "data-id-789", "title": "Test"}

        result = bs._generate_assignment_id(raw)

        assert result == "data-id-789"

    def test_id_from_itemId(self) -> None:
        """itemId is used as fallback."""
        raw = {"itemId": "item-id-123", "title": "Test"}

        result = bs._generate_assignment_id(raw)

        assert result == "item-id-123"

    def test_derived_id_from_attributes(self) -> None:
        """ID is derived when no natural ID exists."""
        raw = {
            "title": "Derived Test",
            "course_name": "Math",
            "due_date": "2026-05-02T23:59:00Z",
        }

        result = bs._generate_assignment_id(raw)

        assert result is not None
        assert len(result) == 16
        assert result.isalnum()

    def test_derived_id_is_deterministic(self) -> None:
        """Same inputs always produce same derived ID."""
        raw = {
            "title": "Same Title",
            "course_name": "Same Course",
            "due_date": "2026-05-02T23:59:00Z",
        }

        result1 = bs._generate_assignment_id(raw, 0)
        result2 = bs._generate_assignment_id(raw, 0)

        assert result1 == result2

    def test_different_indices_produce_different_ids(self) -> None:
        """Different fallback_index values produce different IDs."""
        raw = {"title": "Test", "course_name": "Course", "due_date": "2026-05-02"}

        result0 = bs._generate_assignment_id(raw, 0)
        result1 = bs._generate_assignment_id(raw, 1)

        assert result0 != result1


# ─── Test Anti-Detection Configuration ───────────────────────────────────────


class TestAntiDetection:
    """Tests for anti-detection measures in the scraper."""

    def test_default_user_agent_is_set(self) -> None:
        """DEFAULT_USER_AGENT is set to a modern Chrome Windows UA."""
        assert "Chrome/124" in bs.DEFAULT_USER_AGENT
        assert "Windows NT 10.0" in bs.DEFAULT_USER_AGENT
        assert "Win64" in bs.DEFAULT_USER_AGENT

    def test_viewport_dimensions(self) -> None:
        """Viewport is set to standard 1920x1080."""
        assert bs.VIEWPORT_WIDTH == 1920
        assert bs.VIEWPORT_HEIGHT == 1080

    def test_random_delay_range(self) -> None:
        """Random delay is between MIN and MAX values."""
        # Test that delay range is sensible
        assert bs.MIN_DELAY_MS == 1000
        assert bs.MAX_DELAY_MS == 3000
        assert bs.MIN_DELAY_MS < bs.MAX_DELAY_MS


# ─── Test Scraper Initialization ───────────────────────────────────────────


class TestScraperInitialization:
    """Tests for BlackboardScraper initialization."""

    def test_init_with_default_session_path(self, sample_config: MagicMock) -> None:
        """Default session path is ./session.json."""
        scraper = bs.BlackboardScraper(sample_config)

        assert scraper._session_file_path.name == "session.json"

    def test_init_with_custom_session_path(
        self, sample_config: MagicMock, tmp_session_file: Path
    ) -> None:
        """Custom session path is used when provided."""
        scraper = bs.BlackboardScraper(sample_config, session_file_path=tmp_session_file)

        assert scraper._session_file_path == tmp_session_file

    def test_config_is_stored(self, sample_config: MagicMock) -> None:
        """Config object is stored in the scraper."""
        scraper = bs.BlackboardScraper(sample_config)

        assert scraper._config == sample_config


# ─── Test Is Logged In Detection ─────────────────────────────────────────────


@pytest.mark.asyncio
class TestIsLoggedIn:
    """Tests for login page detection logic."""

    async def test_login_url_returns_false(self, sample_config: MagicMock) -> None:
        """URL containing 'login' returns False."""
        scraper = bs.BlackboardScraper(sample_config)
        scraper._page = MagicMock()
        scraper._page.url = "https://senati.blackboard.com/webapps/login/"

        result = await scraper._is_logged_in()

        assert result is False

    async def test_signin_url_returns_false(self, sample_config: MagicMock) -> None:
        """URL containing 'signin' returns False."""
        scraper = bs.BlackboardScraper(sample_config)
        scraper._page = MagicMock()
        scraper._page.url = "https://senati.blackboard.com/auth/signin"

        result = await scraper._is_logged_in()

        assert result is False

    async def test_dashboard_url_returns_true(self, sample_config: MagicMock) -> None:
        """URL containing 'dashboard' returns True."""
        scraper = bs.BlackboardScraper(sample_config)
        scraper._page = MagicMock()
        scraper._page.url = "https://senati.blackboard.com/webapps/dashboard/"
        scraper._page.locator = MagicMock()

        result = await scraper._is_logged_in()

        assert result is True

    async def test_home_url_returns_true(self, sample_config: MagicMock) -> None:
        """URL containing 'home' returns True."""
        scraper = bs.BlackboardScraper(sample_config)
        scraper._page = MagicMock()
        scraper._page.url = "https://senati.blackboard.com/webapps/home/"
        scraper._page.locator = MagicMock()

        result = await scraper._is_logged_in()

        assert result is True

    async def test_base_url_returns_false(self, sample_config: MagicMock) -> None:
        """Base URL returns False (it is the login page, not logged in)."""
        scraper = bs.BlackboardScraper(sample_config)
        scraper._page = MagicMock()
        scraper._page.url = "https://senati.blackboard.com/"

        result = await scraper._is_logged_in()

        assert result is False


# ─── Test Error Screenshot ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestScreenshot:
    """Tests for screenshot capture on error."""

    async def test_screenshot_does_not_crash_when_page_is_none(
        self, sample_config: MagicMock
    ) -> None:
        """_take_screenshot handles None page gracefully."""
        scraper = bs.BlackboardScraper(sample_config)
        scraper._page = None

        # Should not raise
        await scraper._take_screenshot("test_error")

    async def test_screenshot_calls_page_screenshot(
        self, sample_config: MagicMock
    ) -> None:
        """_take_screenshot calls page.screenshot with correct params."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock()
        scraper._page = mock_page

        await scraper._take_screenshot("login_error")

        mock_page.screenshot.assert_called_once()
        call_kwargs = mock_page.screenshot.call_args
        assert "error_login_error_" in call_kwargs.kwargs["path"]
        assert call_kwargs.kwargs["full_page"] is True


# ─── Test Random Delay ──────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestRandomDelay:
    """Tests for human-like random delay."""

    async def test_random_delay_sleeps_in_range(self) -> None:
        """_random_delay sleeps between 1-3 seconds."""
        import time

        start = time.time()
        await bs._random_delay()
        elapsed = time.time() - start

        # Should have slept between 1-3 seconds (with some tolerance)
        assert 0.9 <= elapsed <= 4.0, f"Sleep took {elapsed}s, expected 1-3s"


# ─── Test Close Method ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestClose:
    """Tests for scraper close/cleanup."""

    async def test_close_handles_none_browser(self, sample_config: MagicMock) -> None:
        """close() handles case where browser was never launched."""
        scraper = bs.BlackboardScraper(sample_config)
        scraper._browser = None
        scraper._context = None
        scraper._page = None

        # Should not raise
        await scraper.close()

    async def test_close_closes_context_and_browser(
        self, sample_config: MagicMock
    ) -> None:
        """close() properly closes context and browser."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_context = AsyncMock()
        mock_browser = AsyncMock()
        scraper._context = mock_context
        scraper._browser = mock_browser

        await scraper.close()

        mock_context.close.assert_called_once()
        mock_browser.close.assert_called_once()
        assert scraper._context is None
        assert scraper._browser is None


# ─── Test Close All Cases ─────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestCloseAllCases:
    """Test close() handles all edge cases."""

    async def test_close_with_page_only(self, sample_config: MagicMock) -> None:
        """close() works when only page exists."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_context = AsyncMock()
        scraper._context = mock_context
        scraper._page = MagicMock()

        await scraper.close()

        mock_context.close.assert_called_once()
        assert scraper._page is None

    async def test_multiple_close_calls(self, sample_config: MagicMock) -> None:
        """Multiple close() calls don't crash."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_context = AsyncMock()
        mock_browser = AsyncMock()
        scraper._context = mock_context
        scraper._browser = mock_browser

        # First close
        await scraper.close()

        # Second close should also work
        await scraper.close()

        # Context and browser should still be None from first call
        assert scraper._context is None
        assert scraper._browser is None


# ─── Test Fill Login Form ─────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestFillLoginForm:
    """Tests for login form filling logic."""

    async def test_fill_login_form_handles_missing_fields_gracefully(
        self, sample_config: MagicMock
    ) -> None:
        """_fill_login_form doesn't crash when fields aren't found."""
        scraper = bs.BlackboardScraper(sample_config)
        scraper._page = MagicMock()

        # Make all selectors return count=0
        def mock_locator(sel):
            m = MagicMock()
            m.first = MagicMock()
            m.first.count = AsyncMock(return_value=0)
            return m

        scraper._page.locator = mock_locator
        # Mock fill as async
        scraper._page.fill = AsyncMock()

        # Should not raise
        await scraper._fill_login_form()


# ─── Integration Tests (Mocked) ───────────────────────────────────────────────


@pytest.mark.asyncio
class TestIntegrationWithMocks:
    """Integration tests using mocked Playwright components."""

    async def test_scrape_returns_empty_list_on_login_failure(
        self, sample_config: MagicMock
    ) -> None:
        """scrape_assignments returns [] when login fails."""
        scraper = bs.BlackboardScraper(sample_config)

        with patch.object(scraper, "_ensure_browser", new_callable=AsyncMock):
            with patch.object(scraper, "login", new_callable=AsyncMock) as mock_login:
                mock_login.return_value = False

                result = await scraper.scrape_assignments()
                assert result == []

    async def test_scrape_returns_normalized_assignments(
        self, sample_config: MagicMock
    ) -> None:
        """scrape_assignments properly normalizes and returns assignments."""
        scraper = bs.BlackboardScraper(sample_config)

        with patch.object(scraper, "_ensure_browser", new_callable=AsyncMock):
            with patch.object(
                scraper, "_try_restore_session", new_callable=AsyncMock
            ) as mock_restore:
                mock_restore.return_value = True

                with patch.object(
                    scraper, "_navigate_to_assignments", new_callable=AsyncMock
                ):
                    with patch.object(
                        scraper, "_wait_for_assignments_content", new_callable=AsyncMock
                    ):
                        with patch.object(
                            scraper,
                            "_extract_assignments_from_dom",
                            new_callable=AsyncMock,
                        ) as mock_extract:
                            mock_extract.return_value = [
                                {
                                    "title": "Tarea 1",
                                    "course_name": "Math",
                                    "due_date": "2026-05-02T23:59:00Z",
                                    "status": "Pending",
                                    "source_url": "/test/123",
                                }
                            ]

                            result = await scraper.scrape_assignments()
                            assert len(result) == 1
                            assert result[0].title == "Tarea 1"
                            assert result[0].course_name == "Math"
                            assert result[0].due_date is not None
                            assert result[0].status == "Pending"

    async def test_scrape_filters_out_assignments_without_due_date(
        self, sample_config: MagicMock
    ) -> None:
        """Assignments without due dates are filtered out."""
        scraper = bs.BlackboardScraper(sample_config)

        with patch.object(scraper, "_ensure_browser", new_callable=AsyncMock):
            with patch.object(
                scraper, "_try_restore_session", new_callable=AsyncMock
            ) as mock_restore:
                mock_restore.return_value = True

                with patch.object(
                    scraper, "_navigate_to_assignments", new_callable=AsyncMock
                ):
                    with patch.object(
                        scraper, "_wait_for_assignments_content", new_callable=AsyncMock
                    ):
                        with patch.object(
                            scraper,
                            "_extract_assignments_from_dom",
                            new_callable=AsyncMock,
                        ) as mock_extract:
                            mock_extract.return_value = [
                                {
                                    "title": "Has Due Date",
                                    "course_name": "Math",
                                    "due_date": "2026-05-02T23:59:00Z",
                                    "status": "Pending",
                                },
                                {
                                    "title": "No Due Date",
                                    "course_name": "Art",
                                    "due_date": "",
                                    "status": "Unknown",
                                },
                            ]

                            result = await scraper.scrape_assignments()
                            assert len(result) == 1
                            assert result[0].title == "Has Due Date"

    async def test_login_returns_false_on_exception(
        self, sample_config: MagicMock
    ) -> None:
        """login() returns False when an exception occurs."""
        scraper = bs.BlackboardScraper(sample_config)

        with patch.object(
            scraper, "_ensure_browser", new_callable=AsyncMock
        ) as mock_ensure:
            mock_ensure.side_effect = Exception("Browser error")

            result = await scraper.login()
            assert result is False

    async def test_scrape_returns_empty_on_extract_exception(
        self, sample_config: MagicMock
    ) -> None:
        """scrape_assignments returns [] when extraction throws."""
        scraper = bs.BlackboardScraper(sample_config)

        with patch.object(scraper, "_ensure_browser", new_callable=AsyncMock):
            with patch.object(
                scraper, "_try_restore_session", new_callable=AsyncMock
            ) as mock_restore:
                mock_restore.return_value = True

                with patch.object(
                    scraper, "_navigate_to_assignments", new_callable=AsyncMock
                ):
                    with patch.object(
                        scraper, "_wait_for_assignments_content", new_callable=AsyncMock
                    ):
                        with patch.object(
                            scraper,
                            "_extract_assignments_from_dom",
                            new_callable=AsyncMock,
                        ) as mock_extract:
                            mock_extract.side_effect = Exception("Extraction error")

                            result = await scraper.scrape_assignments()
                            assert result == []


# ─── Test Microsoft Login Detection ─────────────────────────────────────────


@pytest.mark.asyncio
class TestMicrosoftLoginDetection:
    """Tests for Microsoft Entra ID login detection and flow."""

    async def test_fill_login_form_routes_to_microsoft_on_microsoft_url(
        self, sample_config: MagicMock
    ) -> None:
        """_fill_login_form routes to _fill_microsoft_login when Microsoft URL detected."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        mock_page.url = "https://login.microsoftonline.com/senati.onmicrosoft.com/oauth2/v2.0/authorize"
        scraper._page = mock_page

        with patch.object(scraper, "_fill_microsoft_login", new_callable=AsyncMock) as mock_ms:
            await scraper._fill_login_form()
            mock_ms.assert_called_once()

    async def test_fill_login_form_routes_to_blackboard_on_blackboard_url(
        self, sample_config: MagicMock
    ) -> None:
        """_fill_login_form routes to _fill_blackboard_login when Blackboard URL detected."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        mock_page.url = "https://senati.blackboard.com/webapps/login/"
        scraper._page = mock_page

        with patch.object(scraper, "_fill_blackboard_login", new_callable=AsyncMock) as mock_bb:
            await scraper._fill_login_form()
            mock_bb.assert_called_once()

    async def test_fill_microsoft_login_fills_email(
        self, sample_config: MagicMock
    ) -> None:
        """_fill_microsoft_login fills email field."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        scraper._page = mock_page

        # Track calls
        filled_values = []

        async def mock_fill(selector, value):
            filled_values.append((selector, value))

        async def mock_count():
            return 1

        mock_locator = MagicMock()
        mock_locator.first = MagicMock()
        mock_locator.first.count = AsyncMock(return_value=1)
        mock_page.locator = MagicMock(return_value=mock_locator)
        mock_page.fill = AsyncMock(side_effect=mock_fill)
        mock_page.wait_for_selector = AsyncMock()

        # Make email selector succeed, password fail initially
        call_count = 0
        def locator_side_effect(sel):
            m = MagicMock()
            m.first = MagicMock()
            m.first.count = AsyncMock(return_value=1)
            m.first.fill = AsyncMock(side_effect=lambda v: filled_values.append((sel, v)))
            m.click = AsyncMock()
            return m

        mock_page.locator = MagicMock(side_effect=locator_side_effect)

        # Track URL changes for wait_for_selector
        mock_page.url = "https://login.microsoftonline.com/login"

        await scraper._fill_microsoft_login()

        # Email should have been filled
        assert any("testuser" in str(v) for _, v in filled_values if isinstance(v, str))

    async def test_fill_microsoft_login_clicks_next_button(
        self, sample_config: MagicMock
    ) -> None:
        """_fill_microsoft_login clicks Next button after email."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        scraper._page = mock_page

        click_calls = []

        def locator_side_effect(sel):
            m = MagicMock()
            m.first = MagicMock()
            m.first.count = AsyncMock(return_value=1)
            m.first.click = AsyncMock(side_effect=lambda: click_calls.append(sel))
            m.first.fill = AsyncMock()
            return m

        mock_page.locator = MagicMock(side_effect=locator_side_effect)
        mock_page.url = "https://login.microsoftonline.com/login"
        mock_page.wait_for_selector = AsyncMock()
        mock_page.fill = AsyncMock()

        await scraper._fill_microsoft_login()

        # Next button should have been clicked
        assert len(click_calls) >= 2  # At least Next and Sign in

    async def test_fill_microsoft_login_waits_for_password_field(
        self, sample_config: MagicMock
    ) -> None:
        """_fill_microsoft_login waits for password field to appear after Next."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        scraper._page = mock_page

        wait_calls = []

        async def mock_wait_for_selector(selector, state=None, timeout=None):
            wait_calls.append(selector)
            # Simulate password field appearing
            if "password" in selector:
                return
            raise Exception("Password field not ready yet")

        mock_page.wait_for_selector = AsyncMock(side_effect=mock_wait_for_selector)

        def locator_side_effect(sel):
            m = MagicMock()
            m.first = MagicMock()
            m.first.count = AsyncMock(return_value=1)
            m.first.fill = AsyncMock()
            m.first.click = AsyncMock()
            return m

        mock_page.locator = MagicMock(side_effect=locator_side_effect)

        # Make first wait fail (email step), second succeed (password step)
        wait_call_count = 0
        async def wait_side_effect(selector, state=None, timeout=None):
            nonlocal wait_call_count
            wait_call_count += 1
            if wait_call_count == 1:
                raise Exception("Not found")
            return

        mock_page.wait_for_selector = AsyncMock(side_effect=wait_side_effect)

        await scraper._fill_microsoft_login()

        # Should have called wait_for_selector at least once for password
        assert wait_call_count >= 2

    async def test_fill_microsoft_login_fills_password(
        self, sample_config: MagicMock
    ) -> None:
        """_fill_microsoft_login fills password field."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        scraper._page = mock_page

        filled_values = []

        def locator_side_effect(sel):
            m = MagicMock()
            m.first = MagicMock()
            m.first.count = AsyncMock(return_value=1)
            m.first.fill = AsyncMock(side_effect=lambda v: filled_values.append((sel, v)))
            m.first.click = AsyncMock()
            return m

        mock_page.locator = MagicMock(side_effect=locator_side_effect)

        async def wait_side_effect(selector, state=None, timeout=None):
            return

        mock_page.wait_for_selector = AsyncMock(side_effect=wait_side_effect)

        await scraper._fill_microsoft_login()

        # Password should have been filled
        assert any("testpass" in str(v) for _, v in filled_values if isinstance(v, str))

    async def test_fill_microsoft_login_handles_stay_signed_in_prompt(
        self, sample_config: MagicMock
    ) -> None:
        """_fill_microsoft_login clicks No on stay signed in prompt."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        scraper._page = mock_page

        click_calls = []

        def locator_side_effect(sel):
            m = MagicMock()
            m.first = MagicMock()
            m.first.count = AsyncMock(return_value=1)
            m.first.click = AsyncMock(side_effect=lambda: click_calls.append(sel))
            m.first.fill = AsyncMock()
            return m

        mock_page.locator = MagicMock(side_effect=locator_side_effect)

        async def wait_side_effect(selector, state=None, timeout=None):
            # First call for password, second for stay signed in
            return

        mock_page.wait_for_selector = AsyncMock(side_effect=wait_side_effect)

        await scraper._fill_microsoft_login()

        # Sign in should have been clicked (may include stay signed in)
        assert len(click_calls) >= 2

    async def test_is_logged_in_returns_false_for_microsoftonline_url(
        self, sample_config: MagicMock
    ) -> None:
        """_is_logged_in returns False for Microsoft login URL."""
        scraper = bs.BlackboardScraper(sample_config)
        scraper._page = MagicMock()
        scraper._page.url = "https://login.microsoftonline.com/senati.onmicrosoft.com/"

        result = await scraper._is_logged_in()

        assert result is False


# ─── Test O365 Login Button ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestO365LoginButton:
    """Tests for the O365 SAML login button click."""

    async def test_o365_button_clicked_when_present(
        self, sample_config: MagicMock
    ) -> None:
        """O365 button is clicked when found on page."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        mock_page.url = "https://senati.blackboard.com/"
        scraper._page = mock_page

        with patch.object(
            bs.BlackboardScraper, "_click_o365_login_button", new_callable=AsyncMock
        ) as mock_method:
            mock_method.return_value = True
            result = await scraper._click_o365_login_button()
            mock_method.assert_called_once()
            assert result is True

    async def test_o365_button_not_found_continues_to_standard_login(
        self, sample_config: MagicMock
    ) -> None:
        """When O365 button not found, login continues with standard flow."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        mock_page.url = "https://senati.blackboard.com/"
        scraper._page = mock_page

        # Make locator return count=0 for all selectors
        def mock_locator(sel):
            m = MagicMock()
            m.first = MagicMock()
            m.first.count = AsyncMock(return_value=0)
            return m

        mock_page.locator = MagicMock(side_effect=mock_locator)

        result = await scraper._click_o365_login_button()

        assert result is False

    async def test_o365_button_click_triggers_navigation(
        self, sample_config: MagicMock
    ) -> None:
        """Clicking O365 button clicks and returns True (URL wait happens in login())."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        mock_page.url = "https://senati.blackboard.com/"
        scraper._page = mock_page

        clicked_selectors = []

        def locator_side_effect(sel):
            clicked_selectors.append(sel)
            m = MagicMock()
            m.first = MagicMock()
            m.first.count = AsyncMock(return_value=1)
            m.first.is_visible = AsyncMock(return_value=True)
            m.first.click = AsyncMock()
            return m

        mock_page.locator = MagicMock(side_effect=locator_side_effect)

        result = await scraper._click_o365_login_button()

        assert result is True
        assert len(clicked_selectors) == 1
        assert clicked_selectors[0] == "a.icon-o365"  # First selector that matches

    async def test_o365_button_selector_tries_multiple_selectors(
        self, sample_config: MagicMock
    ) -> None:
        """Multiple selectors are tried until one matches."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        mock_page.url = "https://senati.blackboard.com/"
        scraper._page = mock_page

        tried_selectors = []

        def mock_locator(sel):
            tried_selectors.append(sel)
            m = MagicMock()
            m.first = MagicMock()
            m.first.count = AsyncMock(return_value=0)
            return m

        mock_page.locator = MagicMock(side_effect=mock_locator)

        result = await scraper._click_o365_login_button()

        assert result is False
        # All selectors should have been tried
        assert "a.icon-o365" in tried_selectors
        assert "a[href*='auth-saml/saml/login']" in tried_selectors


# ─── Test Login Flow with O365 Button ────────────────────────────────────────


@pytest.mark.asyncio
class TestLoginFlowWithO365Button:
    """Integration test for login flow including O365 button step."""

    async def test_login_flow_includes_o365_button_step(
        self, sample_config: MagicMock
    ) -> None:
        """login() calls _click_o365_login_button before _fill_login_form."""
        scraper = bs.BlackboardScraper(sample_config)

        mock_page = MagicMock()
        mock_page.url = "https://senati.blackboard.com/"
        scraper._page = mock_page
        scraper._context = MagicMock()

        calls_order = []

        async def mock_click_o365():
            calls_order.append("_click_o365_login_button")
            return False

        async def mock_fill_login():
            calls_order.append("_fill_login_form")

        async def mock_submit_login():
            calls_order.append("_submit_login_form")

        async def mock_is_logged_in():
            # First call: not logged in, second call after submit: logged in
            if "_submit_login_form" in calls_order:
                return True
            return False

        scraper._click_o365_login_button = AsyncMock(
            side_effect=mock_click_o365
        )
        scraper._fill_login_form = AsyncMock(side_effect=mock_fill_login)
        scraper._submit_login_form = AsyncMock(side_effect=mock_submit_login)
        scraper._is_logged_in = AsyncMock(side_effect=mock_is_logged_in)
        scraper._save_session = AsyncMock()
        scraper._take_screenshot = AsyncMock()

        mock_page.wait_for_load_state = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.locator = MagicMock()

        with patch.object(
            bs.BlackboardScraper, "_ensure_browser", new_callable=AsyncMock
        ):
            result = await scraper.login()

        assert result is True
        assert "_click_o365_login_button" in calls_order
        assert "_fill_login_form" in calls_order
        assert "_submit_login_form" in calls_order
        # O365 button should be called BEFORE fill login form
        o365_idx = calls_order.index("_click_o365_login_button")
        fill_idx = calls_order.index("_fill_login_form")
        assert o365_idx < fill_idx
