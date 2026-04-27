"""Main entry point for the Blackboard Discord Bot.

Orchestrates the complete notification cycle:
  1. Load configuration
  2. Initialize logging
  3. Initialize database (with JSON migration if needed)
  4. Launch scraper and scrape assignments
  5. Upsert all assignments into database
  6. Detect and notify new assignments
  7. Compute week boundaries in configured timezone
  8. Send weekly digest if today is digest day and not already sent
  9. Send 24h alerts for assignments due within 24h
  10. Close scraper and database
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from zoneinfo import ZoneInfo

from blackboard_scraper import BlackboardScraper
from config import load
from database import AssignmentDatabase
from discord_notifier import DiscordNotifier


# ─── Module-level logger ───────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


# ─── Helper Functions ─────────────────────────────────────────────────────────


def setup_logging(level: str) -> None:
    """Configure logging format and level.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    # Remove any existing handlers to ensure clean state
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    log_format = "%(asctime)s %(levelname)-5s %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt=date_format,
        stream=sys.stdout,
    )


def get_week_boundaries(now: datetime) -> tuple[datetime, datetime]:
    """Return (monday_start, sunday_end) for the current week in the given timezone.

    The week runs Monday 00:00:00 to Sunday 23:59:59.999999 in the given
    timezone.

    Args:
        now: A timezone-aware datetime in the target timezone.

    Returns:
        A tuple of (monday_start, sunday_end), both timezone-aware.
    """
    # Find Monday of the current week
    # weekday() returns 0=Monday, 6=Sunday
    days_since_monday = now.weekday()
    monday_start = now - timedelta(days=days_since_monday)
    monday_start = monday_start.replace(hour=0, minute=0, second=0, microsecond=0)

    # Sunday end of week: 6 days after Monday
    sunday_end = monday_start + timedelta(days=6, hours=23, minutes=59, seconds=59, microseconds=999999)

    return monday_start, sunday_end


def get_week_start(now: datetime) -> datetime:
    """Return the Monday 00:00:00 of the current week."""
    monday_start, _ = get_week_boundaries(now)
    return monday_start


def get_week_end(now: datetime) -> datetime:
    """Return the Sunday 23:59:59.999999 of the current week."""
    _, sunday_end = get_week_boundaries(now)
    return sunday_end


def is_due_this_week(due_date: datetime, now: datetime) -> bool:
    """Check if due_date falls within the current week (Monday-Sunday) in the given timezone.

    Args:
        due_date: A timezone-aware datetime for the assignment's due date.
        now: A timezone-aware datetime representing "now" in the target timezone.

    Returns:
        True if due_date is on or after Monday 00:00 and on or before Sunday 23:59:59.
    """
    monday_start, sunday_end = get_week_boundaries(now)
    return monday_start <= due_date <= sunday_end


def is_due_within_hours(due_date: datetime, now: datetime, hours: int) -> bool:
    """Check if due_date is within `hours` from now.

    Args:
        due_date: A timezone-aware datetime for the assignment's due date.
        now: A timezone-aware datetime representing "now".
        hours: Number of hours threshold.

    Returns:
        True if now < due_date <= now + hours.
    """
    if due_date <= now:
        return False
    deadline = now + timedelta(hours=hours)
    return due_date <= deadline


def _assignment_to_dict(
    assignment_id: str,
    title: str,
    course_name: str,
    due_date: datetime,
    source_url: str,
) -> dict[str, Any]:
    """Convert assignment fields to a dict suitable for the notifier.

    The notifier expects ISO 8601 strings for due_date.
    """
    return {
        "assignment_id": assignment_id,
        "title": title,
        "course_name": course_name,
        "due_date": due_date.isoformat(),
        "source_url": source_url,
    }


# ─── Main Entry Point ──────────────────────────────────────────────────────────


async def main() -> int:
    """Run the complete notification cycle.

    Returns:
        0 on success, non-zero on errors.
    """
    # 1. Load config
    config = load()
    setup_logging(config.log_level)

    logger.info("Starting check cycle")

    # 2. Init database
    db_path = config.cache_file_path.replace('.json', '.db')
    db = AssignmentDatabase(db_path)
    db.connect()

    # 3. Migration from JSON cache if needed
    json_cache_path = "notified_assignments.json"
    if os.path.exists(json_cache_path) and not os.path.exists(db_path):
        migrated = db.migrate_from_json(json_cache_path)
        logger.info("Migrated %d records from JSON cache", migrated)

    scraper = BlackboardScraper(config)

    try:
        # 4. Scrape
        try:
            assignments = await scraper.scrape_assignments()
        except Exception as exc:
            logger.error("Scraping failed: %s. Skipping notification cycle.", exc)
            return 0

        if not assignments:
            logger.warning("No assignments scraped. Skipping notification cycle.")
            return 0

        logger.info("Scraped %d assignments from Blackboard", len(assignments))

        # 5. Upsert all assignments into DB and detect date changes
        current_ids = set()
        for a in assignments:
            is_new, date_changed = db.upsert_assignment(
                assignment_id=a.assignment_id,
                title=a.title,
                course_name=a.course_name,
                due_date=a.due_date.isoformat() if a.due_date else None,
                status=a.status,
                source_url=a.source_url,
            )
            current_ids.add(a.assignment_id)

            if date_changed:
                old_due = db.get_assignment_due_date(a.assignment_id)
                if old_due:
                    await notifier.send_date_changed(
                        _assignment_to_dict(
                            a.assignment_id,
                            a.title,
                            a.course_name,
                            a.due_date,
                            a.source_url,
                        ),
                        old_date=old_due,
                        new_date=a.due_date.isoformat() if a.due_date else "",
                    )

        # 6. Detect new assignments and notify
        new_assignments = db.get_new_assignments(current_ids)
        if new_assignments:
            logger.info("Found %d new assignment(s)", len(new_assignments))
            notifier = DiscordNotifier(
                config.discord_webhook_url,
                timeout=config.request_timeout_seconds,
                max_retries=config.max_retry_attempts,
            )
            try:
                for a in new_assignments:
                    if not db.is_new_assignment_notified(a.assignment_id):
                        # Get full assignment data from DB
                        full_assignment = db.get_assignment(a.assignment_id)
                        if full_assignment:
                            success = await notifier.send_new_assignment(
                                _assignment_to_dict(
                                    full_assignment.assignment_id,
                                    full_assignment.title,
                                    full_assignment.course_name,
                                    datetime.fromisoformat(full_assignment.due_date.replace("Z", "+00:00")) if full_assignment.due_date else datetime.now(timezone.utc),
                                    full_assignment.source_url,
                                ),
                                tz_name=config.timezone,
                            )
                            if success:
                                db.mark_new_assignment_notified(a.assignment_id)
                                logger.info(
                                    "New assignment notification sent: %s",
                                    full_assignment.title,
                                )
            finally:
                await notifier.close()

        # 7. Compute week info
        now = datetime.now(config.tz)
        week_key = now.strftime("%Y-W%W")  # ISO week
        today = now.isoweekday()  # 1=Monday

        # 8. Weekly digest check
        if today == config.weekly_digest_day:
            logger.info("Today is %s — checking weekly digest", now.strftime("%A"))
            if not db.is_week_digest_sent(week_key):
                week_assignments = db.get_assignments_by_week(
                    get_week_start(now), get_week_end(now)
                )
                logger.info(
                    "Weekly digest: %d assignment(s) due this week",
                    len(week_assignments),
                )

                if week_assignments:
                    # Build dict list for notifier
                    digest_dicts = []
                    for a in week_assignments:
                        if a.due_date:
                            due_dt = datetime.fromisoformat(a.due_date.replace("Z", "+00:00"))
                        else:
                            due_dt = datetime.now(timezone.utc)
                        digest_dicts.append(
                            _assignment_to_dict(
                                a.assignment_id,
                                a.title,
                                a.course_name,
                                due_dt,
                                a.source_url,
                            )
                        )

                    # Init notifier and send
                    notifier = DiscordNotifier(
                        config.discord_webhook_url,
                        timeout=config.request_timeout_seconds,
                        max_retries=config.max_retry_attempts,
                    )
                    try:
                        success = await notifier.send_weekly_digest(
                            digest_dicts,
                            week_key,
                            tz_name=config.timezone,
                        )
                        if success:
                            db.mark_week_digest_sent(
                                week_key, [a.assignment_id for a in week_assignments]
                            )
                            logger.info(
                                "Weekly digest sent: %d assignment(s) due this week",
                                len(week_assignments),
                            )
                        else:
                            logger.warning(
                                "Weekly digest webhook was rejected; will retry next cycle",
                            )
                    finally:
                        await notifier.close()
            else:
                logger.info("Weekly digest already sent this week (%s)", week_key)

        # 9. 24h alerts
        logger.info("Checking 24h alerts...")
        notifier = DiscordNotifier(
            config.discord_webhook_url,
            timeout=config.request_timeout_seconds,
            max_retries=config.max_retry_attempts,
        )
        try:
            alert_count = 0
            for assignment in assignments:
                if is_due_within_hours(assignment.due_date, now, 24):
                    if not db.is_24h_alerted(assignment.assignment_id):
                        success = await notifier.send_24h_alert(
                            _assignment_to_dict(
                                assignment.assignment_id,
                                assignment.title,
                                assignment.course_name,
                                assignment.due_date,
                                assignment.source_url,
                            ),
                            tz_name=config.timezone,
                        )
                        if success:
                            db.mark_24h_alerted(assignment.assignment_id)
                            alert_count += 1
                            logger.info(
                                "24h alert sent: %r (due in %s)",
                                assignment.title,
                                _format_time_remaining(assignment.due_date, now),
                            )
                        else:
                            logger.warning(
                                "24h alert webhook rejected for %s; will retry next cycle",
                                assignment.title,
                            )
                    else:
                        logger.debug(
                            "24h alert already sent for %s",
                            assignment.title,
                        )

            if alert_count == 0:
                logger.info("No 24h alerts to send")

        finally:
            await notifier.close()

        # 10. 3h alerts
        logger.info("Checking 3h alerts...")
        notifier = DiscordNotifier(
            config.discord_webhook_url,
            timeout=config.request_timeout_seconds,
            max_retries=config.max_retry_attempts,
        )
        try:
            alert_3h_count = 0
            for assignment in assignments:
                if is_due_within_hours(assignment.due_date, now, 3):
                    if not db.is_3h_alerted(assignment.assignment_id):
                        success = await notifier.send_3h_alert(
                            _assignment_to_dict(
                                assignment.assignment_id,
                                assignment.title,
                                assignment.course_name,
                                assignment.due_date,
                                assignment.source_url,
                            ),
                            tz_name=config.timezone,
                        )
                        if success:
                            db.mark_3h_alerted(assignment.assignment_id)
                            alert_3h_count += 1
                            logger.info(
                                "3h alert sent: %r (due in %s)",
                                assignment.title,
                                _format_time_remaining(assignment.due_date, now),
                            )
                        else:
                            logger.warning(
                                "3h alert webhook rejected for %s; will retry next cycle",
                                assignment.title,
                            )
                    else:
                        logger.debug(
                            "3h alert already sent for %s",
                            assignment.title,
                        )

            if alert_3h_count == 0:
                logger.info("No 3h alerts to send")

        finally:
            await notifier.close()

        logger.info("Check cycle complete (sent %d notification(s))", alert_count)

    finally:
        await scraper.close()
        db.close()

    return 0


def _format_time_remaining(due_date: datetime, now: datetime) -> str:
    """Format hours remaining into a human-readable string."""
    delta = due_date - now
    total_seconds = delta.total_seconds()
    if total_seconds <= 0:
        return "overdue"
    hours = total_seconds / 3600.0
    if hours < 1:
        return f"~{int(hours * 60)} minutes"
    elif hours < 24:
        return f"~{int(hours)} hours"
    else:
        days = int(hours // 24)
        remaining_hours = int(hours % 24)
        if remaining_hours > 0:
            return f"{days} days, {remaining_hours}h"
        return f"{days} days"


# ─── CLI ───────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
