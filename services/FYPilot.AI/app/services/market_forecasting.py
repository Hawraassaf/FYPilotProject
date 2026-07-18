from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import mean, stdev

from app.models.market_forecast_models import (
    MarketForecastPoint,
    MarketForecastResponse,
    MarketHistoryPoint,
    MarketTrendAnalysis,
)


MINIMUM_POINTS = 5
RECOMMENDED_POINTS = 8
TREND_THRESHOLD_POINTS_PER_WEEK = 0.5


@dataclass(frozen=True)
class LinearFit:
    intercept: float
    slope_per_day: float
    r_squared: float
    residual_standard_error: float
    x_mean: float
    sxx: float


def build_market_forecast(
    points: list[MarketHistoryPoint],
    horizon_weeks: int,
) -> MarketForecastResponse:
    normalized = _normalize_points(points)
    observed_count = len(normalized)
    generated_at = datetime.now(timezone.utc)

    trend = _trend_analysis(normalized)

    if observed_count < MINIMUM_POINTS:
        needed = MINIMUM_POINTS - observed_count

        return MarketForecastResponse(
            status="insufficient-data",
            forecastReady=False,
            forecastReliable=False,
            observedPoints=observed_count,
            minimumPoints=MINIMUM_POINTS,
            recommendedPoints=RECOMMENDED_POINTS,
            modelUsed=None,
            modelMae=None,
            naiveMae=None,
            trend=trend,
            forecastPoints=[],
            warning=(
                f"Add {needed} more saved market-demand observation"
                f"{'s' if needed != 1 else ''} before forecasting."
            ),
            generatedAt=generated_at,
        )

    x_values, y_values = _as_day_axis(normalized)
    final_fit = _fit_linear(x_values, y_values)

    linear_mae, naive_mae = _rolling_origin_mae(
        x_values,
        y_values,
    )

    use_linear = (
        final_fit is not None
        and linear_mae is not None
        and (
            naive_mae is None
            or linear_mae <= naive_mae
        )
    )

    if use_linear:
        model_used = "linear-trend-regression"
        model_mae = linear_mae
        forecast_points = _linear_forecast(
            normalized=normalized,
            x_values=x_values,
            y_values=y_values,
            fit=final_fit,
            horizon_weeks=horizon_weeks,
        )
    else:
        model_used = "naive-last-value"
        model_mae = naive_mae
        forecast_points = _naive_forecast(
            normalized=normalized,
            y_values=y_values,
            horizon_weeks=horizon_weeks,
        )

    reliable = (
        observed_count >= RECOMMENDED_POINTS
        and model_mae is not None
        and model_mae <= 12
    )

    warning = None

    if not reliable:
        warning = (
            "This is an early forecast. Collect at least "
            f"{RECOMMENDED_POINTS} observations and compare forecast errors "
            "before using it for a decision."
        )

    return MarketForecastResponse(
        status="ready",
        forecastReady=True,
        forecastReliable=reliable,
        observedPoints=observed_count,
        minimumPoints=MINIMUM_POINTS,
        recommendedPoints=RECOMMENDED_POINTS,
        modelUsed=model_used,
        modelMae=_round(model_mae),
        naiveMae=_round(naive_mae),
        trend=trend,
        forecastPoints=forecast_points,
        warning=warning,
        generatedAt=generated_at,
    )


def _normalize_points(
    points: list[MarketHistoryPoint],
) -> list[MarketHistoryPoint]:
    """
    Sort chronologically and remove exact duplicate timestamps.

    The last value at a duplicate timestamp wins.
    """
    deduplicated: dict[datetime, MarketHistoryPoint] = {}

    for point in points:
        timestamp = point.timestamp

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(
                tzinfo=timezone.utc
            )
        else:
            timestamp = timestamp.astimezone(
                timezone.utc
            )

        deduplicated[timestamp] = MarketHistoryPoint(
            timestamp=timestamp,
            demandScore=point.demand_score,
            confidenceScore=point.confidence_score,
        )

    return [
        deduplicated[key]
        for key in sorted(deduplicated)
    ]


def _as_day_axis(
    points: list[MarketHistoryPoint],
) -> tuple[list[float], list[float]]:
    first = points[0].timestamp

    x_values = [
        max(
            0.0,
            (point.timestamp - first).total_seconds()
            / 86400.0,
        )
        for point in points
    ]

    # If observations were created extremely close together, preserve their
    # sequence using tiny increasing offsets so the regression remains valid.
    for index in range(1, len(x_values)):
        if x_values[index] <= x_values[index - 1]:
            x_values[index] = (
                x_values[index - 1] + 1.0 / 1440.0
            )

    y_values = [
        float(point.demand_score)
        for point in points
    ]

    return x_values, y_values


def _fit_linear(
    x_values: list[float],
    y_values: list[float],
) -> LinearFit | None:
    count = len(x_values)

    if count < 2:
        return None

    x_mean = mean(x_values)
    y_mean = mean(y_values)

    sxx = sum(
        (x - x_mean) ** 2
        for x in x_values
    )

    if sxx <= 1e-12:
        return None

    sxy = sum(
        (x - x_mean) * (y - y_mean)
        for x, y in zip(x_values, y_values)
    )

    slope = sxy / sxx
    intercept = y_mean - slope * x_mean

    fitted = [
        intercept + slope * x
        for x in x_values
    ]

    residuals = [
        actual - predicted
        for actual, predicted in zip(
            y_values,
            fitted,
        )
    ]

    ss_res = sum(
        residual**2
        for residual in residuals
    )

    ss_total = sum(
        (value - y_mean) ** 2
        for value in y_values
    )

    if ss_total <= 1e-12:
        r_squared = 1.0
    else:
        r_squared = max(
            0.0,
            min(
                1.0,
                1.0 - ss_res / ss_total,
            ),
        )

    if count > 2:
        residual_standard_error = sqrt(
            ss_res / (count - 2)
        )
    else:
        residual_standard_error = 0.0

    return LinearFit(
        intercept=intercept,
        slope_per_day=slope,
        r_squared=r_squared,
        residual_standard_error=(
            residual_standard_error
        ),
        x_mean=x_mean,
        sxx=sxx,
    )


def _rolling_origin_mae(
    x_values: list[float],
    y_values: list[float],
) -> tuple[float | None, float | None]:
    """
    Compare one-step-ahead linear and naive forecasts.

    The first three observations initialize the rolling validation.
    """
    linear_errors: list[float] = []
    naive_errors: list[float] = []

    for index in range(3, len(y_values)):
        train_x = x_values[:index]
        train_y = y_values[:index]

        fit = _fit_linear(
            train_x,
            train_y,
        )

        if fit is not None:
            prediction = (
                fit.intercept
                + fit.slope_per_day
                * x_values[index]
            )

            linear_errors.append(
                abs(
                    y_values[index]
                    - _clamp_score(prediction)
                )
            )

        naive_errors.append(
            abs(
                y_values[index]
                - y_values[index - 1]
            )
        )

    return (
        mean(linear_errors)
        if linear_errors
        else None,
        mean(naive_errors)
        if naive_errors
        else None,
    )


def _linear_forecast(
    *,
    normalized: list[MarketHistoryPoint],
    x_values: list[float],
    y_values: list[float],
    fit: LinearFit,
    horizon_weeks: int,
) -> list[MarketForecastPoint]:
    last_timestamp = normalized[-1].timestamp
    first_timestamp = normalized[0].timestamp
    count = len(y_values)

    points: list[MarketForecastPoint] = []

    # Keep a small non-zero uncertainty width even when the fitted history is
    # perfectly flat. This avoids displaying false mathematical certainty.
    residual_scale = max(
        fit.residual_standard_error,
        1.5,
    )

    for horizon in range(1, horizon_weeks + 1):
        period = (
            last_timestamp
            + timedelta(weeks=horizon)
        )

        future_x = (
            period - first_timestamp
        ).total_seconds() / 86400.0

        predicted = (
            fit.intercept
            + fit.slope_per_day
            * future_x
        )

        if fit.sxx > 1e-12:
            prediction_error = (
                residual_scale
                * sqrt(
                    1.0
                    + 1.0 / count
                    + (
                        (future_x - fit.x_mean) ** 2
                        / fit.sxx
                    )
                )
            )
        else:
            prediction_error = (
                residual_scale * sqrt(horizon)
            )

        margin = 1.96 * prediction_error

        points.append(
            MarketForecastPoint(
                period=period,
                horizonWeek=horizon,
                predictedScore=_round(
                    _clamp_score(predicted)
                ),
                lowerBound=_round(
                    _clamp_score(
                        predicted - margin
                    )
                ),
                upperBound=_round(
                    _clamp_score(
                        predicted + margin
                    )
                ),
            )
        )

    return points


def _naive_forecast(
    *,
    normalized: list[MarketHistoryPoint],
    y_values: list[float],
    horizon_weeks: int,
) -> list[MarketForecastPoint]:
    last_timestamp = normalized[-1].timestamp
    last_value = y_values[-1]

    differences = [
        current - previous
        for previous, current in zip(
            y_values,
            y_values[1:],
        )
    ]

    if len(differences) >= 2:
        scale = max(
            stdev(differences),
            1.5,
        )
    else:
        scale = 3.0

    points: list[MarketForecastPoint] = []

    for horizon in range(1, horizon_weeks + 1):
        margin = (
            1.96
            * scale
            * sqrt(horizon)
        )

        points.append(
            MarketForecastPoint(
                period=(
                    last_timestamp
                    + timedelta(weeks=horizon)
                ),
                horizonWeek=horizon,
                predictedScore=_round(
                    _clamp_score(last_value)
                ),
                lowerBound=_round(
                    _clamp_score(
                        last_value - margin
                    )
                ),
                upperBound=_round(
                    _clamp_score(
                        last_value + margin
                    )
                ),
            )
        )

    return points


def _trend_analysis(
    points: list[MarketHistoryPoint],
) -> MarketTrendAnalysis:
    if len(points) < 2:
        return MarketTrendAnalysis(
            direction="insufficient-data",
            strength="insufficient-data",
            slopePerWeek=0.0,
            totalChange=0.0,
            volatility=0.0,
            rSquared=0.0,
            summary=(
                "At least two observations are required "
                "for trend analysis."
            ),
        )

    x_values, y_values = _as_day_axis(points)
    fit = _fit_linear(
        x_values,
        y_values,
    )

    differences = [
        current - previous
        for previous, current in zip(
            y_values,
            y_values[1:],
        )
    ]

    volatility = (
        stdev(differences)
        if len(differences) >= 2
        else abs(differences[0])
    )

    total_change = (
        y_values[-1] - y_values[0]
    )

    if fit is None:
        slope_per_week = 0.0
        r_squared = 0.0
    else:
        slope_per_week = (
            fit.slope_per_day * 7.0
        )
        r_squared = fit.r_squared

    if (
        slope_per_week
        > TREND_THRESHOLD_POINTS_PER_WEEK
    ):
        direction = "rising"
    elif (
        slope_per_week
        < -TREND_THRESHOLD_POINTS_PER_WEEK
    ):
        direction = "falling"
    else:
        direction = "stable"

    if r_squared >= 0.65:
        strength = "strong"
    elif r_squared >= 0.30:
        strength = "moderate"
    else:
        strength = "weak"

    summary = (
        f"The saved demand score is {direction} at approximately "
        f"{abs(slope_per_week):.2f} point(s) per week. "
        f"The trend fit is {strength} (R²={r_squared:.2f}), "
        f"with change volatility of {volatility:.2f} points."
    )

    return MarketTrendAnalysis(
        direction=direction,
        strength=strength,
        slopePerWeek=_round(
            slope_per_week
        ),
        totalChange=_round(
            total_change
        ),
        volatility=_round(
            volatility
        ),
        rSquared=_round(
            r_squared,
            digits=4,
        ),
        summary=summary,
    )


def _clamp_score(
    value: float,
) -> float:
    return max(
        0.0,
        min(
            100.0,
            float(value),
        ),
    )


def _round(
    value: float | None,
    *,
    digits: int = 2,
) -> float | None:
    if value is None:
        return None

    return round(
        float(value),
        digits,
    )