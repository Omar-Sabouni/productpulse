"""DuckDB schema setup and idempotent, row-validating CSV ingestion."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from productpulse.constants import CSV_FIELDS, PRIMARY_KEYS
from productpulse.validation import RowValidationError, validate_row


@dataclass(frozen=True)
class IngestionResult:
    """Auditable counts from one CSV ingestion attempt."""

    ingestion_id: str
    entity_type: str
    source_filename: str
    source_sha256: str
    status: str
    source_row_count: int
    accepted_row_count: int
    rejected_row_count: int
    duplicate_row_count: int
    data_as_of: datetime | None
    pipeline_run_id: str | None = None
    error_message: str | None = None


class IngestionError(RuntimeError):
    """Raised when a source cannot be ingested as a complete transaction."""

    def __init__(self, result: IngestionResult):
        self.result = result
        super().__init__(
            f"Failed to ingest {result.source_filename}: {result.error_message}"
        )


def initialize_database(conn: duckdb.DuckDBPyConnection) -> None:
    """Create raw, quarantine, and provenance tables if they do not exist."""

    # DuckDB otherwise inherits the host timezone, which would make DATE casts
    # and displayed TIMESTAMPTZ values vary from machine to machine.
    conn.execute("SET TimeZone = 'UTC'")
    statements = (
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id VARCHAR PRIMARY KEY,
            signup_timestamp TIMESTAMPTZ NOT NULL,
            acquisition_channel VARCHAR NOT NULL,
            source_file VARCHAR NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id VARCHAR PRIMARY KEY,
            user_id VARCHAR NOT NULL,
            session_start TIMESTAMPTZ NOT NULL,
            device_type VARCHAR NOT NULL,
            source_file VARCHAR NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id VARCHAR PRIMARY KEY,
            user_id VARCHAR NOT NULL,
            session_id VARCHAR NOT NULL,
            event_name VARCHAR NOT NULL,
            event_timestamp TIMESTAMPTZ NOT NULL,
            source_file VARCHAR NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            pipeline_run_id VARCHAR PRIMARY KEY,
            run_mode VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
            completed_at TIMESTAMPTZ,
            data_as_of TIMESTAMPTZ,
            error_stage VARCHAR,
            error_source VARCHAR,
            error_message VARCHAR
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            ingestion_id VARCHAR PRIMARY KEY,
            pipeline_run_id VARCHAR,
            entity_type VARCHAR NOT NULL,
            source_filename VARCHAR NOT NULL,
            source_sha256 VARCHAR NOT NULL,
            source_row_count BIGINT NOT NULL,
            accepted_row_count BIGINT NOT NULL,
            rejected_row_count BIGINT NOT NULL,
            duplicate_row_count BIGINT NOT NULL,
            status VARCHAR NOT NULL,
            error_message VARCHAR,
            data_as_of TIMESTAMPTZ,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS quarantine (
            rejection_id VARCHAR PRIMARY KEY,
            ingestion_id VARCHAR NOT NULL,
            entity_type VARCHAR NOT NULL,
            source_filename VARCHAR NOT NULL,
            source_sha256 VARCHAR NOT NULL,
            row_number BIGINT NOT NULL,
            raw_payload VARCHAR NOT NULL,
            rejection_reason VARCHAR NOT NULL,
            rejected_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
        )
        """,
    )
    for statement in statements:
        conn.execute(statement)
    # Existing demo databases predate pipeline-level lineage. These additions are
    # deliberately nullable so a standalone ingestion remains a supported API.
    conn.execute(
        "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS pipeline_run_id VARCHAR"
    )
    conn.execute(
        "ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS error_message VARCHAR"
    )


def connect_database(path: Path | str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Open a DuckDB database and ensure its ProductPulse schema exists."""

    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    initialize_database(conn)
    return conn


def _raw_payload(row: dict[str | None, str | list[str] | None]) -> str:
    normalized = {
        "__extra_values__" if key is None else str(key): value
        for key, value in row.items()
    }
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _validate_header(entity_type: str, fieldnames: list[str] | None) -> None:
    """Reject a source whose header does not exactly describe its entity schema."""

    if fieldnames is None:
        raise ValueError("CSV has no header")
    expected = CSV_FIELDS[entity_type]
    missing = [field for field in expected if field not in fieldnames]
    unexpected = sorted({field for field in fieldnames if field not in expected})
    duplicate_headers = sorted(
        {field for field in fieldnames if fieldnames.count(field) > 1}
    )
    problems: list[str] = []
    if missing:
        problems.append("missing required column(s): " + ", ".join(missing))
    if unexpected:
        problems.append("unexpected column(s): " + ", ".join(unexpected))
    if duplicate_headers:
        problems.append("duplicate column name(s): " + ", ".join(duplicate_headers))
    if problems:
        raise ValueError("invalid CSV header: " + "; ".join(problems))


def _dict_row(fieldnames: list[str], values: list[str]) -> dict[str | None, object]:
    """Map one parsed CSV record with DictReader-compatible missing/extra cells."""

    row: dict[str | None, object] = {
        field: values[index] if index < len(values) else None
        for index, field in enumerate(fieldnames)
    }
    if len(values) > len(fieldnames):
        row[None] = values[len(fieldnames) :]
    return row


def _error_message(error: BaseException) -> str:
    detail = " ".join(str(error).split())
    return f"{type(error).__name__}: {detail}".rstrip(": ")[:500]


def record_ingestion_result(
    conn: duckdb.DuckDBPyConnection, result: IngestionResult
) -> None:
    """Persist one completed or failed source-ingestion audit record."""

    conn.execute(
        """
        INSERT INTO ingestion_runs (
            ingestion_id, pipeline_run_id, entity_type, source_filename,
            source_sha256, source_row_count, accepted_row_count,
            rejected_row_count, duplicate_row_count, status, error_message,
            data_as_of
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            result.ingestion_id,
            result.pipeline_run_id,
            result.entity_type,
            result.source_filename,
            result.source_sha256,
            result.source_row_count,
            result.accepted_row_count,
            result.rejected_row_count,
            result.duplicate_row_count,
            result.status,
            result.error_message,
            result.data_as_of,
        ],
    )


def _relation_context(conn: duckdb.DuckDBPyConnection, entity_type: str) -> Any:
    if entity_type == "sessions":
        return {
            user_id: signup_timestamp
            for user_id, signup_timestamp in conn.execute(
                "SELECT user_id, signup_timestamp FROM users"
            ).fetchall()
        }
    if entity_type == "events":
        return {
            session_id: (user_id, session_start)
            for session_id, user_id, session_start in conn.execute(
                "SELECT session_id, user_id, session_start FROM sessions"
            ).fetchall()
        }
    return None


def _validate_relationships(
    entity_type: str, row: dict[str, object], context: Any
) -> None:
    if entity_type == "sessions":
        signup_timestamp = context.get(row["user_id"])
        if signup_timestamp is None:
            raise RowValidationError(f"unknown user_id: {row['user_id']}")
        if row["session_start"] < signup_timestamp:
            raise RowValidationError("session_start cannot be before signup_timestamp")
    elif entity_type == "events":
        session = context.get(row["session_id"])
        if session is None:
            raise RowValidationError(f"unknown session_id: {row['session_id']}")
        session_user_id, session_start = session
        if session_user_id != row["user_id"]:
            raise RowValidationError("session_id belongs to a different user_id")
        if row["event_timestamp"] < session_start:
            raise RowValidationError("event_timestamp cannot be before session_start")


def _insert_row(
    conn: duckdb.DuckDBPyConnection,
    entity_type: str,
    row: dict[str, object],
    source_filename: str,
) -> None:
    columns = CSV_FIELDS[entity_type]
    column_sql = ", ".join(columns) + ", source_file"
    placeholders = ", ".join("?" for _ in range(len(columns) + 1))
    values = [row[column] for column in columns] + [source_filename]
    conn.execute(
        f"INSERT INTO {entity_type} ({column_sql}) VALUES ({placeholders})", values
    )


def _row_timestamp(entity_type: str, row: dict[str, object]) -> datetime:
    field = {
        "users": "signup_timestamp",
        "sessions": "session_start",
        "events": "event_timestamp",
    }[entity_type]
    return row[field]  # type: ignore[return-value]


def ingest_csv(
    conn: duckdb.DuckDBPyConnection,
    entity_type: str,
    path: Path | str,
    *,
    pipeline_run_id: str | None = None,
    manage_transaction: bool = True,
) -> IngestionResult:
    """Validate and ingest one CSV, rejecting bad rows without blocking good ones.

    Natural primary keys make the write idempotent. A repeated valid row is
    counted as a duplicate, not inserted again. Quarantine identifiers are
    content-derived, so repeating a malformed file also preserves one audit row
    per physical rejected row.
    """

    if entity_type not in CSV_FIELDS:
        raise ValueError(f"Unknown entity type: {entity_type}")
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    if manage_transaction:
        initialize_database(conn)
    source_filename = source_path.name
    ingestion_id = str(uuid.uuid4())
    primary_key = PRIMARY_KEYS[entity_type]
    canonical_columns = CSV_FIELDS[entity_type]

    source_sha256 = ""
    source_count = 0
    accepted_count = 0
    rejected_count = 0
    duplicate_count = 0
    data_as_of: datetime | None = None

    if manage_transaction:
        conn.execute("BEGIN TRANSACTION")
    try:
        # Hash and parse the exact same immutable bytes so provenance cannot race
        # a second open of a source that is being replaced concurrently.
        source_bytes = source_path.read_bytes()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        source_text = source_bytes.decode("utf-8-sig")
        handle = io.StringIO(source_text, newline="")
        reader = csv.reader(handle, strict=True)
        try:
            fieldnames = next(reader)
        except StopIteration:
            fieldnames = None
        _validate_header(entity_type, fieldnames)
        assert fieldnames is not None

        existing_rows = {
            row[0]: tuple(row)
            for row in conn.execute(
                f"SELECT {', '.join(canonical_columns)} FROM {entity_type}"
            ).fetchall()
        }
        relation_context = _relation_context(conn, entity_type)

        while True:
            row_number = reader.line_num + 1
            try:
                values = next(reader)
            except StopIteration:
                break
            if not values:
                continue
            raw_row = _dict_row(fieldnames, values)
            source_count += 1
            raw_json = _raw_payload(raw_row)
            is_duplicate = False
            try:
                normalized = validate_row(entity_type, raw_row)
                _validate_relationships(entity_type, normalized, relation_context)
                row_id = normalized[primary_key]
                candidate = tuple(normalized[column] for column in canonical_columns)
                canonical = existing_rows.get(row_id)
                if canonical is not None:
                    if candidate != canonical:
                        raise RowValidationError(
                            f"{primary_key} '{row_id}' conflicts with its "
                            "existing canonical row"
                        )
                    is_duplicate = True
            except RowValidationError as exc:
                rejected_count += 1
                rejection_reason = str(exc)
                rejection_id = hashlib.sha256(
                    (
                        f"{entity_type}:{source_sha256}:{row_number}:"
                        f"{raw_json}:{rejection_reason}"
                    ).encode()
                ).hexdigest()
                conn.execute(
                    """
                    INSERT INTO quarantine (
                        rejection_id, ingestion_id, entity_type,
                        source_filename, source_sha256, row_number,
                        raw_payload, rejection_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (rejection_id) DO NOTHING
                    """,
                    [
                        rejection_id,
                        ingestion_id,
                        entity_type,
                        source_filename,
                        source_sha256,
                        row_number,
                        raw_json,
                        rejection_reason,
                    ],
                )
                continue

            row_time = _row_timestamp(entity_type, normalized)
            data_as_of = max(data_as_of, row_time) if data_as_of else row_time
            if is_duplicate:
                duplicate_count += 1
                continue
            _insert_row(conn, entity_type, normalized, source_filename)
            existing_rows[row_id] = candidate
            accepted_count += 1

        result = IngestionResult(
            ingestion_id=ingestion_id,
            entity_type=entity_type,
            source_filename=source_filename,
            source_sha256=source_sha256,
            status="completed",
            source_row_count=source_count,
            accepted_row_count=accepted_count,
            rejected_row_count=rejected_count,
            duplicate_row_count=duplicate_count,
            data_as_of=data_as_of,
            pipeline_run_id=pipeline_run_id,
        )
        record_ingestion_result(conn, result)
        if manage_transaction:
            conn.execute("COMMIT")
        return result
    except BaseException as exc:
        failed_result = IngestionResult(
            ingestion_id=ingestion_id,
            entity_type=entity_type,
            source_filename=source_filename,
            source_sha256=source_sha256,
            status="failed",
            source_row_count=source_count,
            accepted_row_count=0,
            rejected_row_count=0,
            duplicate_row_count=0,
            data_as_of=None,
            pipeline_run_id=pipeline_run_id,
            error_message=_error_message(exc),
        )
        if manage_transaction:
            try:
                conn.execute("ROLLBACK")
            except Exception as rollback_error:
                exc.add_note(f"Rollback also failed: {rollback_error}")
            else:
                try:
                    record_ingestion_result(conn, failed_result)
                except Exception as audit_error:
                    exc.add_note(
                        f"Recording the failed ingestion also failed: {audit_error}"
                    )
        if isinstance(exc, Exception):
            raise IngestionError(failed_result) from exc
        raise
