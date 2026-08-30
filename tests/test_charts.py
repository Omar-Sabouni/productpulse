from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from productpulse.charts import (
    productpulse_cohort_chart,
    productpulse_funnel_chart,
    productpulse_line_chart,
)


def test_line_chart_preserves_metric_semantics() -> None:
    frame = pd.DataFrame(
        {
            "cohort_date": [date(2026, 1, 1), date(2026, 1, 2)],
            "d1": [0.5, 0.75],
            "d7": [0.25, pd.NA],
        }
    )

    spec = productpulse_line_chart(
        frame,
        "cohort_date",
        {"d1": "D1 retention", "d7": "D7 retention"},
        percentage=True,
    ).to_dict()

    assert spec["mark"] == {"type": "line", "clip": True, "point": True}
    assert spec["encoding"]["y"]["axis"]["format"] == ".1%"
    assert spec["encoding"]["y"]["title"] == "Retention rate"
    assert spec["encoding"]["color"]["sort"] == [
        "D1 retention",
        "D7 retention",
    ]
    assert spec["encoding"]["strokeDash"]["field"] == "series"
    rows = next(iter(spec["datasets"].values()))
    assert {row["series"] for row in rows} == {"D1 retention", "D7 retention"}
    assert rows[-1]["value"] is None


def test_funnel_chart_keeps_stage_order_and_counts() -> None:
    frame = pd.DataFrame(
        {
            "stage": ["Signed up", "Eligible", "Activated"],
            "users": [10, 8, 4],
        }
    )

    spec = productpulse_funnel_chart(frame).to_dict()

    assert spec["mark"]["type"] == "bar"
    assert spec["encoding"]["y"]["sort"] == frame["stage"].tolist()
    assert spec["encoding"]["x"]["field"] == "users"
    assert next(iter(spec["datasets"].values()))[-1] == {
        "stage": "Activated",
        "users": 4,
    }


def test_cohort_chart_keeps_counts_and_marks_pending_cells() -> None:
    frame = pd.DataFrame(
        {
            "cohort_week": [date(2026, 1, 5), date(2026, 1, 5)],
            "weeks_since_signup": [0, 1],
            "cohort_size": [10, 10],
            "active_users": [10, 4],
            "retention_rate": [1.0, pd.NA],
            "is_complete": [True, False],
        }
    )

    spec = productpulse_cohort_chart(frame).to_dict()

    assert spec["mark"]["type"] == "rect"
    assert spec["encoding"]["x"]["sort"] == ["Week 0", "Week 1"]
    assert spec["encoding"]["color"]["scale"]["domain"] == [0, 1]
    rows = next(iter(spec["datasets"].values()))
    assert [row["maturity"] for row in rows] == ["mature", "pending"]
    assert rows[-1]["retention_rate"] is None


def test_line_chart_rejects_missing_or_empty_series() -> None:
    frame = pd.DataFrame({"metric_date": [date(2026, 1, 1)], "users": [1]})

    with pytest.raises(ValueError, match="Missing chart columns: missing"):
        productpulse_line_chart(
            frame,
            "metric_date",
            {"users": "Users", "missing": "Missing"},
        )
    with pytest.raises(ValueError, match="At least one chart series is required"):
        productpulse_line_chart(frame, "metric_date", {})
    with pytest.raises(ValueError, match="Missing funnel columns: stage"):
        productpulse_funnel_chart(pd.DataFrame({"users": [1]}))
    with pytest.raises(ValueError, match="Missing cohort chart columns"):
        productpulse_cohort_chart(pd.DataFrame({"cohort_week": [date(2026, 1, 1)]}))
