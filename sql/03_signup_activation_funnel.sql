-- Signup-to-activation funnel by signup-date cohort.
--
-- A user activates when their first event named "activation" occurs at or
-- after signup and no later than exactly seven elapsed days after signup.
-- The boundary at signup + 7 days is inclusive. Users enter the denominator
-- only once data_as_of reaches that exact elapsed-time boundary.

CREATE OR REPLACE TABLE signup_activation_funnel AS
WITH activation_by_user AS (
    SELECT
        u.user_id,
        MIN(e.event_timestamp) AS first_activation_timestamp
    FROM dim_users AS u
    LEFT JOIN events AS e
        ON e.user_id = u.user_id
        AND e.event_name = 'activation'
        AND e.event_timestamp >= u.signup_timestamp
        AND e.event_timestamp <= u.signup_timestamp + INTERVAL '7 days'
    GROUP BY u.user_id
),
user_outcomes AS (
    SELECT
        u.user_id,
        u.signup_date,
        u.acquisition_channel,
        u.signup_device_type AS device_type,
        a.first_activation_timestamp,
        CASE
            WHEN c.data_as_of >= u.signup_timestamp + INTERVAL '7 days'
                THEN TRUE
            ELSE FALSE
        END AS is_eligible
    FROM dim_users AS u
    INNER JOIN activation_by_user AS a
        ON u.user_id = a.user_id
    CROSS JOIN analytics_context AS c
)
SELECT
    signup_date,
    acquisition_channel,
    device_type,
    CAST(COUNT(*) AS BIGINT) AS signups,
    CAST(
        COUNT(*) FILTER (
            WHERE is_eligible AND first_activation_timestamp IS NOT NULL
        )
        AS BIGINT
    ) AS activated_users,
    CAST(
        COUNT(*) FILTER (
            WHERE is_eligible AND first_activation_timestamp IS NOT NULL
        )
        AS DOUBLE
    ) / NULLIF(COUNT(*) FILTER (WHERE is_eligible), 0) AS activation_rate,
    CAST(COUNT(*) FILTER (WHERE is_eligible) AS BIGINT) AS eligible_signups,
    CAST(COUNT(*) FILTER (WHERE NOT is_eligible) AS BIGINT) AS pending_signups
FROM user_outcomes
GROUP BY
    signup_date,
    acquisition_channel,
    device_type
ORDER BY
    signup_date,
    acquisition_channel,
    device_type;
