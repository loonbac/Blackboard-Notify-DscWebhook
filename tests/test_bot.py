"""Comprehensive unit and integration tests for bot.py."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import (
    _assignment_to_dict,
    _format_time_remaining,
    get_week_boundaries,
    is_due_this_week,
    is_due_within_hours,
    setup_logging,
)


# ─── Mock datetime class ───────────────────────────────────────────────────────


class _MockDatetime:
    """A datetime lookalike that returns a fixed `now` for .now() calls.

    Used to control the "current time" in integration tests without breaking
    datetime comparisons throughout the code.
    """

    def __init__(self, fixed_now: datetime) -> None:
        self._fixed_now = fixed_now

    def now(self, tz: timezone | None = None) -> datetime:
        if tz is not None:
            return self._fixed_now.astimezone(tz)
        return self._fixed_now

    def __call__(self, *args, **kwargs) -> datetime:
        """Construction calls pass through to the real datetime."""
        return datetime(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(datetime, name)

# We import the module-level helpers to test them
from bot import (
    _assignment_to_dict,
    _format_time_remaining,
    get_week_boundaries,
    is_due_this_week,
    is_due_within_hours,
    setup_logging,
)


# ─── Test get_week_boundaries ──────────────────────────────────────────────────


class TestGetWeekBoundaries:
    """Unit tests for get_week_boundaries."""

    def test_monday_start_sunday_end_standard_week(self) -> None:
        """A mid-week date returns Monday 00:00 and Sunday 23:59:59."""
        # Wednesday 2026-04-29 14:30:00
        tz = timezone(timedelta(hours=-5))
        now = datetime(2026, 4, 29, 14, 30, 0, tzinfo=tz)
        monday_start, sunday_end = get_week_boundaries(now)

        assert monday_start == datetime(2026, 4, 27, 0, 0, 0, tzinfo=tz)
        assert sunday_end == datetime(2026, 5, 3, 23, 59, 59, 999999, tzinfo=tz)

    def test_monday_returns_monday_midnight(self) -> None:
        """A Monday date returns that same Monday 00:00 as start."""
        tz = timezone.utc
        now = datetime(2026, 4, 27, 8, 0, 0, tzinfo=tz)  # Monday
        monday_start, sunday_end = get_week_boundaries(now)

        assert monday_start == datetime(2026, 4, 27, 0, 0, 0, tzinfo=tz)
        assert sunday_end == datetime(2026, 5, 3, 23, 59, 59, 999999, tzinfo=tz)

    def test_sunday_returns_same_sunday_end(self) -> None:
        """A Sunday date returns that Sunday 23:59:59 as end."""
        tz = timezone.utc
        now = datetime(2026, 5, 3, 20, 0, 0, tzinfo=tz)  # Sunday
        monday_start, sunday_end = get_week_boundaries(now)

        assert monday_start == datetime(2026, 4, 27, 0, 0, 0, tzinfo=tz)
        assert sunday_end == datetime(2026, 5, 3, 23, 59, 59, 999999, tzinfo=tz)

    def test_week_boundary_crossing_month(self) -> None:
        """A week that crosses a month boundary is handled correctly."""
        # Wednesday May 27 2026 (week that crosses from April to May)
        tz = timezone(timedelta(hours=-5))
        now = datetime(2026, 4, 29, 14, 30, 0, tzinfo=tz)
        monday_start, sunday_end = get_week_boundaries(now)

        assert monday_start.month == 4  # April
        assert monday_start.day == 27
        assert sunday_end.month == 5  # May
        assert sunday_end.day == 3

    def test_week_boundary_crossing_year(self) -> None:
        """A week that crosses a year boundary is handled correctly."""
        # Dec 31 2026 in UTC is a Thursday.
        # The ISO week for Dec 31 2026 is week 53 of 2026.
        # The Monday of that week is Dec 28 2026.
        # The Sunday end is Jan 3 2027 (because Sunday Jan 3 2027 is the end of week 2027-W01).
        # Wait - let me recalculate. Jan 1 2027 is a Friday.
        # ISO week 1 of 2027 starts Monday Jan 3 2027.
        # So for the week containing Dec 31 2026 (Thu):
        #   Monday = Dec 28 2026 (week 53 of 2026)
        #   Sunday = Jan 3 2027 (week 1 of 2027)
        tz = timezone.utc
        now = datetime(2026, 12, 31, 10, 0, 0, tzinfo=tz)  # Thursday
        monday_start, sunday_end = get_week_boundaries(now)

        assert monday_start.year == 2026
        assert monday_start.month == 12
        assert monday_start.day == 28  # Corrected: Dec 28 2026 is the Monday
        assert sunday_end.year == 2027
        assert sunday_end.month == 1
        assert sunday_end.day == 3

    def test_microseconds_preserved_in_sunday_end(self) -> None:
        """Sunday end includes max microseconds to cover the full day."""
        tz = timezone.utc
        now = datetime(2026, 4, 29, 14, 30, 0, tzinfo=tz)
        _monday_start, sunday_end = get_week_boundaries(now)

        assert sunday_end.microsecond == 999999


# ─── Test is_due_this_week ────────────────────────────────────────────────────


class TestIsDueThisWeek:
    """Unit tests for is_due_this_week."""

    def _tz(self) -> timezone:
        return timezone(timedelta(hours=-5))  # America/Lima offset

    def test_assignment_due_monday_returns_true(self) -> None:
        """An assignment due Monday is within the week."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 14, 30, 0, tzinfo=tz)  # Wednesday
        due = datetime(2026, 4, 27, 23, 59, 0, tzinfo=tz)  # Monday 23:59
        assert is_due_this_week(due, now) is True

    def test_assignment_due_sunday_returns_true(self) -> None:
        """An assignment due Sunday is within the week."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 14, 30, 0, tzinfo=tz)  # Wednesday
        due = datetime(2026, 5, 3, 23, 59, 0, tzinfo=tz)  # Sunday 23:59
        assert is_due_this_week(due, now) is True

    def test_assignment_due_next_monday_returns_false(self) -> None:
        """An assignment due the following Monday is OUTSIDE the week."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 14, 30, 0, tzinfo=tz)  # Wednesday
        due = datetime(2026, 5, 4, 23, 59, 0, tzinfo=tz)  # Next Monday
        assert is_due_this_week(due, now) is False

    def test_assignment_due_last_sunday_returns_false(self) -> None:
        """An assignment due last Sunday is OUTSIDE the week."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 14, 30, 0, tzinfo=tz)  # Wednesday
        due = datetime(2026, 4, 26, 23, 59, 0, tzinfo=tz)  # Last Sunday
        assert is_due_this_week(due, now) is False

    def test_assignment_due_wednesday_returns_true(self) -> None:
        """An assignment due Wednesday of the same week returns True."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)  # Wednesday morning
        due = datetime(2026, 4, 29, 23, 59, 0, tzinfo=tz)  # Wednesday evening
        assert is_due_this_week(due, now) is True

    def test_assignment_due_at_monday_midnight_returns_true(self) -> None:
        """An assignment due exactly at Monday 00:00 returns True."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 14, 30, 0, tzinfo=tz)
        due = datetime(2026, 4, 27, 0, 0, 0, tzinfo=tz)
        assert is_due_this_week(due, now) is True

    def test_assignment_due_at_sunday_235959_returns_true(self) -> None:
        """An assignment due exactly at Sunday 23:59:59 returns True."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 14, 30, 0, tzinfo=tz)
        due = datetime(2026, 5, 3, 23, 59, 59, tzinfo=tz)
        assert is_due_this_week(due, now) is True


# ─── Test is_due_within_hours ─────────────────────────────────────────────────


class TestIsDueWithinHours:
    """Unit tests for is_due_within_hours."""

    def _tz(self) -> timezone:
        return timezone(timedelta(hours=-5))

    def test_assignment_due_in_12_hours_returns_true(self) -> None:
        """An assignment due in 12 hours is within the 24h window."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 4, 29, 20, 0, 0, tzinfo=tz)  # 12h later
        assert is_due_within_hours(due, now, 24) is True

    def test_assignment_due_in_30_hours_returns_false(self) -> None:
        """An assignment due in 30 hours is outside the 24h window."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 4, 30, 14, 0, 0, tzinfo=tz)  # 30h later
        assert is_due_within_hours(due, now, 24) is False

    def test_assignment_due_in_past_returns_false(self) -> None:
        """A past-due assignment returns False."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 4, 29, 6, 0, 0, tzinfo=tz)  # 2h ago
        assert is_due_within_hours(due, now, 24) is False

    def test_assignment_due_exactly_at_boundary_returns_true(self) -> None:
        """An assignment due exactly at the boundary (now + 24h) returns True."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 4, 30, 8, 0, 0, tzinfo=tz)  # exactly 24h later
        assert is_due_within_hours(due, now, 24) is True

    def test_assignment_due_just_before_now_returns_false(self) -> None:
        """An assignment due 1 second ago returns False."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 4, 29, 7, 59, 59, tzinfo=tz)  # 1 second before
        assert is_due_within_hours(due, now, 24) is False

    def test_assignment_due_1_hour_returns_true(self) -> None:
        """An assignment due in 1 hour is within the 24h window."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 4, 29, 9, 0, 0, tzinfo=tz)  # 1h later
        assert is_due_within_hours(due, now, 24) is True

    def test_assignment_due_48_hours_with_48h_threshold_returns_true(self) -> None:
        """With a 48h threshold, an assignment due in 48h is True."""
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 5, 1, 8, 0, 0, tzinfo=tz)  # 48h later
        assert is_due_within_hours(due, now, 48) is True


# ─── Test _assignment_to_dict ─────────────────────────────────────────────────


class TestAssignmentToDict:
    """Unit tests for _assignment_to_dict."""

    def test_converts_assignment_to_dict(self) -> None:
        """The dict should have all required keys with ISO date string."""
        tz = timezone.utc
        due = datetime(2026, 5, 2, 23, 59, 0, tzinfo=tz)
        result = _assignment_to_dict(
            assignment_id="abc123",
            title="Tarea 1",
            course_name="Matemáticas",
            due_date=due,
            source_url="https://example.com/assign/1",
        )

        assert result["assignment_id"] == "abc123"
        assert result["title"] == "Tarea 1"
        assert result["course_name"] == "Matemáticas"
        assert result["due_date"] == "2026-05-02T23:59:00+00:00"
        assert result["source_url"] == "https://example.com/assign/1"


# ─── Test _format_time_remaining ─────────────────────────────────────────────


class TestFormatTimeRemaining:
    """Unit tests for _format_time_remaining."""

    def _tz(self) -> timezone:
        return timezone(timedelta(hours=-5))

    def test_overdue(self) -> None:
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 4, 29, 6, 0, 0, tzinfo=tz)  # past
        assert _format_time_remaining(due, now) == "overdue"

    def test_minutes(self) -> None:
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 4, 29, 8, 30, 0, tzinfo=tz)  # 30 min later
        assert _format_time_remaining(due, now) == "~30 minutes"

    def test_hours(self) -> None:
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 4, 29, 14, 0, 0, tzinfo=tz)  # 6h later
        assert _format_time_remaining(due, now) == "~6 hours"

    def test_days(self) -> None:
        tz = self._tz()
        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)
        due = datetime(2026, 5, 3, 8, 0, 0, tzinfo=tz)  # 4 days later
        assert _format_time_remaining(due, now) == "4 days"


# ─── Test setup_logging ────────────────────────────────────────────────────────


class TestSetupLogging:
    """Unit tests for setup_logging."""

    def test_sets_root_level(self) -> None:
        """setup_logging('WARNING') should set root logger level to WARNING."""
        setup_logging("WARNING")
        assert logging.getLogger().level == logging.WARNING

    def test_attachments_includes_stream_handler(self) -> None:
        """A stream handler should be attached to the root logger after setup."""
        setup_logging("DEBUG")
        assert any(isinstance(h, logging.StreamHandler) for h in logging.getLogger().handlers)


# ─── Integration Tests ────────────────────────────────────────────────────────


class TestIntegration:
    """Full-flow integration tests with mocked dependencies."""

    @pytest.fixture
    def sample_assignments(self):
        """Five sample assignments for testing."""
        tz = timezone(timedelta(hours=-5))

        def make_due(year: int, month: int, day: int, hour: int = 23, minute: int = 59) -> datetime:
            return datetime(year, month, day, hour, minute, 0, tzinfo=tz)

        # Dataclass-simulated assignment objects returned by scraper
        class MockAssignment:
            def __init__(
                self,
                assignment_id: str,
                title: str,
                course_name: str,
                due_date: datetime,
                source_url: str,
            ):
                self.assignment_id = assignment_id
                self.title = title
                self.course_name = course_name
                self.due_date = due_date
                self.source_url = source_url
                self.status = "Pending"
                self.scraped_at = datetime.now(tz)

        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=tz)  # Wednesday 08:00

        return [
            MockAssignment(
                "assign_001",
                "Tarea 1",
                "Matemáticas",
                make_due(2026, 5, 2, 23, 59),  # Sat next week - NOT this week
                "https://example.com/1",
            ),
            MockAssignment(
                "assign_002",
                "Tarea 2",
                "Física",
                make_due(2026, 4, 30, 23, 59),  # Thu this week - within week
                "https://example.com/2",
            ),
            MockAssignment(
                "assign_003",
                "Proyecto Final",
                "Programación",
                # Due in 12 hours from now (within 24h window)
                now + timedelta(hours=12),
                "https://example.com/3",
            ),
            MockAssignment(
                "assign_004",
                "Laboratorio 3",
                "Química",
                # Due in 48 hours - NOT within 24h
                now + timedelta(hours=48),
                "https://example.com/4",
            ),
            MockAssignment(
                "assign_005",
                "Ensayo",
                "Historia",
                # Due Monday next week - NOT this week
                make_due(2026, 5, 4),
                "https://example.com/5",
            ),
        ], now

    def _make_db_mock(self):
        """Create a mock AssignmentDatabase."""
        db = MagicMock()
        db.is_week_digest_sent.return_value = False
        db.is_24h_alerted.return_value = False
        db.is_3h_alerted.return_value = False
        db.is_new_assignment_notified.return_value = False
        db.get_new_assignments.return_value = []
        db.get_assignments_by_week.return_value = []
        db.upsert_assignment.return_value = (False, False)
        return db

    # ── Test: digest day sends digest ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_digest_day_sends_digest(
        self, sample_assignments, tmp_path: Path
    ) -> None:
        """When today == weekly_digest_day, a digest should be sent."""
        import bot as bot_module
        from database import AssignmentRow

        assignments, now = sample_assignments

        mock_config = MagicMock()
        mock_config.cache_file_path = str(tmp_path / "cache.json")
        mock_config.log_level = "WARNING"
        mock_config.weekly_digest_day = 3  # Wednesday (today.isoweekday() == 3)
        mock_config.tz = timezone(timedelta(hours=-5))
        mock_config.timezone = "America/Lima"
        mock_config.discord_webhook_url = "https://discord.example.com/webhook"
        mock_config.request_timeout_seconds = 30
        mock_config.max_retry_attempts = 3

        db = self._make_db_mock()
        # Return assignments due this week for digest
        db.get_assignments_by_week.return_value = [
            AssignmentRow(
                assignment_id="assign_002",
                title="Tarea 2",
                course_name="Física",
                course_id="",
                due_date="2026-04-30T23:59:00+00:00",
                status="Pending",
                source_url="https://example.com/2",
                first_seen_at="2026-04-29T08:00:00Z",
                last_seen_at="2026-04-29T08:00:00Z",
            ),
        ]

        notifier = MagicMock()
        notifier.send_weekly_digest = AsyncMock(return_value=True)
        notifier.send_24h_alert = AsyncMock(return_value=True)
        notifier.send_new_assignment = AsyncMock(return_value=True)
        notifier.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape_assignments = AsyncMock(return_value=assignments)
        scraper.close = AsyncMock()

        with patch.object(bot_module, "load", return_value=mock_config):
            with patch.object(bot_module, "setup_logging"):
                with patch.object(bot_module, "AssignmentDatabase", return_value=db):
                    with patch.object(bot_module, "DiscordNotifier", return_value=notifier):
                        with patch.object(bot_module, "BlackboardScraper", return_value=scraper):
                            with patch("bot.datetime", _MockDatetime(now)):
                                # Reload bot.main to use patched modules
                                from bot import main as bot_main

                                result = await bot_main()

                                assert result == 0
                                notifier.send_weekly_digest.assert_called_once()

    # ── Test: non-digest day skips digest ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_non_digest_day_skips_digest(
        self, sample_assignments, tmp_path: Path
    ) -> None:
        """When today != weekly_digest_day, no digest should be sent."""
        import bot as bot_module

        assignments, now = sample_assignments

        mock_config = MagicMock()
        mock_config.cache_file_path = str(tmp_path / "cache.json")
        mock_config.log_level = "WARNING"
        mock_config.weekly_digest_day = 0  # Sunday (today is Wednesday = 3)
        mock_config.tz = timezone(timedelta(hours=-5))
        mock_config.timezone = "America/Lima"
        mock_config.discord_webhook_url = "https://discord.example.com/webhook"
        mock_config.request_timeout_seconds = 30
        mock_config.max_retry_attempts = 3

        db = self._make_db_mock()

        notifier = MagicMock()
        notifier.send_weekly_digest = AsyncMock(return_value=True)
        notifier.send_24h_alert = AsyncMock(return_value=True)
        notifier.send_new_assignment = AsyncMock(return_value=True)
        notifier.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape_assignments = AsyncMock(return_value=assignments)
        scraper.close = AsyncMock()

        with patch.object(bot_module, "load", return_value=mock_config):
            with patch.object(bot_module, "setup_logging"):
                with patch.object(bot_module, "AssignmentDatabase", return_value=db):
                    with patch.object(bot_module, "DiscordNotifier", return_value=notifier):
                        with patch.object(bot_module, "BlackboardScraper", return_value=scraper):
                            with patch("bot.datetime", _MockDatetime(now)):
                                from bot import main as bot_main

                                result = await bot_main()

                                assert result == 0
                                notifier.send_weekly_digest.assert_not_called()

    # ── Test: assignments within 24h trigger alerts ─────────────────────────────

    @pytest.mark.asyncio
    async def test_within_24h_triggers_alert(
        self, sample_assignments, tmp_path: Path
    ) -> None:
        """Assignments due within 24h should trigger an alert."""
        import bot as bot_module

        assignments, now = sample_assignments

        mock_config = MagicMock()
        mock_config.cache_file_path = str(tmp_path / "cache.json")
        mock_config.log_level = "WARNING"
        mock_config.weekly_digest_day = 0  # Sunday
        mock_config.tz = timezone(timedelta(hours=-5))
        mock_config.timezone = "America/Lima"
        mock_config.discord_webhook_url = "https://discord.example.com/webhook"
        mock_config.request_timeout_seconds = 30
        mock_config.max_retry_attempts = 3

        db = self._make_db_mock()
        db.is_week_digest_sent.return_value = True  # Not digest day anyway
        db.is_24h_alerted.return_value = False

        notifier = MagicMock()
        notifier.send_weekly_digest = AsyncMock(return_value=True)
        notifier.send_24h_alert = AsyncMock(return_value=True)
        notifier.send_new_assignment = AsyncMock(return_value=True)
        notifier.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape_assignments = AsyncMock(return_value=assignments)
        scraper.close = AsyncMock()

        with patch.object(bot_module, "load", return_value=mock_config):
            with patch.object(bot_module, "setup_logging"):
                with patch.object(bot_module, "AssignmentDatabase", return_value=db):
                    with patch.object(bot_module, "DiscordNotifier", return_value=notifier):
                        with patch.object(bot_module, "BlackboardScraper", return_value=scraper):
                            with patch("bot.datetime", _MockDatetime(now)):
                                from bot import main as bot_main

                                result = await bot_main()

                                assert result == 0
                                # assign_003 is due in 12h → should trigger alert
                                assert notifier.send_24h_alert.called

    # ── Test: already alerted assignments skipped ─────────────────────────────

    @pytest.mark.asyncio
    async def test_already_alerted_skipped(
        self, sample_assignments, tmp_path: Path
    ) -> None:
        """Assignments already alerted are skipped."""
        import bot as bot_module

        assignments, now = sample_assignments

        mock_config = MagicMock()
        mock_config.cache_file_path = str(tmp_path / "cache.json")
        mock_config.log_level = "WARNING"
        mock_config.weekly_digest_day = 0
        mock_config.tz = timezone(timedelta(hours=-5))
        mock_config.timezone = "America/Lima"
        mock_config.discord_webhook_url = "https://discord.example.com/webhook"
        mock_config.request_timeout_seconds = 30
        mock_config.max_retry_attempts = 3

        db = self._make_db_mock()
        db.is_week_digest_sent.return_value = True
        db.is_24h_alerted.return_value = True  # Already alerted!

        notifier = MagicMock()
        notifier.send_weekly_digest = AsyncMock(return_value=True)
        notifier.send_24h_alert = AsyncMock(return_value=True)
        notifier.send_new_assignment = AsyncMock(return_value=True)
        notifier.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape_assignments = AsyncMock(return_value=assignments)
        scraper.close = AsyncMock()

        with patch.object(bot_module, "load", return_value=mock_config):
            with patch.object(bot_module, "setup_logging"):
                with patch.object(bot_module, "AssignmentDatabase", return_value=db):
                    with patch.object(bot_module, "DiscordNotifier", return_value=notifier):
                        with patch.object(bot_module, "BlackboardScraper", return_value=scraper):
                            with patch("bot.datetime", _MockDatetime(now)):
                                from bot import main as bot_main

                                result = await bot_main()

                                assert result == 0
                                notifier.send_24h_alert.assert_not_called()

    # ── Test: no assignments skips gracefully ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_assignments_skips_gracefully(self, tmp_path: Path) -> None:
        """When no assignments are scraped, the cycle completes silently."""
        import bot as bot_module

        mock_config = MagicMock()
        mock_config.cache_file_path = str(tmp_path / "cache.json")
        mock_config.log_level = "WARNING"
        mock_config.weekly_digest_day = 2
        mock_config.tz = timezone(timedelta(hours=-5))
        mock_config.timezone = "America/Lima"
        mock_config.discord_webhook_url = "https://discord.example.com/webhook"
        mock_config.request_timeout_seconds = 30
        mock_config.max_retry_attempts = 3

        db = self._make_db_mock()
        notifier = MagicMock()
        notifier.send_weekly_digest = AsyncMock(return_value=True)
        notifier.send_24h_alert = AsyncMock(return_value=True)
        notifier.send_new_assignment = AsyncMock(return_value=True)
        notifier.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape_assignments = AsyncMock(return_value=[])
        scraper.close = AsyncMock()

        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=timezone(timedelta(hours=-5)))

        with patch.object(bot_module, "load", return_value=mock_config):
            with patch.object(bot_module, "setup_logging"):
                with patch.object(bot_module, "AssignmentDatabase", return_value=db):
                    with patch.object(bot_module, "DiscordNotifier", return_value=notifier):
                        with patch.object(bot_module, "BlackboardScraper", return_value=scraper):
                            with patch("bot.datetime", _MockDatetime(now)):
                                from bot import main as bot_main

                                result = await bot_main()

                                assert result == 0
                                notifier.send_weekly_digest.assert_not_called()
                                notifier.send_24h_alert.assert_not_called()

    # ── Test: scraper failure handled gracefully ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_scraper_failure_handled(self, tmp_path: Path) -> None:
        """When scrape_assignments raises, the cycle returns 0 without crashing."""
        import bot as bot_module

        mock_config = MagicMock()
        mock_config.cache_file_path = str(tmp_path / "cache.json")
        mock_config.log_level = "WARNING"
        mock_config.weekly_digest_day = 2
        mock_config.tz = timezone(timedelta(hours=-5))
        mock_config.timezone = "America/Lima"
        mock_config.discord_webhook_url = "https://discord.example.com/webhook"
        mock_config.request_timeout_seconds = 30
        mock_config.max_retry_attempts = 3

        db = self._make_db_mock()
        notifier = MagicMock()
        notifier.send_weekly_digest = AsyncMock(return_value=True)
        notifier.send_24h_alert = AsyncMock(return_value=True)
        notifier.send_new_assignment = AsyncMock(return_value=True)
        notifier.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape_assignments = AsyncMock(
            side_effect=Exception("Blackboard down")
        )
        scraper.close = AsyncMock()

        now = datetime(2026, 4, 29, 8, 0, 0, tzinfo=timezone(timedelta(hours=-5)))

        with patch.object(bot_module, "load", return_value=mock_config):
            with patch.object(bot_module, "setup_logging"):
                with patch.object(bot_module, "AssignmentDatabase", return_value=db):
                    with patch.object(bot_module, "DiscordNotifier", return_value=notifier):
                        with patch.object(bot_module, "BlackboardScraper", return_value=scraper):
                            with patch("bot.datetime", _MockDatetime(now)):
                                from bot import main as bot_main

                                result = await bot_main()

                                # Cycle should complete without crashing
                                assert result == 0
                                # When scraping fails, we still close the scraper
                                scraper.close.assert_called_once()

    # ── Test: notifier failure doesn't crash cycle ─────────────────────────────

    @pytest.mark.asyncio
    async def test_notifier_failure_doesnt_crash(
        self, sample_assignments, tmp_path: Path
    ) -> None:
        """When notifier.send_24h_alert returns False, cycle continues."""
        import bot as bot_module

        assignments, now = sample_assignments

        mock_config = MagicMock()
        mock_config.cache_file_path = str(tmp_path / "cache.json")
        mock_config.log_level = "WARNING"
        mock_config.weekly_digest_day = 0  # not digest day
        mock_config.tz = timezone(timedelta(hours=-5))
        mock_config.timezone = "America/Lima"
        mock_config.discord_webhook_url = "https://discord.example.com/webhook"
        mock_config.request_timeout_seconds = 30
        mock_config.max_retry_attempts = 3

        db = self._make_db_mock()
        db.is_week_digest_sent.return_value = True
        db.is_24h_alerted.return_value = False

        notifier = MagicMock()
        notifier.send_weekly_digest = AsyncMock(return_value=True)
        notifier.send_24h_alert = AsyncMock(return_value=False)  # FAILED
        notifier.send_new_assignment = AsyncMock(return_value=True)
        notifier.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape_assignments = AsyncMock(return_value=assignments)
        scraper.close = AsyncMock()

        with patch.object(bot_module, "load", return_value=mock_config):
            with patch.object(bot_module, "setup_logging"):
                with patch.object(bot_module, "AssignmentDatabase", return_value=db):
                    with patch.object(bot_module, "DiscordNotifier", return_value=notifier):
                        with patch.object(bot_module, "BlackboardScraper", return_value=scraper):
                            with patch("bot.datetime", _MockDatetime(now)):
                                from bot import main as bot_main

                                result = await bot_main()

                                # Should complete without crashing
                                assert result == 0
                                notifier.close.assert_called()

    # ── Test: new assignments detected and notified ───────────────────────────

    @pytest.mark.asyncio
    async def test_new_assignments_detected_and_notified(
        self, sample_assignments, tmp_path: Path
    ) -> None:
        """New assignments should trigger new assignment notifications."""
        import bot as bot_module
        from database import AssignmentRow

        assignments, now = sample_assignments

        mock_config = MagicMock()
        mock_config.cache_file_path = str(tmp_path / "cache.json")
        mock_config.log_level = "WARNING"
        mock_config.weekly_digest_day = 0
        mock_config.tz = timezone(timedelta(hours=-5))
        mock_config.timezone = "America/Lima"
        mock_config.discord_webhook_url = "https://discord.example.com/webhook"
        mock_config.request_timeout_seconds = 30
        mock_config.max_retry_attempts = 3

        db = self._make_db_mock()
        db.is_week_digest_sent.return_value = True
        db.is_24h_alerted.return_value = True
        # Simulate assign_003 being a new assignment
        db.get_new_assignments.return_value = [
            AssignmentRow(
                assignment_id="assign_003",
                title="",
                course_name="",
                course_id="",
                due_date=None,
                status="",
                source_url="",
                first_seen_at="",
                last_seen_at="",
            ),
        ]
        db.is_new_assignment_notified.return_value = False
        db.get_assignment.return_value = AssignmentRow(
            assignment_id="assign_003",
            title="Proyecto Final",
            course_name="Programación",
            course_id="",
            due_date=(now + timedelta(hours=12)).isoformat(),
            status="Pending",
            source_url="https://example.com/3",
            first_seen_at="2026-04-29T08:00:00Z",
            last_seen_at="2026-04-29T08:00:00Z",
        )

        notifier = MagicMock()
        notifier.send_weekly_digest = AsyncMock(return_value=True)
        notifier.send_24h_alert = AsyncMock(return_value=True)
        notifier.send_new_assignment = AsyncMock(return_value=True)
        notifier.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape_assignments = AsyncMock(return_value=assignments)
        scraper.close = AsyncMock()

        with patch.object(bot_module, "load", return_value=mock_config):
            with patch.object(bot_module, "setup_logging"):
                with patch.object(bot_module, "AssignmentDatabase", return_value=db):
                    with patch.object(bot_module, "DiscordNotifier", return_value=notifier):
                        with patch.object(bot_module, "BlackboardScraper", return_value=scraper):
                            with patch("bot.datetime", _MockDatetime(now)):
                                from bot import main as bot_main

                                result = await bot_main()

                                assert result == 0
                                notifier.send_new_assignment.assert_called_once()
                                db.mark_new_assignment_notified.assert_called_once_with("assign_003")
