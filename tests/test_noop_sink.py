"""Tests for the no-op sink used during dry-runs."""

import pytest

from src.models import DailyMetrics, MemberState
from src.sinks.noop_sink import NoOpSink


@pytest.fixture
def sink():
    return NoOpSink()


def test_fetch_existing_returns_empty(sink: NoOpSink) -> None:
    states, ids = sink.fetch_existing()
    assert states == {}
    assert ids == {}


def test_write_members_is_no_op(sink: NoOpSink) -> None:
    member = MemberState(email="test@example.com")
    sink.write_members([member], {}, {})
    # No exception and no side effects to verify.
    assert True


def test_write_daily_metrics_is_no_op(sink: NoOpSink) -> None:
    metrics = DailyMetrics(date="2025-01-01")
    sink.write_daily_metrics(metrics)
    assert True


def test_write_sync_run_is_no_op(sink: NoOpSink) -> None:
    sink.write_sync_run({"run_id": "123"})
    assert True
