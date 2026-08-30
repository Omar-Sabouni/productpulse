-- Weekly cohort activity matrix.
--
-- Cohort and activity weeks are Monday-aligned calendar weeks. Cohort size is
-- the number of users who signed up in that week. Active users are distinct
-- cohort members with any accepted event in an activity week. A complete grid
-- is emitted through the latest signup/activity week, including zero-activity
-- cells, which makes dashboard pivots deterministic. Active users remain the
-- observed-to-date count, but retention_rate is NULL until the entire activity
-- week is covered by data_as_of.

CREATE OR REPLACE TABLE weekly_cohorts AS
WITH cohort_sizes AS (
    SELECT
        cohort_week,
        acquisition_channel,
        signup_device_type AS device_type,
        CAST(COUNT(*) AS BIGINT) AS cohort_size
    FROM dim_users
    GROUP BY
        cohort_week,
        acquisition_channel,
        signup_device_type
),
user_weekly_activity AS (
    SELECT DISTINCT
        u.user_id,
        u.cohort_week,
        u.acquisition_channel,
        u.signup_device_type AS device_type,
        CAST(DATE_TRUNC('week', e.event_timestamp) AS DATE)
            AS activity_week
    FROM dim_users AS u
    INNER JOIN events AS e
        ON e.user_id = u.user_id
        AND e.event_timestamp >= u.signup_timestamp
),
activity_counts AS (
    SELECT
        cohort_week,
        activity_week,
        acquisition_channel,
        device_type,
        CAST(COUNT(*) AS BIGINT) AS active_users
    FROM user_weekly_activity
    GROUP BY
        cohort_week,
        activity_week,
        acquisition_channel,
        device_type
),
last_observed_week AS (
    SELECT MAX(week_start) AS week_start
    FROM (
        SELECT cohort_week AS week_start
        FROM cohort_sizes

        UNION ALL

        SELECT activity_week AS week_start
        FROM activity_counts
    ) AS observed_weeks
),
cohort_grid AS (
    SELECT
        c.cohort_week,
        CAST(g.activity_week AS DATE) AS activity_week,
        c.acquisition_channel,
        c.device_type,
        c.cohort_size
    FROM cohort_sizes AS c
    CROSS JOIN last_observed_week AS last_week
    CROSS JOIN LATERAL GENERATE_SERIES(
        c.cohort_week,
        last_week.week_start,
        INTERVAL '1 week'
    ) AS g(activity_week)
)
SELECT
    g.cohort_week,
    g.activity_week,
    DATE_DIFF('week', g.cohort_week, g.activity_week)
        AS weeks_since_signup,
    g.acquisition_channel,
    g.device_type,
    g.cohort_size,
    COALESCE(a.active_users, 0)::BIGINT AS active_users,
    CASE
        WHEN g.activity_week + 7 <= CAST(c.data_as_of AS DATE)
            THEN CAST(COALESCE(a.active_users, 0) AS DOUBLE)
                / NULLIF(g.cohort_size, 0)
        ELSE NULL
    END AS retention_rate,
    CASE
        WHEN g.activity_week + 7 <= CAST(c.data_as_of AS DATE) THEN TRUE
        ELSE FALSE
    END AS is_complete
FROM cohort_grid AS g
CROSS JOIN analytics_context AS c
LEFT JOIN activity_counts AS a
    ON g.cohort_week = a.cohort_week
    AND g.activity_week = a.activity_week
    AND g.acquisition_channel = a.acquisition_channel
    AND g.device_type = a.device_type
ORDER BY
    g.cohort_week,
    g.activity_week,
    g.acquisition_channel,
    g.device_type;
