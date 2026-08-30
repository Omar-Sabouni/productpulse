from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
from streamlit.testing.v1 import AppTest

from productpulse.dashboard import (
    DEFAULT_DATABASE_PATH,
    aggregate_funnel,
    aggregate_retention,
    aggregate_weekly_cohorts,
    build_dau_date_spine,
    configured_database_path,
    database_file_state,
    database_fingerprint,
    latest_failure_message,
    load_dashboard_snapshot,
    normalize_date_range,
)
from productpulse.generate import generate_dataset
from productpulse.pipeline import rebuild_database

DASHBOARD_PATH = Path(__file__).parents[1] / "src" / "productpulse" / "dashboard.py"


def _create_dashboard_database(path: Path, include_data: bool = True) -> None:
    with duckdb.connect(str(path)) as conn:
        conn.execute(
            """
            CREATE TABLE users (user_id VARCHAR, source_file VARCHAR);
            CREATE TABLE sessions (session_id VARCHAR, source_file VARCHAR);
            CREATE TABLE events (
                event_id VARCHAR, event_timestamp TIMESTAMPTZ, source_file VARCHAR
            );
            CREATE TABLE quarantine (
                rejection_id VARCHAR,
                ingestion_id VARCHAR,
                entity_type VARCHAR,
                source_filename VARCHAR,
                row_number BIGINT,
                rejection_reason VARCHAR,
                raw_payload VARCHAR,
                rejected_at TIMESTAMPTZ
            );
            CREATE TABLE pipeline_runs (
                pipeline_run_id VARCHAR,
                run_mode VARCHAR,
                status VARCHAR,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                data_as_of TIMESTAMPTZ,
                error_stage VARCHAR,
                error_source VARCHAR,
                error_message VARCHAR
            );
            CREATE TABLE ingestion_runs (
                ingestion_id VARCHAR,
                pipeline_run_id VARCHAR,
                entity_type VARCHAR,
                source_filename VARCHAR,
                source_row_count BIGINT,
                accepted_row_count BIGINT,
                rejected_row_count BIGINT,
                duplicate_row_count BIGINT,
                data_as_of TIMESTAMPTZ,
                ingested_at TIMESTAMPTZ
            );
            CREATE TABLE analytics_context (data_as_of TIMESTAMPTZ);
            CREATE TABLE dim_users (
                user_id VARCHAR,
                signup_date DATE,
                cohort_week DATE,
                acquisition_channel VARCHAR,
                signup_device_type VARCHAR
            );
            CREATE TABLE daily_active_users (
                metric_date DATE,
                acquisition_channel VARCHAR,
                device_type VARCHAR,
                daily_active_users BIGINT
            );
            CREATE TABLE signup_activation_funnel (
                signup_date DATE,
                acquisition_channel VARCHAR,
                device_type VARCHAR,
                signups BIGINT,
                activated_users BIGINT,
                activation_rate DOUBLE,
                eligible_signups BIGINT,
                pending_signups BIGINT
            );
            CREATE TABLE daily_retention (
                cohort_date DATE,
                acquisition_channel VARCHAR,
                device_type VARCHAR,
                cohort_size BIGINT,
                d1_retained_users BIGINT,
                d1_retention_rate DOUBLE,
                d7_retained_users BIGINT,
                d7_retention_rate DOUBLE,
                d1_eligible_users BIGINT,
                d7_eligible_users BIGINT
            );
            CREATE TABLE weekly_cohorts (
                cohort_week DATE,
                activity_week DATE,
                weeks_since_signup BIGINT,
                acquisition_channel VARCHAR,
                device_type VARCHAR,
                cohort_size BIGINT,
                active_users BIGINT,
                retention_rate DOUBLE,
                is_complete BOOLEAN
            );
            """
        )
        if not include_data:
            return
        conn.execute(
            """
            INSERT INTO users VALUES ('u1', 'users.csv'), ('u2', 'users.csv');
            INSERT INTO sessions VALUES ('s1', 'sessions.csv'), ('s2', 'sessions.csv');
            INSERT INTO events VALUES
                ('e1', '2026-01-10T12:00:00Z', 'events.csv');
            INSERT INTO pipeline_runs VALUES
                ('run-complete', 'rebuild', 'completed',
                 '2026-01-10T12:01:00Z', '2026-01-10T12:02:00Z',
                 '2026-01-10T12:00:00Z', NULL, NULL, NULL),
                ('run-failed', 'incremental', 'failed',
                 '2026-01-10T13:00:00Z', '2026-01-10T13:01:00Z',
                 NULL, 'ingest', 'events.csv', 'synthetic failure');
            INSERT INTO ingestion_runs VALUES
                ('ingest-events', 'run-complete', 'events', 'events.csv',
                 1, 1, 0, 0, '2026-01-10T12:00:00Z',
                 '2026-01-10T12:01:30Z');
            INSERT INTO analytics_context VALUES ('2026-01-10T12:00:00Z');
            INSERT INTO dim_users VALUES
                ('u1', '2026-01-01', '2025-12-29', 'organic', 'desktop'),
                ('u2', '2026-01-08', '2026-01-05', 'organic', 'desktop');
            INSERT INTO daily_active_users VALUES
                ('2026-01-01', 'organic', 'desktop', 1),
                ('2026-01-03', 'organic', 'desktop', 1);
            INSERT INTO signup_activation_funnel VALUES
                ('2026-01-01', 'organic', 'desktop', 1, 1, 1.0, 1, 0),
                ('2026-01-08', 'organic', 'desktop', 1, 0, NULL, 0, 1);
            INSERT INTO daily_retention VALUES
                ('2026-01-01', 'organic', 'desktop', 1, 1, 1.0, 0, 0.0, 1, 1),
                ('2026-01-08', 'organic', 'desktop', 1, 0, NULL, 0, NULL, 0, 0);
            INSERT INTO weekly_cohorts VALUES
                ('2025-12-29', '2025-12-29', 0, 'organic', 'desktop',
                 1, 1, 1.0, TRUE),
                ('2026-01-05', '2026-01-05', 0, 'organic', 'desktop',
                 1, 1, NULL, FALSE);
            """
        )
        conn.executemany(
            """
            INSERT INTO quarantine VALUES (?, ?, 'events', 'events.csv', ?,
                'bad timestamp', ?, '2026-01-10T12:00:00Z')
            """,
            [
                (f"reject-{number}", "ingest-events", number, f'{{"row":{number}}}')
                for number in range(1, 131)
            ],
        )


def test_database_path_and_file_states_are_explicit(tmp_path: Path) -> None:
    configured = tmp_path / "custom.duckdb"
    assert (
        configured_database_path({"PRODUCTPULSE_DATABASE_PATH": str(configured)})
        == configured
    )
    assert configured_database_path({}) == DEFAULT_DATABASE_PATH.resolve(strict=False)
    assert database_file_state(configured) == "missing"
    configured.touch()
    assert database_file_state(configured) == "empty"


def test_incomplete_date_range_is_not_coerced_to_one_day() -> None:
    start = date(2026, 1, 1)
    end = date(2026, 1, 3)
    assert normalize_date_range(()) is None
    assert normalize_date_range((start,)) is None
    assert normalize_date_range(start) is None
    assert normalize_date_range((end, start)) is None
    assert normalize_date_range((start, end)) == (start, end)


def test_dau_date_spine_fills_zeros_but_empty_selection_stays_empty() -> None:
    activity = pd.DataFrame(
        {
            "metric_date": [date(2026, 1, 1), date(2026, 1, 3)],
            "acquisition_channel": ["organic", "organic"],
            "device_type": ["desktop", "desktop"],
            "daily_active_users": [2, 1],
        }
    )
    result = build_dau_date_spine(
        activity,
        date(2026, 1, 1),
        date(2026, 1, 3),
        ["organic"],
        ["desktop"],
    )
    assert result["active_users"].tolist() == [2, 0, 1]
    assert build_dau_date_spine(
        activity,
        date(2026, 1, 1),
        date(2026, 1, 3),
        [],
        ["desktop"],
    ).empty


def test_maturity_aggregations_use_eligible_denominators_and_na() -> None:
    funnel = pd.DataFrame(
        {
            "signup_date": [date(2026, 1, 1), date(2026, 1, 1)],
            "acquisition_channel": ["organic", "email"],
            "device_type": ["desktop", "desktop"],
            "signups": [2, 1],
            "eligible_signups": [2, 0],
            "pending_signups": [0, 1],
            "activated_users": [1, 0],
            "activation_rate": [0.5, pd.NA],
        }
    )
    aggregated_funnel = aggregate_funnel(
        funnel,
        date(2026, 1, 1),
        date(2026, 1, 1),
        ["organic", "email"],
        ["desktop"],
    ).iloc[0]
    assert aggregated_funnel["signups"] == 3
    assert aggregated_funnel["eligible_signups"] == 2
    assert aggregated_funnel["pending_signups"] == 1
    assert aggregated_funnel["activation_rate"] == 0.5

    retention = pd.DataFrame(
        {
            "cohort_date": [date(2026, 1, 1)],
            "acquisition_channel": ["organic"],
            "device_type": ["desktop"],
            "cohort_size": [3],
            "d1_eligible_users": [2],
            "d1_retained_users": [1],
            "d7_eligible_users": [0],
            "d7_retained_users": [0],
        }
    )
    aggregated_retention = aggregate_retention(
        retention,
        date(2026, 1, 1),
        date(2026, 1, 1),
        ["organic"],
        ["desktop"],
    ).iloc[0]
    assert aggregated_retention["d1_pending_users"] == 1
    assert aggregated_retention["d1_retention_rate"] == 0.5
    assert aggregated_retention["d7_pending_users"] == 3
    assert pd.isna(aggregated_retention["d7_retention_rate"])


def test_weekly_aggregation_keeps_incomplete_cells_unknown() -> None:
    cohorts = pd.DataFrame(
        {
            "cohort_week": [date(2026, 1, 5), date(2026, 1, 5)],
            "weeks_since_signup": [0, 0],
            "acquisition_channel": ["organic", "email"],
            "device_type": ["desktop", "desktop"],
            "cohort_size": [2, 1],
            "active_users": [1, 1],
            "is_complete": [True, False],
        }
    )
    result = aggregate_weekly_cohorts(
        cohorts,
        date(2026, 1, 5),
        date(2026, 1, 5),
        ["organic", "email"],
        ["desktop"],
    ).iloc[0]
    assert result["active_users"] == 2
    assert not result["is_complete"]
    assert pd.isna(result["retention_rate"])


def test_snapshot_refreshes_when_the_database_changes(tmp_path: Path) -> None:
    database = tmp_path / "dashboard.duckdb"
    _create_dashboard_database(database)
    fingerprint = database_fingerprint(database)
    load_dashboard_snapshot.clear()

    first = load_dashboard_snapshot(str(database), fingerprint)
    assert first["latest_run"].iloc[0]["status"] == "failed"
    assert pd.Timestamp(first["bounds"].iloc[0]["max_date"]).date() == date(2026, 1, 10)

    with duckdb.connect(str(database)) as conn:
        conn.execute(
            "INSERT INTO events VALUES ('e2', '2026-01-11T12:00:00Z', 'events.csv')"
        )
    refreshed_fingerprint = database_fingerprint(database)
    assert refreshed_fingerprint != fingerprint
    refreshed = load_dashboard_snapshot(str(database), refreshed_fingerprint)
    assert pd.Timestamp(refreshed["bounds"].iloc[0]["max_date"]).date() == date(
        2026, 1, 11
    )
    assert latest_failure_message(refreshed["latest_run"]) == (
        "The latest refresh failed. Showing the last available data."
    )


def test_streamlit_handles_missing_and_empty_analytics_databases(
    tmp_path: Path, monkeypatch
) -> None:
    missing = tmp_path / "missing.duckdb"
    monkeypatch.setenv("PRODUCTPULSE_DATABASE_PATH", str(missing))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=20).run()
    assert not app.exception
    assert any("database is missing" in error.value for error in app.error)

    empty_database = tmp_path / "empty.duckdb"
    _create_dashboard_database(empty_database, include_data=False)
    monkeypatch.setenv("PRODUCTPULSE_DATABASE_PATH", str(empty_database))
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=20).run()
    assert not app.exception
    assert any("no activity data" in info.value for info in app.info)


def test_streamlit_rejects_an_older_analytics_schema_cleanly(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "old_schema.duckdb"
    _create_dashboard_database(database)
    with duckdb.connect(str(database)) as conn:
        conn.execute(
            "ALTER TABLE signup_activation_funnel DROP COLUMN eligible_signups"
        )
    monkeypatch.setenv("PRODUCTPULSE_DATABASE_PATH", str(database))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=20).run()

    assert not app.exception
    assert any("could not be opened" in error.value for error in app.error)


def test_streamlit_waits_for_a_complete_date_range(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "dashboard.duckdb"
    _create_dashboard_database(database)
    monkeypatch.setenv("PRODUCTPULSE_DATABASE_PATH", str(database))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=20).run()
    assert not app.exception
    app.date_input[0].set_value((date(2026, 1, 2),))
    app.run()

    assert not app.exception
    assert any("complete the date range" in info.value.lower() for info in app.info)
    assert len(app.info) == 1


def test_streamlit_scope_filters_keep_empty_selections_explicit(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "dashboard.duckdb"
    _create_dashboard_database(database)
    monkeypatch.setenv("PRODUCTPULSE_DATABASE_PATH", str(database))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=20).run()
    app.multiselect[0].set_value([]).run()

    assert not app.exception
    assert len(app.tabs) == 4
    assert len(app.info) == 4
    assert all("no users match" in message.value.lower() for message in app.info)


def test_real_rebuild_opens_in_dashboard(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    database = tmp_path / "rebuilt.duckdb"
    generate_dataset(input_dir)
    summary = rebuild_database(input_dir, database)
    monkeypatch.setenv("PRODUCTPULSE_DATABASE_PATH", str(database))

    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=20).run()

    assert summary["table_counts"]["events"] == 140
    assert not app.exception
    assert not app.error
    assert [tab.label for tab in app.tabs] == [
        "Activity",
        "Activation",
        "Retention",
        "Cohorts",
    ]
    assert app.sidebar.header[0].value == "Filters"
    assert app.date_input[0].label == "Date range"
    assert [select.label for select in app.multiselect] == [
        "Acquisition channel",
        "Signup device",
    ]
    assert not app.status
    assert not app.metric
    assert any(
        " to " in caption.value and caption.value.endswith(" users")
        for caption in app.caption
    )
    prose = "\n".join(item.value for item in app.markdown)
    assert "rate 72.4%" in prose
    assert {item.label for item in app.expander} == {
        "Daily numbers",
        "Activation by cohort",
        "Retention by cohort",
        "Retention by week",
    }
