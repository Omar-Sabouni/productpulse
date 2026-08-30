-- Build one stable row per user for use by every downstream metric.
--
-- Device is treated as an acquisition attribute: it is the device on the
-- user's first session at or after signup. Keeping that device fixed makes
-- channel/device buckets mutually exclusive and comparable across metrics.

CREATE OR REPLACE TABLE dim_users AS
WITH ranked_users AS (
    SELECT
        user_id,
        signup_timestamp,
        acquisition_channel,
        ROW_NUMBER() OVER (
            PARTITION BY user_id
            ORDER BY
                signup_timestamp ASC,
                acquisition_channel ASC
        ) AS user_row_number
    FROM users
    WHERE
        user_id IS NOT NULL
        AND signup_timestamp IS NOT NULL
),
canonical_users AS (
    SELECT
        user_id,
        signup_timestamp,
        CAST(signup_timestamp AS DATE) AS signup_date,
        CAST(DATE_TRUNC('week', signup_timestamp) AS DATE) AS cohort_week,
        acquisition_channel
    FROM ranked_users
    WHERE user_row_number = 1
),
ranked_sessions AS (
    SELECT
        s.user_id,
        s.session_id,
        s.session_start,
        s.device_type,
        ROW_NUMBER() OVER (
            PARTITION BY s.user_id
            ORDER BY
                s.session_start ASC,
                s.session_id ASC
        ) AS session_row_number
    FROM sessions AS s
    INNER JOIN canonical_users AS u
        ON s.user_id = u.user_id
    WHERE
        s.session_start IS NOT NULL
        AND s.session_start >= u.signup_timestamp
),
first_sessions AS (
    SELECT
        user_id,
        session_id,
        session_start,
        device_type
    FROM ranked_sessions
    WHERE session_row_number = 1
)
SELECT
    u.user_id,
    u.signup_timestamp,
    u.signup_date,
    u.cohort_week,
    COALESCE(NULLIF(TRIM(u.acquisition_channel), ''), 'unknown')
        AS acquisition_channel,
    f.session_id AS first_session_id,
    f.session_start AS first_session_timestamp,
    COALESCE(NULLIF(TRIM(f.device_type), ''), 'unknown')
        AS signup_device_type
FROM canonical_users AS u
LEFT JOIN first_sessions AS f
    ON u.user_id = f.user_id;
