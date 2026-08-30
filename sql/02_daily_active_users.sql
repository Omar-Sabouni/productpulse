-- Daily active users (DAU): distinct users with at least one accepted event
-- on a calendar date. Channel and device are fixed acquisition attributes
-- from dim_users, so summing dimensional buckets does not double-count users.

CREATE OR REPLACE TABLE daily_active_users AS
WITH user_activity_days AS (
    SELECT DISTINCT
        CAST(e.event_timestamp AS DATE) AS metric_date,
        u.user_id,
        u.acquisition_channel,
        u.signup_device_type AS device_type
    FROM events AS e
    INNER JOIN dim_users AS u
        ON e.user_id = u.user_id
    WHERE e.event_timestamp IS NOT NULL
)
SELECT
    metric_date,
    acquisition_channel,
    device_type,
    CAST(COUNT(*) AS BIGINT) AS daily_active_users
FROM user_activity_days
GROUP BY
    metric_date,
    acquisition_channel,
    device_type
ORDER BY
    metric_date,
    acquisition_channel,
    device_type;
