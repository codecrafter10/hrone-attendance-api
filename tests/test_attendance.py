from datetime import datetime, timezone

import pytest

from app.main import (
    compute_late_minutes,
    compute_work_hours,
    compute_overtime,
    epoch_ms,
)


def utc_dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_late_minutes_grace_boundary():
    punch_in = utc_dt("2026-10-13T04:10:00")
    assert compute_late_minutes(punch_in, "09:30", "2026-10-13") == 0


def test_late_minutes_after_grace():
    punch_in = utc_dt("2026-10-13T04:10:01")
    assert compute_late_minutes(punch_in, "09:30", "2026-10-13") == 10


def test_work_hours_rounds_to_two_decimals():
    punch_in = utc_dt("2026-10-13T04:00:00")
    punch_out = utc_dt("2026-10-13T12:35:00")
    assert compute_work_hours(punch_in, punch_out) == 8.58


def test_overtime_requires_at_least_30_minutes():
    punch_out = utc_dt("2026-10-13T12:00:00")
    assert compute_overtime(punch_out, "09:00", "17:00", "2026-10-13") == 30


def test_epoch_ms_rejects_seconds_like_value():
    with pytest.raises((TypeError, ValueError)):
        epoch_ms(1791874200)


def test_epoch_ms_rejects_float():
    with pytest.raises((TypeError, ValueError)):
        epoch_ms(1791874200000.5)


def test_epoch_ms_rejects_boolean():
    with pytest.raises((TypeError, ValueError)):
        epoch_ms(True)
