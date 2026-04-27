"""SQLite database for Blackboard Discord Bot.

Replaces the JSON cache with a proper database for reliable
assignment tracking, new assignment detection, and notification history.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


# ─── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class AssignmentRow:
    """Represents a row from the assignments table."""
    assignment_id: str
    title: str
    course_name: str
    course_id: str
    due_date: Optional[str]  # ISO 8601
    status: str
    source_url: str
    first_seen_at: str  # ISO 8601
    last_seen_at: str   # ISO 8601


@dataclass
class NotificationRow:
    """Represents a row from the notifications table."""
    id: int
    assignment_id: str
    type: str  # 'weekly_digest', '24h_alert', 'new_assignment'
    sent_at: str
    week_key: Optional[str]


# ─── Helper ───────────────────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Database Class ───────────────────────────────────────────────────────────


class AssignmentDatabase:
    """SQLite database for assignment tracking and notification history.

    Thread-safety: single-process only, no locks.
    All datetimes stored as ISO 8601 strings in UTC.
    """

    def __init__(self, db_path: str = "./assignments.db") -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Open connection and ensure tables exist."""
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_tables()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_tables(self) -> None:
        """Create all tables and indexes if they don't exist."""
        assert self._conn is not None
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS assignments (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                course_name TEXT NOT NULL,
                course_id TEXT DEFAULT '',
                due_date TEXT,
                status TEXT DEFAULT 'Pending',
                source_url TEXT DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id TEXT NOT NULL,
                type TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                week_key TEXT,
                FOREIGN KEY (assignment_id) REFERENCES assignments(id)
            );

            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_notifications_type
                ON notifications(type, week_key);
            CREATE INDEX IF NOT EXISTS idx_notifications_assignment
                ON notifications(assignment_id);
            CREATE INDEX IF NOT EXISTS idx_assignments_due
                ON assignments(due_date);
        """)
        self._conn.commit()

    # ── Assignment CRUD ───────────────────────────────────────────────────────

    def upsert_assignment(
        self,
        assignment_id: str,
        title: str,
        course_name: str,
        course_id: str = "",
        due_date: Optional[str] = None,
        status: str = "Pending",
        source_url: str = "",
    ) -> tuple[bool, bool]:
        """Insert or update an assignment.

        Returns:
            A tuple of (is_new, date_changed):
            - is_new: True if this assignment was not in DB before
            - date_changed: True if the due_date changed from what was stored
        """
        assert self._conn is not None
        now = _utc_now_iso()

        # Check if exists and get old due_date
        existing = self._conn.execute(
            "SELECT due_date FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()

        if existing is None:
            # INSERT new
            self._conn.execute(
                """
                INSERT INTO assignments (id, title, course_name, course_id, due_date, status, source_url, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (assignment_id, title, course_name, course_id, due_date, status, source_url, now, now),
            )
            self._conn.commit()
            return (True, False)
        else:
            # UPDATE existing - update last_seen_at and fields if changed
            old_due_date = existing["due_date"]
            date_changed = old_due_date != due_date
            self._conn.execute(
                """
                UPDATE assignments
                SET title = ?, course_name = ?, course_id = ?, due_date = ?, status = ?, source_url = ?, last_seen_at = ?
                WHERE id = ?
                """,
                (title, course_name, course_id, due_date, status, source_url, now, assignment_id),
            )
            self._conn.commit()
            return (False, date_changed)

    def upsert_assignment_from_obj(self, assignment: Any) -> tuple[bool, bool]:
        """Insert or update an assignment from an Assignment dataclass.

        Args:
            assignment: An object with attributes: assignment_id, title,
                       course_name, due_date (datetime|None), status, source_url

        Returns:
            A tuple of (is_new, date_changed) as in upsert_assignment.
        """
        return self.upsert_assignment(
            assignment_id=assignment.assignment_id,
            title=assignment.title,
            course_name=assignment.course_name,
            due_date=assignment.due_date.isoformat() if assignment.due_date else None,
            status=assignment.status,
            source_url=assignment.source_url,
        )

    def get_assignment(self, assignment_id: str) -> Optional[AssignmentRow]:
        """Get a single assignment by ID. Returns None if not found."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
        if row is None:
            return None
        return AssignmentRow(
            assignment_id=row["id"],
            title=row["title"],
            course_name=row["course_name"],
            course_id=row["course_id"],
            due_date=row["due_date"],
            status=row["status"],
            source_url=row["source_url"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
        )

    def get_all_assignments(self) -> list[AssignmentRow]:
        """Get all assignments."""
        assert self._conn is not None
        rows = self._conn.execute("SELECT * FROM assignments").fetchall()
        return [
            AssignmentRow(
                assignment_id=row["id"],
                title=row["title"],
                course_name=row["course_name"],
                course_id=row["course_id"],
                due_date=row["due_date"],
                status=row["status"],
                source_url=row["source_url"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
            )
            for row in rows
        ]

    def get_assignments_due_between(
        self, after: datetime, before: datetime
    ) -> list[AssignmentRow]:
        """Get assignments with due_date between two datetimes (inclusive)."""
        assert self._conn is not None
        after_str = after.strftime("%Y-%m-%dT%H:%M:%SZ")
        before_str = before.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self._conn.execute(
            """
            SELECT * FROM assignments
            WHERE due_date IS NOT NULL
              AND due_date >= ?
              AND due_date <= ?
            ORDER BY due_date
            """,
            (after_str, before_str),
        ).fetchall()
        return [
            AssignmentRow(
                assignment_id=row["id"],
                title=row["title"],
                course_name=row["course_name"],
                course_id=row["course_id"],
                due_date=row["due_date"],
                status=row["status"],
                source_url=row["source_url"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
            )
            for row in rows
        ]

    def get_assignments_due_within_hours(self, hours: int) -> list[AssignmentRow]:
        """Get assignments due within the next N hours."""
        assert self._conn is not None
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = self._conn.execute(
            """
            SELECT * FROM assignments
            WHERE due_date IS NOT NULL
              AND due_date >= ?
            ORDER BY due_date
            """,
            (now_str,),
        ).fetchall()

        result = []
        for row in rows:
            if row["due_date"] is not None:
                try:
                    due = datetime.fromisoformat(row["due_date"].replace("Z", "+00:00"))
                    due_naive = due.replace(tzinfo=None)
                    now_naive = now.replace(tzinfo=None)
                    if due_naive <= now_naive + timedelta(hours=hours):
                        result.append(AssignmentRow(
                            assignment_id=row["id"],
                            title=row["title"],
                            course_name=row["course_name"],
                            course_id=row["course_id"],
                            due_date=row["due_date"],
                            status=row["status"],
                            source_url=row["source_url"],
                            first_seen_at=row["first_seen_at"],
                            last_seen_at=row["last_seen_at"],
                        ))
                except ValueError:
                    continue
        return result

    def get_assignments_by_week(
        self, week_start: datetime, week_end: datetime
    ) -> list[AssignmentRow]:
        """Get assignments due between week_start and week_end (inclusive)."""
        assert self._conn is not None
        start_str = week_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = week_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self._conn.execute(
            """
            SELECT * FROM assignments
            WHERE due_date IS NOT NULL
              AND due_date >= ?
              AND due_date <= ?
            ORDER BY due_date
            """,
            (start_str, end_str),
        ).fetchall()
        return [
            AssignmentRow(
                assignment_id=row["id"],
                title=row["title"],
                course_name=row["course_name"],
                course_id=row["course_id"],
                due_date=row["due_date"],
                status=row["status"],
                source_url=row["source_url"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
            )
            for row in rows
        ]

    # ── Notification Tracking ─────────────────────────────────────────────────

    def is_week_digest_sent(self, week_key: str) -> bool:
        """Return True if a weekly digest was already sent for this ISO week."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT 1 FROM notifications WHERE type = 'weekly_digest' AND week_key = ?",
            (week_key,),
        ).fetchone()
        return row is not None

    def mark_week_digest_sent(self, week_key: str, assignment_ids: list[str]) -> None:
        """Record that a weekly digest was sent for this week."""
        assert self._conn is not None
        now = _utc_now_iso()
        for aid in assignment_ids:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO notifications (assignment_id, type, sent_at, week_key)
                VALUES (?, 'weekly_digest', ?, ?)
                """,
                (aid, now, week_key),
            )
        self._conn.commit()

    def is_24h_alerted(self, assignment_id: str) -> bool:
        """Return True if a 24h alert was already sent for this assignment."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT 1 FROM notifications WHERE type = '24h_alert' AND assignment_id = ?",
            (assignment_id,),
        ).fetchone()
        return row is not None

    def mark_24h_alerted(self, assignment_id: str) -> None:
        """Record that a 24h alert was sent for this assignment."""
        assert self._conn is not None
        now = _utc_now_iso()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO notifications (assignment_id, type, sent_at, week_key)
            VALUES (?, '24h_alert', ?, NULL)
            """,
            (assignment_id, now),
        )
        self._conn.commit()

    def is_3h_alerted(self, assignment_id: str) -> bool:
        """Return True if a 3h alert was already sent for this assignment."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT 1 FROM notifications WHERE assignment_id = ? AND type = '3h_alert'",
            (assignment_id,)
        ).fetchone()
        return row is not None

    def mark_3h_alerted(self, assignment_id: str) -> None:
        """Record that a 3h alert was sent for this assignment."""
        assert self._conn is not None
        now = _utc_now_iso()
        self._conn.execute(
            "INSERT INTO notifications (assignment_id, type, sent_at) VALUES (?, '3h_alert', ?)",
            (assignment_id, now)
        )
        self._conn.commit()

    def get_assignment_due_date(self, assignment_id: str) -> Optional[str]:
        """Return due_date from DB for an assignment."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT due_date FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
        return row["due_date"] if row else None

    def is_new_assignment_notified(self, assignment_id: str) -> bool:
        """Return True if a new assignment notification was sent."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT 1 FROM notifications WHERE type = 'new_assignment' AND assignment_id = ?",
            (assignment_id,)
        ).fetchone()
        return row is not None

    def mark_new_assignment_notified(self, assignment_id: str) -> None:
        """Record that a new assignment notification was sent."""
        assert self._conn is not None
        now = _utc_now_iso()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO notifications (assignment_id, type, sent_at, week_key)
            VALUES (?, 'new_assignment', ?, NULL)
            """,
            (assignment_id, now),
        )
        self._conn.commit()

    # ── Bot State ─────────────────────────────────────────────────────────────

    def get_state(self, key: str, default: str = "") -> str:
        """Get a bot state value. Returns default if key is missing."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        return row["value"]

    def set_state(self, key: str, value: str) -> None:
        """Set a bot state value."""
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    # ── New Assignment Detection ───────────────────────────────────────────────

    def get_new_assignments(self, current_ids: set[str]) -> list[AssignmentRow]:
        """Return assignments in current scrape that are NOT in DB (newly appeared)."""
        assert self._conn is not None
        if not current_ids:
            return []
        # Get all existing IDs
        existing_ids = set(
            row["id"] for row in self._conn.execute("SELECT id FROM assignments").fetchall()
        )
        new_ids = current_ids - existing_ids
        # Return minimal AssignmentRow objects for the new IDs (only assignment_id is populated)
        return [
            AssignmentRow(
                assignment_id=aid,
                title="",
                course_name="",
                course_id="",
                due_date=None,
                status="",
                source_url="",
                first_seen_at="",
                last_seen_at="",
            )
            for aid in new_ids
        ]

    def get_assignments_not_in_current(self, current_ids: set[str]) -> list[AssignmentRow]:
        """Return assignments in DB but NOT in current scrape (might have been removed/completed)."""
        assert self._conn is not None
        all_rows = self._conn.execute("SELECT * FROM assignments").fetchall()
        result = []
        for row in all_rows:
            if row["id"] not in current_ids:
                result.append(AssignmentRow(
                    assignment_id=row["id"],
                    title=row["title"],
                    course_name=row["course_name"],
                    course_id=row["course_id"],
                    due_date=row["due_date"],
                    status=row["status"],
                    source_url=row["source_url"],
                    first_seen_at=row["first_seen_at"],
                    last_seen_at=row["last_seen_at"],
                ))
        return result

    # ── Migration from JSON ────────────────────────────────────────────────────

    def migrate_from_json(self, json_path: str) -> int:
        """Import existing notified_assignments.json into SQLite. Returns count of migrated records."""
        path = Path(json_path)
        if not path.exists():
            return 0

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0

        count = 0
        # The JSON format expected: { "assignments": [...], "weekly_digests": {...}, ... }
        assignments = data.get("assignments", []) if isinstance(data, dict) else data
        if not isinstance(assignments, list):
            assignments = []

        for item in assignments:
            if not isinstance(item, dict):
                continue
            assignment_id = item.get("id") or item.get("assignment_id")
            if not assignment_id:
                continue

            self.upsert_assignment(
                assignment_id=assignment_id,
                title=item.get("title", ""),
                course_name=item.get("course_name", ""),
                course_id=item.get("course_id", ""),
                due_date=item.get("due_date"),
                status=item.get("status", "Pending"),
                source_url=item.get("source_url", ""),
            )
            count += 1

        return count