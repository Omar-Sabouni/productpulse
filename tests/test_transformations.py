from __future__ import annotations

from productpulse.transform import run_transformations


def test_daily_active_users_exact_answers(connection, load_valid_sources) -> None:
    load_valid_sources(connection)
    run_transformations(connection)

    actual = connection.execute(
        """
        SELECT CAST(metric_date AS VARCHAR), acquisition_channel, device_type,
               daily_active_users
        FROM daily_active_users
        ORDER BY 1, 2, 3
        """
    ).fetchall()

    assert actual == [
        ("2026-01-05", "organic", "desktop", 2),
        ("2026-01-05", "paid_search", "mobile", 1),
        ("2026-01-06", "organic", "desktop", 2),
        ("2026-01-06", "organic", "mobile", 1),
        ("2026-01-06", "paid_search", "mobile", 1),
        ("2026-01-12", "organic", "desktop", 1),
        ("2026-01-12", "paid_search", "mobile", 1),
        ("2026-01-12", "referral", "tablet", 1),
        ("2026-01-13", "organic", "mobile", 1),
        ("2026-01-13", "referral", "tablet", 1),
        ("2026-01-19", "organic", "desktop", 1),
    ]


def test_signup_to_activation_funnel_exact_answers(
    connection, load_valid_sources
) -> None:
    load_valid_sources(connection)
    run_transformations(connection)

    actual = connection.execute(
        """
        SELECT CAST(signup_date AS VARCHAR), acquisition_channel, device_type,
               signups, eligible_signups, pending_signups, activated_users,
               round(CAST(activation_rate AS DOUBLE), 4)
        FROM signup_activation_funnel
        ORDER BY 1, 2, 3
        """
    ).fetchall()

    assert actual == [
        ("2026-01-05", "organic", "desktop", 2, 2, 0, 1, 0.5),
        ("2026-01-05", "paid_search", "mobile", 1, 1, 0, 1, 1.0),
        ("2026-01-06", "organic", "mobile", 1, 1, 0, 1, 1.0),
        ("2026-01-12", "referral", "tablet", 1, 1, 0, 1, 1.0),
    ]


def test_d1_and_d7_retention_exact_answers(connection, load_valid_sources) -> None:
    load_valid_sources(connection)
    run_transformations(connection)

    actual = connection.execute(
        """
        SELECT CAST(cohort_date AS VARCHAR), acquisition_channel, device_type,
               cohort_size, d1_eligible_users, d1_retained_users,
               round(CAST(d1_retention_rate AS DOUBLE), 4),
               d7_eligible_users, d7_retained_users,
               round(CAST(d7_retention_rate AS DOUBLE), 4)
        FROM daily_retention
        ORDER BY 1, 2, 3
        """
    ).fetchall()

    assert actual == [
        ("2026-01-05", "organic", "desktop", 2, 2, 2, 1.0, 2, 1, 0.5),
        ("2026-01-05", "paid_search", "mobile", 1, 1, 1, 1.0, 1, 1, 1.0),
        ("2026-01-06", "organic", "mobile", 1, 1, 0, 0.0, 1, 1, 1.0),
        ("2026-01-12", "referral", "tablet", 1, 1, 1, 1.0, 0, 0, None),
    ]


def test_weekly_cohort_retention_exact_answers(connection, load_valid_sources) -> None:
    load_valid_sources(connection)
    run_transformations(connection)

    actual = connection.execute(
        """
        SELECT CAST(cohort_week AS VARCHAR), CAST(activity_week AS VARCHAR),
               weeks_since_signup, acquisition_channel, device_type,
               cohort_size, active_users, is_complete,
               round(CAST(retention_rate AS DOUBLE), 4)
        FROM weekly_cohorts
        ORDER BY 1, 2, 4, 5
        """
    ).fetchall()

    assert actual == [
        ("2026-01-05", "2026-01-05", 0, "organic", "desktop", 2, 2, True, 1.0),
        ("2026-01-05", "2026-01-05", 0, "organic", "mobile", 1, 1, True, 1.0),
        ("2026-01-05", "2026-01-05", 0, "paid_search", "mobile", 1, 1, True, 1.0),
        ("2026-01-05", "2026-01-12", 1, "organic", "desktop", 2, 1, True, 0.5),
        ("2026-01-05", "2026-01-12", 1, "organic", "mobile", 1, 1, True, 1.0),
        ("2026-01-05", "2026-01-12", 1, "paid_search", "mobile", 1, 1, True, 1.0),
        ("2026-01-05", "2026-01-19", 2, "organic", "desktop", 2, 1, False, None),
        ("2026-01-05", "2026-01-19", 2, "organic", "mobile", 1, 0, False, None),
        ("2026-01-05", "2026-01-19", 2, "paid_search", "mobile", 1, 0, False, None),
        ("2026-01-12", "2026-01-12", 0, "referral", "tablet", 1, 1, True, 1.0),
        ("2026-01-12", "2026-01-19", 1, "referral", "tablet", 1, 0, False, None),
    ]


def test_maturity_boundaries_use_the_shared_data_as_of(connection) -> None:
    connection.executemany(
        """
        INSERT INTO users (
            user_id, signup_timestamp, acquisition_channel, source_file
        ) VALUES (?, CAST(? AS TIMESTAMPTZ), 'organic', 'maturity_users.csv')
        """,
        [
            ("eligible", "2026-01-01T12:00:00Z"),
            ("pending", "2026-01-01T12:01:00Z"),
        ],
    )
    connection.executemany(
        """
        INSERT INTO sessions (
            session_id, user_id, session_start, device_type, source_file
        ) VALUES (?, ?, CAST(? AS TIMESTAMPTZ), 'desktop', 'maturity_sessions.csv')
        """,
        [
            ("eligible_session", "eligible", "2026-01-01T12:00:00Z"),
            ("pending_session", "pending", "2026-01-01T12:01:00Z"),
        ],
    )
    connection.executemany(
        """
        INSERT INTO events (
            event_id, user_id, session_id, event_name, event_timestamp, source_file
        ) VALUES (?, ?, ?, ?, CAST(? AS TIMESTAMPTZ), 'maturity_events.csv')
        """,
        [
            (
                "eligible_activation",
                "eligible",
                "eligible_session",
                "activation",
                "2026-01-01T12:30:00Z",
            ),
            (
                "pending_activation",
                "pending",
                "pending_session",
                "activation",
                "2026-01-01T12:31:00Z",
            ),
            (
                "eligible_d1",
                "eligible",
                "eligible_session",
                "page_view",
                "2026-01-02T09:00:00Z",
            ),
            (
                "observation_cutoff",
                "eligible",
                "eligible_session",
                "page_view",
                "2026-01-08T12:00:00Z",
            ),
        ],
    )

    run_transformations(connection)

    assert connection.execute(
        """
        SELECT data_as_of = TIMESTAMPTZ '2026-01-08 12:00:00+00'
        FROM analytics_context
        """
    ).fetchone() == (True,)
    assert connection.execute(
        """
        SELECT signups, eligible_signups, pending_signups, activated_users,
               activation_rate
        FROM signup_activation_funnel
        """
    ).fetchone() == (2, 1, 1, 1, 1.0)
    assert connection.execute(
        """
        SELECT cohort_size, d1_eligible_users, d1_retained_users,
               d1_retention_rate, d7_eligible_users, d7_retained_users,
               d7_retention_rate
        FROM daily_retention
        """
    ).fetchone() == (2, 2, 1, 0.5, 0, 0, None)
    assert connection.execute(
        """
        SELECT CAST(activity_week AS VARCHAR), active_users, is_complete,
               retention_rate
        FROM weekly_cohorts
        ORDER BY activity_week
        """
    ).fetchall() == [
        ("2025-12-29", 2, True, 1.0),
        ("2026-01-05", 1, False, None),
    ]


def test_transformations_can_join_a_caller_managed_transaction(
    connection, load_valid_sources
) -> None:
    load_valid_sources(connection)

    connection.execute("BEGIN TRANSACTION")
    filenames = run_transformations(connection, manage_transaction=False)
    assert filenames[0] == "00_analytics_context.sql"
    assert connection.execute("SELECT COUNT(*) FROM dim_users").fetchone() == (5,)
    connection.execute("ROLLBACK")

    tables = {
        row[0]
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            """
        ).fetchall()
    }
    assert "analytics_context" not in tables
    assert "dim_users" not in tables
