"""
Impact results, calculated only from numbers somebody actually recorded.

A figure appears when - and only when - both halves it needs are present, so an
incomplete record shows what it has and says nothing about what it lacks. Nothing
is estimated, defaulted or carried over from a different measurement, and the
*estimated* and *measured* savings are kept apart: a measured number is never
filled in from an estimate, and the variance between them exists only when both do.
"""

from decimal import ROUND_HALF_UP, Decimal

from automation.models import ImpactRecord


def _pct_reduction(before, after) -> Decimal | None:
    if before is None or after is None or before == 0:
        return None
    return ((Decimal(before) - Decimal(after)) / Decimal(before) * 100).quantize(
        Decimal('0.1'), rounding=ROUND_HALF_UP
    )


def _difference(before, after):
    if before is None or after is None:
        return None
    return Decimal(before) - Decimal(after)


def calculate(impact: ImpactRecord) -> dict:
    """Every derivable result, each `None` where its inputs were not recorded."""
    measured = impact.measured_hours_saved_per_week
    estimated = impact.estimated_hours_saved_per_week
    return {
        'time_saved_percent': _pct_reduction(
            impact.before_processing_minutes, impact.after_processing_minutes
        ),
        'people_reduced': _difference(impact.before_people_involved, impact.after_people_involved),
        'error_reduction_percent': _pct_reduction(
            impact.before_error_rate, impact.after_error_rate
        ),
        'workload_reduction_percent': _pct_reduction(
            impact.before_workload_hours, impact.after_workload_hours
        ),
        'cost_difference': _difference(impact.before_cost, impact.after_cost),
        'requests_difference': _difference(impact.before_requests, impact.after_requests),
        'estimated_hours_saved_per_week': estimated,
        'measured_hours_saved_per_week': measured,
        # Only with both: otherwise it would be a comparison with nothing.
        'hours_saved_variance': _difference(measured, estimated),
    }
