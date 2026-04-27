"""Discord notifier for Blackboard Discord Bot.

Sends weekly digest and 24h alert embeds to Discord via webhook.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ─── Constants ─────────────────────────────────────────────────────────────────

DIGEST_COLOR: int = 3447003  # Blue
ALERT_COLOR: int = 16711680  # Red
NEW_ASSIGNMENT_COLOR: int = 8388736  # Purple
THREE_H_ALERT_COLOR: int = 15105570  # Orange #E67E22
DATE_CHANGED_COLOR: int = 15844367  # Yellow #F1C40F

# Emoji indicators based on urgency
EMOJI_URGENT: str = "🔴"   # Due within 24h
EMOJI_SOON: str = "🟡"     # Due within 3 days
EMOJI_NORMAL: str = "🟢"   # Due in 4+ days

# Footer prefix
FOOTER_PREFIX: str = "Bot Blackboard"


# ─── Exceptions ────────────────────────────────────────────────────────────────


class NotifierError(Exception):
    """Raised when a Discord notification fails after all retries."""

    pass


# ─── Embed builders ────────────────────────────────────────────────────────────


def _urgency_emoji(hours_remaining: float) -> str:
    """Return emoji based on hours until deadline."""
    if hours_remaining < 24:
        return EMOJI_URGENT
    elif hours_remaining < 72:
        return EMOJI_SOON
    else:
        return EMOJI_NORMAL


def _build_digest_embed(
    assignments: list[dict[str, Any]],
    week_key: str,
    checked_at: str,
    tz_name: str,
) -> dict:
    """Build the weekly digest Discord embed (blue)."""
    week_parts = week_key.split("-")
    week_num = week_parts[1] if len(week_parts) == 2 else week_parts[-1]  # "2026-W18" → "18"

    # Build description with date range
    description = "Tus tareas que vencen esta semana (Lun {start} — Dom {end}):\n\n"

    fields = []
    for a in assignments:
        due_date_str = a.get("due_date", "")
        # Parse due_date to compute remaining time
        hours_remaining = _hours_until(due_date_str)
        emoji = _urgency_emoji(hours_remaining)

        # Format due date for display
        due_display = _format_due_date_display(due_date_str, tz_name)
        remaining_str = _format_remaining(hours_remaining)

        title = a.get("title", "Unknown")
        course = a.get("course_name", a.get("course", ""))

        field_name = f"{emoji} {title}"
        if course:
            field_name += f" — {course}"

        field_value = f"Vence: {due_display} | {remaining_str}"

        fields.append(
            {
                "name": field_name,
                "value": field_value,
                "inline": False,
            }
        )

    embed = {
        "title": f"📋 Tareas de la Semana — Semana {week_num}",
        "description": description.strip(),
        "color": DIGEST_COLOR,
        "fields": fields,
        "footer": {
            "text": f"{FOOTER_PREFIX} | Checked at {checked_at}",
        },
        "timestamp": checked_at,
    }

    # If no assignments, add a friendly "all clear" note
    if not fields:
        embed["description"] = "✅ Todo al día. No hay tareas pendientes esta semana."
        embed["color"] = DIGEST_COLOR

    return embed


def _build_alert_embed(
    assignment: dict[str, Any],
    hours_remaining: float,
    checked_at: str,
    tz_name: str,
) -> dict:
    """Build the 24h alert Discord embed (red)."""
    due_date_str = assignment.get("due_date", "")
    due_display = _format_due_date_display(due_date_str, tz_name)
    remaining_str = _format_remaining(hours_remaining)
    title = assignment.get("title", "Unknown")
    course = assignment.get("course_name", assignment.get("course", ""))
    source_url = assignment.get("source_url", "")

    embed = {
        "title": "⏰ ¡Tarea por Vencer!",
        "url": source_url if source_url else None,
        "description": "Esta tarea vence en menos de 24 horas.",
        "color": ALERT_COLOR,
        "fields": [
            {
                "name": "Tarea",
                "value": title,
                "inline": True,
            },
            {
                "name": "Fecha de entrega",
                "value": due_display,
                "inline": True,
            },
            {
                "name": "Tiempo restante",
                "value": remaining_str,
                "inline": True,
            },
            {
                "name": "Curso",
                "value": course if course else "—",
                "inline": True,
            },
        ],
        "footer": {
            "text": f"{FOOTER_PREFIX} | Checked at {checked_at}",
        },
        "timestamp": checked_at,
    }

    return embed


def _build_new_assignment_embed(
    assignment: dict[str, Any],
    checked_at: str,
    tz_name: str,
) -> dict:
    """Build the new assignment Discord embed (purple)."""
    due_date_str = assignment.get("due_date", "")
    due_display = _format_due_date_display(due_date_str, tz_name) if due_date_str else "—"
    title = assignment.get("title", "Unknown")
    course = assignment.get("course_name", assignment.get("course", ""))
    source_url = assignment.get("source_url", "")

    embed = {
        "title": "🆕 ¡Nueva Tarea Publicada!",
        "url": source_url if source_url else None,
        "description": "Se ha agregado una nueva tarea a tus cursos.",
        "color": NEW_ASSIGNMENT_COLOR,
        "fields": [
            {
                "name": "Tarea",
                "value": title,
                "inline": True,
            },
            {
                "name": "Fecha de entrega",
                "value": due_display,
                "inline": True,
            },
            {
                "name": "Curso",
                "value": course if course else "—",
                "inline": True,
            },
        ],
        "footer": {
            "text": f"{FOOTER_PREFIX} | Checked at {checked_at}",
        },
        "timestamp": checked_at,
    }

    return embed


def _build_3h_alert_embed(
    assignment: dict[str, Any],
    hours_remaining: float,
    checked_at: str,
    tz_name: str,
) -> dict:
    """Build orange '🚨 ¡Tarea Vence en 3 Horas!' embed."""
    due_date_str = assignment.get("due_date", "")
    due_display = _format_due_date_display(due_date_str, tz_name)
    remaining_str = _format_remaining(hours_remaining)
    title = assignment.get("title", "Unknown")
    course = assignment.get("course_name", assignment.get("course", ""))
    source_url = assignment.get("source_url", "")

    embed = {
        "title": "🚨 ¡Tarea Vence en 3 Horas!",
        "url": source_url if source_url else None,
        "description": "Esta tarea vence en menos de 3 horas. ¡Rápido!",
        "color": THREE_H_ALERT_COLOR,
        "fields": [
            {
                "name": "Tarea",
                "value": title,
                "inline": True,
            },
            {
                "name": "Fecha de entrega",
                "value": due_display,
                "inline": True,
            },
            {
                "name": "Tiempo restante",
                "value": remaining_str,
                "inline": True,
            },
            {
                "name": "Curso",
                "value": course if course else "—",
                "inline": True,
            },
        ],
        "footer": {
            "text": f"{FOOTER_PREFIX} | Checked at {checked_at}",
        },
        "timestamp": checked_at,
    }

    return embed


def _build_date_changed_embed(
    assignment: dict[str, Any],
    old_date: str,
    new_date: str,
    checked_at: str,
    tz_name: str,
) -> dict:
    """Build yellow '📅 Fecha Actualizada' embed showing old → new dates."""
    due_date_str = assignment.get("due_date", "")
    title = assignment.get("title", "Unknown")
    course = assignment.get("course_name", assignment.get("course", ""))
    source_url = assignment.get("source_url", "")

    # Format dates for display
    old_display = _format_due_date_display(old_date, tz_name) if old_date else "—"
    new_display = _format_due_date_display(new_date, tz_name) if new_date else "—"

    embed = {
        "title": "📅 Fecha Actualizada",
        "url": source_url if source_url else None,
        "description": "La fecha de entrega de una tarea ha cambiado.",
        "color": DATE_CHANGED_COLOR,
        "fields": [
            {
                "name": "Tarea",
                "value": title,
                "inline": False,
            },
            {
                "name": "Fecha anterior",
                "value": old_display,
                "inline": True,
            },
            {
                "name": "Nueva fecha",
                "value": new_display,
                "inline": True,
            },
            {
                "name": "Curso",
                "value": course if course else "—",
                "inline": True,
            },
        ],
        "footer": {
            "text": f"{FOOTER_PREFIX} | Checked at {checked_at}",
        },
        "timestamp": checked_at,
    }

    return embed


def _hours_until(due_date_str: str) -> float:
    """Compute hours between now (UTC) and the due date string."""
    if not due_date_str:
        return 0.0
    try:
        from dateutil import parser as dateutil_parser
        due = dateutil_parser.isoparse(due_date_str)
        # Make naive datetimes naive-UTC for comparison
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = due - now
        return delta.total_seconds() / 3600.0
    except Exception:
        return 0.0


def _format_due_date_display(due_date_str: str, tz_name: str) -> str:
    """Format due date for human display in Discord."""
    if not due_date_str:
        return "—"
    try:
        from dateutil import parser as dateutil_parser
        from zoneinfo import ZoneInfo

        due = dateutil_parser.isoparse(due_date_str)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)

        # Convert to configured timezone for display
        try:
            tz = ZoneInfo(tz_name)
            due_local = due.astimezone(tz)
        except Exception:
            due_local = due

        return due_local.strftime("%d %b %Y %H:%M")
    except Exception:
        return due_date_str


def _format_remaining(hours: float) -> str:
    """Format hours into human-readable remaining time string."""
    if hours <= 0:
        return "vencida!"
    elif hours < 1:
        return f"~{int(hours * 60)} minutos"
    elif hours < 24:
        return f"~{int(hours)} horas"
    else:
        days = int(hours // 24)
        remaining_hours = int(hours % 24)
        if remaining_hours > 0:
            return f"{days} días, {remaining_hours}h"
        return f"{days} días"


# ─── Discord Notifier ──────────────────────────────────────────────────────────


class DiscordNotifier:
    """Sends Discord webhook notifications for Blackboard assignments.

    Args:
        webhook_url: Full Discord webhook URL.
        timeout: Request timeout in seconds (default 30).
        max_retries: Maximum retry attempts on transient failures (default 3).
    """

    def __init__(
        self,
        webhook_url: str,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        self._webhook_url = webhook_url
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create the httpx client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _payload(self, embed: dict, username: str = "Bot Blackboard") -> dict:
        """Build the Discord webhook payload."""
        return {
            "username": username,
            "embeds": [embed],
        }

    async def _send(self, payload: dict) -> bool:
        """Send payload to Discord with retry and rate-limit handling."""
        client = await self._get_client()

        async def attempt(attempt_num: int) -> httpx.Response:
            response = await client.post(
                self._webhook_url,
                json=payload,
            )
            return response

        for attempt_num in range(1, self._max_retries + 1):
            try:
                response = await attempt(attempt_num)

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "1")
                    try:
                        wait_seconds = max(1.0, float(retry_after))
                    except ValueError:
                        wait_seconds = 1.0

                    logger.warning(
                        "Discord rate limited. Waiting %.1f seconds before retry %d/%d.",
                        wait_seconds,
                        attempt_num,
                        self._max_retries,
                    )
                    await asyncio.sleep(wait_seconds)
                    continue

                if response.status_code >= 400:
                    logger.warning(
                        "Discord webhook returned HTTP %d (attempt %d/%d): %s",
                        response.status_code,
                        attempt_num,
                        self._max_retries,
                        response.text[:500],
                    )
                    if attempt_num < self._max_retries:
                        # Exponential backoff
                        wait = 2.0 ** (attempt_num - 1)
                        await asyncio.sleep(wait)
                        continue
                    return False

                return True

            except httpx.TimeoutException:
                logger.warning(
                    "Discord webhook timeout (attempt %d/%d).",
                    attempt_num,
                    self._max_retries,
                )
                if attempt_num < self._max_retries:
                    wait = 2.0 ** (attempt_num - 1)
                    await asyncio.sleep(wait)
                    continue
                return False
            except httpx.RequestError as exc:
                logger.warning(
                    "Discord webhook request error (attempt %d/%d): %s",
                    attempt_num,
                    self._max_retries,
                    exc,
                )
                if attempt_num < self._max_retries:
                    wait = 2.0 ** (attempt_num - 1)
                    await asyncio.sleep(wait)
                    continue
                return False

        return False

    async def send_weekly_digest(
        self,
        assignments: list[dict[str, Any]],
        week_key: str,
        tz_name: str = "UTC",
    ) -> bool:
        """Send a weekly digest embed listing all assignments due this week.

        Args:
            assignments: List of assignment dicts with at least title, due_date, course_name.
            week_key: ISO week key like "2026-W18".
            tz_name: IANA timezone for date display.

        Returns:
            True if Discord accepted the webhook, False otherwise.
        """
        checked_at = datetime.now(timezone.utc).isoformat()
        embed = _build_digest_embed(assignments, week_key, checked_at, tz_name)
        payload = self._payload(embed)
        return await self._send(payload)

    async def send_24h_alert(
        self,
        assignment: dict[str, Any],
        tz_name: str = "UTC",
    ) -> bool:
        """Send a 24h deadline alert embed for a single assignment.

        Args:
            assignment: Assignment dict with title, due_date, course_name, source_url.
            tz_name: IANA timezone for date display.

        Returns:
            True if Discord accepted the webhook, False otherwise.
        """
        checked_at = datetime.now(timezone.utc).isoformat()
        due_date_str = assignment.get("due_date", "")
        hours = _hours_until(due_date_str)
        embed = _build_alert_embed(assignment, hours, checked_at, tz_name)
        payload = self._payload(embed)
        return await self._send(payload)

    async def send_new_assignment(
        self,
        assignment: dict[str, Any],
        tz_name: str = "UTC",
    ) -> bool:
        """Send a new assignment notification embed.

        Args:
            assignment: Assignment dict with title, due_date, course_name, source_url.
            tz_name: IANA timezone for date display.

        Returns:
            True if Discord accepted the webhook, False otherwise.
        """
        checked_at = datetime.now(timezone.utc).isoformat()
        embed = _build_new_assignment_embed(assignment, checked_at, tz_name)
        payload = self._payload(embed)
        return await self._send(payload)

    async def send_3h_alert(
        self,
        assignment: dict[str, Any],
        tz_name: str = "UTC",
    ) -> bool:
        """Send a 3h deadline alert via webhook. Returns True on success."""
        checked_at = datetime.now(timezone.utc).isoformat()
        due_date_str = assignment.get("due_date", "")
        hours = _hours_until(due_date_str)
        embed = _build_3h_alert_embed(assignment, hours, checked_at, tz_name)
        payload = self._payload(embed)
        return await self._send(payload)

    async def send_date_changed(
        self,
        assignment: dict[str, Any],
        old_date: str,
        new_date: str,
        tz_name: str = "UTC",
    ) -> bool:
        """Send a date-changed notification via webhook. Returns True on success."""
        checked_at = datetime.now(timezone.utc).isoformat()
        embed = _build_date_changed_embed(assignment, old_date, new_date, checked_at, tz_name)
        payload = self._payload(embed)
        return await self._send(payload)