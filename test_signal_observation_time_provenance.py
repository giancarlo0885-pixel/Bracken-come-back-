"""Regression-first tests: a quote is not observable before its signal is persisted."""
from datetime import datetime, timedelta, timezone

from paper_exit_research import signal_price_samples


T0 = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def signal(*, quote_at=T0, created_at=T0, price=110):
    row = {
        "market": "crypto",
        "symbol": "TEST-USD",
        "price": price,
        "details": {
            "quote_verified": True,
            "symbol": "TEST-USD",
            "quote_timestamp": quote_at.isoformat(),
        },
    }
    if created_at is not None:
        row["created_at"] = created_at.isoformat() if isinstance(created_at, datetime) else created_at
    return row


def test_late_persisted_quote_cannot_be_seen_at_earlier_asof():
    row = signal(created_at=T0 + timedelta(minutes=30))
    assert not signal_price_samples([row], T0 + timedelta(minutes=10))


def test_observation_time_is_persistence_time_not_backdated_quote_time():
    persisted = T0 + timedelta(seconds=3)
    samples = signal_price_samples([signal(created_at=persisted)], T0 + timedelta(minutes=1))
    assert samples[("crypto", "TEST-USD")][0]["observed_at"] == persisted


def test_quote_from_future_relative_to_persistence_is_not_evidence():
    row = signal(quote_at=T0 + timedelta(seconds=5), created_at=T0)
    assert not signal_price_samples([row], T0 + timedelta(minutes=1))


def test_missing_or_malformed_persistence_timestamp_is_unqualified():
    rows = [signal(created_at=None), signal(created_at="not-a-timestamp")]
    assert not signal_price_samples(rows, T0 + timedelta(minutes=1))


def test_valid_quote_and_persistence_at_same_time_still_qualifies():
    samples = signal_price_samples([signal()], T0 + timedelta(seconds=1))
    assert len(samples[("crypto", "TEST-USD")]) == 1
    assert samples[("crypto", "TEST-USD")][0]["price"] == 110
