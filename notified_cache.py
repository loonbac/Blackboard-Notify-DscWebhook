"""Persistent notification cache for Blackboard Discord Bot.

Manages the JSON cache that tracks:
- Weekly digest send status (prevent re-sending same digest)
- 24h alert dedup (prevent duplicate alerts for same assignment+due_date)
- Due date change detection (re-alert when due date shifts)

Schema version 2 — see spec for JSON structure.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


# ─── Constants ────────────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION: int = 2

_CACHE_DEFAULTS: dict = {
    "schema_version": CURRENT_SCHEMA_VERSION,
    "last_checked_at": None,
    "weekly_digests": {},
    "notified_24h_alerts": {},
}


# ─── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class WeekDigestEntry:
    """Record of a sent weekly digest for a given ISO week key."""

    sent_at: str  # ISO 8601 timestamp
    assignment_count: int


@dataclass
class AlertedEntry:
    """Record of a 24h alert sent for an assignment."""

    title: str
    course: str
    due_date: str  # ISO 8601
    notified_at: str  # ISO 8601
    notified_count: int


# ─── Exceptions ────────────────────────────────────────────────────────────────


class CacheError(Exception):
    """Raised when cache read/write operations fail."""

    pass


# ─── Cache class ───────────────────────────────────────────────────────────────


class NotificationCache:
    """Persistent JSON cache for notification deduplication and tracking.

    Thread-safety: single-process only. No locks needed.

    The cache is loaded lazily on first access and saved after every mutation.

    Args:
        file_path: Path to the JSON cache file. Created automatically if missing.
    """

    def __init__(self, file_path: str | Path) -> None:
        self._file_path: Path = Path(file_path)
        # Use deepcopy of defaults so each instance gets independent nested dicts
        self._data: dict = copy.deepcopy(_CACHE_DEFAULTS)
        self._dirty: bool = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_week_digest_sent(self, week_key: str) -> bool:
        """Return True if a weekly digest was already sent for this ISO week."""
        return week_key in self._data.get("weekly_digests", {})

    def mark_week_digest_sent(self, week_key: str, count: int) -> None:
        """Record that a weekly digest was sent for this week."""
        now = _utc_now_iso()
        if "weekly_digests" not in self._data:
            self._data["weekly_digests"] = {}
        self._data["weekly_digests"][week_key] = {
            "sent_at": now,
            "assignment_count": count,
        }
        self._dirty = True

    def is_24h_alerted(self, assignment_id: str, due_date: str) -> bool:
        """Return True if a 24h alert was already sent for this assignment+due_date."""
        alerts = self._data.get("notified_24h_alerts", {})
        entry = alerts.get(assignment_id)
        if entry is None:
            return False
        # Must also match due_date to avoid re-alerting when only the date changed
        return entry.get("due_date") == due_date

    def mark_24h_alerted(
        self,
        assignment_id: str,
        title: str,
        course: str,
        due_date: str,
    ) -> None:
        """Record that a 24h alert was sent for this assignment."""
        now = _utc_now_iso()
        if "notified_24h_alerts" not in self._data:
            self._data["notified_24h_alerts"] = {}

        existing = self._data["notified_24h_alerts"].get(assignment_id)
        notified_count = (existing["notified_count"] + 1) if existing else 1

        self._data["notified_24h_alerts"][assignment_id] = {
            "title": title,
            "course": course,
            "due_date": due_date,
            "notified_at": now,
            "notified_count": notified_count,
        }
        self._dirty = True

    def has_due_date_changed(self, assignment_id: str, new_due_date: str) -> bool:
        """Return True if the cached due_date differs from new_due_date.

        Used to detect when an already-notified assignment's deadline moved
        and we should re-alert.
        """
        entry = self._data.get("notified_24h_alerts", {}).get(assignment_id)
        if entry is None:
            return False
        return entry.get("due_date") != new_due_date

    def get_all(self) -> dict:
        """Return a deep copy of the full cache content for debugging."""
        return copy.deepcopy(self._data)

    def save(self) -> None:
        """Write cache to disk atomically (write-then-rename)."""
        if not self._dirty:
            return

        self._data["last_checked_at"] = _utc_now_iso()

        # Write to temp file then rename for atomicity
        temp_path = self._file_path.with_suffix(".tmp")
        text = json.dumps(self._data, indent=2, ensure_ascii=False)
        temp_path.write_text(text, encoding="utf-8")

        try:
            temp_path.rename(self._file_path)
        except OSError:
            # Cross-device rename fallback: write directly
            self._file_path.write_text(text, encoding="utf-8")
            temp_path.unlink(missing_ok=True)

        self._dirty = False

    def load(self) -> None:
        """Load cache from disk. Creates default cache if file is missing."""
        if not self._file_path.exists():
            self._data = copy.deepcopy(_CACHE_DEFAULTS)
            self._dirty = False
            return

        try:
            text = self._file_path.read_text(encoding="utf-8")
            parsed = json.loads(text)
        except (json.JSONDecodeError, OSError) as exc:
            raise CacheError(f"Failed to read cache from {self._file_path}: {exc}") from exc

        # Validate schema version
        schema = parsed.get("schema_version")
        if schema is None:
            raise CacheError(
                f"Cache schema_version missing in {self._file_path}. "
                f"Expected {CURRENT_SCHEMA_VERSION}."
            )
        if schema != CURRENT_SCHEMA_VERSION:
            raise CacheError(
                f"Cache schema_version {schema} not supported. "
                f"Expected {CURRENT_SCHEMA_VERSION}."
            )

        self._data = copy.deepcopy(parsed)
        self._dirty = False


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")