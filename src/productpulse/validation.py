"""Pure, row-level schema validation for ProductPulse CSV inputs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime

from productpulse.constants import (
    ALLOWED_CHANNELS,
    ALLOWED_DEVICES,
    ALLOWED_EVENTS,
    CSV_FIELDS,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class RowValidationError(ValueError):
    """Raised when one input row violates the declared schema."""


def _identifier(row: Mapping[str, str | None], field: str) -> str:
    value = (row.get(field) or "").strip()
    if not _IDENTIFIER.fullmatch(value):
        raise RowValidationError(
            f"{field} must be 1-64 letters, numbers, dots, underscores, or hyphens"
        )
    return value


def _category(
    row: Mapping[str, str | None], field: str, allowed: frozenset[str]
) -> str:
    value = (row.get(field) or "").strip()
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise RowValidationError(f"{field} must be one of: {choices}")
    return value


def parse_utc_timestamp(value: str | None, field: str) -> datetime:
    """Parse a timezone-aware ISO-8601 value and normalize it to UTC."""

    candidate = (value or "").strip()
    if not candidate:
        raise RowValidationError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RowValidationError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RowValidationError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def validate_row(entity_type: str, row: Mapping[str, str | None]) -> dict[str, object]:
    """Validate and normalize one user, session, or event row."""

    if entity_type not in CSV_FIELDS:
        raise ValueError(f"Unknown entity type: {entity_type}")

    missing_columns = [field for field in CSV_FIELDS[entity_type] if field not in row]
    if missing_columns:
        raise RowValidationError(
            "missing required column(s): " + ", ".join(missing_columns)
        )
    unexpected_columns = [
        str(field)
        for field in row
        if field is not None and field not in CSV_FIELDS[entity_type]
    ]
    if unexpected_columns:
        raise RowValidationError(
            "unexpected column(s): " + ", ".join(sorted(unexpected_columns))
        )
    if None in row:
        raise RowValidationError("row contains more values than the CSV header")

    if entity_type == "users":
        return {
            "user_id": _identifier(row, "user_id"),
            "signup_timestamp": parse_utc_timestamp(
                row.get("signup_timestamp"), "signup_timestamp"
            ),
            "acquisition_channel": _category(
                row, "acquisition_channel", ALLOWED_CHANNELS
            ),
        }
    if entity_type == "sessions":
        return {
            "session_id": _identifier(row, "session_id"),
            "user_id": _identifier(row, "user_id"),
            "session_start": parse_utc_timestamp(
                row.get("session_start"), "session_start"
            ),
            "device_type": _category(row, "device_type", ALLOWED_DEVICES),
        }
    return {
        "event_id": _identifier(row, "event_id"),
        "user_id": _identifier(row, "user_id"),
        "session_id": _identifier(row, "session_id"),
        "event_name": _category(row, "event_name", ALLOWED_EVENTS),
        "event_timestamp": parse_utc_timestamp(
            row.get("event_timestamp"), "event_timestamp"
        ),
    }
