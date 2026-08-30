-- Exact-calendar-day D1 and D7 retention by signup-date cohort.
--
-- A retained user has at least one accepted event whose DATE is exactly one
-- (D1) or seven (D7) calendar days after signup_date. These are calendar-day
-- definitions, not rolling 24-hour or seven-day windows. A target day becomes
-- eligible only after that entire UTC calendar day has elapsed.

CREATE OR REPLACE TABLE daily_retention AS
WITH user_retention_flags AS (
    SELECT
        u.user_id,
        u.signup_date AS cohort_date,
        u.acquisition_channel,
        u.signup_device_type AS device_type,
        CASE
            WHEN u.signup_date + 1 < CAST(c.data_as_of AS DATE) THEN TRUE
            ELSE FALSE
        END AS is_d1_eligible,
        CASE
            WHEN u.signup_date + 7 < CAST(c.data_as_of AS DATE) THEN TRUE
            ELSE FALSE
        END AS is_d7_eligible,
        MAX(
            CASE
                WHEN CAST(e.event_timestamp AS DATE) = u.signup_date + 1
                    THEN 1
                ELSE 0
            END
        ) AS retained_d1,
        MAX(
            CASE
                WHEN CAST(e.event_timestamp AS DATE) = u.signup_date + 7
                    THEN 1
                ELSE 0
            END
        ) AS retained_d7
    FROM dim_users AS u
    CROSS JOIN analytics_context AS c
    LEFT JOIN events AS e
        ON e.user_id = u.user_id
        AND e.event_timestamp >= u.signup_timestamp
    GROUP BY
        u.user_id,
        u.signup_date,
        u.acquisition_channel,
        u.signup_device_type,
        c.data_as_of
)
SELECT
    cohort_date,
    acquisition_channel,
    device_type,
    CAST(COUNT(*) AS BIGINT) AS cohort_size,
    CAST(
        SUM(CASE WHEN is_d1_eligible THEN retained_d1 ELSE 0 END)
        AS BIGINT
    ) AS d1_retained_users,
    CAST(
        SUM(CASE WHEN is_d1_eligible THEN retained_d1 ELSE 0 END)
        AS DOUBLE
    ) / NULLIF(COUNT(*) FILTER (WHERE is_d1_eligible), 0)
        AS d1_retention_rate,
    CAST(
        SUM(CASE WHEN is_d7_eligible THEN retained_d7 ELSE 0 END)
        AS BIGINT
    ) AS d7_retained_users,
    CAST(
        SUM(CASE WHEN is_d7_eligible THEN retained_d7 ELSE 0 END)
        AS DOUBLE
    ) / NULLIF(COUNT(*) FILTER (WHERE is_d7_eligible), 0)
        AS d7_retention_rate,
    CAST(COUNT(*) FILTER (WHERE is_d1_eligible) AS BIGINT)
        AS d1_eligible_users,
    CAST(COUNT(*) FILTER (WHERE is_d7_eligible) AS BIGINT)
        AS d7_eligible_users
FROM user_retention_flags
GROUP BY
    cohort_date,
    acquisition_channel,
    device_type
ORDER BY
    cohort_date,
    acquisition_channel,
    device_type;
