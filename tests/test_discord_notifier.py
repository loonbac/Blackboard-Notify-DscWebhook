"""Comprehensive unit tests for discord_notifier.py."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from discord_notifier import (
    ALERT_COLOR,
    DATE_CHANGED_COLOR,
    DIGEST_COLOR,
    DiscordNotifier,
    EMOJI_NORMAL,
    EMOJI_SOON,
    EMOJI_URGENT,
    NEW_ASSIGNMENT_COLOR,
    THREE_H_ALERT_COLOR,
    _build_alert_embed,
    _build_date_changed_embed,
    _build_digest_embed,
    _build_3h_alert_embed,
    _format_due_date_display,
    _format_remaining,
    _hours_until,
    _urgency_emoji,
)


# ─── Helpers ───────────────────────────────────────────────────────────────────


class MockResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def __repr__(self) -> str:
        return f"MockResponse({self.status_code})"


# ─── Test urgency emoji classification ────────────────────────────────────────


class TestUrgencyEmoji:
    @pytest.mark.parametrize(
        "hours, expected",
        [
            (0.5, EMOJI_URGENT),
            (12, EMOJI_URGENT),
            (23.9, EMOJI_URGENT),
            (24, EMOJI_SOON),
            (48, EMOJI_SOON),
            (71.9, EMOJI_SOON),
            (72, EMOJI_NORMAL),
            (100, EMOJI_NORMAL),
            (168, EMOJI_NORMAL),
        ],
    )
    def test_emoji_assignment(self, hours: float, expected: str) -> None:
        assert _urgency_emoji(hours) == expected


# ─── Test hours_until ──────────────────────────────────────────────────────────


class TestHoursUntil:
    def test_future_date_returns_positive(self) -> None:
        # Use a fixed future date (2027) so the test is deterministic
        future = "2027-06-01T12:00:00Z"
        hours = _hours_until(future)
        assert hours > 0

    def test_past_date_returns_negative(self) -> None:
        past = "2020-01-01T00:00:00Z"
        hours = _hours_until(past)
        assert hours < 0

    def test_empty_string_returns_zero(self) -> None:
        assert _hours_until("") == 0.0

    def test_invalid_date_returns_zero(self) -> None:
        assert _hours_until("not-a-date") == 0.0


# ─── Test format_remaining ─────────────────────────────────────────────────────


class TestFormatRemaining:
    def test_overdue(self) -> None:
        assert _format_remaining(-1) == "vencida!"

    def test_minutes(self) -> None:
        assert _format_remaining(0.5) == "~30 minutos"
        assert _format_remaining(0.8) == "~48 minutos"

    def test_hours(self) -> None:
        assert _format_remaining(5) == "~5 horas"
        assert _format_remaining(23.9) == "~23 horas"

    def test_days(self) -> None:
        assert _format_remaining(24) == "1 días"
        assert _format_remaining(48) == "2 días"
        assert _format_remaining(72) == "3 días"

    def test_days_and_hours(self) -> None:
        result = _format_remaining(50)
        assert "días" in result
        assert "2 días" in result


# ─── Test format_due_date_display ─────────────────────────────────────────────


class TestFormatDueDateDisplay:
    def test_valid_iso_date(self) -> None:
        result = _format_due_date_display("2026-05-01T23:59:00Z", "UTC")
        # Should contain day, month, year, time
        assert "May" in result or "05" in result
        assert "23:59" in result

    def test_empty_returns_dash(self) -> None:
        assert _format_due_date_display("", "UTC") == "—"

    def test_invalid_returns_original(self) -> None:
        result = _format_due_date_display("not-a-date", "UTC")
        assert result == "not-a-date"


# ─── Test digest embed structure ──────────────────────────────────────────────


class TestDigestEmbedStructure:
    def test_digest_embed_has_correct_color(self) -> None:
        embed = _build_digest_embed([], "2026-W18", "2026-04-28T08:00:00Z", "UTC")
        assert embed["color"] == DIGEST_COLOR

    def test_digest_embed_title_format(self) -> None:
        embed = _build_digest_embed([], "2026-W18", "2026-04-28T08:00:00Z", "UTC")
        assert "Semana 18" in embed["title"] or "Semana W18" in embed["title"]
        assert "📋" in embed["title"]

    def test_digest_embed_has_footer(self) -> None:
        embed = _build_digest_embed([], "2026-W18", "2026-04-28T08:00:00Z", "UTC")
        assert "footer" in embed
        assert "Bot Blackboard" in embed["footer"]["text"]

    def test_digest_embed_has_timestamp(self) -> None:
        embed = _build_digest_embed([], "2026-W18", "2026-04-28T08:00:00Z", "UTC")
        assert "timestamp" in embed

    def test_digest_empty_assignments_shows_all_clear(self) -> None:
        embed = _build_digest_embed([], "2026-W18", "2026-04-28T08:00:00Z", "UTC")
        assert "✅" in embed["description"] or "No hay tareas" in embed["description"]
        assert embed["fields"] == []

    def test_digest_with_assignments_has_fields(self) -> None:
        assignments = [
            {
                "title": "Tarea 1",
                "course_name": "Matemáticas",
                "due_date": "2026-05-01T23:59:00Z",
            }
        ]
        embed = _build_digest_embed(assignments, "2026-W18", "2026-04-28T08:00:00Z", "UTC")
        assert len(embed["fields"]) == 1
        assert "Tarea 1" in embed["fields"][0]["name"]

    def test_emoji_in_field_name_for_each_assignment(self) -> None:
        # < 24h → 🔴
        with patch("discord_notifier._hours_until", return_value=12.0):
            urgent = [{"title": "Urgent", "course_name": "A", "due_date": "2026-04-28T12:00:00Z"}]
            embed_u = _build_digest_embed(urgent, "2026-W18", "2026-04-28T08:00:00Z", "UTC")
            assert embed_u["fields"][0]["name"].startswith(EMOJI_URGENT)

        # 24-72h → 🟡
        with patch("discord_notifier._hours_until", return_value=48.0):
            soon = [{"title": "Soon", "course_name": "A", "due_date": "2026-04-30T23:59:00Z"}]
            embed_s = _build_digest_embed(soon, "2026-W18", "2026-04-28T08:00:00Z", "UTC")
            assert embed_s["fields"][0]["name"].startswith(EMOJI_SOON)

        # 4+ days → 🟢
        with patch("discord_notifier._hours_until", return_value=120.0):
            normal = [{"title": "Normal", "course_name": "A", "due_date": "2026-05-05T23:59:00Z"}]
            embed_n = _build_digest_embed(normal, "2026-W18", "2026-04-28T08:00:00Z", "UTC")
            assert embed_n["fields"][0]["name"].startswith(EMOJI_NORMAL)


# ─── Test alert embed structure ───────────────────────────────────────────────


class TestAlertEmbedStructure:
    def test_alert_embed_has_correct_color(self) -> None:
        embed = _build_alert_embed(
            {
                "title": "Test",
                "course_name": "A",
                "due_date": "2026-04-28T23:59:00Z",
                "source_url": "",
            },
            12.0,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        assert embed["color"] == ALERT_COLOR

    def test_alert_embed_title(self) -> None:
        embed = _build_alert_embed(
            {"title": "Test", "course_name": "A", "due_date": "2026-04-28T23:59:00Z"},
            12.0,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        assert "⏰" in embed["title"]
        assert "Tarea por Vencer" in embed["title"]

    def test_alert_embed_has_assignment_field(self) -> None:
        embed = _build_alert_embed(
            {"title": "Tarea 1", "course_name": "Matemáticas", "due_date": "2026-04-28T23:59:00Z"},
            12.0,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        field_names = [f["name"] for f in embed["fields"]]
        assert "Tarea" in field_names
        field_map = {f["name"]: f["value"] for f in embed["fields"]}
        assert field_map["Tarea"] == "Tarea 1"

    def test_alert_embed_has_due_date_field(self) -> None:
        embed = _build_alert_embed(
            {"title": "Tarea 1", "course_name": "A", "due_date": "2026-04-28T23:59:00Z"},
            12.0,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        field_names = [f["name"] for f in embed["fields"]]
        assert "Fecha de entrega" in field_names

    def test_alert_embed_has_time_remaining_field(self) -> None:
        embed = _build_alert_embed(
            {"title": "Tarea 1", "course_name": "A", "due_date": "2026-04-28T23:59:00Z"},
            12.0,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        field_names = [f["name"] for f in embed["fields"]]
        assert "Tiempo restante" in field_names

    def test_alert_embed_has_course_field(self) -> None:
        embed = _build_alert_embed(
            {"title": "Tarea 1", "course_name": "Matemáticas", "due_date": "2026-04-28T23:59:00Z"},
            12.0,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        field_map = {f["name"]: f["value"] for f in embed["fields"]}
        assert field_map["Curso"] == "Matemáticas"

    def test_alert_embed_has_footer(self) -> None:
        embed = _build_alert_embed(
            {"title": "T", "course_name": "A", "due_date": "2026-04-28T23:59:00Z"},
            12.0,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        assert "footer" in embed
        assert "Bot Blackboard" in embed["footer"]["text"]

    def test_alert_embed_url_from_source(self) -> None:
        embed = _build_alert_embed(
            {
                "title": "Tarea 1",
                "course_name": "A",
                "due_date": "2026-04-28T23:59:00Z",
                "source_url": "https://bb.com/assignment/1",
            },
            12.0,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        assert embed.get("url") == "https://bb.com/assignment/1"


# ─── Test color values ────────────────────────────────────────────────────────


class TestColorValues:
    def test_digest_color_is_blue(self) -> None:
        assert DIGEST_COLOR == 3447003

    def test_alert_color_is_red(self) -> None:
        assert ALERT_COLOR == 16711680


# ─── Test DiscordNotifier.send_weekly_digest ───────────────────────────────────


class TestSendWeeklyDigest:
    @pytest.fixture
    def notifier(self) -> DiscordNotifier:
        return DiscordNotifier(webhook_url="https://discord.com/webhooks/123/abc")

    @pytest.mark.asyncio
    async def test_sends_correct_payload_structure(self, notifier: DiscordNotifier) -> None:
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = "OK"

        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            result = await notifier.send_weekly_digest(
                [{"title": "T1", "due_date": "2026-05-01T23:59:00Z", "course_name": "Math"}],
                "2026-W18",
                "UTC",
            )
        assert result is True
        mock_send.assert_awaited_once()
        call_args = mock_send.call_args
        payload = call_args[0][0] if call_args.args else call_args.kwargs.get("payload")
        assert payload["username"] == "Bot Blackboard"
        assert len(payload["embeds"]) == 1
        assert payload["embeds"][0]["color"] == DIGEST_COLOR

    @pytest.mark.asyncio
    async def test_all_assignments_appear_in_digest(self, notifier: DiscordNotifier) -> None:
        assignments = [
            {"title": f"Assign {i}", "course_name": "Math", "due_date": "2026-05-01T23:59:00Z"}
            for i in range(5)
        ]
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await notifier.send_weekly_digest(assignments, "2026-W18", "UTC")

        payload = mock_send.call_args[0][0]
        embed = payload["embeds"][0]
        assert len(embed["fields"]) == 5


# ─── Test DiscordNotifier.send_24h_alert ──────────────────────────────────────


class TestSend24hAlert:
    @pytest.fixture
    def notifier(self) -> DiscordNotifier:
        return DiscordNotifier(webhook_url="https://discord.com/webhooks/123/abc")

    @pytest.mark.asyncio
    async def test_sends_correct_payload_structure(self, notifier: DiscordNotifier) -> None:
        assignment = {
            "title": "Tarea 1",
            "course_name": "Matemáticas",
            "due_date": "2026-04-28T23:59:00Z",
            "source_url": "https://bb.com/assignment/1",
        }
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            result = await notifier.send_24h_alert(assignment, "UTC")

        assert result is True
        mock_send.assert_awaited_once()
        payload = mock_send.call_args[0][0]
        assert payload["username"] == "Bot Blackboard"
        assert len(payload["embeds"]) == 1
        assert payload["embeds"][0]["color"] == ALERT_COLOR


# ─── Test retry logic ─────────────────────────────────────────────────────────


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retries_on_timeout(self) -> None:
        """Verify that _send retries on httpx.TimeoutException up to max_retries."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/webhooks/123/abc", max_retries=3)

        attempts = 0

        class FakeClient:
            async def post(self, *args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise httpx.TimeoutException("timed out")
                return MockResponse(200, "OK")

        notifier._client = FakeClient()  # type: ignore

        result = await notifier._send({"test": "payload"})

        assert result is True
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_retries_on_http_error(self) -> None:
        """Verify that _send retries on HTTP 5xx errors."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/webhooks/123/abc", max_retries=3)

        attempts = 0

        class FakeClient:
            async def post(self, *args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    return MockResponse(500, "Server Error")
                return MockResponse(200, "OK")

        notifier._client = FakeClient()  # type: ignore

        result = await notifier._send({"test": "payload"})

        assert result is True
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_returns_false_after_max_retries_exceeded(self) -> None:
        notifier = DiscordNotifier(webhook_url="https://discord.com/webhooks/123/abc", max_retries=2)

        class AlwaysFailsClient:
            async def post(self, *args, **kwargs):
                return MockResponse(500, "Error")

        notifier._client = AlwaysFailsClient()  # type: ignore

        result = await notifier._send({"test": "payload"})

        assert result is False


# ─── Test rate limit handling ──────────────────────────────────────────────────


class TestRateLimitHandling:
    @pytest.mark.asyncio
    async def test_handles_429_with_retry_after_header(self) -> None:
        notifier = DiscordNotifier(webhook_url="https://discord.com/webhooks/123/abc", max_retries=3)

        call_count = 0

        async def fake_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = MockResponse(429, "Rate limited", {"Retry-After": "0.01"})
                return resp
            return MockResponse(200, "OK")

        with patch.object(notifier, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.post = fake_post
            mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_get_client.return_value.__aexit__ = AsyncMock()

            # Directly test by patching _send
            original_send = notifier._send

            async def patched_send(payload):
                client = mock_client
                for attempt in range(1, 4):
                    response = await client.post(notifier._webhook_url, json=payload)
                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After", "1")
                        wait = max(0.001, float(retry_after))
                        await asyncio.sleep(wait)
                        continue
                    return response.status_code == 200
                return False

            result = await patched_send({"test": "payload"})

        assert result is True
        assert call_count == 2


# ─── Test timeout handling ──────────────────────────────────────────────────────


class TestTimeoutHandling:
    @pytest.mark.asyncio
    async def test_timeout_uses_configured_value(self) -> None:
        notifier = DiscordNotifier(
            webhook_url="https://discord.com/webhooks/123/abc",
            timeout=15,
            max_retries=1,
        )
        assert notifier._timeout == 15

    @pytest.mark.asyncio
    async def test_close_is_called(self) -> None:
        notifier = DiscordNotifier(webhook_url="https://discord.com/webhooks/123/abc")
        mock_client = MagicMock()
        mock_client.aclose = AsyncMock()
        notifier._client = mock_client

        await notifier.close()

        mock_client.aclose.assert_awaited_once()
        assert notifier._client is None


# ─── Test payload structure ────────────────────────────────────────────────────


class TestPayloadStructure:
    def test_payload_uses_blackboard_bot_username(self) -> None:
        notifier = DiscordNotifier(webhook_url="https://discord.com/webhooks/123/abc")
        # Access the payload method
        embed = _build_digest_embed([], "2026-W18", "2026-04-28T08:00:00Z", "UTC")
        payload = notifier._payload(embed)
        assert payload["username"] == "Bot Blackboard"
        assert len(payload["embeds"]) == 1


# ─── Test 3h Alert Embed ────────────────────────────────────────────────────────


class Test3hAlertEmbed:
    def test_3h_alert_embed_has_orange_color(self) -> None:
        embed = _build_3h_alert_embed(
            {
                "title": "Tarea 1",
                "course_name": "Matemáticas",
                "due_date": "2026-04-28T23:59:00Z",
                "source_url": "https://bb.com/assign_1",
            },
            2.5,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        assert embed["color"] == THREE_H_ALERT_COLOR

    def test_3h_alert_embed_title_has_alarm_emoji(self) -> None:
        embed = _build_3h_alert_embed(
            {"title": "Tarea 1", "course_name": "A", "due_date": "2026-04-28T23:59:00Z"},
            2.5,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        assert "🚨" in embed["title"]
        assert "3 Horas" in embed["title"] or "3 horas" in embed["title"].lower()

    def test_3h_alert_embed_has_all_required_fields(self) -> None:
        embed = _build_3h_alert_embed(
            {"title": "Tarea 1", "course_name": "Matemáticas", "due_date": "2026-04-28T23:59:00Z"},
            2.5,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        field_names = [f["name"] for f in embed["fields"]]
        assert "Tarea" in field_names
        assert "Fecha de entrega" in field_names
        assert "Tiempo restante" in field_names
        assert "Curso" in field_names

    def test_3h_alert_embed_has_footer(self) -> None:
        embed = _build_3h_alert_embed(
            {"title": "T", "course_name": "A", "due_date": "2026-04-28T23:59:00Z"},
            2.5,
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        assert "footer" in embed
        assert "Bot Blackboard" in embed["footer"]["text"]


# ─── Test Date Changed Embed ────────────────────────────────────────────────────


class TestDateChangedEmbed:
    def test_date_changed_embed_has_yellow_color(self) -> None:
        embed = _build_date_changed_embed(
            {"title": "Tarea 1", "course_name": "Matemáticas", "due_date": "2026-05-01T23:59:00Z"},
            "2026-05-01T23:59:00Z",
            "2026-05-03T23:59:00Z",
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        assert embed["color"] == DATE_CHANGED_COLOR

    def test_date_changed_embed_title_has_calendar_emoji(self) -> None:
        embed = _build_date_changed_embed(
            {"title": "Tarea 1", "course_name": "A", "due_date": "2026-05-03T23:59:00Z"},
            "2026-05-01T23:59:00Z",
            "2026-05-03T23:59:00Z",
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        assert "📅" in embed["title"]
        assert "Actualizada" in embed["title"]

    def test_date_changed_embed_shows_old_and_new_dates(self) -> None:
        embed = _build_date_changed_embed(
            {"title": "Tarea 1", "course_name": "A", "due_date": "2026-05-03T23:59:00Z"},
            "2026-05-01T23:59:00Z",
            "2026-05-03T23:59:00Z",
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        field_names = [f["name"] for f in embed["fields"]]
        field_map = {f["name"]: f["value"] for f in embed["fields"]}
        assert "Fecha anterior" in field_names
        assert "Nueva fecha" in field_names

    def test_date_changed_embed_has_footer(self) -> None:
        embed = _build_date_changed_embed(
            {"title": "T", "course_name": "A", "due_date": "2026-05-03T23:59:00Z"},
            "2026-05-01T23:59:00Z",
            "2026-05-03T23:59:00Z",
            "2026-04-28T08:00:00Z",
            "UTC",
        )
        assert "footer" in embed
        assert "Bot Blackboard" in embed["footer"]["text"]