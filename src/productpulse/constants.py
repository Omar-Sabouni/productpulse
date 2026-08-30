"""Shared input schemas and accepted categorical values."""

from __future__ import annotations

from typing import Final

CSV_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "users": ("user_id", "signup_timestamp", "acquisition_channel"),
    "sessions": ("session_id", "user_id", "session_start", "device_type"),
    "events": (
        "event_id",
        "user_id",
        "session_id",
        "event_name",
        "event_timestamp",
    ),
}

ALLOWED_CHANNELS: Final[frozenset[str]] = frozenset(
    {"organic", "paid_search", "referral", "email", "social"}
)
ALLOWED_DEVICES: Final[frozenset[str]] = frozenset({"desktop", "mobile", "tablet"})
ALLOWED_EVENTS: Final[frozenset[str]] = frozenset(
    {"signup", "activation", "page_view", "feature_use", "purchase"}
)

PRIMARY_KEYS: Final[dict[str, str]] = {
    "users": "user_id",
    "sessions": "session_id",
    "events": "event_id",
}
