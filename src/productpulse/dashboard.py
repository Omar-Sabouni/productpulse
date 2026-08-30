"""Streamlit dashboard for the ProductPulse DuckDB analytics tables."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from productpulse.charts import (
    productpulse_cohort_chart,
    productpulse_funnel_chart,
    productpulse_line_chart,
)
from productpulse.theme import CONSOLE_MASTHEAD, CONSOLE_STYLES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "productpulse.duckdb"
DATABASE_PATH_ENV = "PRODUCTPULSE_DATABASE_PATH"

DatabaseFingerprint = tuple[int, int, int, int]


def configured_database_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return the configured database path without freezing environment state."""

    values = os.environ if environ is None else environ
    configured = values.get(DATABASE_PATH_ENV, "").strip()
    path = Path(configured).expanduser() if configured else DEFAULT_DATABASE_PATH
    return path.resolve(strict=False)


def database_file_state(path: Path) -> str:
    """Classify a database path before asking DuckDB to open it."""

    try:
        if not path.is_file():
            return "missing"
        if path.stat().st_size == 0:
            return "empty"
    except OSError:
        return "missing"
    return "ready"


def database_fingerprint(path: Path) -> DatabaseFingerprint:
    """Build a cache key that changes when a database file is replaced or updated."""

    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def normalize_date_range(value: object) -> tuple[date, date] | None:
    """Accept only a complete, ordered two-date range from Streamlit."""

    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    start_date, end_date = value
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        return None
    if start_date > end_date:
        return None
    return start_date, end_date


def format_rate(value: object) -> str:
    """Format a mature rate while preserving unknown maturity as N/A."""

    return "N/A" if pd.isna(value) else f"{float(value):.1%}"


def filter_clause(
    date_column: str,
    start_date: date,
    end_date: date,
    channels: Iterable[str],
    devices: Iterable[str],
) -> tuple[str, tuple[object, ...]]:
    """Build a parameterized dimensional filter for a trusted date column."""

    allowed_date_columns = {"metric_date", "signup_date", "cohort_date", "cohort_week"}
    if date_column not in allowed_date_columns:
        raise ValueError(f"Unsupported date column: {date_column}")
    channel_values = tuple(channels)
    device_values = tuple(devices)
    predicates = [f"{date_column} BETWEEN ? AND ?"]
    params: list[object] = [start_date, end_date]
    if channel_values:
        predicates.append(
            "acquisition_channel IN (" + ", ".join("?" for _ in channel_values) + ")"
        )
        params.extend(channel_values)
    else:
        predicates.append("FALSE")
    if device_values:
        predicates.append(
            "device_type IN (" + ", ".join("?" for _ in device_values) + ")"
        )
        params.extend(device_values)
    else:
        predicates.append("FALSE")
    return " AND ".join(predicates), tuple(params)


def _selected_rows(
    frame: pd.DataFrame,
    date_column: str,
    start_date: date,
    end_date: date,
    channels: Iterable[str],
    devices: Iterable[str],
) -> pd.DataFrame:
    channel_values = tuple(channels)
    device_values = tuple(devices)
    if frame.empty or not channel_values or not device_values:
        return frame.iloc[0:0].copy()
    metric_dates = pd.to_datetime(frame[date_column]).dt.date
    mask = (
        metric_dates.between(start_date, end_date)
        & frame["acquisition_channel"].isin(channel_values)
        & frame["device_type"].isin(device_values)
    )
    selected = frame.loc[mask].copy()
    selected[date_column] = pd.to_datetime(selected[date_column]).dt.date
    return selected


def selected_population_count(
    population: pd.DataFrame,
    channels: Iterable[str],
    devices: Iterable[str],
) -> int:
    """Count canonical users represented by the selected dimensions."""

    channel_values = tuple(channels)
    device_values = tuple(devices)
    if population.empty or not channel_values or not device_values:
        return 0
    selected = population[
        population["acquisition_channel"].isin(channel_values)
        & population["device_type"].isin(device_values)
    ]
    return int(selected["population"].sum()) if not selected.empty else 0


def build_dau_date_spine(
    daily_activity: pd.DataFrame,
    start_date: date,
    end_date: date,
    channels: Iterable[str],
    devices: Iterable[str],
) -> pd.DataFrame:
    """Aggregate DAU and fill zero-activity dates for a real selection."""

    columns = ["metric_date", "active_users"]
    channel_values = tuple(channels)
    device_values = tuple(devices)
    if not channel_values or not device_values or start_date > end_date:
        return pd.DataFrame(columns=columns)
    selected = _selected_rows(
        daily_activity,
        "metric_date",
        start_date,
        end_date,
        channel_values,
        device_values,
    )
    if selected.empty:
        totals = pd.DataFrame(columns=columns)
    else:
        totals = (
            selected.groupby("metric_date", as_index=False)["daily_active_users"]
            .sum()
            .rename(columns={"daily_active_users": "active_users"})
        )
    spine = pd.DataFrame(
        {"metric_date": pd.date_range(start_date, end_date, freq="D").date}
    )
    result = spine.merge(totals, on="metric_date", how="left")
    result["active_users"] = result["active_users"].fillna(0).astype("int64")
    return result


def aggregate_funnel(
    funnel: pd.DataFrame,
    start_date: date,
    end_date: date,
    channels: Iterable[str],
    devices: Iterable[str],
) -> pd.DataFrame:
    """Aggregate funnel counts and recompute the mature-only denominator."""

    columns = [
        "signup_date",
        "signups",
        "eligible_signups",
        "pending_signups",
        "activated_users",
        "activation_rate",
    ]
    selected = _selected_rows(
        funnel, "signup_date", start_date, end_date, channels, devices
    )
    if selected.empty:
        return pd.DataFrame(columns=columns)
    count_columns = columns[1:5]
    result = selected.groupby("signup_date", as_index=False)[count_columns].sum()
    result["activation_rate"] = (
        result["activated_users"] / result["eligible_signups"]
    ).where(result["eligible_signups"].gt(0))
    return result[columns].sort_values("signup_date", ignore_index=True)


def aggregate_retention(
    retention: pd.DataFrame,
    start_date: date,
    end_date: date,
    channels: Iterable[str],
    devices: Iterable[str],
) -> pd.DataFrame:
    """Aggregate mature retention counts and expose pending denominators."""

    base_columns = [
        "cohort_size",
        "d1_eligible_users",
        "d1_retained_users",
        "d7_eligible_users",
        "d7_retained_users",
    ]
    columns = [
        "cohort_date",
        "cohort_size",
        "d1_eligible_users",
        "d1_pending_users",
        "d1_retained_users",
        "d1_retention_rate",
        "d7_eligible_users",
        "d7_pending_users",
        "d7_retained_users",
        "d7_retention_rate",
    ]
    selected = _selected_rows(
        retention, "cohort_date", start_date, end_date, channels, devices
    )
    if selected.empty:
        return pd.DataFrame(columns=columns)
    result = selected.groupby("cohort_date", as_index=False)[base_columns].sum()
    result["d1_pending_users"] = (
        result["cohort_size"] - result["d1_eligible_users"]
    ).clip(lower=0)
    result["d7_pending_users"] = (
        result["cohort_size"] - result["d7_eligible_users"]
    ).clip(lower=0)
    result["d1_retention_rate"] = (
        result["d1_retained_users"] / result["d1_eligible_users"]
    ).where(result["d1_eligible_users"].gt(0))
    result["d7_retention_rate"] = (
        result["d7_retained_users"] / result["d7_eligible_users"]
    ).where(result["d7_eligible_users"].gt(0))
    return result[columns].sort_values("cohort_date", ignore_index=True)


def aggregate_weekly_cohorts(
    cohorts: pd.DataFrame,
    start_date: date,
    end_date: date,
    channels: Iterable[str],
    devices: Iterable[str],
) -> pd.DataFrame:
    """Aggregate cohort cells while keeping incomplete rates unknown."""

    columns = [
        "cohort_week",
        "weeks_since_signup",
        "cohort_size",
        "active_users",
        "is_complete",
        "retention_rate",
    ]
    selected = _selected_rows(
        cohorts, "cohort_week", start_date, end_date, channels, devices
    )
    if selected.empty:
        return pd.DataFrame(columns=columns)
    selected["is_complete"] = selected["is_complete"].fillna(False).astype(bool)
    result = (
        selected.groupby(["cohort_week", "weeks_since_signup"], as_index=False)
        .agg(
            cohort_size=("cohort_size", "sum"),
            active_users=("active_users", "sum"),
            is_complete=("is_complete", "all"),
        )
        .sort_values(["cohort_week", "weeks_since_signup"], ignore_index=True)
    )
    result["retention_rate"] = (result["active_users"] / result["cohort_size"]).where(
        result["is_complete"] & result["cohort_size"].gt(0)
    )
    return result[columns]


def latest_failure_message(latest_run: pd.DataFrame) -> str | None:
    """Return a short operational warning when the latest refresh failed."""

    if latest_run.empty:
        return None
    row = latest_run.iloc[0]
    if str(row.get("status", "")).lower() != "failed":
        return None
    return "The latest refresh failed. Showing the last available data."


@st.cache_data(ttl=30, show_spinner=False)
def load_dashboard_snapshot(
    database_path: str,
    fingerprint: DatabaseFingerprint,
) -> dict[str, pd.DataFrame]:
    """Load one cache-invalidated, transactionally consistent dashboard snapshot."""

    del fingerprint  # The value intentionally participates in Streamlit's cache key.
    queries = {
        "bounds": """
            WITH relevant_dates AS (
                SELECT CAST(event_timestamp AS DATE) AS metric_date FROM events
                UNION ALL
                SELECT signup_date AS metric_date FROM dim_users
                UNION ALL
                SELECT cohort_week AS metric_date FROM dim_users
            )
            SELECT MIN(metric_date) AS min_date, MAX(metric_date) AS max_date
            FROM relevant_dates
        """,
        "latest_run": """
            SELECT status
            FROM pipeline_runs
            ORDER BY started_at DESC, pipeline_run_id DESC
            LIMIT 1
        """,
        "population": """
            SELECT acquisition_channel, signup_device_type AS device_type,
                   COUNT(*)::BIGINT AS population
            FROM dim_users
            GROUP BY acquisition_channel, signup_device_type
            ORDER BY acquisition_channel, signup_device_type
        """,
        "daily_activity": """
            SELECT metric_date, acquisition_channel, device_type,
                   daily_active_users
            FROM daily_active_users
        """,
        "funnel": """
            SELECT signup_date, acquisition_channel, device_type, signups,
                   activated_users, activation_rate, eligible_signups,
                   pending_signups
            FROM signup_activation_funnel
        """,
        "retention": """
            SELECT cohort_date, acquisition_channel, device_type, cohort_size,
                   d1_retained_users, d1_retention_rate, d7_retained_users,
                   d7_retention_rate, d1_eligible_users, d7_eligible_users
            FROM daily_retention
        """,
        "cohorts": """
            SELECT cohort_week, activity_week, weeks_since_signup,
                   acquisition_channel, device_type, cohort_size, active_users,
                   retention_rate, is_complete
            FROM weekly_cohorts
        """,
    }
    frames: dict[str, pd.DataFrame] = {}
    with duckdb.connect(database_path, read_only=True) as conn:
        conn.execute("SET TimeZone = 'UTC'")
        conn.execute("BEGIN TRANSACTION")
        try:
            for name, sql in queries.items():
                frames[name] = conn.execute(sql).df()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return frames


def main() -> None:
    st.set_page_config(page_title="ProductPulse", layout="wide")
    st.html(CONSOLE_STYLES)
    st.html(CONSOLE_MASTHEAD)

    database_path = configured_database_path()
    state = database_file_state(database_path)
    if state == "missing":
        st.error("The ProductPulse database is missing. Run `make rebuild`.")
        return
    if state == "empty":
        st.error("The ProductPulse database is empty. Run `make rebuild`.")
        return

    try:
        fingerprint = database_fingerprint(database_path)
        snapshot = load_dashboard_snapshot(str(database_path), fingerprint)
    except (duckdb.Error, OSError):
        st.error(
            "The ProductPulse database could not be opened. Run `make rebuild`, "
            "then reload this page."
        )
        return

    failure = latest_failure_message(snapshot["latest_run"])
    if failure:
        st.warning(failure)

    bounds = snapshot["bounds"]
    if (
        bounds.empty
        or pd.isna(bounds.iloc[0]["min_date"])
        or pd.isna(bounds.iloc[0]["max_date"])
    ):
        st.info("The database is ready, but it contains no activity data yet.")
        return

    population = snapshot["population"]
    if population.empty:
        st.info("The database contains no user population to analyze.")
        return

    min_date = pd.Timestamp(bounds.iloc[0]["min_date"]).date()
    max_date = pd.Timestamp(bounds.iloc[0]["max_date"]).date()
    all_channels = sorted(population["acquisition_channel"].dropna().unique().tolist())
    all_devices = sorted(population["device_type"].dropna().unique().tolist())

    with st.sidebar:
        st.header("Filters")
        selected_dates = st.date_input(
            "Date range",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
        selected_channels = st.multiselect(
            "Acquisition channel", all_channels, default=all_channels
        )
        selected_devices = st.multiselect(
            "Signup device", all_devices, default=all_devices
        )

    date_range = normalize_date_range(selected_dates)
    if date_range is None:
        st.info("Complete the date range to calculate dashboard metrics.")
        return
    start_date, end_date = date_range
    population_count = selected_population_count(
        population, selected_channels, selected_devices
    )

    dau = build_dau_date_spine(
        snapshot["daily_activity"],
        start_date,
        end_date,
        selected_channels,
        selected_devices,
    )
    funnel = aggregate_funnel(
        snapshot["funnel"],
        start_date,
        end_date,
        selected_channels,
        selected_devices,
    )
    retention = aggregate_retention(
        snapshot["retention"],
        start_date,
        end_date,
        selected_channels,
        selected_devices,
    )
    cohorts = aggregate_weekly_cohorts(
        snapshot["cohorts"],
        start_date,
        end_date,
        selected_channels,
        selected_devices,
    )

    st.caption(
        f"{start_date.isoformat()} to {end_date.isoformat()} UTC, "
        f"{population_count:,} users"
    )
    activity_tab, funnel_tab, retention_tab, cohorts_tab = st.tabs(
        ["Activity", "Activation", "Retention", "Cohorts"]
    )
    with activity_tab:
        st.header("Daily active users")
        st.caption(
            "DAU = distinct users with at least one event on a UTC calendar date."
        )
        if population_count == 0:
            st.info("No users match the selected dimensions.")
        else:
            peak_dau = int(dau["active_users"].max()) if not dau.empty else 0
            active_days = int(dau["active_users"].gt(0).sum())
            average_dau = float(dau["active_users"].mean()) if not dau.empty else 0.0
            st.write(
                f"{population_count:,} users, {active_days:,} active days, "
                f"peak {peak_dau:,}, mean {average_dau:.1f} a day."
            )
            st.altair_chart(
                productpulse_line_chart(
                    dau,
                    "metric_date",
                    {"active_users": "Active users"},
                ),
                width="stretch",
            )
            with st.expander("Daily numbers", expanded=False):
                st.dataframe(
                    dau.rename(
                        columns={"metric_date": "Date", "active_users": "Active users"}
                    ),
                    hide_index=True,
                    width="stretch",
                )
                st.write(
                    "Dates with no activity inside the selected window appear as zero."
                )

    with funnel_tab:
        st.header("Activation within 7 days")
        st.caption(
            "Activation rate = eligible signups with a qualifying activation event "
            "from signup through day 7, inclusive."
        )
        if population_count == 0:
            st.info("No users match the selected dimensions.")
        elif funnel.empty:
            st.info("No signup cohorts match the selected date range.")
        else:
            totals = funnel.iloc[:, 1:5].sum()
            total_rate = (
                totals["activated_users"] / totals["eligible_signups"]
                if totals["eligible_signups"]
                else pd.NA
            )
            st.write(
                f"{int(totals['signups']):,} signups, "
                f"{int(totals['eligible_signups']):,} eligible, "
                f"{int(totals['activated_users']):,} activated, "
                f"rate {format_rate(total_rate)}, "
                f"{int(totals['pending_signups']):,} pending"
            )
            stage_counts = pd.DataFrame(
                (
                    {
                        "stage": "Signed up",
                        "users": int(totals["signups"]),
                    },
                    {
                        "stage": "Eligible for measurement",
                        "users": int(totals["eligible_signups"]),
                    },
                    {
                        "stage": "Activated within 7 days",
                        "users": int(totals["activated_users"]),
                    },
                )
            )
            st.altair_chart(
                productpulse_funnel_chart(stage_counts),
                width="stretch",
            )
            display_funnel = funnel.copy()
            display_funnel["activation_rate"] = display_funnel["activation_rate"].map(
                format_rate
            )
            display_funnel = display_funnel.rename(
                columns={
                    "signup_date": "Signup date",
                    "signups": "Signups",
                    "eligible_signups": "Eligible",
                    "pending_signups": "Pending",
                    "activated_users": "Activated",
                    "activation_rate": "Activation rate",
                }
            )
            with st.expander("Activation by cohort", expanded=False):
                st.dataframe(display_funnel, hide_index=True, width="stretch")
                st.write(
                    "A signup enters the denominator seven elapsed days after its "
                    "signup timestamp. The first "
                    "`activation` event through that boundary counts. Pending signups "
                    "remain in the table but do not depress the rate."
                )

    with retention_tab:
        st.header("Day-1 and day-7 retention")
        st.caption(
            "Retention = eligible cohort users with an event on the exact "
            "UTC date one or seven days after signup."
        )
        if population_count == 0:
            st.info("No users match the selected dimensions.")
        elif retention.empty:
            st.info("No retention cohorts match the selected date range.")
        else:
            cohort_total = int(retention["cohort_size"].sum())
            d1_eligible = int(retention["d1_eligible_users"].sum())
            d1_retained = int(retention["d1_retained_users"].sum())
            d7_eligible = int(retention["d7_eligible_users"].sum())
            d7_retained = int(retention["d7_retained_users"].sum())
            st.write(
                f"{cohort_total:,} cohort users, D1 "
                f"{format_rate(d1_retained / d1_eligible if d1_eligible else pd.NA)} "
                f"({d1_retained:,}/{d1_eligible:,}), D7 "
                f"{format_rate(d7_retained / d7_eligible if d7_eligible else pd.NA)} "
                f"({d7_retained:,}/{d7_eligible:,})"
            )
            chart = retention[["cohort_date", "d1_retention_rate", "d7_retention_rate"]]
            if chart.iloc[:, 1:].dropna(how="all").empty:
                st.write("All selected retention cohorts are pending maturity.")
            else:
                st.altair_chart(
                    productpulse_line_chart(
                        chart,
                        "cohort_date",
                        {
                            "d1_retention_rate": "D1 retention",
                            "d7_retention_rate": "D7 retention",
                        },
                        percentage=True,
                    ),
                    width="stretch",
                )
            display_retention = retention.copy()
            for column in ("d1_retention_rate", "d7_retention_rate"):
                display_retention[column] = display_retention[column].map(format_rate)
            display_retention = display_retention.rename(
                columns={
                    "cohort_date": "Cohort date",
                    "cohort_size": "Cohort users",
                    "d1_eligible_users": "D1 eligible",
                    "d1_pending_users": "D1 pending",
                    "d1_retained_users": "D1 retained",
                    "d1_retention_rate": "D1 rate",
                    "d7_eligible_users": "D7 eligible",
                    "d7_pending_users": "D7 pending",
                    "d7_retained_users": "D7 retained",
                    "d7_retention_rate": "D7 rate",
                }
            )
            with st.expander("Retention by cohort", expanded=False):
                st.dataframe(display_retention, hide_index=True, width="stretch")
                st.write(
                    "A rate appears only after the entire target UTC calendar date "
                    "has elapsed. Pending users are excluded from its denominator."
                )
                st.write(
                    f"D1 pending: {int(retention['d1_pending_users'].sum()):,}, "
                    f"D7 pending: {int(retention['d7_pending_users'].sum()):,}"
                )

    with cohorts_tab:
        st.header("Weekly signup cohorts")
        st.caption(
            "Weekly retention = distinct cohort users active in a Monday-aligned "
            "week divided by the starting cohort size."
        )
        if population_count == 0:
            st.info("No users match the selected dimensions.")
        elif cohorts.empty:
            st.info("No weekly cohorts match the selected date range.")
        else:
            incomplete_cells = int((~cohorts["is_complete"]).sum())
            cohort_count = int(cohorts["cohort_week"].nunique())
            period_count = int(cohorts["weeks_since_signup"].nunique())
            st.write(
                f"{cohort_count:,} cohorts, {period_count:,} observed weeks, "
                f"{incomplete_cells:,} pending cells"
            )
            st.altair_chart(
                productpulse_cohort_chart(cohorts),
                width="stretch",
            )
            display_cohorts = cohorts.copy()
            display_cohorts["maturity"] = display_cohorts["is_complete"].map(
                {True: "mature", False: "pending"}
            )
            display_cohorts["retention_rate"] = display_cohorts["retention_rate"].map(
                format_rate
            )
            display_cohorts = display_cohorts.drop(columns=["is_complete"]).rename(
                columns={
                    "cohort_week": "Cohort week",
                    "weeks_since_signup": "Week since signup",
                    "cohort_size": "Cohort users",
                    "active_users": "Active users",
                    "retention_rate": "Retention rate",
                    "maturity": "Maturity",
                }
            )
            with st.expander("Retention by week", expanded=False):
                st.dataframe(display_cohorts, hide_index=True, width="stretch")
                st.write(
                    "A rate appears only after the entire activity week has ended. "
                    "Pending cells stay N/A; their observed active-user counts "
                    "remain in the table."
                )


if __name__ == "__main__":
    main()
