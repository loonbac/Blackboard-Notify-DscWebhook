"""Comprehensive unit tests for config.py."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import config
from config import (
    ConfigurationError,
    Config,
    _parse_bool,
    _parse_int,
    _validate_required,
    _validate_timezone,
    _validate_weekly_digest_day,
    all_config_keys,
    load,
)


# ─── Base environment with all required fields ────────────────────────────────

def full_env(overrides: dict[str, str | None] | None = None) -> dict[str, str | None]:
    """Minimal valid environment (all required + defaults)."""
    base: dict[str, str | None] = {
        "BLACKBOARD_URL": "https://senati.blackboard.com",
        "BLACKBOARD_USER": "testuser",
        "BLACKBOARD_PASS": "testpass",
        "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/abc",
        "WEEKLY_DIGEST_DAY": "1",
        "CACHE_FILE_PATH": "./cache.json",
        "TIMEZONE": "America/Lima",
        "REQUEST_TIMEOUT_SECONDS": "30",
        "MAX_RETRY_ATTEMPTS": "3",
        "HEADLESS": "true",
        "LOG_LEVEL": "INFO",
    }
    if overrides:
        for k, v in overrides.items():
            base[k] = v
    return base


# ─── Test _validate_required ─────────────────────────────────────────────────


class TestValidateRequired:
    def test_passes_when_all_present(self) -> None:
        env = {k: "value" for k in config.REQUIRED_FIELDS}
        _validate_required(env, config.REQUIRED_FIELDS)  # should not raise

    def test_raises_on_missing_single(self) -> None:
        env = {k: "value" for k in config.REQUIRED_FIELDS}
        env.pop("BLACKBOARD_USER")
        with pytest.raises(ConfigurationError) as exc_info:
            _validate_required(env, config.REQUIRED_FIELDS)
        assert "BLACKBOARD_USER" in str(exc_info.value)

    def test_raises_on_missing_multiple(self) -> None:
        env = {}
        with pytest.raises(ConfigurationError) as exc_info:
            _validate_required(env, config.REQUIRED_FIELDS)
        msg = str(exc_info.value)
        assert "BLACKBOARD_USER" in msg
        assert "BLACKBOARD_PASS" in msg
        assert "DISCORD_WEBHOOK_URL" in msg

    def test_empty_string_is_missing(self) -> None:
        env = {k: "" for k in config.REQUIRED_FIELDS}
        with pytest.raises(ConfigurationError):
            _validate_required(env, config.REQUIRED_FIELDS)


# ─── Test _validate_weekly_digest_day ───────────────────────────────────────


class TestValidateWeeklyDigestDay:
    @pytest.mark.parametrize("day", range(7))
    def test_valid_days_accepted(self, day: int) -> None:
        _validate_weekly_digest_day(day)  # should not raise

    @pytest.mark.parametrize("day", [-1, 7, 10, 100, -999])
    def test_out_of_range_rejected(self, day: int) -> None:
        with pytest.raises(ConfigurationError) as exc_info:
            _validate_weekly_digest_day(day)
        assert str(day) in str(exc_info.value)

    def test_non_integer_type_rejected(self) -> None:
        with pytest.raises(ConfigurationError):
            _validate_weekly_digest_day("abc")  # type: ignore[arg-type]


# ─── Test _validate_timezone ─────────────────────────────────────────────────


class TestValidateTimezone:
    @pytest.mark.parametrize(
        "tz",
        [
            "UTC",
            "America/Lima",
            "America/New_York",
            "Europe/London",
            "Asia/Tokyo",
            "Africa/Johannesburg",
        ],
    )
    def test_valid_timezone_accepted(self, tz: str) -> None:
        result = _validate_timezone(tz)
        assert str(result) == tz

    @pytest.mark.parametrize(
        "tz",
        [
            "Invalid/Timezone",
            "PST",
            "GMT+5",
            "NotATimezone",
            "",
            "America/Invalid_City",
        ],
    )
    def test_invalid_timezone_rejected(self, tz: str) -> None:
        with pytest.raises(ConfigurationError) as exc_info:
            _validate_timezone(tz)
        msg = str(exc_info.value)
        assert "TIMEZONE" in msg or tz in msg


# ─── Test _parse_bool ────────────────────────────────────────────────────────


class TestParseBool:
    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1"])
    def test_truthy_values(self, value: str) -> None:
        assert _parse_bool(value) is True

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0"])
    def test_falsy_values(self, value: str) -> None:
        assert _parse_bool(value) is False

    @pytest.mark.parametrize("value", ["2", "yes", "no", "abc", ""])
    def test_invalid_values_rejected(self, value: str) -> None:
        with pytest.raises(ConfigurationError) as exc_info:
            _parse_bool(value)
        assert value in str(exc_info.value)


# ─── Test _parse_int ─────────────────────────────────────────────────────────


class TestParseInt:
    @pytest.mark.parametrize("value", ["0", "1", "42", "-1", "999", "300"])
    def test_valid_integers(self, value: str) -> None:
        assert _parse_int(value, "TEST_FIELD") == int(value)

    @pytest.mark.parametrize("value", ["abc", "1.5", "true", ""])
    def test_invalid_values_rejected(self, value: str) -> None:
        with pytest.raises(ConfigurationError) as exc_info:
            _parse_int(value, "TEST_FIELD")
        msg = str(exc_info.value).lower()
        assert "invalid integer" in msg or value in msg


# ─── Test Config dataclass ────────────────────────────────────────────────────


class TestConfigDataclass:
    def test_all_fields_set(self) -> None:
        from zoneinfo import ZoneInfo

        c = Config(
            blackboard_url="https://example.com",
            blackboard_user="user",
            blackboard_pass="pass",
            discord_webhook_url="https://discord.com/webhook",
            weekly_digest_day=1,
            cache_file_path="./cache.json",
            timezone="UTC",
            request_timeout_seconds=30,
            max_retry_attempts=3,
            headless=True,
            log_level="DEBUG",
            _tz=ZoneInfo("UTC"),
        )
        assert c.blackboard_url == "https://example.com"
        assert c.blackboard_user == "user"
        assert c.blackboard_pass == "pass"
        assert c.discord_webhook_url == "https://discord.com/webhook"
        assert c.weekly_digest_day == 1
        assert c.cache_file_path == "./cache.json"
        assert c.timezone == "UTC"
        assert c.request_timeout_seconds == 30
        assert c.max_retry_attempts == 3
        assert c.headless is True
        assert c.log_level == "DEBUG"
        assert c.tz == ZoneInfo("UTC")

    def test_frozen_dataclass(self) -> None:
        from zoneinfo import ZoneInfo

        c = Config(
            blackboard_url="https://example.com",
            blackboard_user="user",
            blackboard_pass="pass",
            discord_webhook_url="https://discord.com/webhook",
            _tz=ZoneInfo("UTC"),
        )
        with pytest.raises(Exception):  # frozen dataclass raises any Exception
            c.blackboard_user = "hacked"  # type: ignore[assignment]


# ─── Test load() — defaults applied when field absent ─────────────────────────


class TestLoadDefaults:
    """Test that optional fields get their documented defaults when absent."""

    @pytest.fixture(autouse=True)
    def base_env(self) -> dict[str, str | None]:
        return full_env()

    def test_default_blackboard_url(self, base_env: dict[str, str | None]) -> None:
        base_env.pop("BLACKBOARD_URL")
        with patch.dict(os.environ, base_env, clear=True):
            c = load(env_path=None)
        assert c.blackboard_url == config.DEFAULT_BLACKBOARD_URL

    def test_default_weekly_digest_day(self, base_env: dict[str, str | None]) -> None:
        base_env.pop("WEEKLY_DIGEST_DAY")
        with patch.dict(os.environ, base_env, clear=True):
            c = load(env_path=None)
        assert c.weekly_digest_day == config.DEFAULT_WEEKLY_DIGEST_DAY

    def test_default_cache_file_path(self, base_env: dict[str, str | None]) -> None:
        base_env.pop("CACHE_FILE_PATH")
        with patch.dict(os.environ, base_env, clear=True):
            c = load(env_path=None)
        assert c.cache_file_path == config.DEFAULT_CACHE_FILE_PATH

    def test_default_timezone(self, base_env: dict[str, str | None]) -> None:
        base_env.pop("TIMEZONE")
        with patch.dict(os.environ, base_env, clear=True):
            c = load(env_path=None)
        assert c.timezone == config.DEFAULT_TIMEZONE

    def test_default_request_timeout(self, base_env: dict[str, str | None]) -> None:
        base_env.pop("REQUEST_TIMEOUT_SECONDS")
        with patch.dict(os.environ, base_env, clear=True):
            c = load(env_path=None)
        assert c.request_timeout_seconds == config.DEFAULT_REQUEST_TIMEOUT_SECONDS

    def test_default_max_retry_attempts(self, base_env: dict[str, str | None]) -> None:
        base_env.pop("MAX_RETRY_ATTEMPTS")
        with patch.dict(os.environ, base_env, clear=True):
            c = load(env_path=None)
        assert c.max_retry_attempts == config.DEFAULT_MAX_RETRY_ATTEMPTS

    def test_default_headless(self, base_env: dict[str, str | None]) -> None:
        base_env.pop("HEADLESS")
        with patch.dict(os.environ, base_env, clear=True):
            c = load(env_path=None)
        assert c.headless is config.DEFAULT_HEADLESS

    def test_default_log_level(self, base_env: dict[str, str | None]) -> None:
        base_env.pop("LOG_LEVEL")
        with patch.dict(os.environ, base_env, clear=True):
            c = load(env_path=None)
        assert c.log_level == config.DEFAULT_LOG_LEVEL


# ─── Test load() — missing required fields ────────────────────────────────────


class TestLoadRequiredFields:
    REQUIRED = ["BLACKBOARD_USER", "BLACKBOARD_PASS", "DISCORD_WEBHOOK_URL"]

    @pytest.mark.parametrize("missing", REQUIRED)
    def test_missing_single_required_field(self, missing: str) -> None:
        env = full_env()
        env.pop(missing)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load(env_path=None)
        assert missing in str(exc_info.value)

    def test_missing_all_required_fields(self) -> None:
        env = {}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load(env_path=None)
        msg = str(exc_info.value)
        for field in self.REQUIRED:
            assert field in msg

    def test_error_message_is_clear(self) -> None:
        env = full_env()
        env.pop("BLACKBOARD_USER")
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load(env_path=None)
        assert "Missing required configuration" in str(exc_info.value)


# ─── Test load() — invalid WEEKLY_DIGEST_DAY ─────────────────────────────────


class TestLoadWeeklyDigestDay:
    @pytest.mark.parametrize("day", [-1, 7, 100, -999])
    def test_out_of_range_day_rejected(self, day: int) -> None:
        env = full_env({"WEEKLY_DIGEST_DAY": str(day)})
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load(env_path=None)
        assert "WEEKLY_DIGEST_DAY" in str(exc_info.value)

    def test_non_integer_day_rejected(self) -> None:
        env = full_env({"WEEKLY_DIGEST_DAY": "abc"})
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load(env_path=None)
        assert "WEEKLY_DIGEST_DAY" in str(exc_info.value)

    @pytest.mark.parametrize("day", range(7))
    def test_all_valid_days_accepted(self, day: int) -> None:
        env = full_env({"WEEKLY_DIGEST_DAY": str(day)})
        with patch.dict(os.environ, env, clear=True):
            c = load(env_path=None)
        assert c.weekly_digest_day == day


# ─── Test load() — invalid TIMEZONE ──────────────────────────────────────────


class TestLoadTimezone:
    @pytest.mark.parametrize("tz", ["Invalid/TZ", "Not/Valid"])
    def test_invalid_timezone_rejected(self, tz: str) -> None:
        env = full_env({"TIMEZONE": tz})
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load(env_path=None)
        assert "TIMEZONE" in str(exc_info.value)

    @pytest.mark.parametrize(
        "tz",
        ["UTC", "America/Lima", "America/New_York", "Europe/London", "Asia/Tokyo"],
    )
    def test_valid_timezones_accepted(self, tz: str) -> None:
        env = full_env({"TIMEZONE": tz})
        with patch.dict(os.environ, env, clear=True):
            c = load(env_path=None)
        assert c.timezone == tz


# ─── Test load() — HEADLESS boolean parsing ──────────────────────────────────


class TestLoadHeadless:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("true", True),
            ("false", False),
            ("True", True),
            ("False", False),
            ("TRUE", True),
            ("FALSE", False),
            ("1", True),
            ("0", False),
        ],
    )
    def test_headless_boolean_parsing(self, raw: str, expected: bool) -> None:
        env = full_env({"HEADLESS": raw})
        with patch.dict(os.environ, env, clear=True):
            c = load(env_path=None)
        assert c.headless is expected

    def test_headless_invalid_rejected(self) -> None:
        env = full_env({"HEADLESS": "yes"})
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load(env_path=None)
        assert "HEADLESS" in str(exc_info.value)


# ─── Test load() — integer field parsing ─────────────────────────────────────


class TestLoadIntegerFields:
    @pytest.mark.parametrize(
        ("field", "raw", "attr"),
        [
            ("REQUEST_TIMEOUT_SECONDS", "15", "request_timeout_seconds"),
            ("MAX_RETRY_ATTEMPTS", "5", "max_retry_attempts"),
            ("WEEKLY_DIGEST_DAY", "3", "weekly_digest_day"),
        ],
    )
    def test_integer_parsing(self, field: str, raw: str, attr: str) -> None:
        env = full_env({field: raw})
        with patch.dict(os.environ, env, clear=True):
            c = load(env_path=None)
        assert getattr(c, attr) == int(raw)

    def test_non_integer_rejected(self) -> None:
        env = full_env({"REQUEST_TIMEOUT_SECONDS": "abc"})
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load(env_path=None)
        assert "REQUEST_TIMEOUT_SECONDS" in str(exc_info.value)


# ─── Test load() — successful full load ─────────────────────────────────────


class TestLoadSuccess:
    def test_full_valid_config_loads(self) -> None:
        with patch.dict(os.environ, full_env(), clear=True):
            c = load(env_path=None)
        assert isinstance(c, Config)
        assert c.blackboard_user == "testuser"
        assert c.blackboard_pass == "testpass"
        assert c.discord_webhook_url == "https://discord.com/api/webhooks/123/abc"
        assert c.weekly_digest_day == 1
        assert c.cache_file_path == "./cache.json"
        assert c.timezone == "America/Lima"
        assert c.request_timeout_seconds == 30
        assert c.max_retry_attempts == 3
        assert c.headless is True
        assert c.log_level == "INFO"

    def test_all_known_keys_in_all_config_keys(self) -> None:
        keys = all_config_keys()
        expected = {
            "BLACKBOARD_URL",
            "BLACKBOARD_USER",
            "BLACKBOARD_PASS",
            "DISCORD_WEBHOOK_URL",
            "WEEKLY_DIGEST_DAY",
            "CACHE_FILE_PATH",
            "TIMEZONE",
            "REQUEST_TIMEOUT_SECONDS",
            "MAX_RETRY_ATTEMPTS",
            "HEADLESS",
            "LOG_LEVEL",
        }
        assert keys == expected


# ─── Test .env.example matches config keys ────────────────────────────────────


class TestEnvExampleMatches:
    """Verify that every key documented in .env.example is a supported config key."""

    def test_env_example_keys_match_supported_keys(self) -> None:
        env_example_path = Path(__file__).parents[1] / ".env.example"
        assert env_example_path.exists()

        raw = env_example_path.read_text()
        keys_in_file: set[str] = set()
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key = line.split("=")[0].strip()
                keys_in_file.add(key)

        supported_keys = all_config_keys()
        assert keys_in_file == supported_keys, (
            f".env.example keys {sorted(keys_in_file)} != "
            f"supported keys {sorted(supported_keys)}"
        )

    def test_every_supported_key_is_in_env_example(self) -> None:
        env_example_path = Path(__file__).parents[1] / ".env.example"
        raw = env_example_path.read_text()
        supported_keys = all_config_keys()
        missing: list[str] = []
        for key in supported_keys:
            if f"{key}=" not in raw:
                missing.append(key)
        assert not missing, f"Keys missing from .env.example: {missing}"


# ─── Test load() — empty optional strings use defaults ───────────────────────


class TestLoadEmptyOptionalStrings:
    def test_empty_weekly_digest_day_uses_default(self) -> None:
        env = full_env({"WEEKLY_DIGEST_DAY": ""})
        with patch.dict(os.environ, env, clear=True):
            c = load(env_path=None)
        assert c.weekly_digest_day == config.DEFAULT_WEEKLY_DIGEST_DAY

    def test_empty_timezone_uses_default(self) -> None:
        env = full_env({"TIMEZONE": ""})
        with patch.dict(os.environ, env, clear=True):
            c = load(env_path=None)
        assert c.timezone == config.DEFAULT_TIMEZONE

    def test_empty_request_timeout_uses_default(self) -> None:
        env = full_env({"REQUEST_TIMEOUT_SECONDS": ""})
        with patch.dict(os.environ, env, clear=True):
            c = load(env_path=None)
        assert c.request_timeout_seconds == config.DEFAULT_REQUEST_TIMEOUT_SECONDS

    def test_empty_headless_uses_default(self) -> None:
        env = full_env({"HEADLESS": ""})
        with patch.dict(os.environ, env, clear=True):
            c = load(env_path=None)
        assert c.headless is config.DEFAULT_HEADLESS


# ─── Test Config types ────────────────────────────────────────────────────────


class TestConfigTypes:
    def test_blackboard_url_is_str(self) -> None:
        with patch.dict(os.environ, full_env(), clear=True):
            c = load(env_path=None)
        assert type(c.blackboard_url) is str

    def test_weekly_digest_day_is_int(self) -> None:
        with patch.dict(os.environ, full_env(), clear=True):
            c = load(env_path=None)
        assert type(c.weekly_digest_day) is int

    def test_headless_is_bool(self) -> None:
        with patch.dict(os.environ, full_env(), clear=True):
            c = load(env_path=None)
        assert type(c.headless) is bool

    def test_tz_is_zoneinfo(self) -> None:
        from zoneinfo import ZoneInfo

        with patch.dict(os.environ, full_env(), clear=True):
            c = load(env_path=None)
        assert type(c.tz) is ZoneInfo

    def test_tz_property_returns_correct_zone(self) -> None:
        with patch.dict(os.environ, full_env(), clear=True):
            c = load(env_path=None)
        assert str(c.tz) == c.timezone


# ─── Test load() edge: env_path=None skips .env loading ───────────────────────


class TestLoadEnvPathNoneSkipsDotenv:
    def test_none_means_use_os_environ_only(self) -> None:
        """When env_path=None we use os.environ as-is (no .env file loaded)."""
        with patch.dict(os.environ, full_env(), clear=True):
            # Should succeed with env_path=None, not try to load .env
            c = load(env_path=None)
        assert isinstance(c, Config)
