"""One run per attempt, also under racing requests and a retry that lands as a run finishes."""

from fastapi import BackgroundTasks

from app.features.attempts import pipeline


def _reset():
    pipeline._active.clear()
    pipeline._scheduled.clear()
    pipeline._rerun.clear()


def test_scheduling_twice_queues_one_run():
    _reset()
    background = BackgroundTasks()
    pipeline.schedule(background, 7, None, None)
    pipeline.schedule(background, 7, None, None)
    assert len(background.tasks) == 1
    assert pipeline.is_live(7)
    pipeline.unschedule(7)
    assert not pipeline.is_live(7)


def test_retry_during_a_run_makes_that_run_go_round_once_more(monkeypatch):
    _reset()
    calls = []

    class Session:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_run(session, settings, attempt_id, providers):
        calls.append(attempt_id)
        if len(calls) == 1:  # a retry arrives while this run is finishing
            background = BackgroundTasks()
            pipeline.schedule(background, attempt_id, None, None)
            assert background.tasks == []

    class Settings:
        effective_database_url = "sqlite://"

    monkeypatch.setattr(pipeline, "SessionLocal", Session)
    monkeypatch.setattr(pipeline, "_run", fake_run)
    pipeline.run(9, Settings(), None)
    assert calls == [9, 9]
    assert not pipeline.is_live(9)


def test_a_second_run_of_a_live_attempt_does_nothing(monkeypatch):
    _reset()
    called = []
    monkeypatch.setattr(pipeline, "_run", lambda *args: called.append(args))
    pipeline._active.add(5)
    try:
        pipeline.run(5, None, None)
    finally:
        pipeline._active.discard(5)
    assert called == []
