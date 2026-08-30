"""Build the local ProductPulse analytics database."""

from __future__ import annotations

import argparse
import os
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import duckdb

from productpulse.generate import generate_dataset
from productpulse.ingest import (
    IngestionError,
    IngestionResult,
    connect_database,
    ingest_csv,
    initialize_database,
    record_ingestion_result,
)
from productpulse.transform import run_transformations

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "input"
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "productpulse.duckdb"
CORE_TABLES = (
    "users",
    "sessions",
    "events",
    "pipeline_runs",
    "ingestion_runs",
    "quarantine",
)
ANALYTICS_TABLES = (
    "analytics_context",
    "dim_users",
    "daily_active_users",
    "signup_activation_funnel",
    "daily_retention",
    "weekly_cohorts",
)
ERROR_MESSAGE_LIMIT = 500


def _audit_error_message(
    error: BaseException, redact_paths: tuple[Path, ...] = ()
) -> str:
    """Return bounded, single-line audit text without local absolute paths."""

    detail = " ".join(str(error).split())
    for path in redact_paths:
        candidates = {str(path)}
        try:
            candidates.add(str(path.resolve(strict=False)))
        except OSError:
            pass
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate not in {"", "."}:
                detail = detail.replace(candidate, "<path>")
    message = f"{type(error).__name__}: {detail}".rstrip(": ")
    return message[:ERROR_MESSAGE_LIMIT]


def run_pipeline(
    input_dir: Path,
    db_path: Path,
) -> dict[str, Any]:
    """Ingest available sources in dependency order and rebuild analytics."""

    return _run_pipeline(
        input_dir,
        db_path,
        run_mode="incremental",
    )


def _run_pipeline(
    input_dir: Path,
    db_path: Path,
    *,
    run_mode: str,
) -> dict[str, Any]:
    """Run one auditable, failure-atomic pipeline transaction."""

    input_dir = Path(input_dir)
    db_path = Path(db_path)
    sources = [
        ("users", input_dir / "users.csv"),
        ("sessions", input_dir / "sessions.csv"),
        ("events", input_dir / "events.csv"),
    ]

    pipeline_run_id = str(uuid.uuid4())
    results: list[IngestionResult] = []
    conn = connect_database(db_path)
    conn.execute(
        """
        INSERT INTO pipeline_runs (pipeline_run_id, run_mode, status)
        VALUES (?, ?, 'started')
        """,
        [pipeline_run_id, run_mode],
    )
    transaction_started = False
    error_stage = "preflight"
    error_source: str | None = None
    try:
        conn.execute("BEGIN TRANSACTION")
        transaction_started = True

        for _, source in sources:
            if not source.is_file():
                error_source = source.name
                raise FileNotFoundError(f"Required input is missing: {source}")

        error_stage = "ingestion"
        for entity_type, source in sources:
            if not source.is_file():
                continue
            error_source = source.name
            results.append(
                ingest_csv(
                    conn,
                    entity_type,
                    source,
                    pipeline_run_id=pipeline_run_id,
                    manage_transaction=False,
                )
            )

        error_stage = "transformations"
        error_source = None
        transformations = run_transformations(conn, manage_transaction=False)

        error_stage = "finalization"
        data_as_of = conn.execute("SELECT MAX(event_timestamp) FROM events").fetchone()[
            0
        ]
        table_counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("users", "sessions", "events", "quarantine")
        }
        conn.execute(
            """
            UPDATE pipeline_runs
            SET status = 'completed', completed_at = current_timestamp,
                data_as_of = ?, error_stage = NULL, error_source = NULL,
                error_message = NULL
            WHERE pipeline_run_id = ?
            """,
            [data_as_of, pipeline_run_id],
        )
        conn.execute("COMMIT")
        transaction_started = False
    except BaseException as exc:
        if transaction_started:
            try:
                conn.execute("ROLLBACK")
            except Exception as rollback_error:
                exc.add_note(f"Pipeline rollback also failed: {rollback_error}")
            transaction_started = False

        # Source-level failures are useful on their own; successful source audit
        # rows deliberately disappear with the outer rollback.
        if isinstance(exc, IngestionError):
            try:
                record_ingestion_result(conn, exc.result)
            except Exception as audit_error:
                exc.add_note(
                    f"Recording the failed ingestion also failed: {audit_error}"
                )

        try:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'failed', completed_at = current_timestamp,
                    error_stage = ?, error_source = ?, error_message = ?
                WHERE pipeline_run_id = ?
                """,
                [
                    error_stage,
                    error_source,
                    _audit_error_message(exc, (input_dir, db_path, PROJECT_ROOT)),
                    pipeline_run_id,
                ],
            )
        except Exception as audit_error:
            exc.add_note(
                f"Recording the failed pipeline run also failed: {audit_error}"
            )
        raise
    finally:
        conn.close()

    return {
        "pipeline_run_id": pipeline_run_id,
        "status": "completed",
        "run_mode": run_mode,
        "ingestions": [asdict(result) for result in results],
        "transformations": transformations,
        "table_counts": table_counts,
        "data_as_of": data_as_of,
    }


def _validate_rebuilt_database(db_path: Path, pipeline_run_id: str) -> None:
    """Reopen a staged database and verify its completed, queryable contract."""

    with duckdb.connect(str(db_path), read_only=True) as conn:
        run = conn.execute(
            "SELECT status FROM pipeline_runs WHERE pipeline_run_id = ?",
            [pipeline_run_id],
        ).fetchone()
        if run != ("completed",):
            raise RuntimeError(
                f"Staged pipeline run {pipeline_run_id} is not completed: {run}"
            )
        for table in CORE_TABLES + ANALYTICS_TABLES:
            conn.execute(f'SELECT * FROM "{table}" LIMIT 0')


def _mark_staged_rebuild_failed(
    db_path: Path,
    pipeline_run_id: str,
    *,
    error_stage: str,
    error_source: str | None,
    error: BaseException,
    redact_paths: tuple[Path, ...] = (),
) -> None:
    """Change a staged completed run to failed when validation/replacement fails."""

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE pipeline_runs
            SET status = 'failed', completed_at = current_timestamp,
                data_as_of = NULL, error_stage = ?, error_source = ?,
                error_message = ?
            WHERE pipeline_run_id = ?
            """,
            [
                error_stage,
                error_source,
                _audit_error_message(error, redact_paths),
                pipeline_run_id,
            ],
        )


def _copy_failed_run_to_existing_database(staged_path: Path, target_path: Path) -> None:
    """Copy only failed-run audit metadata into a compatible prior database."""

    if not staged_path.is_file() or not target_path.is_file():
        return
    with duckdb.connect(str(staged_path), read_only=True) as staged_conn:
        failed_run = staged_conn.execute(
            """
            SELECT pipeline_run_id, run_mode, status, started_at, completed_at,
                   data_as_of, error_stage, error_source, error_message
            FROM pipeline_runs
            WHERE status = 'failed'
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
    if failed_run is None:
        return

    with duckdb.connect(str(target_path)) as target_conn:
        table_names = {row[0] for row in target_conn.execute("SHOW TABLES").fetchall()}
        required_existing = {"users", "sessions", "events", "ingestion_runs"}
        if not required_existing.issubset(table_names):
            return
        initialize_database(target_conn)
        target_conn.execute(
            """
            INSERT INTO pipeline_runs (
                pipeline_run_id, run_mode, status, started_at, completed_at,
                data_as_of, error_stage, error_source, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (pipeline_run_id) DO NOTHING
            """,
            list(failed_run),
        )


def rebuild_database(
    input_dir: Path,
    db_path: Path,
) -> dict[str, Any]:
    """Build a sibling database and atomically replace the target on success."""

    input_dir = Path(input_dir)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{db_path.name}.",
        suffix=".tmp",
        dir=db_path.parent,
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    # DuckDB creates a new database when the path is absent. mkstemp reserves a
    # collision-free sibling name first; removing only that empty file is safe.
    temporary_path.unlink()
    summary: dict[str, Any] | None = None
    failure_stage = "pipeline"
    try:
        summary = _run_pipeline(
            input_dir,
            temporary_path,
            run_mode="rebuild",
        )
        failure_stage = "validation"
        _validate_rebuilt_database(temporary_path, summary["pipeline_run_id"])
        failure_stage = "replacement"
        os.replace(temporary_path, db_path)
        return summary
    except BaseException as exc:
        if summary is not None and temporary_path.is_file():
            try:
                _mark_staged_rebuild_failed(
                    temporary_path,
                    summary["pipeline_run_id"],
                    error_stage=failure_stage,
                    error_source=db_path.name,
                    error=exc,
                    redact_paths=(temporary_path, db_path, PROJECT_ROOT),
                )
            except Exception as audit_error:
                exc.add_note(
                    f"Marking the staged rebuild failed also failed: {audit_error}"
                )
        try:
            _copy_failed_run_to_existing_database(temporary_path, db_path)
        except Exception as audit_error:
            exc.add_note(
                f"Copying failed rebuild audit metadata also failed: {audit_error}"
            )
        raise
    finally:
        temporary_path.unlink(missing_ok=True)
        Path(f"{temporary_path}.wal").unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Regenerate inputs and replace only the target DuckDB file.",
    )
    parser.add_argument(
        "--skip-generate",
        action="store_true",
        help="Use existing CSV inputs instead of regenerating them.",
    )
    args = parser.parse_args()

    if not args.skip_generate:
        generate_dataset(args.input_dir)

    if args.rebuild:
        rebuild_database(args.input_dir, args.db_path)
    else:
        run_pipeline(args.input_dir, args.db_path)
    print("ProductPulse database is ready.")


if __name__ == "__main__":
    main()
