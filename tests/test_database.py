"""Comprehensive unit tests for database.py."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from database import AssignmentDatabase, AssignmentRow


# ─── Helpers ───────────────────────────────────────────────────────────────────


def make_db(tmp_path: Path) -> AssignmentDatabase:
    """Create a fresh database backed by a guaranteed-unique temp file.

    Uses (pid, UUID) to guarantee uniqueness across all processes and calls,
    avoiding pytest's tmp_path reuse across tests that share the same directory.
    """
    db_file = tmp_path / f"db{os.getpid()}_{uuid.uuid4().hex}.db"
    db = AssignmentDatabase(str(db_file))
    db.connect()
    return db


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Test connection and table creation ────────────────────────────────────────


class TestConnection:
    def test_connect_creates_db_file(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test.db"
        db = AssignmentDatabase(str(db_file))
        assert not db_file.exists()
        db.connect()
        assert db_file.exists()
        db.close()

    def test_close_cleans_up_conn(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        assert db._conn is not None
        db.close()
        assert db._conn is None

    def test_tables_created_on_connect(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        # Query sqlite_master to verify tables exist
        tables = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {row["name"] for row in tables}
        assert "assignments" in table_names
        assert "notifications" in table_names
        assert "bot_state" in table_names
        db.close()

    def test_indexes_created(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        indexes = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = {row["name"] for row in indexes}
        assert "idx_notifications_type" in index_names
        assert "idx_notifications_assignment" in index_names
        assert "idx_assignments_due" in index_names
        db.close()


# ─── Test Assignment CRUD ───────────────────────────────────────────────────────


class TestAssignmentCRUD:
    def test_upsert_new_assignment_returns_tuple_true_false(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        result = db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1",
            course_name="Matemáticas",
            course_id="MATH101",
            due_date="2026-05-01T23:59:00Z",
            status="Pending",
            source_url="https://example.com/assign_001",
        )
        assert result == (True, False)
        db.close()

    def test_upsert_existing_assignment_returns_tuple_false_false(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1",
            course_name="Matemáticas",
        )
        result = db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1",
            course_name="Matemáticas",
        )
        assert result == (False, False)
        db.close()

    def test_upsert_existing_assignment_with_changed_date_returns_tuple_false_true(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1",
            course_name="Matemáticas",
            due_date="2026-05-01T23:59:00Z",
        )
        result = db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1",
            course_name="Matemáticas",
            due_date="2026-05-02T23:59:00Z",
        )
        assert result == (False, True)
        db.close()

    def test_upsert_updates_last_seen_at(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1",
            course_name="Matemáticas",
        )
        first_row = db.get_assignment("assign_001")
        assert first_row is not None
        first_last_seen = first_row.last_seen_at

        # Wait a tiny bit then upsert again
        import time
        time.sleep(0.01)
        db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1",
            course_name="Matemáticas",
        )
        second_row = db.get_assignment("assign_001")
        assert second_row is not None
        assert second_row.last_seen_at >= first_last_seen
        db.close()

    def test_upsert_updates_fields_if_changed(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1",
            course_name="Matemáticas",
            due_date="2026-05-01T23:59:00Z",
        )
        db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1 Updated",
            course_name="Física",
            due_date="2026-05-02T23:59:00Z",
        )
        row = db.get_assignment("assign_001")
        assert row is not None
        assert row.title == "Tarea 1 Updated"
        assert row.course_name == "Física"
        assert row.due_date == "2026-05-02T23:59:00Z"
        db.close()

    def test_get_assignment_returns_correct_data(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1",
            course_name="Matemáticas",
            course_id="MATH101",
            due_date="2026-05-01T23:59:00Z",
            status="Pending",
            source_url="https://example.com/assign_001",
        )
        row = db.get_assignment("assign_001")
        assert row is not None
        assert row.assignment_id == "assign_001"
        assert row.title == "Tarea 1"
        assert row.course_name == "Matemáticas"
        assert row.course_id == "MATH101"
        assert row.due_date == "2026-05-01T23:59:00Z"
        assert row.status == "Pending"
        assert row.source_url == "https://example.com/assign_001"
        db.close()

    def test_get_assignment_returns_none_for_missing(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        row = db.get_assignment("nonexistent")
        assert row is None
        db.close()

    def test_get_all_assignments_returns_all(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment("assign_001", "Tarea 1", "Matemáticas")
        db.upsert_assignment("assign_002", "Tarea 2", "Física")
        db.upsert_assignment("assign_003", "Tarea 3", "Química")
        rows = db.get_all_assignments()
        assert len(rows) == 3
        ids = {r.assignment_id for r in rows}
        assert ids == {"assign_001", "assign_002", "assign_003"}
        db.close()

    def test_empty_db_returns_empty_list(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        rows = db.get_all_assignments()
        assert rows == []
        db.close()


# ─── Test Due Date Query ────────────────────────────────────────────────────────


class TestDueDateQueries:
    def test_get_assignments_due_between(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment("a1", "A1", "C1", due_date="2026-05-01T10:00:00Z")
        db.upsert_assignment("a2", "A2", "C1", due_date="2026-05-03T10:00:00Z")
        db.upsert_assignment("a3", "A3", "C1", due_date="2026-05-05T10:00:00Z")

        after = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
        before = datetime(2026, 5, 4, 0, 0, 0, tzinfo=timezone.utc)
        rows = db.get_assignments_due_between(after, before)
        assert len(rows) == 2
        ids = {r.assignment_id for r in rows}
        assert "a1" in ids
        assert "a2" in ids
        assert "a3" not in ids
        db.close()

    def test_get_assignments_due_within_hours(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        now = utc_now()
        # Due in 2 hours - should be included
        due_2h = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.upsert_assignment("a1", "A1", "C1", due_date=due_2h)
        # Due in 48 hours - should NOT be included
        due_48h = (now + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.upsert_assignment("a2", "A2", "C1", due_date=due_48h)
        # No due date - should NOT be included
        db.upsert_assignment("a3", "A3", "C1", due_date=None)

        rows = db.get_assignments_due_within_hours(24)
        ids = {r.assignment_id for r in rows}
        assert "a1" in ids
        assert "a2" not in ids
        db.close()

    def test_get_assignments_by_week(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        # Monday of week: 2026-04-27, Sunday of week: 2026-05-03
        # week_end is Saturday May 2 23:59:59 to NOT include Sunday
        db.upsert_assignment("a1", "A1", "C1", due_date="2026-04-28T10:00:00Z")  # Tuesday (in week)
        db.upsert_assignment("a2", "A2", "C1", due_date="2026-04-30T10:00:00Z")  # Thursday (in week)
        db.upsert_assignment("a3", "A3", "C1", due_date="2026-05-02T10:00:00Z")  # Saturday (in week)
        db.upsert_assignment("a4", "A4", "C1", due_date="2026-05-03T10:00:00Z")  # Sunday (NOT in week - week ends Saturday)

        week_start = datetime(2026, 4, 27, 0, 0, 0, tzinfo=timezone.utc)
        week_end = datetime(2026, 5, 2, 23, 59, 59, tzinfo=timezone.utc)
        rows = db.get_assignments_by_week(week_start, week_end)
        assert len(rows) == 3
        ids = {r.assignment_id for r in rows}
        assert "a1" in ids
        assert "a2" in ids
        assert "a3" in ids
        assert "a4" not in ids
        db.close()

    def test_no_assignments_due_returns_empty(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment("a1", "A1", "C1", due_date="2026-06-01T10:00:00Z")
        after = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
        before = datetime(2026, 5, 2, 0, 0, 0, tzinfo=timezone.utc)
        rows = db.get_assignments_due_between(after, before)
        assert rows == []
        db.close()

    def test_all_assignments_due_returns_all(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        now = utc_now()
        for i in range(5):
            due = (now + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            db.upsert_assignment(f"a{i}", f"A{i}", "C1", due_date=due)

        week_start = now
        week_end = now + timedelta(days=7)
        rows = db.get_assignments_by_week(week_start, week_end)
        assert len(rows) == 5
        db.close()


# ─── Test Notification Tracking ────────────────────────────────────────────────


class TestNotificationTracking:
    def test_is_week_digest_sent_false_initially(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        assert db.is_week_digest_sent("2026-W18") is False
        db.close()

    def test_is_week_digest_sent_true_after_mark(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.mark_week_digest_sent("2026-W18", ["assign_001", "assign_002"])
        assert db.is_week_digest_sent("2026-W18") is True
        db.close()

    def test_is_week_digest_sent_different_weeks(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.mark_week_digest_sent("2026-W17", ["assign_001"])
        db.mark_week_digest_sent("2026-W19", ["assign_002"])
        assert db.is_week_digest_sent("2026-W17") is True
        assert db.is_week_digest_sent("2026-W18") is False
        assert db.is_week_digest_sent("2026-W19") is True
        db.close()

    def test_mark_week_digest_sent_persists(self, tmp_path: Path) -> None:
        db1 = make_db(tmp_path)
        db1.mark_week_digest_sent("2026-W20", ["assign_001"])
        db1.close()

        db2 = AssignmentDatabase(db1.db_path)
        db2.connect()
        assert db2.is_week_digest_sent("2026-W20") is True
        db2.close()

    def test_is_24h_alerted_false_initially(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        assert db.is_24h_alerted("assign_001") is False
        db.close()

    def test_is_24h_alerted_true_after_mark(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.mark_24h_alerted("assign_001")
        assert db.is_24h_alerted("assign_001") is True
        db.close()

    def test_is_24h_alerted_different_assignments(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.mark_24h_alerted("assign_001")
        assert db.is_24h_alerted("assign_001") is True
        assert db.is_24h_alerted("assign_002") is False
        db.close()

    def test_mark_24h_alerted_persists(self, tmp_path: Path) -> None:
        db1 = make_db(tmp_path)
        db1.mark_24h_alerted("assign_001")
        db1.close()

        db2 = AssignmentDatabase(db1.db_path)
        db2.connect()
        assert db2.is_24h_alerted("assign_001") is True
        db2.close()

    def test_is_3h_alerted_false_initially(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        assert db.is_3h_alerted("assign_001") is False
        db.close()

    def test_is_3h_alerted_true_after_mark(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.mark_3h_alerted("assign_001")
        assert db.is_3h_alerted("assign_001") is True
        db.close()

    def test_is_3h_alerted_different_assignments(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.mark_3h_alerted("assign_001")
        assert db.is_3h_alerted("assign_001") is True
        assert db.is_3h_alerted("assign_002") is False
        db.close()

    def test_mark_3h_alerted_persists(self, tmp_path: Path) -> None:
        db1 = make_db(tmp_path)
        db1.mark_3h_alerted("assign_001")
        db1.close()

        db2 = AssignmentDatabase(db1.db_path)
        db2.connect()
        assert db2.is_3h_alerted("assign_001") is True
        db2.close()

    def test_get_assignment_due_date_returns_correct_date(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment(
            assignment_id="assign_001",
            title="Tarea 1",
            course_name="Matemáticas",
            due_date="2026-05-01T23:59:00Z",
        )
        due = db.get_assignment_due_date("assign_001")
        assert due == "2026-05-01T23:59:00Z"
        db.close()

    def test_get_assignment_due_date_returns_none_for_missing(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        due = db.get_assignment_due_date("nonexistent")
        assert due is None
        db.close()

    def test_is_new_assignment_notified_false_initially(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        assert db.is_new_assignment_notified("assign_001") is False
        db.close()

    def test_is_new_assignment_notified_true_after_mark(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.mark_new_assignment_notified("assign_001")
        assert db.is_new_assignment_notified("assign_001") is True
        db.close()

    def test_mark_new_assignment_notified_persists(self, tmp_path: Path) -> None:
        db1 = make_db(tmp_path)
        db1.mark_new_assignment_notified("assign_001")
        db1.close()

        db2 = AssignmentDatabase(db1.db_path)
        db2.connect()
        assert db2.is_new_assignment_notified("assign_001") is True
        db2.close()


# ─── Test Bot State ─────────────────────────────────────────────────────────────


class TestBotState:
    def test_get_state_returns_default_when_missing(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        result = db.get_state("nonexistent_key")
        assert result == ""
        result = db.get_state("nonexistent_key", "default_value")
        assert result == "default_value"
        db.close()

    def test_set_and_get_state(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.set_state("last_run", "2026-04-27T10:00:00Z")
        result = db.get_state("last_run")
        assert result == "2026-04-27T10:00:00Z"
        db.close()

    def test_set_state_overwrites_previous(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.set_state("counter", "1")
        db.set_state("counter", "2")
        result = db.get_state("counter")
        assert result == "2"
        db.close()

    def test_state_persists(self, tmp_path: Path) -> None:
        db1 = make_db(tmp_path)
        db1.set_state("test_key", "test_value")
        db1.close()

        db2 = AssignmentDatabase(db1.db_path)
        db2.connect()
        result = db2.get_state("test_key")
        assert result == "test_value"
        db2.close()


# ─── Test New Assignment Detection ─────────────────────────────────────────────


class TestNewAssignmentDetection:
    def test_get_new_assignments_empty_db(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        current = {"assign_001", "assign_002"}
        new_assignments = db.get_new_assignments(current)
        assert len(new_assignments) == 2
        ids = {a.assignment_id for a in new_assignments}
        assert ids == current
        db.close()

    def test_get_new_assignments_some_exist(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        # Existing assignment
        db.upsert_assignment("assign_001", "Existing", "C1")
        # New assignments
        current = {"assign_001", "assign_002", "assign_003"}
        new_assignments = db.get_new_assignments(current)
        assert len(new_assignments) == 2
        ids = {a.assignment_id for a in new_assignments}
        assert "assign_002" in ids
        assert "assign_003" in ids
        assert "assign_001" not in ids
        db.close()

    def test_get_new_assignments_all_exist(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment("assign_001", "A1", "C1")
        db.upsert_assignment("assign_002", "A2", "C1")
        current = {"assign_001", "assign_002"}
        new_assignments = db.get_new_assignments(current)
        assert new_assignments == []
        db.close()

    def test_get_new_assignments_empty_current(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment("assign_001", "A1", "C1")
        new_assignments = db.get_new_assignments(set())
        assert new_assignments == []
        db.close()

    def test_get_assignments_not_in_current(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment("assign_001", "A1", "C1")
        db.upsert_assignment("assign_002", "A2", "C1")
        db.upsert_assignment("assign_003", "A3", "C1")

        not_in_current = db.get_assignments_not_in_current({"assign_001"})
        assert len(not_in_current) == 2
        ids = {a.assignment_id for a in not_in_current}
        assert "assign_002" in ids
        assert "assign_003" in ids
        assert "assign_001" not in ids
        db.close()

    def test_get_assignments_not_in_current_all_gone(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment("assign_001", "A1", "C1")
        db.upsert_assignment("assign_002", "A2", "C1")
        # DB has assign_001, assign_002; current has only assign_002
        # assign_001 is "all gone" from current
        not_in_current = db.get_assignments_not_in_current({"assign_002"})
        assert len(not_in_current) == 1
        assert not_in_current[0].assignment_id == "assign_001"
        db.close()

    def test_get_assignments_not_in_current_empty_db(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        not_in_current = db.get_assignments_not_in_current({"assign_001"})
        assert not_in_current == []
        db.close()


# ─── Test Migration from JSON ──────────────────────────────────────────────────


class TestMigration:
    def test_migrate_from_json_preserves_data(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        json_file = tmp_path / "notified_assignments.json"
        data = {
            "assignments": [
                {
                    "id": "assign_001",
                    "title": "Tarea 1",
                    "course_name": "Matemáticas",
                    "course_id": "MATH101",
                    "due_date": "2026-05-01T23:59:00Z",
                    "status": "Pending",
                    "source_url": "https://example.com/1",
                },
                {
                    "id": "assign_002",
                    "title": "Tarea 2",
                    "course_name": "Física",
                    "course_id": "PHYS101",
                    "due_date": "2026-05-03T23:59:00Z",
                    "status": "Submitted",
                    "source_url": "https://example.com/2",
                },
            ]
        }
        json_file.write_text(json.dumps(data), encoding="utf-8")

        count = db.migrate_from_json(str(json_file))
        assert count == 2

        row1 = db.get_assignment("assign_001")
        assert row1 is not None
        assert row1.title == "Tarea 1"
        assert row1.course_name == "Matemáticas"

        row2 = db.get_assignment("assign_002")
        assert row2 is not None
        assert row2.title == "Tarea 2"
        assert row2.status == "Submitted"
        db.close()

    def test_migrate_empty_json(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        json_file = tmp_path / "empty.json"
        json_file.write_text(json.dumps({"assignments": []}), encoding="utf-8")

        count = db.migrate_from_json(str(json_file))
        assert count == 0
        db.close()

    def test_migrate_missing_file_returns_zero(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        count = db.migrate_from_json("/nonexistent/path.json")
        assert count == 0
        db.close()

    def test_migrate_invalid_json_returns_zero(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        json_file = tmp_path / "invalid.json"
        json_file.write_text("not valid json{", encoding="utf-8")

        count = db.migrate_from_json(str(json_file))
        assert count == 0
        db.close()

    def test_migrate_uses_assignment_id_field(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        json_file = tmp_path / "test.json"
        data = {
            "assignments": [
                {
                    "assignment_id": "assign_003",  # Using assignment_id instead of id
                    "title": "Tarea 3",
                    "course_name": "Química",
                }
            ]
        }
        json_file.write_text(json.dumps(data), encoding="utf-8")

        count = db.migrate_from_json(str(json_file))
        assert count == 1
        row = db.get_assignment("assign_003")
        assert row is not None
        assert row.title == "Tarea 3"
        db.close()


# ─── Test edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_due_date_null_handled(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment("a1", "A1", "C1", due_date=None)
        row = db.get_assignment("a1")
        assert row is not None
        assert row.due_date is None
        db.close()

    def test_upsert_assignment_with_minimal_fields(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment("a1", "Minimal Assignment", "Some Course")
        row = db.get_assignment("a1")
        assert row is not None
        assert row.assignment_id == "a1"
        assert row.title == "Minimal Assignment"
        assert row.course_id == ""
        assert row.due_date is None
        assert row.status == "Pending"
        assert row.source_url == ""
        db.close()

    def test_multiple_upserts_same_id_increments_last_seen(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.upsert_assignment("a1", "A1", "C1")
        row1 = db.get_assignment("a1")
        first_seen = row1.first_seen_at
        last_seen_before = row1.last_seen_at

        import time
        time.sleep(0.01)
        db.upsert_assignment("a1", "A1 Updated", "C1")
        row2 = db.get_assignment("a1")
        assert row2.first_seen_at == first_seen
        assert row2.last_seen_at >= last_seen_before
        db.close()

    def test_bot_state_empty_key(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.set_state("", "empty_key_value")
        result = db.get_state("")
        assert result == "empty_key_value"
        db.close()

    def test_bot_state_empty_value(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        db.set_state("some_key", "")
        result = db.get_state("some_key")
        assert result == ""
        db.close()

    def test_different_notification_types_independent(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        # Mark week digest
        db.mark_week_digest_sent("2026-W18", ["assign_001"])
        # Mark 24h alert
        db.mark_24h_alerted("assign_001")
        # Mark new assignment notified
        db.mark_new_assignment_notified("assign_001")

        assert db.is_week_digest_sent("2026-W18") is True
        assert db.is_24h_alerted("assign_001") is True
        assert db.is_new_assignment_notified("assign_001") is True

        # Check a different week/assignment is not affected
        assert db.is_week_digest_sent("2026-W19") is False
        assert db.is_24h_alerted("assign_002") is False
        assert db.is_new_assignment_notified("assign_002") is False
        db.close()

    def test_large_assignment_id(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        large_id = "x" * 1000
        db.upsert_assignment(large_id, "Large ID Assignment", "C1")
        row = db.get_assignment(large_id)
        assert row is not None
        assert row.assignment_id == large_id
        db.close()

    def test_special_characters_in_title(self, tmp_path: Path) -> None:
        db = make_db(tmp_path)
        title = "Test & < > \" ' Assignment: 100%!"
        db.upsert_assignment("a1", title, "C1")
        row = db.get_assignment("a1")
        assert row is not None
        assert row.title == title
        db.close()