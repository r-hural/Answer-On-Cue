import pytest
from pydantic import ValidationError

from app.config import Settings

BAND_MESSAGE = "Band bounds must increase and lie within 1–99."


def test_default_band_bounds(monkeypatch):
    monkeypatch.delenv("AOC_FACTUAL_BAND_BOUNDS", raising=False)
    assert Settings(_env_file=None).factual_band_bounds == (30, 60, 80)


def test_band_bounds_from_env(monkeypatch):
    monkeypatch.setenv("AOC_FACTUAL_BAND_BOUNDS", "[25,55,85]")
    assert Settings(_env_file=None).factual_band_bounds == (25, 55, 85)


@pytest.mark.parametrize("bounds", [(60, 30, 80), (0, 60, 80), (30, 60, 100), (30, 30, 80)])
def test_invalid_band_bounds_rejected(bounds):
    with pytest.raises(ValidationError, match=BAND_MESSAGE):
        Settings(_env_file=None, factual_band_bounds=bounds)


WEIGHTS_MESSAGE = "Correctness and completeness weights must each be 10–70 and sum to 80."


def test_default_factual_weights(monkeypatch):
    monkeypatch.delenv("AOC_FACTUAL_WEIGHTS", raising=False)
    assert Settings(_env_file=None).factual_weights == (30, 50)


def test_factual_weights_from_env(monkeypatch):
    monkeypatch.setenv("AOC_FACTUAL_WEIGHTS", "[40,40]")
    assert Settings(_env_file=None).factual_weights == (40, 40)


@pytest.mark.parametrize("weights", [(50, 50), (5, 75), (75, 5), (30, 40)])
def test_invalid_factual_weights_rejected(weights):
    with pytest.raises(ValidationError, match=WEIGHTS_MESSAGE):
        Settings(_env_file=None, factual_weights=weights)
