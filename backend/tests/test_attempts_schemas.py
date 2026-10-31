import pytest
from pydantic import ValidationError
from test_attempts_support import events_body

from app.features.attempts.pipeline import operation_id
from app.features.attempts.providers import RegistryProviders
from app.features.attempts.schemas import AttemptEventsIn, AttemptResultOut
from app.features.providers.types import ProviderError
from app.features.providers.usage import InMemoryUsageRecorder

CONTRACT_KEYS = {
    "attempt_id",
    "outcome",
    "status",
    "stage",
    "recording",
    "failure",
    "withheld",
    "transcript",
    "metrics",
    "assessment",
}


def _result(**over) -> AttemptResultOut:
    base = dict(
        attempt_id=7,
        outcome="answered",
        status="processing",
        stage="transcribing",
        recording={"id": 31, "duration_ms": 12000},
        failure=None,
        withheld=False,
        transcript=None,
        metrics=None,
        assessment=None,
    )
    base.update(over)
    return AttemptResultOut(**base)


def test_events_accepts_contract_example():
    events = AttemptEventsIn.model_validate(events_body())
    assert (events.start_reason, events.capture_start_at) == ("auto", 9400.0)
    body = events_body()
    del body["listening_available"]
    assert AttemptEventsIn.model_validate(body).listening_available is True


def test_events_rejects_unknown_start_reason():
    with pytest.raises(ValidationError):
        AttemptEventsIn.model_validate(events_body(start_reason="voice"))


def test_result_has_exactly_the_contract_keys():
    dumped = _result().model_dump()
    assert set(dumped) == CONTRACT_KEYS
    assert dumped["recording"] == {"id": 31, "duration_ms": 12000}


def test_result_rejects_unknown_status():
    with pytest.raises(ValidationError):
        _result(status="done")


def test_operation_id():
    assert operation_id(7, "assess", 2) == "attempt-7:assess:2"
    assert operation_id(7, "transcribe", 1) == "attempt-7:transcribe:1"


def test_registry_providers_without_keys(settings):
    providers = RegistryProviders(settings, InMemoryUsageRecorder())  # nothing built yet
    with pytest.raises(ProviderError) as error:
        providers.transcriber()
    assert error.value.kind == "unavailable"
    with pytest.raises(ProviderError) as error:
        providers.assessor()
    assert error.value.kind == "unavailable"
