from datetime import datetime, timezone

from realtime_runtime import cadence_for, market_session, status_age_seconds


def test_crypto_is_always_live():
    assert market_session("crypto") == "24/7"
    assert cadence_for("crypto").pulse_seconds >= 5


def test_stock_regular_session_detection():
    # 14:00 UTC is 10:00 ET during daylight saving time.
    now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)
    assert market_session("cash", now) == "regular"


def test_status_age_parses_iso_timestamp():
    now = datetime(2026, 7, 27, 14, 0, 10, tzinfo=timezone.utc)
    age = status_age_seconds("2026-07-27T14:00:00+00:00", now)
    assert age == 10
