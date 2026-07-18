from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, stdev

from app.models.market_needs_models import (
    AnnualForecast,
    AnnualForecastPoint,
    AnnualTrendAnalysis,
    YearlyMarketPoint,
)


MINIMUM_TREND_YEARS = 3
MINIMUM_FORECAST_YEARS = 4
RELIABLE_FORECAST_YEARS = 5
MINIMUM_FORECAST_SPAN = 3
RELIABLE_FORECAST_SPAN = 4
TREND_THRESHOLD_POINTS_PER_YEAR = 0.75


@dataclass(frozen=True)
class LinearFit:
    intercept: float
    slope: float
    r_squared: float
    residual_standard_error: float
    x_mean: float
    sxx: float


def build_annual_forecast(
    points: list[YearlyMarketPoint],
    forecast_years: int,
) -> AnnualForecast:
    normalized = _normalize(points)
    average_confidence = (
        mean(point.confidence_score for point in normalized)
        if normalized
        else 0.0
    )
    trend = _trend_analysis(normalized)

    start_year = normalized[0].year if normalized else None
    end_year = normalized[-1].year if normalized else None
    span = (
        end_year - start_year
        if start_year is not None and end_year is not None
        else 0
    )

    if (
        len(normalized) < MINIMUM_FORECAST_YEARS
        or span < MINIMUM_FORECAST_SPAN
    ):
        return AnnualForecast(
            status="insufficient-source-backed-history",
            forecastReady=False,
            forecastReliable=False,
            modelUsed=None,
            modelMae=None,
            naiveMae=None,
            averageYearlyConfidence=round(average_confidence, 2),
            historicalStartYear=start_year,
            historicalEndYear=end_year,
            forecastHorizonYears=forecast_years,
            trend=trend,
            forecastPoints=[],
            warning=(
                "A yearly forecast requires at least four distinct, "
                "source-backed years spanning three or more years."
            ),
        )

    x_values = [float(point.year) for point in normalized]
    y_values = [float(point.demand_index) for point in normalized]
    fit = _fit_linear(x_values, y_values)
    linear_mae, naive_mae = _rolling_origin_mae(x_values, y_values)

    use_linear = (
        fit is not None
        and linear_mae is not None
        and fit.r_squared >= 0.20
        and (naive_mae is None or linear_mae <= naive_mae)
    )

    if use_linear:
        model_used = "annual-linear-trend"
        model_mae = linear_mae
        forecast_points = _linear_forecast(
            normalized,
            fit,
            forecast_years,
        )
    else:
        model_used = "annual-naive-last-value"
        model_mae = naive_mae
        forecast_points = _naive_forecast(
            normalized,
            forecast_years,
        )

    reliable = (
        len(normalized) >= RELIABLE_FORECAST_YEARS
        and span >= RELIABLE_FORECAST_SPAN
        and average_confidence >= 55
        and model_mae is not None
        and model_mae <= 12
        and trend.direction != "unstable"
    )

    warning = None
    if not reliable:
        warning = (
            "This is an early annual forecast. Treat it as directional, "
            "not as a market-size prediction. More verified yearly evidence "
            "will improve reliability."
        )

    return AnnualForecast(
        status="ready",
        forecastReady=True,
        forecastReliable=reliable,
        modelUsed=model_used,
        modelMae=_round(model_mae),
        naiveMae=_round(naive_mae),
        averageYearlyConfidence=round(average_confidence, 2),
        historicalStartYear=start_year,
        historicalEndYear=end_year,
        forecastHorizonYears=forecast_years,
        trend=trend,
        forecastPoints=forecast_points,
        warning=warning,
    )


def _normalize(points: list[YearlyMarketPoint]) -> list[YearlyMarketPoint]:
    by_year: dict[int, YearlyMarketPoint] = {}

    for point in points:
        current = by_year.get(point.year)
        if current is None or point.confidence_score > current.confidence_score:
            by_year[point.year] = point

    return [by_year[year] for year in sorted(by_year)]


def _fit_linear(
    x_values: list[float],
    y_values: list[float],
) -> LinearFit | None:
    if len(x_values) < 2:
        return None

    x_mean = mean(x_values)
    y_mean = mean(y_values)
    sxx = sum((x - x_mean) ** 2 for x in x_values)
    if sxx <= 1e-12:
        return None

    sxy = sum(
        (x - x_mean) * (y - y_mean)
        for x, y in zip(x_values, y_values)
    )
    slope = sxy / sxx
    intercept = y_mean - slope * x_mean
    fitted = [intercept + slope * x for x in x_values]
    residuals = [
        actual - predicted
        for actual, predicted in zip(y_values, fitted)
    ]
    ss_res = sum(value**2 for value in residuals)
    ss_total = sum((value - y_mean) ** 2 for value in y_values)
    r_squared = (
        1.0
        if ss_total <= 1e-12
        else max(0.0, min(1.0, 1.0 - ss_res / ss_total))
    )
    residual_standard_error = (
        sqrt(ss_res / (len(x_values) - 2))
        if len(x_values) > 2
        else 0.0
    )

    return LinearFit(
        intercept=intercept,
        slope=slope,
        r_squared=r_squared,
        residual_standard_error=residual_standard_error,
        x_mean=x_mean,
        sxx=sxx,
    )


def _rolling_origin_mae(
    x_values: list[float],
    y_values: list[float],
) -> tuple[float | None, float | None]:
    linear_errors: list[float] = []
    naive_errors: list[float] = []

    for index in range(3, len(y_values)):
        fit = _fit_linear(x_values[:index], y_values[:index])
        if fit is not None:
            predicted = fit.intercept + fit.slope * x_values[index]
            linear_errors.append(
                abs(y_values[index] - _clamp(predicted))
            )

        naive_errors.append(
            abs(y_values[index] - y_values[index - 1])
        )

    return (
        mean(linear_errors) if linear_errors else None,
        mean(naive_errors) if naive_errors else None,
    )


def _linear_forecast(
    points: list[YearlyMarketPoint],
    fit: LinearFit,
    horizon: int,
) -> list[AnnualForecastPoint]:
    count = len(points)
    last_year = points[-1].year
    scale = max(fit.residual_standard_error, 1.5)
    output: list[AnnualForecastPoint] = []

    for step in range(1, horizon + 1):
        year = last_year + step
        predicted = fit.intercept + fit.slope * year
        prediction_error = scale * sqrt(
            1.0
            + 1.0 / count
            + ((year - fit.x_mean) ** 2 / fit.sxx)
        )
        margin = 1.96 * prediction_error

        output.append(
            AnnualForecastPoint(
                year=year,
                predictedScore=_round(_clamp(predicted)),
                lowerBound=_round(_clamp(predicted - margin)),
                upperBound=_round(_clamp(predicted + margin)),
            )
        )

    return output


def _naive_forecast(
    points: list[YearlyMarketPoint],
    horizon: int,
) -> list[AnnualForecastPoint]:
    last_year = points[-1].year
    last_value = points[-1].demand_index
    differences = [
        current.demand_index - previous.demand_index
        for previous, current in zip(points, points[1:])
    ]
    scale = (
        max(stdev(differences), 2.0)
        if len(differences) >= 2
        else 4.0
    )

    return [
        AnnualForecastPoint(
            year=last_year + step,
            predictedScore=_round(_clamp(last_value)),
            lowerBound=_round(
                _clamp(last_value - 1.96 * scale * sqrt(step))
            ),
            upperBound=_round(
                _clamp(last_value + 1.96 * scale * sqrt(step))
            ),
        )
        for step in range(1, horizon + 1)
    ]


def _trend_analysis(
    points: list[YearlyMarketPoint],
) -> AnnualTrendAnalysis:
    if len(points) < MINIMUM_TREND_YEARS:
        return AnnualTrendAnalysis(
            direction="insufficient-data",
            strength="insufficient-data",
            slopePerYear=0,
            totalChange=0,
            volatility=0,
            rSquared=0,
            summary=(
                "At least three source-backed years are required "
                "for annual trend analysis."
            ),
        )

    x_values = [float(point.year) for point in points]
    y_values = [float(point.demand_index) for point in points]
    fit = _fit_linear(x_values, y_values)
    differences = [
        current - previous
        for previous, current in zip(y_values, y_values[1:])
    ]
    volatility = (
        stdev(differences)
        if len(differences) >= 2
        else abs(differences[0])
    )

    slope = fit.slope if fit else 0.0
    r_squared = fit.r_squared if fit else 0.0
    total_change = y_values[-1] - y_values[0]

    if r_squared < 0.20:
        direction = "unstable"
    elif slope > TREND_THRESHOLD_POINTS_PER_YEAR:
        direction = "rising"
    elif slope < -TREND_THRESHOLD_POINTS_PER_YEAR:
        direction = "falling"
    else:
        direction = "stable"

    if r_squared >= 0.70:
        strength = "strong"
    elif r_squared >= 0.40:
        strength = "moderate"
    else:
        strength = "weak"

    summary = (
        f"The source-backed annual demand index is {direction}. "
        f"The fitted change is {slope:+.2f} points per year, "
        f"with {strength} fit (R²={r_squared:.2f}) and "
        f"{volatility:.2f}-point year-to-year volatility."
    )

    return AnnualTrendAnalysis(
        direction=direction,
        strength=strength,
        slopePerYear=_round(slope),
        totalChange=_round(total_change),
        volatility=_round(volatility),
        rSquared=_round(r_squared, 4),
        summary=summary,
    )


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)
