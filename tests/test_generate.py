from __future__ import annotations

import csv
from pathlib import Path

from productpulse.generate import generate_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _csv_snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*.csv"))
    }


def _rows_for(directory: Path, filename: str) -> list[dict[str, str]]:
    matches = list(directory.rglob(filename))
    assert len(matches) == 1, filename
    with matches[0].open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_generate_dataset_writes_complete_sample(tmp_path: Path) -> None:
    output = tmp_path / "sample"
    generate_dataset(output)

    snapshot = _csv_snapshot(output)
    assert snapshot
    names = {Path(relative_path).name for relative_path in snapshot}
    assert names == {"users.csv", "sessions.csv", "events.csv"}

    for relative_path in snapshot:
        with (output / relative_path).open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) >= 2, relative_path
        assert all(rows[0]), relative_path

    users = _rows_for(output, "users.csv")
    sessions = _rows_for(output, "sessions.csv")
    events = _rows_for(output, "events.csv")

    assert len({row["signup_timestamp"][:10] for row in users}) >= 3
    assert len({row["acquisition_channel"] for row in users}) >= 3
    assert len({row["device_type"] for row in sessions}) >= 3
    assert len({row["event_timestamp"][:10] for row in events}) >= 3

    user_ids = {row["user_id"] for row in users}
    session_ids = {row["session_id"] for row in sessions}
    assert {row["user_id"] for row in sessions} <= user_ids
    assert {row["user_id"] for row in events} <= user_ids
    assert {row["session_id"] for row in events} <= session_ids


def test_checked_in_inputs_match_the_default_generator(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    generate_dataset(generated)

    assert _csv_snapshot(generated) == _csv_snapshot(PROJECT_ROOT / "data" / "input")
