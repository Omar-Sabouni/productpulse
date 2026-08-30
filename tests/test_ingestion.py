from __future__ import annotations

import hashlib

import pytest
from conftest import FIXTURES, VALID_FIXTURES

import productpulse.ingest as ingest_module
from productpulse.ingest import IngestionError, ingest_csv


def _assert_success(result) -> None:
    assert result.status == "completed"


def test_ingestion_is_idempotent(connection) -> None:
    users = ingest_csv(connection, "users", VALID_FIXTURES / "users.csv")
    sessions = ingest_csv(connection, "sessions", VALID_FIXTURES / "sessions.csv")
    first = ingest_csv(connection, "events", VALID_FIXTURES / "events.csv")
    second = ingest_csv(connection, "events", VALID_FIXTURES / "events.csv")

    for result in (users, sessions, first, second):
        _assert_success(result)

    assert (users.source_row_count, users.accepted_row_count) == (5, 5)
    assert (sessions.source_row_count, sessions.accepted_row_count) == (13, 13)
    assert (first.source_row_count, first.accepted_row_count) == (14, 14)
    assert first.rejected_row_count == 0
    assert first.duplicate_row_count == 0

    assert second.source_row_count == 14
    assert second.accepted_row_count == 0
    assert second.rejected_row_count == 0
    assert second.duplicate_row_count == 14
    assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 14


def test_invalid_rows_are_quarantined_without_blocking_valid_rows(connection) -> None:
    ingest_csv(connection, "users", VALID_FIXTURES / "users.csv")
    ingest_csv(connection, "sessions", VALID_FIXTURES / "sessions.csv")

    result = ingest_csv(
        connection,
        "events",
        FIXTURES / "malformed" / "events_malformed.csv",
    )

    _assert_success(result)
    assert result.source_row_count == 3
    assert result.accepted_row_count == 1
    assert result.rejected_row_count == 2
    assert result.duplicate_row_count == 0
    assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert connection.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 2
    quarantined = connection.execute(
        """
        SELECT entity_type, source_filename, row_number,
               rejection_reason, raw_payload
        FROM quarantine
        ORDER BY row_number
        """
    ).fetchall()
    assert [row[:3] for row in quarantined] == [
        ("events", "events_malformed.csv", 3),
        ("events", "events_malformed.csv", 4),
    ]
    assert all(row[3] for row in quarantined)
    assert all(row[4] for row in quarantined)
    assert "timestamp" in quarantined[0][3].lower()
    assert "event_id" in quarantined[1][3].lower()


def test_corrected_rejected_row_can_be_recovered_idempotently(connection) -> None:
    ingest_csv(connection, "users", VALID_FIXTURES / "users.csv")
    ingest_csv(connection, "sessions", VALID_FIXTURES / "sessions.csv")
    ingest_csv(
        connection,
        "events",
        FIXTURES / "malformed" / "events_malformed.csv",
    )

    corrected_path = FIXTURES / "recovered" / "events_recovered.csv"
    recovered = ingest_csv(connection, "events", corrected_path)
    repeated = ingest_csv(connection, "events", corrected_path)

    _assert_success(recovered)
    _assert_success(repeated)
    assert (
        recovered.source_row_count,
        recovered.accepted_row_count,
        recovered.rejected_row_count,
        recovered.duplicate_row_count,
    ) == (1, 1, 0, 0)
    assert (
        repeated.source_row_count,
        repeated.accepted_row_count,
        repeated.rejected_row_count,
        repeated.duplicate_row_count,
    ) == (1, 0, 0, 1)
    assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    corrected = connection.execute(
        """
        SELECT user_id, session_id, event_name,
               CAST(event_timestamp AS DATE), EXTRACT(minute FROM event_timestamp)
        FROM events
        WHERE event_id = 'recoverable_1'
        """
    ).fetchone()
    assert corrected[:3] == ("u5", "s_u5_0", "page_view")
    assert str(corrected[3]) == "2026-01-12"
    assert corrected[4] == 15
    # Quarantine is an audit trail; recovery does not erase the original rejection.
    assert connection.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 2


def test_reused_id_with_changed_payload_is_quarantined(connection) -> None:
    ingest_csv(connection, "users", VALID_FIXTURES / "users.csv")
    ingest_csv(connection, "sessions", VALID_FIXTURES / "sessions.csv")
    ingest_csv(connection, "events", VALID_FIXTURES / "events.csv")

    result = ingest_csv(
        connection,
        "events",
        FIXTURES / "malformed" / "events_conflicting_id.csv",
    )

    assert (result.accepted_row_count, result.rejected_row_count) == (0, 1)
    assert result.duplicate_row_count == 0
    assert (
        connection.execute(
            "SELECT event_name FROM events WHERE event_id = 'e01'"
        ).fetchone()[0]
        == "activation"
    )
    reason = connection.execute("SELECT rejection_reason FROM quarantine").fetchone()[0]
    assert "conflicts with" in reason


def test_duplicate_csv_header_fails_source_preflight(connection) -> None:
    ingest_csv(connection, "users", VALID_FIXTURES / "users.csv")
    ingest_csv(connection, "sessions", VALID_FIXTURES / "sessions.csv")

    source = FIXTURES / "malformed" / "events_duplicate_header.csv"
    with pytest.raises(IngestionError) as captured:
        ingest_csv(connection, "events", source)

    result = captured.value.result
    assert result.status == "failed"
    assert result.source_row_count == 0
    assert result.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "duplicate column name" in result.error_message
    assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 0
    recorded = connection.execute(
        """
        SELECT status, error_message, source_sha256
        FROM ingestion_runs
        WHERE ingestion_id = ?
        """,
        [result.ingestion_id],
    ).fetchone()
    assert recorded == ("failed", result.error_message, result.source_sha256)


def test_header_only_wrong_schema_is_a_failed_ingestion(connection, tmp_path) -> None:
    source = tmp_path / "users_wrong_header.csv"
    source.write_text("wrong_column\n", encoding="utf-8")

    with pytest.raises(IngestionError) as captured:
        ingest_csv(connection, "users", source)

    result = captured.value.result
    assert result.status == "failed"
    assert result.source_row_count == 0
    assert "missing required column" in result.error_message
    assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    assert (
        connection.execute(
            "SELECT status FROM ingestion_runs WHERE ingestion_id = ?",
            [result.ingestion_id],
        ).fetchone()[0]
        == "failed"
    )


def test_strict_csv_parse_failure_rolls_back_earlier_rows(connection, tmp_path) -> None:
    ingest_csv(connection, "users", VALID_FIXTURES / "users.csv")
    ingest_csv(connection, "sessions", VALID_FIXTURES / "sessions.csv")
    source = tmp_path / "events_broken_quote.csv"
    source.write_text(
        "event_id,user_id,session_id,event_name,event_timestamp\n"
        "new_event,u1,s_u1_0,page_view,2026-01-05T09:10:00Z\n"
        '"unterminated,u1,s_u1_0,page_view,2026-01-05T09:11:00Z\n',
        encoding="utf-8",
    )

    with pytest.raises(IngestionError) as captured:
        ingest_csv(connection, "events", source)

    result = captured.value.result
    assert result.status == "failed"
    assert result.source_row_count == 1
    assert "unexpected end of data" in result.error_message
    assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert (
        connection.execute(
            "SELECT status FROM ingestion_runs WHERE ingestion_id = ?",
            [result.ingestion_id],
        ).fetchone()[0]
        == "failed"
    )


@pytest.mark.parametrize(
    ("filename", "contents", "expected_error"),
    [
        ("blank.csv", b"", "CSV has no header"),
        (
            "invalid_utf8.csv",
            b"user_id,signup_timestamp,acquisition_channel\n\xff",
            "UnicodeDecodeError",
        ),
    ],
)
def test_unreadable_source_is_audited_without_canonical_writes(
    connection, tmp_path, filename, contents, expected_error
) -> None:
    source = tmp_path / filename
    source.write_bytes(contents)

    with pytest.raises(IngestionError) as captured:
        ingest_csv(connection, "users", source)

    result = captured.value.result
    assert result.status == "failed"
    assert result.source_row_count == 0
    assert expected_error in result.error_message
    assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    assert connection.execute(
        "SELECT status, error_message FROM ingestion_runs WHERE ingestion_id = ?",
        [result.ingestion_id],
    ).fetchone() == ("failed", result.error_message)


def test_quarantine_uses_physical_start_line_for_multiline_records(
    connection, tmp_path
) -> None:
    ingest_csv(connection, "users", VALID_FIXTURES / "users.csv")
    ingest_csv(connection, "sessions", VALID_FIXTURES / "sessions.csv")
    source = tmp_path / "events_multiline.csv"
    source.write_text(
        "event_id,user_id,session_id,event_name,event_timestamp\n"
        '"bad\nid",u1,s_u1_0,page_view,2026-01-05T09:10:00Z\n'
        ",u1,s_u1_0,page_view,2026-01-05T09:11:00Z\n",
        encoding="utf-8",
    )

    result = ingest_csv(connection, "events", source)

    assert (result.source_row_count, result.rejected_row_count) == (2, 2)
    row_numbers = connection.execute(
        "SELECT row_number FROM quarantine ORDER BY row_number"
    ).fetchall()
    assert row_numbers == [(2,), (4,)]


def test_quarantine_line_number_skips_blank_physical_lines(
    connection, tmp_path
) -> None:
    source = tmp_path / "users_with_blanks.csv"
    source.write_text(
        "user_id,signup_timestamp,acquisition_channel\n\n\n"
        ",2026-01-05T09:00:00Z,organic\n",
        encoding="utf-8",
    )

    result = ingest_csv(connection, "users", source)

    assert (result.source_row_count, result.rejected_row_count) == (1, 1)
    assert connection.execute("SELECT row_number FROM quarantine").fetchone() == (4,)


def test_keyboard_interrupt_rolls_back_standalone_ingestion(
    connection, monkeypatch
) -> None:
    original_validate_row = ingest_module.validate_row
    call_count = 0

    def interrupt_on_second_row(entity_type, row):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise KeyboardInterrupt
        return original_validate_row(entity_type, row)

    monkeypatch.setattr(ingest_module, "validate_row", interrupt_on_second_row)

    with pytest.raises(KeyboardInterrupt):
        ingest_csv(connection, "users", VALID_FIXTURES / "users.csv")

    assert connection.execute("SELECT COUNT(*) FROM users").fetchone() == (0,)
    assert connection.execute(
        "SELECT status, error_message FROM ingestion_runs"
    ).fetchone() == ("failed", "KeyboardInterrupt")
    connection.execute("BEGIN TRANSACTION")
    connection.execute("ROLLBACK")
