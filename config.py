"""Typed configuration loader for Blackboard Discord Bot.

Loads configuration from environment variables (via python-dotenv),
validates required fields, applies defaults, and returns an immutable
Config dataclass instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from dotenv import load_dotenv
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_BLACKBOARD_URL: Final[str] = "https://senati.blackboard.com"
DEFAULT_CACHE_FILE_PATH: Final[str] = "./notified_assignments.json"
DEFAULT_TIMEZONE: Final[str] = "America/Lima"
DEFAULT_REQUEST_TIMEOUT_SECONDS: Final[int] = 30
DEFAULT_MAX_RETRY_ATTEMPTS: Final[int] = 3
DEFAULT_HEADLESS: Final[bool] = True
DEFAULT_LOG_LEVEL: Final[str] = "INFO"
DEFAULT_WEEKLY_DIGEST_DAY: Final[int] = 1  # Monday

VALID_WEEKLY_DIGEST_DAYS: Final[set[int]] = set(range(7))  # 0-6

REQUIRED_FIELDS: Final[list[str]] = [
    "BLACKBOARD_USER",
    "BLACKBOARD_PASS",
    "DISCORD_WEBHOOK_URL",
]

# Map public config key -> environment variable name
_ENV_VAR_NAMES: Final[dict[str, str]] = {
    "BLACKBOARD_URL": "BLACKBOARD_URL",
    "BLACKBOARD_USER": "BLACKBOARD_USER",
    "BLACKBOARD_PASS": "BLACKBOARD_PASS",
    "DISCORD_WEBHOOK_URL": "DISCORD_WEBHOOK_URL",
    "WEEKLY_DIGEST_DAY": "WEEKLY_DIGEST_DAY",
    "CACHE_FILE_PATH": "CACHE_FILE_PATH",
    "TIMEZONE": "TIMEZONE",
    "REQUEST_TIMEOUT_SECONDS": "REQUEST_TIMEOUT_SECONDS",
    "MAX_RETRY_ATTEMPTS": "MAX_RETRY_ATTEMPTS",
    "HEADLESS": "HEADLESS",
    "LOG_LEVEL": "LOG_LEVEL",
}


# ─── Exceptions ───────────────────────────────────────────────────────────────


class ConfigurationError(ValueError):
    """Raised when configuration is missing, malformed, or invalid."""

    pass


# ─── Dataclass ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Config:
    """Immutable typed configuration for the Blackboard Discord Bot."""

    # Required fields
    blackboard_url: str
    blackboard_user: str
    blackboard_pass: str
    discord_webhook_url: str

    # Optional fields with defaults
    weekly_digest_day: int = DEFAULT_WEEKLY_DIGEST_DAY
    cache_file_path: str = DEFAULT_CACHE_FILE_PATH
    timezone: str = DEFAULT_TIMEZONE
    request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    max_retry_attempts: int = DEFAULT_MAX_RETRY_ATTEMPTS
    headless: bool = DEFAULT_HEADLESS
    log_level: str = DEFAULT_LOG_LEVEL

    # Internal: derived timezone object (stored as _tz, exposed via property)
    _tz: ZoneInfo = field(
        default_factory=lambda: ZoneInfo("UTC"),
        repr=False,
        hash=False,
        compare=False,
    )

    @property
    def tz(self) -> ZoneInfo:
        """IANA timezone object derived from the TIMEZONE string."""
        return self._tz


# ─── Validation helpers (unit-testable) ─────────────────────────────────────


def _validate_required(env: dict[str, str | None], required: list[str]) -> None:
    """Check that all required environment variables are present.

    Raises:
        ConfigurationError: if any required field is missing.
    """
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise ConfigurationError(
            f"Missing required configuration field(s): {', '.join(missing)}. "
            "Set them in your .env file or environment."
        )


def _validate_weekly_digest_day(value: int) -> None:
    """Validate WEEKLY_DIGEST_DAY is an integer in range [0, 6].

    Raises:
        ConfigurationError: if value is outside valid range.
    """
    if value not in VALID_WEEKLY_DIGEST_DAYS:
        raise ConfigurationError(
            f"Invalid WEEKLY_DIGEST_DAY value {value!r}. "
            f"Must be an integer in range 0–6 (0=Sunday, 1=Monday, …, 6=Saturday)."
        )


def _validate_timezone(tz_str: str) -> ZoneInfo:
    """Validate that tz_str is a valid IANA timezone.

    Raises:
        ConfigurationError: if tz_str is not a recognised IANA timezone.
    """
    try:
        return ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigurationError(
            f"Invalid TIMEZONE value {tz_str!r}. "
            "Must be a valid IANA timezone identifier (e.g. 'America/Lima', 'UTC')."
        )


def _parse_bool(value: str, field_name: str = "") -> bool:
    """Parse a boolean from a string value.

    Accepts: 'true', 'false', '1', '0' (case-insensitive).
    Other values raise a ConfigurationError.
    """
    low = value.lower()
    if low in ("true", "1"):
        return True
    if low in ("false", "0"):
        return False
    if field_name:
        raise ConfigurationError(
            f"Invalid boolean value {value!r} for {field_name}. "
            "Must be one of: true, false, 1, 0."
        )
    raise ConfigurationError(
        f"Invalid boolean value {value!r}. Must be one of: true, false, 1, 0."
    )


def _parse_int(value: str, field_name: str) -> int:
    """Parse an integer from a string value.

    Raises:
        ConfigurationError: if the value cannot be parsed as an integer.
    """
    try:
        return int(value)
    except ValueError:
        raise ConfigurationError(
            f"Invalid integer value {value!r} for {field_name}. "
            "Must be a valid integer."
        )


def _getenv(key: str) -> str | None:
    """Return the value of an environment variable, or None if unset."""
    import os

    return os.environ.get(key)


# ─── Loader ──────────────────────────────────────────────────────────────────


def load(env_path: str | Path | None = ".env") -> Config:
    """Load and validate configuration from environment.

    Args:
        env_path: Path to the .env file. Defaults to ".env" in the current
                  working directory. Pass None to skip loading a .env file
                  (useful when environment variables are already set).

    Returns:
        A validated Config instance.

    Raises:
        ConfigurationError: if required fields are missing or values are invalid.
    """
    # Load .env file if provided
    if env_path is not None:
        load_dotenv(dotenv_path=env_path)

    # Read all relevant environment variables
    env_vals: dict[str, str | None] = {
        key: _getenv(env_name) for key, env_name in _ENV_VAR_NAMES.items()
    }

    # 1. Required fields
    _validate_required(env_vals, REQUIRED_FIELDS)

    # 2. Parse typed values
    blackboard_url = env_vals["BLACKBOARD_URL"] or DEFAULT_BLACKBOARD_URL
    blackboard_user: str = env_vals["BLACKBOARD_USER"]  # type: ignore[assignment] — validated above
    blackboard_pass: str = env_vals["BLACKBOARD_PASS"]  # type: ignore[assignment]
    discord_webhook_url: str = env_vals["DISCORD_WEBHOOK_URL"]  # type: ignore[assignment]

    def _default(
        raw: str | None, default: str | int | bool, parser: callable | None = None
    ) -> str | int | bool:
        """Return default if raw is None/'', otherwise parse if parser given."""
        if raw is None or raw == "":
            return default
        if parser:
            return parser(raw)
        return raw

    weekly_digest_day_raw = env_vals["WEEKLY_DIGEST_DAY"]
    weekly_digest_day = (
        _parse_int(weekly_digest_day_raw, "WEEKLY_DIGEST_DAY")
        if weekly_digest_day_raw not in (None, "")
        else DEFAULT_WEEKLY_DIGEST_DAY
    )
    cache_file_path = env_vals["CACHE_FILE_PATH"] or DEFAULT_CACHE_FILE_PATH
    timezone_str = env_vals["TIMEZONE"] or DEFAULT_TIMEZONE
    request_timeout_seconds = (
        _parse_int(env_vals["REQUEST_TIMEOUT_SECONDS"], "REQUEST_TIMEOUT_SECONDS")
        if env_vals["REQUEST_TIMEOUT_SECONDS"] not in (None, "")
        else DEFAULT_REQUEST_TIMEOUT_SECONDS
    )
    max_retry_attempts = (
        _parse_int(env_vals["MAX_RETRY_ATTEMPTS"], "MAX_RETRY_ATTEMPTS")
        if env_vals["MAX_RETRY_ATTEMPTS"] not in (None, "")
        else DEFAULT_MAX_RETRY_ATTEMPTS
    )
    headless = (
        _parse_bool(env_vals["HEADLESS"], field_name="HEADLESS")
        if env_vals["HEADLESS"] not in (None, "")
        else DEFAULT_HEADLESS
    )
    log_level = env_vals["LOG_LEVEL"] or DEFAULT_LOG_LEVEL

    # 3. Semantic validations
    _validate_weekly_digest_day(weekly_digest_day)
    tz = _validate_timezone(timezone_str)

    return Config(
        blackboard_url=blackboard_url,
        blackboard_user=blackboard_user,
        blackboard_pass=blackboard_pass,
        discord_webhook_url=discord_webhook_url,
        weekly_digest_day=weekly_digest_day,
        cache_file_path=cache_file_path,
        timezone=timezone_str,
        request_timeout_seconds=request_timeout_seconds,
        max_retry_attempts=max_retry_attempts,
        headless=headless,
        log_level=log_level,
        _tz=tz,
    )


# ─── Public helpers ────────────────────────────────────────────────────────────


def all_config_keys() -> set[str]:
    """Return the set of all supported configuration keys."""
    return set(_ENV_VAR_NAMES.keys())
