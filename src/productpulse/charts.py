"""Small Altair charts for the ProductPulse analytics workflow."""

from __future__ import annotations

from collections.abc import Mapping

import altair as alt
import pandas as pd


def productpulse_line_chart(
    frame: pd.DataFrame,
    date_column: str,
    series: Mapping[str, str],
    *,
    percentage: bool = False,
) -> alt.Chart:
    """Return a compact time-series chart."""

    required = {date_column, *series}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing chart columns: {', '.join(sorted(missing))}")
    if not series:
        raise ValueError("At least one chart series is required")
    chart_data = (
        frame[[date_column, *series]]
        .rename(columns=series)
        .melt(
            id_vars=date_column,
            var_name="series",
            value_name="value",
        )
    )
    chart_data[date_column] = pd.to_datetime(chart_data[date_column])
    value_format = ".1%" if percentage else ",.0f"
    y_title = "Retention rate" if percentage else "Active users"
    return (
        alt.Chart(chart_data)
        .mark_line(point=True, clip=True)
        .encode(
            x=alt.X(
                f"{date_column}:T",
                title=None,
                axis=alt.Axis(format="%d %b", labelAngle=0),
            ),
            y=alt.Y(
                "value:Q",
                title=y_title,
                axis=alt.Axis(format=value_format),
                scale=alt.Scale(zero=True, nice=True),
            ),
            color=alt.Color(
                "series:N",
                title=None,
                sort=list(series.values()),
                legend=alt.Legend(orient="top"),
            ),
            strokeDash=alt.StrokeDash("series:N", title=None),
            tooltip=(
                alt.Tooltip(f"{date_column}:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("series:N", title="Metric"),
                alt.Tooltip("value:Q", title=y_title, format=value_format),
            ),
        )
        .properties(height=300)
    )


def productpulse_funnel_chart(stage_counts: pd.DataFrame) -> alt.Chart:
    """Return a horizontal stage-count chart for the activation funnel."""

    required = {"stage", "users"}
    missing = required.difference(stage_counts.columns)
    if missing:
        raise ValueError(f"Missing funnel columns: {', '.join(sorted(missing))}")
    stages = stage_counts["stage"].tolist()
    return (
        alt.Chart(stage_counts)
        .mark_bar()
        .encode(
            x=alt.X("users:Q", title="Users", axis=alt.Axis(format=",")),
            y=alt.Y("stage:N", title=None, sort=stages),
            tooltip=(
                alt.Tooltip("stage:N", title="Stage"),
                alt.Tooltip("users:Q", title="Users", format=","),
            ),
        )
        .properties(height=170)
    )


def productpulse_cohort_chart(cohorts: pd.DataFrame) -> alt.Chart:
    """Return a compact cohort heatmap with incomplete periods left blank."""

    columns = (
        "cohort_week",
        "weeks_since_signup",
        "cohort_size",
        "active_users",
        "retention_rate",
        "is_complete",
    )
    required = set(columns)
    missing = required.difference(cohorts.columns)
    if missing:
        raise ValueError(f"Missing cohort chart columns: {', '.join(sorted(missing))}")

    chart_data = cohorts[list(columns)].copy()
    chart_data["cohort_week"] = pd.to_datetime(chart_data["cohort_week"])
    chart_data["period"] = chart_data["weeks_since_signup"].map(
        lambda value: f"Week {int(value)}"
    )
    chart_data["maturity"] = chart_data["is_complete"].map(
        {True: "mature", False: "pending"}
    )
    period_order = [
        f"Week {int(value)}"
        for value in sorted(chart_data["weeks_since_signup"].unique())
    ]
    height = min(360, max(170, 34 * chart_data["cohort_week"].nunique()))

    return (
        alt.Chart(chart_data)
        .mark_rect(stroke="white", strokeWidth=1)
        .encode(
            x=alt.X("period:N", title=None, sort=period_order),
            y=alt.Y(
                "cohort_week:T",
                title="Cohort week",
                timeUnit="yearmonthdate",
                axis=alt.Axis(format="%d %b"),
            ),
            color=alt.Color(
                "retention_rate:Q",
                title="Retention",
                scale=alt.Scale(domain=[0, 1]),
                legend=alt.Legend(format=".0%"),
            ),
            tooltip=(
                alt.Tooltip("cohort_week:T", title="Cohort week", format="%Y-%m-%d"),
                alt.Tooltip("period:N", title="Period"),
                alt.Tooltip("active_users:Q", title="Active users", format=","),
                alt.Tooltip("cohort_size:Q", title="Cohort users", format=","),
                alt.Tooltip("retention_rate:Q", title="Retention", format=".1%"),
                alt.Tooltip("maturity:N", title="Maturity"),
            ),
        )
        .properties(height=height)
    )
