-- One shared observation cutoff for every maturity-aware metric.
--
-- data_as_of is the latest accepted event timestamp represented by the
-- canonical event table. Downstream transformations interpret it in UTC.

CREATE OR REPLACE TABLE analytics_context AS
SELECT MAX(event_timestamp) AS data_as_of
FROM events;
