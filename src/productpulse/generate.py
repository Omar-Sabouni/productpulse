"""Create the ProductPulse input CSV files."""

from __future__ import annotations

import argparse
import csv
import random
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from productpulse.constants import CSV_FIELDS


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_csv(path: Path, entity_type: str, rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS[entity_type])
        writer.writeheader()
        writer.writerows(rows)


def generate_dataset(output_dir: Path) -> dict[str, int]:
    """Write the normalized input CSVs."""

    output_dir = Path(output_dir)
    rng = random.Random(20260101)
    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    channels = ("organic", "paid_search", "referral", "email", "social")
    devices = ("desktop", "mobile", "tablet")

    users: list[dict[str, str]] = []
    sessions: list[dict[str, str]] = []
    events: list[dict[str, str]] = []
    event_counter = 1

    def add_event(
        user_id: str,
        session_id: str,
        event_name: str,
        happened_at: datetime,
    ) -> None:
        nonlocal event_counter
        events.append(
            {
                "event_id": f"evt_{event_counter:04d}",
                "user_id": user_id,
                "session_id": session_id,
                "event_name": event_name,
                "event_timestamp": _timestamp(happened_at),
            }
        )
        event_counter += 1

    for index in range(30):
        number = index + 1
        user_id = f"usr_{number:03d}"
        signup_at = base + timedelta(
            days=index // 2,
            minutes=(index % 2) * 41 + rng.randint(0, 8),
        )
        channel = channels[index % len(channels)]
        signup_device = devices[(index + rng.randint(0, 2)) % len(devices)]
        signup_session = f"ses_{number:03d}_00"
        users.append(
            {
                "user_id": user_id,
                "signup_timestamp": _timestamp(signup_at),
                "acquisition_channel": channel,
            }
        )
        sessions.append(
            {
                "session_id": signup_session,
                "user_id": user_id,
                "session_start": _timestamp(signup_at),
                "device_type": signup_device,
            }
        )
        add_event(user_id, signup_session, "signup", signup_at)
        add_event(
            user_id, signup_session, "page_view", signup_at + timedelta(minutes=3)
        )
        if index % 4 != 0:
            add_event(
                user_id,
                signup_session,
                "activation",
                signup_at + timedelta(minutes=12 + index % 9),
            )

        return_days: list[int] = []
        if index % 2 == 0 or index % 5 == 0:
            return_days.append(1)
        if index % 3 != 1:
            return_days.append(3)
        if index % 3 == 0 or index % 7 == 0:
            return_days.append(7)
        for session_number, day_offset in enumerate(return_days, start=1):
            session_id = f"ses_{number:03d}_{session_number:02d}"
            started_at = signup_at + timedelta(
                days=day_offset, minutes=session_number * 7
            )
            sessions.append(
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "session_start": _timestamp(started_at),
                    "device_type": devices[(index + day_offset) % len(devices)],
                }
            )
            add_event(
                user_id, session_id, "feature_use", started_at + timedelta(minutes=2)
            )
            if day_offset == 7 and index % 2 == 0:
                add_event(
                    user_id, session_id, "purchase", started_at + timedelta(minutes=9)
                )

    users.sort(key=lambda row: row["user_id"])
    sessions.sort(key=lambda row: (row["session_start"], row["session_id"]))
    events.sort(key=lambda row: (row["event_timestamp"], row["event_id"]))
    _write_csv(output_dir / "users.csv", "users", users)
    _write_csv(output_dir / "sessions.csv", "sessions", sessions)
    _write_csv(output_dir / "events.csv", "events", events)

    return {
        "users": len(users),
        "sessions": len(sessions),
        "events": len(events),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    generate_dataset(args.output_dir)
    print("ProductPulse data files are ready.")


if __name__ == "__main__":
    main()
