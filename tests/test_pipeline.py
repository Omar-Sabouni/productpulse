from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from conftest import FIXTURES, VALID_FIXTURES

from productpulse.generate import generate_dataset
from productpulse.ingest import IngestionError
from productpulse.pipeline import rebuild_database, run_pipeline


def _copy_valid_inputs(input_dir: Path) -> None:
    input_dir.mkdir()
    for filename in ("users.csv", "sessions.csv", "events.csv"):
        shutil.copyfile(VALID_FIXTURES / filename, input_dir / filename)


def test_rebuild_database_loads_core_inputs_and_builds_analytics(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    _copy_valid_inputs(input_dir)

    summary = rebuild_database(
        input_dir=input_dir,
        db_path=tmp_path / "pipeline.duckdb",
    )

    assert summary["table_counts"] == {
        "users": 5,
        "sessions": 13,
        "events": 14,
        "quarantine": 0,
    }
    assert [run["source_filename"] for run in summary["ingestions"]] == [
        "users.csv",
        "sessions.csv",
        "events.csv",
    ]
    assert [run["source_row_count"] for run in summary["ingestions"]] == [
        5,
        13,
        14,
    ]
    assert [run["rejected_row_count"] for run in summary["ingestions"]] == [
        0,
        0,
        0,
    ]
    assert summary["data_as_of"].astimezone(UTC) == datetime(
        2026, 1, 19, 9, 5, tzinfo=UTC
    )
    assert summary["status"] == "completed"
    assert summary["run_mode"] == "rebuild"
    assert summary["pipeline_run_id"]
    assert summary["transformations"] == [
        "00_analytics_context.sql",
        "01_dim_users.sql",
        "02_daily_active_users.sql",
        "03_signup_activation_funnel.sql",
        "04_daily_retention.sql",
        "05_weekly_cohorts.sql",
    ]
    with duckdb.connect(str(tmp_path / "pipeline.duckdb"), read_only=True) as conn:
        pipeline_run = conn.execute(
            """
            SELECT run_mode, status, data_as_of, error_message
            FROM pipeline_runs
            WHERE pipeline_run_id = ?
            """,
            [summary["pipeline_run_id"]],
        ).fetchone()
        ingestion_run_ids = conn.execute(
            "SELECT DISTINCT pipeline_run_id FROM ingestion_runs"
        ).fetchall()
    assert pipeline_run[:2] == ("rebuild", "completed")
    assert pipeline_run[2].astimezone(UTC) == summary["data_as_of"]
    assert pipeline_run[3] is None
    assert ingestion_run_ids == [(summary["pipeline_run_id"],)]


def test_generated_inputs_build_expected_analytics(tmp_path: Path) -> None:
    input_dir = tmp_path / "generated"
    generate_dataset(input_dir)

    summary = rebuild_database(
        input_dir=input_dir,
        db_path=tmp_path / "generated.duckdb",
    )

    assert [item["source_filename"] for item in summary["ingestions"]] == [
        "users.csv",
        "sessions.csv",
        "events.csv",
    ]
    assert summary["table_counts"] == {
        "users": 30,
        "sessions": 81,
        "events": 140,
        "quarantine": 0,
    }


def test_failed_incremental_run_rolls_back_all_sources_and_records_failure(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copyfile(VALID_FIXTURES / "users.csv", input_dir / "users.csv")
    shutil.copyfile(VALID_FIXTURES / "sessions.csv", input_dir / "sessions.csv")
    shutil.copyfile(
        FIXTURES / "malformed" / "events_duplicate_header.csv",
        input_dir / "events.csv",
    )
    db_path = tmp_path / "incremental.duckdb"

    with pytest.raises(IngestionError):
        run_pipeline(input_dir, db_path)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("users", "sessions", "events", "quarantine")
        }
        pipeline_run = conn.execute(
            """
            SELECT run_mode, status, error_stage, error_source, error_message
            FROM pipeline_runs
            """
        ).fetchone()
        ingestion_runs = conn.execute(
            """
            SELECT entity_type, source_filename, status, pipeline_run_id
            FROM ingestion_runs
            """
        ).fetchall()

    assert counts == {"users": 0, "sessions": 0, "events": 0, "quarantine": 0}
    assert pipeline_run[:4] == (
        "incremental",
        "failed",
        "ingestion",
        "events.csv",
    )
    assert "duplicate column name" in pipeline_run[4]
    assert len(ingestion_runs) == 1
    assert ingestion_runs[0][:3] == ("events", "events.csv", "failed")
    assert ingestion_runs[0][3]


def test_failed_transformation_rolls_back_all_source_work(
    tmp_path: Path, monkeypatch
) -> None:
    input_dir = tmp_path / "input"
    _copy_valid_inputs(input_dir)
    db_path = tmp_path / "transform_failure.duckdb"

    sensitive_path = Path(__file__).resolve().parents[1] / "sql" / "broken.sql"

    def fail_transformations(*args, **kwargs):
        raise RuntimeError(f"deliberate transformation failure at {sensitive_path}")

    monkeypatch.setattr(
        "productpulse.pipeline.run_transformations", fail_transformations
    )
    with pytest.raises(RuntimeError, match="deliberate transformation failure"):
        run_pipeline(input_dir, db_path)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
        failure = conn.execute(
            "SELECT status, error_stage, error_source, error_message FROM pipeline_runs"
        ).fetchone()
    assert failure[:3] == ("failed", "transformations", None)
    assert "deliberate transformation failure" in failure[3]
    assert str(sensitive_path) not in failure[3]


def test_keyboard_interrupt_rolls_back_pipeline_and_marks_run_failed(
    tmp_path: Path, monkeypatch
) -> None:
    input_dir = tmp_path / "input"
    _copy_valid_inputs(input_dir)
    db_path = tmp_path / "interrupted.duckdb"

    def interrupt_transformations(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "productpulse.pipeline.run_transformations", interrupt_transformations
    )
    with pytest.raises(KeyboardInterrupt):
        run_pipeline(input_dir, db_path)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone() == (0,)
        failure = conn.execute(
            "SELECT status, error_stage, error_message FROM pipeline_runs"
        ).fetchone()
    assert failure == ("failed", "transformations", "KeyboardInterrupt")


def test_failed_rebuild_preserves_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "preserved.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE sentinel(value VARCHAR)")
        conn.execute("INSERT INTO sentinel VALUES ('keep me')")
    missing_input = tmp_path / "missing_input"
    missing_input.mkdir()

    with pytest.raises(FileNotFoundError):
        rebuild_database(missing_input, db_path)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("SELECT value FROM sentinel").fetchone()[0] == "keep me"
    assert not list(tmp_path.glob(f".{db_path.name}.*.tmp"))


def test_failed_rebuild_adds_failure_audit_to_compatible_prior_database(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "valid_input"
    _copy_valid_inputs(input_dir)
    db_path = tmp_path / "existing.duckdb"
    completed = rebuild_database(input_dir, db_path)
    missing_input = tmp_path / "missing_input"
    missing_input.mkdir()

    with pytest.raises(FileNotFoundError):
        rebuild_database(missing_input, db_path)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("users", "sessions", "events")
        }
        failed = conn.execute(
            """
            SELECT run_mode, status, error_stage, error_source, error_message
            FROM pipeline_runs
            WHERE pipeline_run_id <> ?
            """,
            [completed["pipeline_run_id"]],
        ).fetchone()
    assert counts == {"users": 5, "sessions": 13, "events": 14}
    assert failed[:4] == ("rebuild", "failed", "preflight", "users.csv")
    assert "Required input is missing" in failed[4]
    assert str(missing_input) not in failed[4]
    assert not list(tmp_path.glob(f".{db_path.name}.*.tmp"))


def test_staged_validation_failure_preserves_prior_data_and_is_audited(
    tmp_path: Path, monkeypatch
) -> None:
    input_dir = tmp_path / "input"
    _copy_valid_inputs(input_dir)
    db_path = tmp_path / "existing.duckdb"
    completed = rebuild_database(input_dir, db_path)

    def fail_validation(*args, **kwargs):
        raise RuntimeError("deliberate staged validation failure")

    monkeypatch.setattr(
        "productpulse.pipeline._validate_rebuilt_database", fail_validation
    )
    with pytest.raises(RuntimeError, match="deliberate staged validation failure"):
        rebuild_database(input_dir, db_path)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 5
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 14
        failed = conn.execute(
            """
            SELECT status, error_stage, error_source, error_message
            FROM pipeline_runs
            WHERE pipeline_run_id <> ?
            """,
            [completed["pipeline_run_id"]],
        ).fetchone()
    assert failed[:3] == ("failed", "validation", db_path.name)
    assert "deliberate staged validation failure" in failed[3]


def test_failed_rebuild_audit_error_does_not_mask_pipeline_error(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "existing.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE sentinel(value INTEGER)")
        conn.execute("INSERT INTO sentinel VALUES (1)")
    missing_input = tmp_path / "missing_input"
    missing_input.mkdir()

    def fail_audit_copy(*args, **kwargs):
        raise RuntimeError("audit destination unavailable")

    monkeypatch.setattr(
        "productpulse.pipeline._copy_failed_run_to_existing_database",
        fail_audit_copy,
    )
    with pytest.raises(FileNotFoundError) as captured:
        rebuild_database(missing_input, db_path)

    assert any(
        "audit destination unavailable" in note
        for note in getattr(captured.value, "__notes__", [])
    )
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("SELECT value FROM sentinel").fetchone()[0] == 1
