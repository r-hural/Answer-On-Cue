import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.features.coverage.rules import (
    Evidence,
    status_for,
    topic_status,
)

BASE = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
BANDS = {"W": "weak", "N": "needs_improvement", "G": "good", "V": "very_good"}
FACTUAL = {"W": 0.0, "N": 51.0, "G": 66.0, "V": 88.0}


def ev(attempt_id, band, day, *, round_id=None, independent=True, factual=None):
    code = BANDS[band]
    return Evidence(
        attempt_id=attempt_id,
        round_id=round_id if round_id is not None else attempt_id,
        at=BASE + timedelta(days=day),
        band=code,
        factual=factual if factual is not None else FACTUAL[band],
        independent=independent,
    )


def status(evidence):
    return status_for(evidence, UTC)


def test_no_evidence_is_not_practised():
    result = status([])
    assert result.status == "not_practised"
    assert result.deciding is None


def test_latest_weak_or_needs_improvement_is_needs_work():
    last = ev(2, "N", 2)
    result = status([ev(1, "V", 1), last])
    assert result.status == "needs_work"
    assert result.deciding == last


def test_dont_know_counts_as_weak():
    assert status([ev(1, "W", 1, factual=0.0)]).status == "needs_work"


def test_single_very_good_is_improving():
    assert status([ev(1, "V", 1)]).status == "improving"


def test_good_is_improving():
    assert status([ev(1, "G", 1)]).status == "improving"


def test_two_very_good_rounds_different_days_is_strong():
    assert status([ev(1, "V", 1), ev(2, "V", 3)]).status == "strong"


def test_two_very_good_same_day_is_not_strong():
    assert status([ev(1, "V", 1), ev(2, "V", 1)]).status == "improving"


def test_two_very_good_same_round_is_not_strong():
    midnight = datetime(2026, 10, 1, 23, 59, tzinfo=UTC)
    first = Evidence(1, 7, midnight, "very_good", 88.0, True)
    second = Evidence(2, 7, midnight + timedelta(minutes=2), "very_good", 88.0, True)
    assert status([first, second]).status == "improving"


def test_later_weak_overrides_strong():
    evidence = [ev(1, "V", 1), ev(2, "V", 3), ev(3, "N", 5)]
    assert status(evidence).status == "needs_work"
    evidence.append(ev(4, "V", 6))
    assert status(evidence).status == "improving"
    evidence.append(ev(5, "V", 8))
    assert status(evidence).status == "strong"


def test_later_good_keeps_strong():
    assert status([ev(1, "V", 1), ev(2, "V", 3), ev(3, "G", 5)]).status == "strong"


def test_assisted_success_after_weak_is_improving_with_help():
    helped = ev(2, "G", 2, independent=False)
    result = status([ev(1, "N", 1), helped])
    assert result.status == "improving"
    assert result.assisted is True
    assert result.deciding == helped
    result = status([ev(1, "N", 1), helped, ev(3, "N", 3)])
    assert result.status == "needs_work"
    assert result.assisted is False


def test_assisted_very_good_never_counts_toward_strong():
    assert status([ev(1, "V", 1), ev(2, "V", 3, independent=False)]).status == "improving"


def test_only_assisted():
    good = status([ev(1, "G", 1, independent=False)])
    assert (good.status, good.assisted) == ("improving", True)
    weak = status([ev(1, "W", 1, independent=False, factual=0.0)])
    assert weak.status == "needs_work"


def test_order_is_time_then_id():
    low = ev(1, "V", 1)
    high = ev(2, "N", 1)
    assert status([high, low]).status == "needs_work"
    assert status([low, high]).status == "needs_work"


def test_topic_status():
    assert topic_status(["strong", "strong"]) == "strong"
    assert topic_status(["strong", "not_practised"]) == "improving"
    assert topic_status(["improving", "needs_work"]) == "needs_work"
    assert topic_status(["not_practised", "not_practised"]) == "not_practised"
    assert topic_status([]) == "not_practised"


def very_good_at(*moments):
    return [Evidence(n + 1, n + 1, at, "very_good", 88.0, True) for n, at in enumerate(moments)]


AUCKLAND = timezone(timedelta(hours=13), "NZDT")


def test_days_follow_the_given_zone():
    # 23:30Z and 00:30Z the next UTC day are 12:30 and 13:30 of one Auckland day (NZDT, +13).
    # A fixed +13 stands in for ZoneInfo("Pacific/Auckland"): Windows has no tz database and
    # tzdata is not a dependency; on these dates Auckland is at +13.
    pair = very_good_at(
        datetime(2026, 10, 1, 23, 30, tzinfo=UTC), datetime(2026, 10, 2, 0, 30, tzinfo=UTC)
    )
    assert status_for(pair, UTC).status == "strong"
    assert status_for(pair, AUCKLAND).status == "improving"


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset is POSIX only")
def test_default_zone_is_the_server_zone_posix(monkeypatch):
    pair = very_good_at(
        datetime(2026, 10, 1, 23, 30, tzinfo=UTC), datetime(2026, 10, 2, 0, 30, tzinfo=UTC)
    )
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    time.tzset()
    try:
        assert status_for(pair).status == "improving"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_default_zone_is_the_server_zone():
    # Windows has no time.tzset, so the process zone cannot be switched here. Instead the moments
    # are placed around the server's own local midnight and UTC midnight: tz=None must split days
    # where the local clock does, whatever the zone of the machine running the tests.
    local_midnight = datetime(2026, 10, 2).astimezone()  # naive -> aware in the local zone
    around_local = very_good_at(
        local_midnight - timedelta(minutes=30), local_midnight + timedelta(minutes=30)
    )
    assert status_for(around_local).status == "strong"
    utc_midnight = datetime(2026, 10, 2, tzinfo=UTC)
    around_utc = very_good_at(
        utc_midnight - timedelta(minutes=30), utc_midnight + timedelta(minutes=30)
    )
    local_days = {datetime.fromtimestamp(e.at.timestamp()).date() for e in around_utc}
    expected = "strong" if len(local_days) == 2 else "improving"
    assert status_for(around_utc).status == expected
