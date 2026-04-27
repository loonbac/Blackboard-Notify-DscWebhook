"""Comprehensive unit tests for notified_cache.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from notified_cache import (
    CacheError,
    CURRENT_SCHEMA_VERSION,
    NotificationCache,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


import os
import uuid


def make_cache(tmp_path: Path) -> NotificationCache:
    """Create a fresh cache backed by a guaranteed-unique temp file.

    Uses (pid, UUID) to guarantee uniqueness across all processes and calls,
    avoiding pytest's tmp_path reuse across tests that share the same directory.
    """
    # pid + uuid to avoid any reuse across processes/test workers
    cache_file = tmp_path / f"nc{os.getpid()}_{uuid.uuid4().hex}.json"
    cache = NotificationCache(cache_file)
    cache.load()
    cache._dirty = True
    cache.save()
    cache._dirty = False
    return cache


# ─── Test file creation on init ────────────────────────────────────────────────


class TestFileCreation:
    def test_load_on_nonexistent_file_creates_default_cache(self, tmp_path: Path) -> None:
        """load() on a non-existent file initializes default cache without raising."""
        cache_file = tmp_path / "cache.json"
        cache = NotificationCache(cache_file)
        cache.load()  # should not raise
        assert cache.get_all()["schema_version"] == CURRENT_SCHEMA_VERSION

    def test_default_cache_structure(self, tmp_path: Path) -> None:
        """A newly created cache has the expected default structure."""
        cache = make_cache(tmp_path)
        data = cache.get_all()
        assert data["schema_version"] == CURRENT_SCHEMA_VERSION
        assert data["weekly_digests"] == {}
        assert data["notified_24h_alerts"] == {}

    def test_cache_file_not_created_just_by_init(self, tmp_path: Path) -> None:
        """Just creating a NotificationCache does NOT create the file."""
        cache_file = tmp_path / "cache.json"
        _ = NotificationCache(cache_file)
        assert not cache_file.exists()

    def test_cache_file_created_after_save(self, tmp_path: Path) -> None:
        """File is created when save() is called."""
        cache = make_cache(tmp_path)
        assert cache._file_path.exists()


# ─── Test is_week_digest_sent ──────────────────────────────────────────────────


class TestWeekDigest:
    def test_returns_false_for_unknown_week(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        result = cache.is_week_digest_sent("2026-W18")
        assert result is False, f"Expected False but got True. cache file: {cache._file_path}, data: {cache.get_all()}"

    def test_returns_true_after_mark_sent(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        cache.mark_week_digest_sent("2026-W18", 3)
        result = cache.is_week_digest_sent("2026-W18")
        assert result is True

    def test_multiple_weeks_tracked_independently(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        # Verify clean state before any modifications
        data_before = cache.get_all()
        assert data_before["weekly_digests"] == {}, f"Expected empty digests but got: {data_before}"
        cache.mark_week_digest_sent("2026-W17", 1)
        cache.mark_week_digest_sent("2026-W19", 5)
        assert cache.is_week_digest_sent("2026-W17") is True
        assert cache.is_week_digest_sent("2026-W18") is False, f"W18 should be False but got True. data: {cache.get_all()}"
        assert cache.is_week_digest_sent("2026-W19") is True

    def test_mark_then_check_persists_after_save(self, tmp_path: Path) -> None:
        cache1 = make_cache(tmp_path)
        cache1.mark_week_digest_sent("2026-W20", 2)
        cache1.save()

        # Re-load from the SAME file that cache1 used
        cache2 = NotificationCache(cache1._file_path)
        cache2.load()
        assert cache2.is_week_digest_sent("2026-W20") is True


# ─── Test 24h alert ────────────────────────────────────────────────────────────


class Test24hAlert:
    def test_is_24h_alerted_false_for_unknown_assignment(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        assert cache.is_24h_alerted("assignment_abc", "2026-05-01T23:59:00Z") is False

    def test_is_24h_alerted_true_after_mark(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        cache.mark_24h_alerted(
            assignment_id="assignment_abc",
            title="Tarea 1",
            course="Matemáticas",
            due_date="2026-05-01T23:59:00Z",
        )
        assert cache.is_24h_alerted("assignment_abc", "2026-05-01T23:59:00Z") is True

    def test_different_assignment_not_confused(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        cache.mark_24h_alerted(
            assignment_id="assignment_abc",
            title="Tarea 1",
            course="Matemáticas",
            due_date="2026-05-01T23:59:00Z",
        )
        assert cache.is_24h_alerted("assignment_xyz", "2026-05-01T23:59:00Z") is False

    def test_different_due_date_not_confused(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        cache.mark_24h_alerted(
            assignment_id="assignment_abc",
            title="Tarea 1",
            course="Matemáticas",
            due_date="2026-05-01T23:59:00Z",
        )
        assert cache.is_24h_alerted("assignment_abc", "2026-05-02T23:59:00Z") is False

    def test_notified_count_increments_on_realert(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        cache.mark_24h_alerted(
            "assignment_abc", "Tarea 1", "Matemáticas", "2026-05-01T23:59:00Z"
        )
        cache.mark_24h_alerted(
            "assignment_abc", "Tarea 1", "Matemáticas", "2026-05-01T23:59:00Z"
        )
        entry = cache.get_all()["notified_24h_alerts"]["assignment_abc"]
        assert entry["notified_count"] == 2

    def test_mark_persists_after_save(self, tmp_path: Path) -> None:
        cache1 = make_cache(tmp_path)
        cache1.mark_24h_alerted(
            "assignment_abc", "Tarea 1", "Matemáticas", "2026-05-01T23:59:00Z"
        )
        cache1.save()

        # Reload from the SAME file that cache1 used
        cache2 = NotificationCache(cache1._file_path)
        cache2.load()
        assert cache2.is_24h_alerted("assignment_abc", "2026-05-01T23:59:00Z") is True


# ─── Test due date change detection ───────────────────────────────────────────


class TestDueDateChange:
    def test_returns_false_when_no_entry(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        assert cache.has_due_date_changed("assignment_abc", "2026-05-01T23:59:00Z") is False

    def test_returns_false_when_date_same(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        cache.mark_24h_alerted(
            "assignment_abc", "Tarea 1", "Matemáticas", "2026-05-01T23:59:00Z"
        )
        assert cache.has_due_date_changed("assignment_abc", "2026-05-01T23:59:00Z") is False

    def test_returns_true_when_date_different(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        cache.mark_24h_alerted(
            "assignment_abc", "Tarea 1", "Matemáticas", "2026-05-01T23:59:00Z"
        )
        assert (
            cache.has_due_date_changed("assignment_abc", "2026-05-02T23:59:00Z")
            is True
        )

    def test_date_change_allows_re_alert(self, tmp_path: Path) -> None:
        cache1 = make_cache(tmp_path)
        cache1.mark_24h_alerted(
            "assignment_abc", "Tarea 1", "Matemáticas", "2026-05-01T23:59:00Z"
        )
        cache1.save()

        # Reload from the SAME file that cache1 used
        cache2 = NotificationCache(cache1._file_path)
        cache2.load()
        # Old date still cached
        assert cache2.is_24h_alerted("assignment_abc", "2026-05-01T23:59:00Z") is True
        # New date NOT cached → should re-alert
        assert cache2.is_24h_alerted("assignment_abc", "2026-05-02T23:59:00Z") is False


# ─── Test save/load roundtrip ─────────────────────────────────────────────────


class TestSaveLoadRoundtrip:
    def test_save_load_preserves_all_data(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        cache.mark_week_digest_sent("2026-W18", 3)
        cache.mark_week_digest_sent("2026-W17", 1)
        cache.mark_24h_alerted(
            "assignment_abc", "Tarea 1", "Matemáticas", "2026-05-01T23:59:00Z"
        )
        cache.mark_24h_alerted(
            "assignment_xyz", "Tarea 2", "Física", "2026-05-03T23:59:00Z"
        )
        cache.save()
        # Reload from the same file that was just saved
        cache2 = NotificationCache(cache._file_path)
        cache2.load()
        data = cache2.get_all()

        assert data["schema_version"] == CURRENT_SCHEMA_VERSION, f"schema: {data}"
        assert "2026-W18" in data["weekly_digests"], f"W18 missing. data: {data}"
        assert data["weekly_digests"]["2026-W18"]["assignment_count"] == 3, f"count wrong: {data}"
        assert "2026-W17" in data["weekly_digests"], f"W17 missing: {data}"
        assert "assignment_abc" in data["notified_24h_alerts"], f"abc missing: {data}"
        assert "assignment_xyz" in data["notified_24h_alerts"], f"xyz missing: {data}"
        assert data["notified_24h_alerts"]["assignment_abc"]["course"] == "Matemáticas", f"course wrong: {data}"

    def test_last_checked_at_updated_on_save(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        cache.mark_week_digest_sent("2026-W18", 3)
        cache.save()

        data = cache.get_all()
        assert data["last_checked_at"] is not None

    def test_save_without_changes_does_not_corrupt(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        cache.save()
        cache2 = make_cache(tmp_path)
        assert cache2.get_all()["schema_version"] == CURRENT_SCHEMA_VERSION


# ─── Test schema version ──────────────────────────────────────────────────────


class TestSchemaVersion:
    def test_schema_version_set_on_load(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        assert cache.get_all()["schema_version"] == CURRENT_SCHEMA_VERSION

    def test_missing_schema_version_raises(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({"weekly_digests": {}}), encoding="utf-8")
        cache = NotificationCache(cache_file)
        with pytest.raises(CacheError) as exc_info:
            cache.load()
        assert "schema_version" in str(exc_info.value)

    def test_wrong_schema_version_raises(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(
            json.dumps({"schema_version": 99, "weekly_digests": {}}),
            encoding="utf-8",
        )
        cache = NotificationCache(cache_file)
        with pytest.raises(CacheError) as exc_info:
            cache.load()
        assert "99" in str(exc_info.value)


# ─── Test multiple assignments ───────────────────────────────────────────────


class TestMultipleAssignments:
    def test_many_24h_alerts_tracked(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        for i in range(20):
            cache.mark_24h_alerted(
                f"assignment_{i}",
                f"Tarea {i}",
                "Matemáticas",
                f"2026-05-{i+1:02d}T23:59:00Z",
            )
        data = cache.get_all()
        assert len(data["notified_24h_alerts"]) == 20

    def test_many_week_digests_tracked(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        for week in range(1, 53):
            cache.mark_week_digest_sent(f"2026-W{week:02d}", week)
        data = cache.get_all()
        assert len(data["weekly_digests"]) == 52


# ─── Test edge cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_cache(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        assert cache.is_week_digest_sent("2026-W99") is False
        assert cache.is_24h_alerted("any", "any") is False
        assert cache.has_due_date_changed("any", "any") is False

    def test_corrupted_json_raises(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("not valid json{", encoding="utf-8")
        cache = NotificationCache(cache_file)
        with pytest.raises(CacheError):
            cache.load()

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("", encoding="utf-8")
        cache = NotificationCache(cache_file)
        with pytest.raises(CacheError):
            cache.load()

    def test_extra_fields_preserved(self, tmp_path: Path) -> None:
        """Unknown fields in the cache should not be destroyed on save."""
        cache_file = tmp_path / "cache.json"
        raw = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "weekly_digests": {"2026-W18": {"sent_at": "2026-04-28T08:00:00Z", "assignment_count": 2}},
            "notified_24h_alerts": {},
            "last_checked_at": "2026-04-28T08:00:00Z",
            "some_extra_field": "should be preserved",
        }
        cache_file.write_text(json.dumps(raw), encoding="utf-8")
        cache = NotificationCache(cache_file)
        cache.load()
        cache.mark_week_digest_sent("2026-W19", 1)
        cache.save()

        cache2 = NotificationCache(cache_file)
        cache2.load()
        assert cache2.get_all().get("some_extra_field") == "should be preserved"

    def test_iso_timestamp_ends_with_z(self, tmp_path: Path) -> None:
        """Timestamps should end with Z (UTC)."""
        cache = make_cache(tmp_path)
        cache.mark_week_digest_sent("2026-W18", 3)
        cache.save()

        # Reload from the same file
        cache2 = NotificationCache(cache._file_path)
        cache2.load()
        entry = cache2.get_all()["weekly_digests"]["2026-W18"]
        assert entry["sent_at"].endswith("Z"), f"Timestamp {entry['sent_at']} should end with Z"

    def test_schema_version_integer(self, tmp_path: Path) -> None:
        cache = make_cache(tmp_path)
        data = cache.get_all()
        assert type(data["schema_version"]) is int